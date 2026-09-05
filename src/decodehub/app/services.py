"""应用层用例编排（docs/30 运行时数据流）。

多源模型（ADR-008 v1.2）：**每源独立协议锁与解码**——各源时间轴独立、互不影响；
合并/对齐是库能力（Project.merged），不进入工具流程。
协议目录 PROTOCOL_CATALOG 从协议绑定（decode/bindings.py，ADR-014）派生；
新协议接入的唯一登记点在 protocols/<p>/binding.py（扩展指南）。
"""

from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

import numpy as np

from ..acquisition.project import Project, SourceEntry
from ..acquisition.service import load_capture
from ..acquisition.sniff import PLANNED_FORMATS, SUPPORTED_FORMATS
from ..decode.events import DecodeReport, DecodedEvent
from ..decode.graph import Graph, evaluate, validate
from ..decode.presentation import all_preview_kinds
from ..decode.bindings import (
    all_bindings,
    auto_map_channels,
    build_lock_graph,
    clone_graph,
    normalize_chain_steps,
    get_binding,
    strip_anchor_prefix,
)
from ..decode.registry import get_registry
from ..render.format import events_markdown, export_report, EXPORT_FORMAT_KEYS, EXPORT_FORMAT_SPECS
from ..render.plots import analog_plot
from ..render.contrib import resolve_route
from ..render.routes import RenderInput
from ..shared.errors import DecodehubError, ProtocolLockError
from ..shared.waves import Capture, DigitalWave
from .session import (ProtocolLock, SessionState, Stage, make_lock_key,
                      name_constraint_problems)

# ------------------------------------------------------------ 协议目录 ---

def _derive_protocol_catalog() -> dict[str, dict]:
    """工具层协议目录：从协议绑定（ADR-014）派生。

    唯一登记点在 protocols/<p>/binding.py；节点参数文档取自 Node.PARAMS.doc
    （与校验同源，不再人工复写），角色覆盖与工具级参数（uplink_source）由
    绑定补充。非模拟直达协议在**模拟源**上经 slicer 切片解码（lock_protocol
    放行 slicer PARAMS，ADR-021），目录同步列出（标注适用条件）——`params`
    命令 / capabilities 与实际可配集合同源，不再漏报切片参数。
    """
    reg = get_registry()
    slicer_docs = {name: p.doc for name, p in reg["slicer"].PARAMS.items() if p.doc}
    out: dict[str, dict] = {}
    for b in all_bindings():
        params = {name: p.doc for name, p in reg[b.node_type].PARAMS.items() if p.doc}
        if b.precond_node_type:
            params.update({name: p.doc for name, p in reg[b.precond_node_type].PARAMS.items()
                           if p.doc and name not in params})
        for r in b.roles:
            params.setdefault(r, f"{r} 角色显式指定通道名（覆盖自动映射）")
        params.update(b.tool_params_doc)
        if not b.analog_direct:
            params.update({k: f"{doc}（模拟源切片时）" for k, doc in slicer_docs.items()
                           if k not in params})
        out[b.protocol] = {"roles": list(b.roles), "params": params,
                           "needs": dict(b.needs), "hint": b.hint}
    return out


PROTOCOL_CATALOG: dict[str, dict] = _derive_protocol_catalog()


# ---------------------------------------------------------------- 源管理 ---

def _default_alias(project: Project | None, path: str) -> str:
    stem = Path(path).stem
    base = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_\-]+", "_", stem)[:24] or "src"
    taken = {e.alias for e in project.entries} if project else set()
    if base not in taken:
        return base
    i = 2
    while f"{base}_{i}" in taken:
        i += 1
    return f"{base}_{i}"


def _parse_t_wall(options: dict | None) -> datetime | None:
    raw = (options or {}).get("t_wall")
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    return datetime.fromisoformat(str(raw))


def _add_entry(state: SessionState, path: str, fmt: str | None, options: dict | None) -> tuple[str, Capture]:
    cap = load_capture(path, format_key=fmt, options=options)
    opts = dict(options or {})
    alias = opts.pop("alias", None) or _default_alias(state.project, path)
    if state.project is None:
        state.project = Project()
        state.stage = Stage.SOURCE_LOCKED
        state.locks.clear()
        state.reports.clear()
    state.project.add(SourceEntry(alias=alias, capture=cap,
                                  offset=float(opts.get("offset", 0.0)),
                                  t_wall=_parse_t_wall(opts),
                                  options=opts))
    return alias, cap


def ingest(state: SessionState, path: str, fmt: str | None, options: dict | None) -> str:
    """摄取采集：DISCOVERY 下创建工程；SOURCE_LOCKED 之后等价于 add_source。"""
    if state.project is not None and state.project.entries:
        return add_source(state, path, fmt, options)
    alias, cap = _add_entry(state, path, fmt, options)
    names = cap.channel_names()
    rate = cap.meta.sample_rate
    lines = [
        f"✅ 已摄取采集 `{cap.capture_id}`（源别名 `{alias}`）",
        f"- 格式: {cap.meta.format_key}（源: {cap.meta.source_kind}）",
        f"- 时长: {cap.duration:.6g} s；采样率: "
        + (f"{rate:.6g} Hz" if rate else "文件未提供（不影响解码）"),
    ]
    if names["digital"]:
        lines.append(f"- 数字通道 {len(names['digital'])}: {names['digital']}")
    if names["analog"]:
        lines.append(f"- 模拟通道 {len(names['analog'])}: {names['analog']}")
    lines.append("\n下一步: `add_source`（多采集器并行分析）/ `describe_capture` / `lock_protocol`")
    return "\n".join(lines)


