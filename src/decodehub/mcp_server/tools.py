"""工具目录（分阶段，docs/50-mcp-gateway.md）。

可见性规则：rank(tool.stage) <= rank(session.stage)（累积解锁）。
handler 返回 list[str | Path]：str → TextContent；Path(.png) → ImageContent。
锁定动作由 server 检测阶段变化后推送 tools/list_changed。

多源模型（ADR-008 v1.2）：各源独立分析；带 `source` 参数的工具在多源时必须显式指定
（唯一源时缺省）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..app import services
from ..app.session import SessionState, Stage
from ..acquisition.adapters import options_properties
from ..decode.capabilities import protocol_capabilities
from ..decode.registry import node_catalog
from ..render.format import EXPORT_FORMAT_KEYS
from ..render.format import events_markdown

_STAGE_RANK = {Stage.DISCOVERY: 0, Stage.SOURCE_LOCKED: 1, Stage.READY: 2}


def visible(stage: Stage, tool_stage: Stage) -> bool:
    return _STAGE_RANK[tool_stage] <= _STAGE_RANK[stage]


def _sources_line(state: SessionState) -> str:
    if state.project and state.project.entries:
        parts = []
        for e in state.project.entries:
            protos = [l.protocol for l in state.locks.values() if l.source == e.alias]
            parts.append(f"`{e.alias}`[{e.capture.meta.format_key}]"
                         + ("".join(f"🔒{p}" for p in protos) if protos else ""))
        return "- 源: " + ", ".join(parts)
    return ""


def _session_text(state: SessionState) -> str:
    lines = [f"当前阶段: **{state.stage.value}**"]
    s = _sources_line(state)
    if s:
        lines.append(s)
    if state.reports:
        for key, r in state.reports.items():
            c = r.counts()
            lines.append(f"- 解码报告 `{key}`（{r.protocol}）: {c['total']} 事件，错误 {c['errors']}")
    if state.artifacts.items:
        lines.append("- 制品:\n" + state.artifacts.manifest_markdown())
    next_actions = {
        Stage.DISCOVERY: "lock_source(path=…) 摄取采集文件",
        Stage.SOURCE_LOCKED: "add_source 追加采集器（可选）→ describe_capture → lock_protocol（每源独立）",
        Stage.READY: "run_decode（全部已锁源）→ get_events / render_timing / export_events（source 选择）",
    }[state.stage]
    lines.append(f"- 下一步: {next_actions}")
    return "\n".join(lines)


# ------------------------------------------------------------ handlers ---

def _list_capabilities(args, state):
    return [services.capabilities_text(), "\n\n---\n" + _session_text(state)]


def _lock_source(args, state):
    text = services.ingest(state, args["path"], args.get("format"), args.get("options"))
    if state.stage == Stage.SOURCE_LOCKED and len(state.project.entries) == 1:
        return [text + "\n\n🔓 已解锁新工具: describe_capture, add_source, lock_protocol, "
                "unlock_protocol\n（客户端收到 tools/list_changed 后自动刷新列表）"]
    return [text]


def _add_source(args, state):
    return [services.add_source(state, args["path"], args.get("format"), args.get("options"))]


def _get_session(args, state):
    return [_session_text(state)]


def _reset_session(args, state):
    return [services.reset(state)]


def _list_profiles(args, state):
    return [services.list_profiles()]


def _open_project(args, state):
    return [services.open_project(state, args["profile"], args.get("files") or {})
            + "\n\n🔓 已解锁至 READY 全工具（run_decode / get_events / render_* / export_events）"]


def _save_profile(args, state):
    return [services.save_profile(state, args["name"], args.get("description"))]


def _describe_capture(args, state):
    return [services.describe_capture(state)]


def _lock_protocol(args, state):
    plan, _g = services.lock_protocol(state, args["protocol"], args.get("params"),
                                      args.get("source"), name=args.get("name"))
    return [plan + "\n\n🔓 已解锁新工具: run_decode, get_events, export_events, "
            "render_timing, render_analog, inspect_graph, bind_pipeline"]


def _unlock_protocol(args, state):
    return [services.unlock_protocol(state, args.get("source"), args.get("protocol"))]


def _run_decode(args, state):
    return [services.run_decode(state, args.get("overrides"), args.get("source"))]


def _redecode(args, state):
    return [services.run_decode(state, args.get("params") or {}, args.get("source"))]


def _bind_pipeline(args, state):
    return [services.bind_pipeline(state, args["name"], args.get("tap"),
                                   args.get("chain") or [])
            + "\n\n管线报告与上游协议各自独立：get_events / export_events / "
            "render_timing 用 protocol=管线名 选择。"]


def _get_events(args, state):
    events = services.filter_events(
        state, args.get("source"), args.get("protocol"), args.get("kind"),
        args.get("t_min"), args.get("t_max"), args.get("has_errors"),
    )
    limit = int(args.get("limit", 50))
    offset = int(args.get("offset", 0))
    page = events[offset : offset + limit]
    header = (f"事件 {len(events)} 条（kind={args.get('kind') or '全部'}），"
              f"显示 {offset + 1}–{offset + len(page)}：\n\n")
    footer = f"\n\n（下一页: offset={offset + limit}）" if offset + limit < len(events) else ""
    return [header + events_markdown(page) + footer]


def _export_events(args, state):
    p = services.export_events(state, args.get("format", "md"), args.get("path"),
                               args.get("source"), args.get("protocol"))
    n = sum(r.counts()["total"] for r in state.reports.values())
    return [f"✅ 已导出 → `{p}`（会话累计 {n} 事件）\n\n{state.artifacts.manifest_markdown()}"]


def _render_timing(args, state):
    p, table = services.render_timing(
        state, args.get("t_min"), args.get("t_max"),
        args.get("max_frames") or 60, args.get("dpi") or 150,
        args.get("source"), args.get("protocol"),
    )
    return [Path(p), "## 时序图（span 编号 ↔ 下表 #）\n" + table
            + "\n\n## 制品\n" + state.artifacts.manifest_markdown()]


def _render_analog(args, state):
    p = services.render_analog(state, args.get("channel"), args.get("t_min"),
                               args.get("t_max"), args.get("dpi"), args.get("source"))
    return [Path(p), "## 模拟波形图\n" + state.artifacts.manifest_markdown()]


def _inspect_graph(args, state):
    return ["```\n" + services.graph_text(state, args.get("source"), args.get("protocol")) + "\n```\n"
            "节点目录（全部已注册类型）: " + ", ".join(n["type"] for n in node_catalog())]


@dataclass
class ToolSpec:
    name: str
    description: str
    stage: Stage
    schema: dict
    handler: Callable[[dict, SessionState], list]


_P = {"type": "object", "properties": {}, "required": []}
_SRC = {"type": "string", "description": "源别名（多源必填；唯一源可省）"}
_PRO = {"type": "string", "description": "协议或锁实例名消歧（一源多锁时必填，如 uart / uart1；实例名见 get_session 的锁键 源|名）"}
_PROTOCOL_NAMES = [item["protocol"] for item in protocol_capabilities()]
_LOCK_PROTOCOL = {"type": "string", "enum": _PROTOCOL_NAMES}

TOOLS: list[ToolSpec] = [
    ToolSpec(
        "list_capabilities",
        "列出支持的采集文件格式与解码协议（含参数说明）+ 当前会话状态。入口工具。",
        Stage.DISCOVERY, _P, _list_capabilities),
    ToolSpec(
        "lock_source",
        "摄取采集文件并归一化（首个源；之后追加源用 add_source）。",
        Stage.DISCOVERY,
        {"type": "object",
         "properties": {
             "path": {"type": "string", "description": "采集文件绝对路径"},
             "format": {"type": "string", "description": "格式键（可选，默认自动嗅探）"},
             "options": {"type": "object",
                         "properties": options_properties(),
                         "description":
                             "格式相关选项（必填项标注与每格式明细见 list_capabilities）；"
                             "alias=源别名"},
         },
         "required": ["path"]},
        _lock_source),
    ToolSpec(
        "get_session", "查看当前会话阶段、源与协议锁、解码报告与制品清单。",
        Stage.DISCOVERY, _P, _get_session),
    ToolSpec(
        "reset_session", "重置会话（清空源与协议锁，回到 DISCOVERY）。",
        Stage.DISCOVERY, _P, _reset_session),
    ToolSpec(
        "list_profiles",
        "列出已保存的工程档案（固化了源定义与各源协议锁；适用于 IO/仪器固定的重复调试）。",
        Stage.DISCOVERY, _P, _list_profiles),
    ToolSpec(
        "open_project",
        "按工程档案一步开工程：摄取各源文件 + 应用全部协议锁 → 直达 READY。"
        "files={别名: 采集文件路径}（档案定义了需要哪些源）。通道角色已在档案中钉死——"
        "若接线与档案不符会立即报错（接线防线）。",
        Stage.DISCOVERY,
        {"type": "object",
         "properties": {
             "profile": {"type": "string", "description": "档案名（list_profiles 查看）"},
             "files": {"type": "object",
                       "description": "源别名 → 本次采集文件绝对路径"},
         },
         "required": ["profile"]},
        _open_project),

    ToolSpec(
        "describe_capture",
        "描述各源通道统计（数字: 跳变/活动率；模拟: Vpp/极值/采样率）与协议锁状态。",
        Stage.SOURCE_LOCKED, _P, _describe_capture),
    ToolSpec(
        "add_source",
        "追加一个采集源（多采集器并行分析；可重复调用；不影响已锁定的协议）。"
        "options 可含 alias(源别名) 与格式特定参数（sample_rate 等）。",
        Stage.SOURCE_LOCKED,
        {"type": "object",
         "properties": {
             "path": {"type": "string", "description": "采集文件绝对路径"},
             "format": {"type": "string", "description": "格式键（默认自动嗅探）"},
             "options": {"type": "object",
                         "properties": options_properties(),
                         "description":
                             "格式相关选项（必填项标注与每格式明细见 list_capabilities）；"
                             "alias=源别名"},
         },
         "required": ["path"]},
        _add_source),
    ToolSpec(
        "lock_protocol",
        "按源锁定已注册解码协议并自动映射通道。多源时 source 必填。"
        "各源可锁不同协议，互不影响。",
        Stage.SOURCE_LOCKED,
        {"type": "object",
         "properties": {
             "protocol": _LOCK_PROTOCOL,
             "source": _SRC,
             "params": {"type": "object", "description":
                        "如 {\"baud\":115200} / {\"scl\":\"D0\",\"sda\":\"D1\"} / "
                        "{\"cpol\":1,\"cpha\":1} / 模拟源: {\"threshold\":1.65}"},
             "name": {"type": "string",
                      "description": "锁实例名（ADR-023）：同源同协议多路并存时区分，"
                                     "如两路 uart 各钉不同通道 → uart1/uart2；缺省 = 协议名；"
                                     "不能包含 '|' 字符（锁键分隔符）"},
         },
         "required": ["protocol"]},
        _lock_protocol),
    ToolSpec(
        "unlock_protocol", "解除某源的协议锁（多锁时 source 必填；全部解除回到 SOURCE_LOCKED）。",
        Stage.SOURCE_LOCKED,
        {"type": "object", "properties": {"source": _SRC, "protocol": _PRO}, "required": []},
        _unlock_protocol),
    ToolSpec(
        "save_profile",
        "把当前会话固化为工程档案（源别名/格式/选项 + 各源协议/参数/通道角色 → "
        "profiles/<name>.json）。IO 与仪器固定的项目保存一次，之后 open_project 一步直达。",
        Stage.SOURCE_LOCKED,
        {"type": "object",
         "properties": {
             "name": {"type": "string", "description": "档案名（如 gizmo-v3）"},
             "description": {"type": "string", "description": "说明（板卡/固件版本等）"},
         },
         "required": ["name"]},
        _save_profile),

    ToolSpec(
        "run_decode",
        "执行解码：source 缺省 = 全部已锁源并行独立解码；指定 source 可带 overrides 覆盖参数。",
        Stage.READY,
        {"type": "object",
         "properties": {"overrides": {"type": "object"}, "source": _SRC, "protocol": _PRO},
         "required": []},
        _run_decode),
    ToolSpec(
        "get_events",
        "分页查询解码事件（Markdown 表）。source 选择源；kind/t_min/t_max/has_errors 过滤。",
        Stage.READY,
        {"type": "object",
         "properties": {"source": _SRC, "protocol": _PRO,
                        "kind": {"type": "string", "description": "如 uart.frame / i2c.transfer"},
                        "t_min": {"type": "number"}, "t_max": {"type": "number"},
                        "has_errors": {"type": "boolean"},
                        "limit": {"type": "integer", "default": 50},
                        "offset": {"type": "integer", "default": 0}},
         "required": []},
        _get_events),
    ToolSpec(
        "export_events",
        f"导出事件到文件（format: {'/'.join(EXPORT_FORMAT_KEYS)}；source 选择源）。",
        Stage.READY,
        {"type": "object",
         "properties": {"format": {"type": "string", "enum": list(EXPORT_FORMAT_KEYS)},
                        "path": {"type": "string"}, "source": _SRC, "protocol": _PRO},
         "required": []},
        _export_events),
    ToolSpec(
        "render_timing",
        "渲染某源的数字时序图 PNG（帧 span 着色+编号，编号与返回表对应）。可指定时间窗放大。",
        Stage.READY,
        {"type": "object",
         "properties": {"source": _SRC, "protocol": _PRO, "t_min": {"type": "number"},
                        "t_max": {"type": "number"},
                        "max_frames": {"type": "integer", "default": 60},
                        "dpi": {"type": "integer", "default": 150}},
         "required": []},
        _render_timing),
    ToolSpec(
        "render_analog", "渲染某源的模拟波形图 PNG（含阈值线；仅模拟源）。",
        Stage.READY,
        {"type": "object",
         "properties": {"source": _SRC, "protocol": _PRO, "channel": {"type": "string"},
                        "t_min": {"type": "number"}, "t_max": {"type": "number"},
                        "dpi": {"type": "integer"}},
         "required": []},
        _render_analog),
    ToolSpec(
        "inspect_graph", "查看某源的解码管线（DAG 节点/边/参数）与全部已注册节点类型。",
        Stage.READY,
        {"type": "object", "properties": {"source": _SRC, "protocol": _PRO}, "required": []},
        _inspect_graph),
    ToolSpec(
        "redecode", "调整协议参数并重解码某源（run_decode 的别名）。",
        Stage.READY,
        {"type": "object",
         "properties": {"params": {"type": "object"}, "source": _SRC, "protocol": _PRO},
         "required": []},
        _redecode),
    ToolSpec(
        "bind_pipeline",
        "绑定管线（ADR-019）：tap 某协议锁的输出（如 uart 解码出的事件），串一条"
        "通用节点链（event_filter / field_split 等）成独立报告 sink——与上游协议"
        "分开查询/导出/渲染。管线本身可再被 tap（链上链）。",
        Stage.READY,
        {"type": "object",
         "properties": {
             "name": {"type": "string",
                      "description": "管线名（报告键 = 源|管线名）；不能包含 '|' 字符，"
                                     "且不得与任何锁实例名同名（会覆盖其报告）"},
             "tap": {"type": "string",
                     "description": "上游锁：源|协议 键（唯一锁/唯一协议名/唯一源时可省）"},
             "chain": {"type": "array",
                       "description": "节点链（单输入节点）。每步 = type + 参数平铺，如 "
                                      "{\"type\":\"event_filter\",\"kinds\":[\"uart.frame\"]}；"
                                      "也可嵌套 {\"type\":…,\"params\":{…}}（等价）",
                       "items": {"type": "object",
                                 "properties": {"type": {"type": "string"},
                                                "params": {"type": "object"}},
                                 "required": ["type"]}},
         },
         "required": ["name", "chain"]},
        _bind_pipeline),
]

TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def stage_tool_names(stage: Stage) -> list[str]:
    return sorted(t.name for t in TOOLS if visible(stage, t.stage))