def add_source(state: SessionState, path: str, fmt: str | None, options: dict | None) -> str:
    """追加源（各源独立分析，不影响已锁定的协议；ADR-008 v1.2）。"""
    if state.project is None:
        raise RuntimeError("先 lock_source 创建工程")
    alias, cap = _add_entry(state, path, fmt, options)
    names = cap.channel_names()
    lines = [
        f"✅ 已追加源 `{alias}`（{cap.meta.format_key}），工程共 {len(state.project.entries)} 个源: "
        + ", ".join(f"`{e.alias}`" for e in state.project.entries),
        f"- 新源: 数字 {len(names['digital'])} / 模拟 {len(names['analog'])} 通道",
        f"- 已锁协议的源: {sorted(state.locks) or '（无）'}（各源独立解码，互不影响）",
        f"\n下一步: `lock_protocol(protocol=…, source=\"{alias}\")`（多源时需指定 source）",
    ]
    return "\n".join(lines)


def describe_capture(state: SessionState) -> str:
    if state.project is None or not state.project.entries:
        raise RuntimeError("会话无采集源")
    lines = []
    for e in state.project.entries:
        cap = e.capture
        names = cap.channel_names()
        lines.append(
            f"### 源 `{e.alias}` [{cap.meta.format_key}] · 时长 {cap.duration:.6g}s"
            + (f" · 已锁 " + ",".join(
                l.protocol for l in state.locks.values() if l.source == e.alias)
               if any(l.source == e.alias for l in state.locks.values()) else "")
        )
        if cap.digital is not None:
            for name in cap.digital.channels:
                et, _lv = cap.digital.edge_stream(name)
                if et.size >= 2:
                    pulse = float(np.min(np.diff(et)))
                    freq = f"~{1.0 / (2 * pulse):.4g} Hz" if pulse > 0 else "-"
                else:
                    freq = "-"
                act = et.size / max(cap.digital.duration, 1e-12)
                lines.append(f"- 数字 `{name}`: 跳变 {et.size}（活动率 {act:.3g}/s，最短脉冲 {freq}）")
        for ch in cap.analog:
            v = ch.samples
            lines.append(
                f"- 模拟 `{ch.name}`: {ch.n} 点 @ "
                f"{f'{1 / ch.dt:.6g}' if ch.dt else '非均匀'}Hz，{ch.units}，"
                f"Vpp={float(np.ptp(v)):.4g}，min={float(v.min()):.4g}，max={float(v.max()):.4g}"
            )
        lines.append("")
    lines.append("下一步: `lock_protocol(protocol=…)`（多源时 params/source 指定源）")
    return "\n".join(lines)


# ------------------------------------------------------------ 协议锁定 ---

def lock_protocol(state: SessionState, protocol: str, params: dict | None,
                  source: str | None, name: str | None = None) -> tuple[str, Graph]:
    """按源锁定协议：source 缺省 = 唯一源；多源必须显式指定。

    name = 锁实例名（ADR-023）：同源同协议多路并存时区分（如两路 uart 分别
    钉不同通道 → name="uart1"/"uart2"，锁键 = `源|uart1`）；缺省 = 协议名
    （向后兼容）。重锁同一锁键 = 显式错误（先 unlock_protocol，绝不静默覆盖）。

    图模板/参数路由/角色映射全部来自协议绑定（decode/bindings.py，ADR-014）；
    本函数只做会话编排：源类型约束、锚点解析、同触发校验、别名注入。
    """
    if state.project is None or not state.project.entries:
        raise ProtocolLockError("请先 lock_source")
    binding = get_binding(protocol)
    if source is None and len(state.project.entries) > 1:
        raise ProtocolLockError(
            f"多源工程必须指定 source；可用: {state.source_aliases()}"
        )
    cap = state.capture_of(source)
    alias = source or state.single_alias()
    params = dict(params or {})
    defaults = cap.meta.extra.get("protocol_defaults", {})
    saved = defaults.get(protocol, {}) if isinstance(defaults, dict) else {}
    if isinstance(saved, dict):
        params = {**saved, **params}
    name = (name or "").strip() or protocol
    # 实例名约束（Bug 6）：不能包含锁键分隔符 |，否则报告键解析错位
    probs = name_constraint_problems(name, f"lock_protocol(源 {alias!r})")
    if probs:
        raise ProtocolLockError(probs[0])
    lock_key = make_lock_key(alias, name)
    if lock_key in state.locks:
        raise ProtocolLockError(
            f"锁 {lock_key} 已存在（重锁会被拒绝，绝不静默覆盖）；"
            f"先 unlock_protocol(source={alias!r}, protocol={name!r})，"
            f"或换一个 name 区分同源的多路同协议（如 {name}1/{name}2）"
        )

    # 源类型约束：模拟直达协议只接受模拟源（解调需要原始波形/幅度信息）
    if binding.analog_direct and (cap.digital is not None or not cap.analog):
        raise ProtocolLockError(
            f"{protocol} 需要模拟通道（该协议需要原始波形，数字切片信号不可用）；"
            f"该源请改用示波器/MCU ADC 导出"
        )
    if binding.analog_direct or cap.digital is None:
        chs = [c.name for c in cap.analog]
    else:
        chs = list(cap.digital.channels)
    cmap = auto_map_channels(chs, binding, params)

    # 锚点解析 + 同触发校验（会话编排，ADR-011：绝不假设锚点偏移）
    anchor: tuple[str, ProtocolLock] | None = None
    if binding.requires_sync:
        anchor = _resolve_sync_anchor(state, binding, alias, cap, params)

    # 参数严格校验（ADR-021）：可配参数 = 节点 PARAMS（单一权威）+ 角色覆盖 +
    # 会话级参数；未知键报错而非静默按默认值解码
    _reg = get_registry()
    _role_keys = set(binding.role_param.values())
    allowed = (set(binding.roles) | set(binding.tool_params_doc)
               | {k for k in _reg[binding.node_type].PARAMS} - _role_keys)
    if binding.analog_direct and binding.precond_node_type:
        allowed |= {k for k in _reg[binding.precond_node_type].PARAMS} - _role_keys
    if not binding.analog_direct and cap.digital is None:
        allowed |= set(_reg["slicer"].PARAMS)
    unknown = set(params) - allowed
    if unknown:
        raise ProtocolLockError(
            f"未知参数 {sorted(unknown)}（是拼写错误？将被忽略的键一律拒绝）；"
            f"{protocol} 可配参数: {sorted(allowed)}"
        )

    graph, input_nodes = build_lock_graph(
        binding,
        channel_map=cmap,
        tool_params=params,
        source_kind="analog" if (binding.analog_direct or cap.digital is None) else "digital",
        anchor_graph=anchor[1].graph if anchor else None,
    )
    validate(graph, get_registry())
    source_inputs = {node: (anchor[0] if role == "anchor" else alias)
                     for role, node in input_nodes.items()}

    state.locks[lock_key] = ProtocolLock(source=alias, protocol=protocol,
                                         params=params, channel_map=cmap, graph=graph,
                                         source_inputs=source_inputs,
                                         graph_kind=binding.graph_kind_for(cap),
                                         name=name)
    state.memos.pop(lock_key, None)  # 重建的图参数可能变化,缓存一律淘汰（run_decode 自行继承）
    state.stage = Stage.READY
    role_txt = ", ".join(f"{r}→`{c}`" for r, c in cmap.items())
    plan = (
        f"✅ 锁 `{lock_key}` 已锁定: **{protocol}**（通道映射: {role_txt}）\n\n"
        f"解码计划（inspect_graph 可查）:\n```\n{graph.to_text()}\n```"
    )
    return plan, graph


def _resolve_sync_anchor(state: SessionState, binding, alias: str, cap, params: dict) -> tuple[str, ProtocolLock]:
    """定位 requires_sync 协议的锚锁并做同触发校验（下行以上行帧网格为锚，ADR-011）。

    锚可按 源别名 / 完整锁键 `源|实例名` / 实例名 指定（ADR-023：同源多实例）；
    缺省 = 唯一锚锁。返回（锚源别名, 锚锁）。
    """
    sync_protocol = binding.requires_sync
    sync_param = f"{sync_protocol}_source"
    sync_locks = [l for l in state.locks.values() if l.protocol == sync_protocol]
    keys = {f"{l.source}|{l.report_name}": l for l in sync_locks}
    wanted = params.get(sync_param)
    if wanted:
        by_alias = {l.source: l for l in sync_locks}
        by_name = {l.report_name: l for l in sync_locks}
        anchor_lock = (keys.get(wanted) or by_alias.get(wanted) or by_name.get(wanted))
        if anchor_lock is None:
            raise ProtocolLockError(
                f"{sync_param}={wanted!r} 未命中任何 {sync_protocol} 锁；"
                f"已锁: {sorted(keys)}（可用 源别名 / 实例名 / 完整键）"
            )
    elif len(sync_locks) == 1:
        anchor_lock = sync_locks[0]
    else:
        raise ProtocolLockError(
            f"{binding.protocol} 需要 {sync_param} 参数指定锚锁"
            + (f"；已锁 {sync_protocol}: {sorted(keys)}" if sync_locks
               else f"（请先 lock_protocol(protocol='{sync_protocol}')）")
        )
    wanted = f"{anchor_lock.source}|{anchor_lock.report_name}"  # 物化为完整键（重建/档案复用）
    params[sync_param] = wanted
    anchor_cap = state.capture_of(anchor_lock.source)
    if not anchor_cap.analog:
        raise ProtocolLockError(f"锚源 {anchor_lock.source!r} 无模拟通道")
    # 同触发校验：锚/本源必须来自同一次采集（时间轴一致），跨仪器对齐被
    # ADR-008 v1.2 裁定不可行，此处不放松
    t0_a = float(anchor_cap.analog[0].t0)
    t0_b = float(cap.analog[0].t0)
    if abs(t0_a - t0_b) > 1e-3:
        raise ProtocolLockError(
            f"锚源({anchor_lock.source}) 与本源({alias}) t0 相差 {abs(t0_a - t0_b)*1e3:.2f} ms——"
            f"{binding.protocol} 锚定要求两通道来自同一次采集（同触发）。"
            f"请用示波器双通道同时导出。"
        )
    return anchor_lock.source, anchor_lock


def unlock_protocol(state: SessionState, source: str | None, protocol: str | None) -> str:
    if not state.locks:
        return "当前没有已锁定的协议。"
    hits = [(k, l) for k, l in state.locks.items()
            if source is None or k == source or l.source == source]
    if protocol:
        hits = [(k, l) for k, l in hits
                if l.protocol == protocol or (l.name and l.name == protocol)]
    if not hits:
        return f"无匹配协议锁（source={source!r}, protocol={protocol!r}）；" \
               f"已有: {sorted(state.locks)}"
    for k, _l in hits:
        state.locks.pop(k, None)
        state.reports.pop(k, None)
        state.memos.pop(k, None)
    if not state.locks:
        state.stage = Stage.SOURCE_LOCKED
    left = [f"{l.source}|{l.protocol}" for l in state.locks.values()]
    return "已解锁 " + ", ".join(k for k, _ in hits) + f"；剩余: {left or '无'}"


# ---------------------------------------------------------------- 管线（ADR-019）---

PIPE_UP_PREFIX = "up_"  # 管线克隆上游子图的节点 id 前缀


def _report_node(lock: ProtocolLock) -> str:
    """报告输出节点 = 图汇（无出边节点）。

    协议图汇即 *_decode（兼容旧硬编码语义）；管线图汇即链尾节点。
    与锚扇入的"sync 抽头=汇"（ADR-014）同一条结构性判定。
    """
    has_out = {e.src for e in lock.graph.edges}
    sinks = [nid for nid in lock.graph.nodes if nid not in has_out]
    if len(sinks) != 1:
        raise ProtocolLockError(f"锁 {lock.source}|{lock.protocol} 的图应有唯一报告节点，实际: {sinks}")
    return sinks[0]


def _resolve_tap(state: SessionState, tap: str | None) -> ProtocolLock:
    """tap 定位上游锁：完整键 `源|协议` > 唯一协议名 > 唯一源 > 缺省唯一锁。"""
    if not state.locks:
        raise ProtocolLockError("尚未锁定协议，管线没有可 tap 的上游")
    if tap:
        if tap in state.locks:
            return state.locks[tap]
        hits = [l for l in state.locks.values() if l.protocol == tap]
        if len(hits) == 1:
            return hits[0]
        hits = [l for l in state.locks.values() if l.source == tap]
        if len(hits) == 1:
            return hits[0]
        raise ProtocolLockError(
            f"tap {tap!r} 无法唯一定位锁（可用 键/协议名/源名）；已有: {sorted(state.locks)}"
        )
    if len(state.locks) == 1:
        return next(iter(state.locks.values()))
    raise ProtocolLockError(f"多锁工程必须指定 tap（源|协议）；已有: {sorted(state.locks)}")


def bind_pipeline(state: SessionState, name: str, tap: str | None,
                  chain: list[dict]) -> str:
    """绑定管线（ADR-019）：tap 某协议锁的输出（图汇），串一条通用节点链成独立报告 sink。

    管线实现为特殊 ProtocolLock（键 `源|管线名`，protocol 字段 = 管线名，
    params._pipeline 标记）——run_decode/get_events/export/render 的既有 sink
    语义全部按锁键工作，管线在会话层零特判。管线本身可再被 tap（链上链）。
    """
    if state.project is None or not state.project.entries:
        raise ProtocolLockError("请先 lock_source")
    if not chain:
        raise ProtocolLockError("管线 chain 不能为空")
    # 管线名约束（Bug 6）：不能包含锁键分隔符 |
    probs = name_constraint_problems(name, "bind_pipeline", what="管线名")
    if probs:
        raise ProtocolLockError(probs[0])
    upstream = _resolve_tap(state, tap)
    if any(l.protocol == name for l in state.locks.values()):
        raise ProtocolLockError(f"名称 {name!r} 已被协议/管线占用")
    # 管线 sink 键 = `源|管线名`，与任何既有锁/管线同键即拒绝（Bug 2/2b）：
    # 命名锁（name ≠ 协议名）与同源同名管线都会被这里拦下，绝不静默覆盖报告
    lock_key = make_lock_key(upstream.source, name)
    if lock_key in state.locks:
        occ = state.locks[lock_key]
        kind = "管线" if occ.params.get("_pipeline") else "协议锁"
        raise ProtocolLockError(
            f"管线键 {lock_key} 已被{kind}（{occ.protocol}）占用——"
            f"绑定会静默覆盖它的报告；请换一个管线名（绝不静默覆盖）"
        )
    registry = get_registry()

    def _err(msg: str) -> None:
        raise ProtocolLockError(msg)

    chain = normalize_chain_steps(chain, _err)

    # 克隆上游子图。注入点沿用上游锁登记的 source_inputs（节点 → 源别名）——
    # 普通锁单根、锚扇入锁多根（下行：上行子图 + 本源）都被覆盖；
    # 唯一性只约束汇（= 报告输出节点）。
    graph = Graph()
    up_nodes, up_edges = clone_graph(upstream.graph, PIPE_UP_PREFIX)
    for nid, ntype, p in up_nodes:
        graph.add_node(nid, ntype, **p)
    for s, sp, d, dp in up_edges:
        graph.add_edge(s, sp, d, dp)
    has_out = {e.src for e in upstream.graph.edges}
    sinks = [nid for nid in upstream.graph.nodes if nid not in has_out]
    if len(sinks) != 1:
        raise ProtocolLockError(
            f"tap 锁 `{upstream.source}|{upstream.protocol}` 的图应有唯一汇（报告节点），"
            f"实际: {sinks}"
        )
    sink_cls = registry[upstream.graph.nodes[sinks[0]].type]
    prev_type = sink_cls.OUTPUTS.get("out")
    if prev_type is None:
        raise ProtocolLockError(f"tap 锁的汇节点没有 out 端口: {sinks[0]}")

    source_inputs = {f"{PIPE_UP_PREFIX}{nid}": a
                     for nid, a in upstream.source_inputs.items()}
    prev_id = f"{PIPE_UP_PREFIX}{sinks[0]}"
    chain_ids = []
    for i, step in enumerate(chain):
        ntype = step["type"]
        cls = registry.get(ntype)
        if cls is None:
            raise ProtocolLockError(
                f"chain[{i}] 节点类型未注册: {ntype!r}；可用: {sorted(registry)}"
            )
        if len(cls.INPUTS) != 1:
            raise ProtocolLockError(
                f"chain[{i}] {ntype} 是多输入节点；管线链只支持单输入节点"
            )
        in_port, in_type = next(iter(cls.INPUTS.items()))
        if in_type != prev_type:
            raise ProtocolLockError(
                f"chain[{i}] {ntype} 输入端口类型 {in_type!r} 与上游输出 {prev_type!r} 不匹配"
                f"（端口类型严格相等，无隐式转换）"
            )
        nid = f"pipe{i}_{ntype}"
        graph.add_node(nid, ntype, **(step.get("params") or {}))
        graph.add_edge(prev_id, "out", nid, in_port)
        prev_id, prev_type = nid, cls.OUTPUTS.get("out")
        chain_ids.append(nid)
    validate(graph, registry)

    state.locks[lock_key] = ProtocolLock(
        source=upstream.source, protocol=name,
        params={"_pipeline": True, "tap": f"{upstream.source}|{upstream.protocol}",
                "chain": chain},
        channel_map={}, graph=graph, source_inputs=source_inputs,
        graph_kind=upstream.graph_kind,
    )
    state.memos.pop(lock_key, None)
    state.stage = Stage.READY
    flow = " → ".join(s["type"] for s in chain)
    plan = (
        f"✅ 管线已绑定: `{upstream.source}|{name}`"
        f"（tap {upstream.protocol} 输出 → {flow}）\n\n"
        f"解码计划（inspect_graph 可查）:\n```\n{graph.to_text()}\n```"
    )
    return plan


# ---------------------------------------------------------------- 解码 ---

def _locks_for(state: SessionState, source: str | None) -> list[tuple[str, ProtocolLock]]:
    """源别名（或协议锁键）→ [(键, 锁)]。"""
    if not state.locks:
        raise ProtocolLockError("请先 lock_protocol")
    if source is None:
        return list(state.locks.items())
    if source in state.locks:  # 完整锁键
        return [(source, state.locks[source])]
    hits = [(k, l) for k, l in state.locks.items() if l.source == source]
    if not hits:
        raise ProtocolLockError(
            f"源 {source!r} 尚未锁定协议；已锁: "
            f"{[f'{l.source}|{l.protocol}' for l in state.locks.values()]}"
        )
    return hits


def run_decode(state: SessionState, overrides: dict | None, source: str | None) -> str:
    """执行解码：source/键 缺省 = 全部锁（一源可有多协议锁并行）；overrides 单锁时有效。"""
    targets = _locks_for(state, source)
    if overrides and len(targets) > 1:
        raise ProtocolLockError("overrides 仅在指定单锁（source 或 源|协议 键）时有效")

    sections = []
    for key, lock in targets:
        graph, params = lock.graph, lock.params
        memo = state.memos.pop(key, None) or {}
        if overrides:
            if lock.params.get("_pipeline"):
                raise ProtocolLockError(
                    "管线不支持 overrides；请调整其上游锁的参数后重建管线"
                )
            merged = {**lock.params, **overrides}
            old_graph = lock.graph
            if lock.protocol == "downlink":
                graph, params = _rebuild_downlink(lock, merged), merged
            else:
                state.locks.pop(key, None)  # 重建即替换（重锁拒绝只针对外部重复绑定）
                _p, graph = lock_protocol(state, lock.protocol, merged, lock.source,
                                          name=lock.name or None)
            memo = _inherit_memo(old_graph, graph, memo)
        node_id = _report_node(lock)
        sources = {node: {"in": state.capture_of(a)}
                   for node, a in lock.source_inputs.items()}
        t0 = time.perf_counter()
        memo = evaluate(graph, get_registry(), targets=[node_id], sources=sources, memo=memo)
        state.memos[key] = memo
        if "slice" in memo:  # 切片所用阈值回写 meta（docs/40；供图表标注）
            state.capture_of(lock.source).meta.threshold_v = memo["slice"]["threshold"]
        wall_ms = (time.perf_counter() - t0) * 1000
        events: list[DecodedEvent] = memo[node_id]["out"]
        report = DecodeReport(
            protocol=lock.report_name,
            params={k: v for k, v in graph.nodes[node_id].params.items()},
            events=events, node_id=node_id, wall_ms=wall_ms,
        )
        state.reports[key] = report
        sections.append(_summary_section(lock.source, report))
    n = len(targets)
    header = "## 解码完成\n" + (f"（{n} 个协议锁并行解码）\n" if n > 1 else "")
    return header + "\n".join(sections)


def _inherit_memo(old_graph: Graph, new_graph: Graph, memo: dict | None) -> dict:
    """跨图继承求值缓存：仅保留 type+params 完全一致的节点（参数变化即失效）。"""
    memo = memo or {}
    kept: dict = {}
    for nid, out in memo.items():
        old_spec, new_spec = old_graph.nodes.get(nid), new_graph.nodes.get(nid)
        if (old_spec is not None and new_spec is not None
                and old_spec.type == new_spec.type
                and old_spec.params == new_spec.params):
            kept[nid] = out
    return kept


def _rebuild_downlink(lock: ProtocolLock, merged_params: dict) -> Graph:
    """下行锁参数重建：从锁内嵌克隆还原锚子图（同别名上行锁可能已不存在），
    经同一绑定模板重建——图构建逻辑全库只有 build_lock_graph 一份（ADR-014）。"""
    binding = get_binding(lock.protocol)
    graph, _nodes = build_lock_graph(
        binding,
        channel_map=lock.channel_map,
        tool_params=merged_params,
        source_kind="analog",
        anchor_graph=strip_anchor_prefix(lock.graph),
    )
    validate(graph, get_registry())
    return graph


def _summary_section(alias: str, report: DecodeReport) -> str:
    c = report.counts()
    by_kind = "\n".join(f"  - {k}: {v}" for k, v in c["by_kind"].items()) or "  - 无"
    err_line = (f"  ⚠️ 解码错误事件 {c['errors']} 个（get_events has_errors=true）"
                if c["errors"] else "")
    preview = [e for e in report.events if e.kind in all_preview_kinds()][:20]
    table = events_markdown(preview)
    return (f"### 源 `{alias}`（{report.protocol}，{report.wall_ms:.1f} ms）\n"
            f"事件 {c['total']}：\n{by_kind}\n{err_line}\n\n{table}\n")


def _resolve_report(state: SessionState, source: str | None, protocol: str | None):
    hits = [(k, r) for k, r in state.reports.items()
            if source is None or k == source or k.startswith(source + "|")]
    if protocol:
        hits = [(k, r) for k, r in hits if r.protocol == protocol]
    if not hits:
        raise ProtocolLockError(
            f"无匹配解码报告（source={source!r}, protocol={protocol!r}）；已有: "
            f"{sorted(state.reports)}"
        )
    if len(hits) > 1:
        srcs = {k.split("|")[0] for k, _ in hits}
        hint = ("请用 source 参数指定源"
                if len(srcs) > 1 else "请用 protocol 参数区分同源的多协议")
        raise ProtocolLockError(
            f"解码报告不唯一，{hint}: {[(k, r.protocol) for k, r in hits]}"
        )
    key, rep = hits[0]
    alias = key.split("|")[0]
    return key, alias, rep


def filter_events(state: SessionState, source: str | None, protocol: str | None,
                  kind: str | None, t_min: float | None, t_max: float | None,
                  has_errors: bool | None) -> list[DecodedEvent]:
    _key, _alias, report = _resolve_report(state, source, protocol)
    out = []
    for ev in report.events:
        if kind and ev.kind != kind:
            continue
        if t_min is not None and ev.t_start < t_min:
            continue
        if t_max is not None and ev.t_start > t_max:
            continue
        if has_errors is True and not ev.errors:
            continue
        if has_errors is False and ev.errors:
            continue
        out.append(ev)
    return out


def export_events(state: SessionState, fmt: str, path: str | None,
                  source: str | None, protocol: str | None = None) -> Path:
    """导出解码报告：格式查注册表（render/format.py ADR-019），应用层不分派。"""
    _key, alias, report = _resolve_report(state, source, protocol)
    cap = state.capture_of(alias)
    spec = EXPORT_FORMAT_SPECS.get(fmt)
    if spec is None:
        raise DecodehubError(f"未知导出格式 {fmt!r}；可用: {list(EXPORT_FORMAT_KEYS)}")
    p = Path(path) if path else store_path(state, cap.capture_id, f"events.{spec.ext}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(export_report(fmt, report), encoding="utf-8")
    state.artifacts.register(p, "export", f"源 {alias}：{report.counts()['total']} 事件（{fmt}）")
    return p


def store_path(state: SessionState, capture_id: str, name: str) -> Path:
    return state.artifacts.path_for(capture_id, name)


def _alias_of_report(state: SessionState, report: DecodeReport) -> str:
    for alias, r in state.reports.items():
        if r is report:
            return alias
    raise RuntimeError("报告不属于任何源")


def _next_figure_path(state: SessionState, cap: Capture, stem: str) -> Path:
    """制品命名：out/<capture_id>/<stem>_<序号>.png（按登记表计数，确定性幂等）。"""
    n = 1 + len([a for a in state.artifacts.items
                 if a.kind == "figure" and a.path.name.startswith(f"{stem}_")])
    return state.artifacts.path_for(cap.capture_id, f"{stem}_{n}.png")


def _materialize_slice(state: SessionState, key: str, lock, cap: Capture) -> DigitalWave:
    """模拟源切片：复用图求值取切片输出（命中会话 memo——run_decode 已算过 pick/slice，零重算）。"""
    sl = evaluate(lock.graph, get_registry(), targets=["slice"],
                  sources={"apick": {"in": cap}}, memo=state.memos.get(key))
    state.memos[key] = sl
    return sl["slice"]["out"]


def render_timing(state: SessionState, t_min, t_max, max_frames, dpi, source,
                  protocol: str | None = None,
                  filename: str | None = None) -> tuple[Path, str]:
    """渲染某源解码时序：两级查表分派（render/contrib.py ADR-023 → routes.py ADR-019）。

    协议客制路由优先（分派族 = 首事件 kind 前缀，管线报告的 protocol 字段是
    管线名故不可用；空报告退 report.protocol），无客制按锁的 graph_kind 走
    通用路由。应用层只负责物化切片与组装输入，画法选择全在路由表。
    """
    _key, alias, report = _resolve_report(state, source, protocol)
    lock = next((l for l in state.locks.values()
                 if l.source == alias and l.report_name == report.protocol), None)
    cap = state.capture_of(alias)
    kind = lock.graph_kind if lock else "digital"
    family = report.events[0].kind.split(".")[0] if report.events else report.protocol
    route = resolve_route(family, kind)

    digital = cap.digital
    if digital is None and route.needs_slice:
        assert lock is not None
        digital = _materialize_slice(state, _key, lock, cap)

    if filename:
        p = state.artifacts.path_for(cap.capture_id, filename)
        p.parent.mkdir(parents=True, exist_ok=True)
    else:
        p = _next_figure_path(state, cap, "timing")
    route.plot(RenderInput(
        path=p, events=report.events, dpi=dpi or 150,
        title=f"{report.protocol} {route.label} · 源 {alias} · {cap.capture_id}",
        digital=digital, analog=cap.analog,
        t_min=t_min, t_max=t_max, max_frames=max_frames or 60,
    ))
    state.artifacts.register(p, "figure",
                             f"{route.label}图（源 {alias}，窗口 {t_min or '起点'}–{t_max or '终点'}）")
    vis = [e for e in report.events
           if (t_min is None or e.t_end >= t_min) and (t_max is None or e.t_start <= t_max)
           and not e.kind.endswith(".warn")]
    return p, events_markdown(vis[: (max_frames or 60)])


def render_analog(state: SessionState, channel, t_min, t_max, dpi, source,
                  filename: str | None = None) -> Path:
    alias = state.resolve_alias(source)
    cap = state.capture_of(alias)
    if not cap.analog:
        raise ProtocolLockError(f"源 `{alias}` 没有模拟通道（render_analog 仅用于模拟源）")
    chs = [c for c in cap.analog if (channel is None or c.name == channel)]
    if not chs:
        raise ProtocolLockError(f"模拟通道 {channel!r} 不存在；可用: {[c.name for c in cap.analog]}")
    store = state.artifacts
    if filename:
        p = store.path_for(cap.capture_id, filename)
        p.parent.mkdir(parents=True, exist_ok=True)
    else:
        tag = chs[0].name if len(chs) == 1 else "multi"
        p = _next_figure_path(state, cap, f"analog_{tag}")
    lock = next((l for l in state.locks.values()
                 if l.source == alias), None)
    thr = cap.meta.threshold_v  # 切片实际所用阈值（run_decode 回写,含缺省计算值）
    if thr is None and lock is not None:
        thr = lock.params.get("threshold")
    analog_plot(chs, p, digital=None, threshold=thr, t_min=t_min, t_max=t_max, dpi=dpi or 150,
                title=f"模拟波形 · 源 {alias} · {cap.capture_id}")
    store.register(p, "figure", f"模拟波形（源 {alias}，{[c.name for c in chs]}）")
    return p


def graph_text(state: SessionState, source: str | None, protocol: str | None = None) -> str:
    hits = [(k, l) for k, l in state.locks.items()
            if source is None or k == source or k.startswith(source + "|")]
    if protocol:
        hits = [(k, l) for k, l in hits if l.protocol == protocol]
    if len(hits) != 1:
        raise ProtocolLockError(
            f"需要唯一协议锁（source={source!r}, protocol={protocol!r}）；候选: "
            f"{[(k, l.protocol) for k, l in hits]}"
        )
    return hits[0][1].graph.to_text()


def reset(state: SessionState) -> str:
    was = state.stage.value
    state.__init__()  # dataclass 重置
    return f"会话已重置（原阶段 {was} → DISCOVERY）。"


# ------------------------------------------------------- 工程档案（ADR-009）---

def list_profiles() -> str:
    from .profile import list_profiles as _ls

    items = _ls()
    if not items:
        return ("暂无工程档案。流程：lock_source/add_source → 各源 lock_protocol → "
                "`save_profile(name=…)` 固化；下次 `open_project` 一步直达 READY。")
    rows = ["| 档案 | 源 | 协议锁 | 说明 |", "|---|---|---|---|"]
    for it in items:
        rows.append(f"| `{it['name']}` | {it['sources']} | {it['locks']} | {it['description']} |")
    return "\n".join(rows) + "\n\n打开: `open_project(profile=…, files={别名: 路径})` → `run_decode`"


def save_profile(state: SessionState, name: str, description: str | None) -> str:
    """把当前会话的源定义与协议锁固化为 profiles/<name>.json。"""
    from .profile import ProfileSpec, LockSpec, SourceSpec, save_profile as _save

    if state.project is None or not state.project.entries:
        raise ProtocolLockError("先 lock_source（至少一个源）再保存档案")
    spec = ProfileSpec(
        name=name,
        description=description or "",
        sources=[SourceSpec(alias=e.alias, format=e.capture.meta.format_key,
                            options=dict(e.options))
                 for e in state.project.entries],
        # 管线锁不入档案（ADR-009 档案=源+协议锁；管线经 decodehub.toml 的
        # [runs.*.pipelines] 声明或 bind_pipeline 工具重建）
        locks=[LockSpec(source=l.source, protocol=l.protocol,
                        params=dict(l.params),
                        name=l.name if l.name != l.protocol else "")
               for l in state.locks.values() if not l.params.get("_pipeline")],
    )
    path = _save(spec)
    n_locks = len(spec.locks)
    return (f"✅ 工程档案已保存: `{path}`\n"
            f"- 源 {len(spec.sources)} 个（别名/格式/选项已固化）\n"
            f"- 协议锁 {n_locks} 个（协议/参数/通道角色已钉死）\n"
            f"- 文件路径不固化（每次采集不同）；档案可提交进固件仓库、跨机器/团队共享\n"
            f"\n下次会话: `open_project(profile=\"{name}\", files={{…}})` 一步直达 READY")


def open_project(state: SessionState, profile: str, files: dict) -> str:
    """按档案一步开工程：摄取各源 + 应用全部协议锁 → READY。"""
    from .profile import load_profile as _load

    if state.project is not None and state.project.entries:
        raise ProtocolLockError("当前会话已有源；如需换档案请先 reset_session")
    spec = _load(profile)
    missing = [s.alias for s in spec.sources if s.alias not in (files or {})]
    if missing:
        raise ProtocolLockError(
            f"档案要求源 {missing} 的文件路径（files={{别名: 路径}}）；"
            f"档案定义的源: {[s.alias for s in spec.sources]}"
        )
    sections = [f"## 工程档案 `{spec.name}` 已打开"
                + (f"（{spec.description}）" if spec.description else "")]
    for s in spec.sources:
        _add_entry(state, files[s.alias], s.format, {**s.options, "alias": s.alias})
        cap = state.project.find(s.alias).capture
        names = cap.channel_names()
        sections.append(
            f"- 源 `{s.alias}` [{cap.meta.format_key}]：数字 {len(names['digital'])} / "
            f"模拟 {len(names['analog'])} 通道，时长 {cap.duration:.6g}s"
        )
    applied = []
    for lk in spec.locks:
        try:
            plan, _g = lock_protocol(state, lk.protocol, dict(lk.params), lk.source,
                                     name=lk.name or None)
        except ProtocolLockError as e:
            raise ProtocolLockError(
                f"档案协议锁应用失败（锁 `{lk.source}|{lk.name or lk.protocol}`）: {e}\n"
                f"→ 常见原因：探头接线与档案不符（通道集合变了）。"
                f"请核对接线，或修正档案后重试。"
            ) from e
        applied.append(f"`{lk.source}`🔒{lk.protocol}")
    if not spec.locks:
        state.stage = Stage.SOURCE_LOCKED
        sections.append("\n（档案无协议锁）下一步: lock_protocol → run_decode")
    else:
        sections.append("- 协议锁: " + ", ".join(applied))
        sections.append("\n下一步: `run_decode`（全部已锁源并行解码）")
    return "\n".join(sections)


def capabilities_text() -> str:
    from ..acquisition.adapters import options_line

    fmt_rows = []
    for k, v in SUPPORTED_FORMATS.items():
        ol = options_line(k)
        fmt_rows.append(f"- `{k}`: {v}" + (f"；选项: {ol}" if ol else ""))
    fmts = "\n".join(fmt_rows)
    planned = "\n".join(f"- `{k}`: {v}" for k, v in PLANNED_FORMATS.items())
    protos = []
    for name, c in PROTOCOL_CATALOG.items():
        ps = "; ".join(f"{k}={v}" for k, v in c["params"].items())
        protos.append(f"- **{name}**: 角色 {c['roles']}。{c['hint']}\n  参数: {ps}")
    return (
        "## 支持的采集格式（lock_source 的 format 可选值；默认自动嗅探；选项带 * 为必填）\n"
        f"{fmts}\n\n## 延后支持\n{planned}\n\n"
        "## 支持的解码协议（lock_protocol 的 protocol 值）\n" + "\n".join(protos) +
        "\n\n## 建议流程\n"
        "**重复调试（推荐）**：`list_profiles` 查看工程档案 → "
        "`open_project(profile=…, files={别名: 路径})` 一步直达 READY → `run_decode`\n"
        "**首次调试**：\n"
        "1. `lock_source(path=…)` 摄取第一个源 →\n"
        "2. `add_source(path=…)` 追加更多源（可重复；各源独立分析）→\n"
        "3. `describe_capture` 查看各源通道 →\n"
        "4. 每源 `lock_protocol(protocol=…, source=别名)` →\n"
        "5. `run_decode`（一次解码全部已锁源）→ `save_profile(name=…)` 固化，下次一步直达"
    )
