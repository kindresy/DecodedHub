"""协议绑定：协议 → 角色/需求/参数路由/图模板（ADR-014）。

图模板与参数路由是**核心域知识**（C3 拥有图），此前散落在应用层三处人工同步：
`PROTOCOL_CATALOG` 文案、`lock_protocol` 的 if/elif 模板分支、每协议参数白名单。
Binding 把它们声明在协议侧——`protocols/<p>/binding.py` 注册（跟随解码器注册链），
应用层只做会话编排（锚点解析、同触发校验、源注入别名填充）。

新增协议 = 协议目录四件套（decode/encode/binding/README）+ `__init__.py` 一行导入；
引擎/网关/呈现/应用层零改动。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping

from ..shared.errors import ProtocolLockError
from .graph import Graph
from .registry import get_registry

# 克隆锚子图（下行扇入）的节点 id 前缀——全库唯一定义处
UL_PREFIX = "ul_"

# 角色 → 常见通道名启发式（域内知识；自动映射用）
ROLE_ALIASES: dict[str, frozenset[str]] = {
    "rx": frozenset({"rx", "rxd", "din", "di", "sdin"}),
    "tx": frozenset({"tx", "txd", "dout", "do", "sdout"}),
    "scl": frozenset({"scl", "sck", "clk", "clock", "a5"}),
    "sda": frozenset({"sda", "dio", "data", "a4"}),
    "clk": frozenset({"sck", "clk", "scl", "clock"}),
    "mosi": frozenset({"mosi", "dout", "do", "sdout", "copi"}),
    "miso": frozenset({"miso", "din", "di", "sdin", "cipo"}),
    "cs": frozenset({"cs", "nss", "ss", "nsc", "enable", "ce"}),
}

_BINDINGS: dict[str, "ProtocolBinding"] = {}


@dataclass(frozen=True)
class ProtocolBinding:
    """一个协议的声明式绑定：图模板怎么建、参数怎么路由、通道角色怎么映射。

    roles           通道角色（自动映射顺序即声明顺序）。
    optional_roles  可缺席角色（不参与逐项必需校验）。
    require_any     每组至少映射一个角色（如 SPI 的 mosi/miso）。
    needs           源通道数下限（min_digital / min_analog）。
    analog_direct   模拟直达（不经 slicer，ADR-010）；此类协议只接受模拟源。
    precond_node_type 预条件节点 type（analog_direct 时的可选中间节点，如 uplink_precond）。
    requires_sync   锚协议名（如 downlink→"uplink"）：图内克隆锚协议子图做扇入
                    （ADR-011）。锚点解析/同触发校验是会话编排，留在应用层。
    decoder_params  工具参数 → 解码节点 的白名单。
    precond_params  工具参数 → 预条件节点 的白名单。
    slicer_params   工具参数 → slicer 节点 的白名单（模拟源上的数字协议）。
    role_param      角色名 → 解码节点参数名（缺省同名；如 uplink/downlink 的 rx→channel）。
    tool_params_doc 节点参数之外的工具级参数文档（如 downlink 的 uplink_source）。
    """

    protocol: str
    node_type: str
    roles: tuple[str, ...]
    needs: Mapping[str, int] = field(default_factory=dict)
    optional_roles: tuple[str, ...] = ()
    require_any: tuple[tuple[str, ...], ...] = ()
    role_aliases: Mapping[str, frozenset[str]] | None = None  # None = 用 ROLE_ALIASES
    hint: str = ""
    analog_direct: bool = False
    precond_node_type: str | None = None
    requires_sync: str | None = None
    # 以下三个白名单字段已退役（ADR-021）：参数路由派生自 Node.PARAMS 单一权威，
    # 字段仅为既有注册兼容保留，引擎不再读取——新参数直接加在节点 PARAMS 即可配置。
    decoder_params: tuple[str, ...] = ()
    precond_params: tuple[str, ...] = ()
    slicer_params: tuple[str, ...] = ("threshold", "hysteresis")
    role_param: Mapping[str, str] = field(default_factory=dict)
    tool_params_doc: Mapping[str, str] = field(default_factory=dict)

    def aliases_for(self, role: str) -> frozenset[str]:
        if self.role_aliases and role in self.role_aliases:
            return self.role_aliases[role]
        return ROLE_ALIASES.get(role, frozenset({role}))

    @property
    def required_roles(self) -> tuple[str, ...]:
        return tuple(r for r in self.roles if r not in self.optional_roles)

    def graph_kind_for(self, capture) -> str:
        """图形状元数据（ProtocolLock.graph_kind）：呈现/回写按此分派，禁止嗅探节点 id。"""
        if self.requires_sync:
            return "fan_in"
        if self.analog_direct:
            return "analog_direct"
        return "digital" if capture.digital is not None else "sliced"


def register_binding(b: ProtocolBinding) -> None:
    """注册协议绑定；重复 protocol 抛 ValueError（仿节点注册表）。"""
    if b.protocol in _BINDINGS:
        raise ValueError(f"协议绑定重复注册: {b.protocol}")
    if b.node_type not in get_registry():
        raise ValueError(
            f"协议 {b.protocol} 的绑定引用未注册节点类型: {b.node_type}"
            f"（binding.py 应在 decode.py 之后导入/注册）"
        )
    _BINDINGS[b.protocol] = b


def get_binding(protocol: str) -> ProtocolBinding:
    b = _BINDINGS.get(protocol)
    if b is None:
        raise ProtocolLockError(f"未知协议 {protocol!r}；可用: {sorted(_BINDINGS)}")
    return b


def all_bindings() -> tuple[ProtocolBinding, ...]:
    return tuple(_BINDINGS.values())


# ------------------------------------------------------------ 通道自动映射 ---

_CH_NUM = re.compile(r"(?:通道|channel|ch|d)\s*(\d+)", re.IGNORECASE)
_ROLE_TOKEN = re.compile(r"[A-Za-z0-9]+")


def _norm(name: str) -> str:
    return re.sub(r"[\s_\-]+", "", name).lower()


def _role_hit(name: str, aliases: frozenset[str]) -> bool:
    suffix = name.rsplit(":", 1)[-1]
    return (
        _norm(name) in aliases
        or _norm(suffix) in aliases
        or any(_norm(token) in aliases for token in _ROLE_TOKEN.findall(suffix))
    )


def auto_map_channels(chs: list[str], binding: ProtocolBinding, overrides: dict) -> dict:
    """角色 → 通道名：先保留显式设置，再对未提供的角色运行启发式。"""
    if not chs:
        raise ProtocolLockError(
            f"协议 {binding.protocol} 需要通道，但该源没有任何通道（数字或模拟）"
        )
    numbers = {}
    for c in chs:
        m = _CH_NUM.search(_norm(c))
        numbers[c] = int(m.group(1)) if m else None

    mapping: dict[str, str] = {}
    used: set[str] = set()
    supplied = {role for role in binding.roles if role in overrides}
    for role in binding.roles:
        if role not in supplied:
            continue
        want = overrides[role]
        if want is None or (isinstance(want, str) and not want.strip()):
            continue
        if want not in chs:
            raise ProtocolLockError(f"通道 {want!r} 不存在；可用: {chs}")
        mapping[role] = want
        used.add(want)

    for role in binding.roles:
        if role in supplied:
            continue
        aliases = binding.aliases_for(role)
        hit = next((c for c in chs if c not in used and _role_hit(c, aliases)), None)
        if hit is not None:
            mapping[role] = hit
            used.add(hit)

    for role in binding.roles:
        if role in supplied or role in mapping:
            continue
        numbered = [c for c in chs if numbers[c] is not None and c not in used]
        others = [c for c in chs if numbers[c] is None and c not in used]
        pool = sorted(numbered, key=lambda c: numbers[c]) + others
        if pool:
            mapping[role] = pool[0]
            used.add(pool[0])

    need_min = binding.needs.get("min_digital", 0)
    got = [r for r in binding.required_roles if r in mapping]
    missing_groups = [group for group in binding.require_any
                      if not any(role in mapping for role in group)]
    if len(got) < len(binding.required_roles) or missing_groups or len(mapping) < need_min:
        any_text = "" if not missing_groups else f"；至少需要其一 {list(missing_groups[0])}"
        raise ProtocolLockError(
            f"协议 {binding.protocol} 至少需要 {need_min} 个数字通道"
            f"（角色 {list(binding.roles)}{any_text}），实际可用 {len(chs)} 个: {chs}"
        )
    return mapping


# -------------------------------------------------------------- 图模板构建 ---

def _filter_params(tool_params: dict, whitelist: tuple[str, ...]) -> dict:
    """已退役（ADR-021）：仅存兼容。请用 `node_routed_params`。"""
    return {k: tool_params[k] for k in whitelist if k in tool_params}


def node_routed_params(registry: Mapping[str, type], node_type: str,
                       tool_params: dict, *, exclude: Mapping[str, str] | set = ()) -> dict:
    """tool_params ∩ Node.PARAMS —— 参数路由的单一权威（ADR-021）。

    节点 PARAMS 里声明的键即可配置，未声明的键不进节点（lock_protocol 会
    对剩余未知键报错）。exclude = 角色占用的节点参数名（由 channel_map 填充）。
    """
    exclude_keys = set(exclude)
    return {k: v for k, v in tool_params.items()
            if k in registry[node_type].PARAMS and k not in exclude_keys}


def _role_params(binding: ProtocolBinding, channel_map: dict) -> dict:
    return {binding.role_param.get(r, r): channel_map[r]
            for r in binding.roles if r in channel_map}


def clone_graph(graph: Graph, prefix: str) -> tuple[list, list]:
    """子图克隆（加前缀）：[(新id, type, params), …] + 边——锚扇入/管线 tap 共用。"""
    nodes = [(f"{prefix}{nid}", spec.type, dict(spec.params))
             for nid, spec in graph.nodes.items()]
    edges = [(f"{prefix}{e.src}", e.src_port, f"{prefix}{e.dst}", e.dst_port)
             for e in graph.edges]
    return nodes, edges


def clone_anchor_graph(anchor_graph: Graph) -> tuple[list, list]:
    """锚子图克隆（ADR-011 扇入）——clone_graph 的锚定语义别名。"""
    return clone_graph(anchor_graph, UL_PREFIX)


def normalize_chain_steps(chain: list, err) -> list[dict]:
    """管线链步骤归一（ADR-020）：每步归一为 {type, params}。

    两种写法语义等价、可混用：
    - 扁写（推荐）：{"type": "event_filter", "kinds": ["uart.frame"]}
    - 嵌套：{"type": "event_filter", "params": {"kinds": ["uart.frame"]}}
    扁写时除 type 外的键即节点参数；显式 params 表与扁写混用报错。
    err(msg) 由调用方注入（ProtocolLockError/ConfigError），消息自带 chain[i] 前缀。
    """
    out: list[dict] = []
    for i, step in enumerate(chain):
        w = f"chain[{i}]"
        if not isinstance(step, dict) or not isinstance(step.get("type"), str):
            err(f"{w}: 必须是含 type 的表")
        if "params" in step:
            extra = set(step) - {"type", "params"}
            if extra:
                err(f"{w}: params 表与扁写参数混用（多余键 {sorted(extra)}）")
            if not isinstance(step["params"], dict):
                err(f"{w}: params 必须是表")
            params = dict(step["params"])
        else:
            params = {k: v for k, v in step.items() if k != "type"}
        out.append({"type": step["type"], "params": params})
    return out


def strip_anchor_prefix(graph: Graph) -> Graph:
    """从内嵌克隆中还原锚子图（去 UL_PREFIX）——下行锁参数重建用。"""
    g = Graph()
    for nid, spec in graph.nodes.items():
        if nid.startswith(UL_PREFIX):
            g.add_node(nid[len(UL_PREFIX):], spec.type, **spec.params)
    for e in graph.edges:
        if e.src.startswith(UL_PREFIX) and e.dst.startswith(UL_PREFIX):
            g.add_edge(e.src[len(UL_PREFIX):], e.src_port,
                       e.dst[len(UL_PREFIX):], e.dst_port)
    return g


def build_lock_graph(
    binding: ProtocolBinding,
    *,
    channel_map: dict,
    tool_params: dict,
    source_kind: str,                       # "digital" | "analog"
    anchor_graph: Graph | None = None,      # requires_sync 协议必填
) -> tuple[Graph, dict[str, str]]:
    """构建协议锁的解码图（图模板的唯一权威实现）。

    返回 (graph, input_nodes)；input_nodes: 注入角色（"main"/"anchor"）→
    摄取源注入节点 id，应用层据此把源别名填进 source_inputs。
    锚子图的注入点 = 锚图根（无入边）、sync 抽头 = 锚图汇（无出边，即其
    解码节点）——两者都是结构性质，与具体协议无关。
    """
    g = Graph()
    input_nodes: dict[str, str] = {}
    reg = get_registry()
    role_keys = {binding.role_param.get(role, role) for role in binding.roles}
    decode_params = {**node_routed_params(reg, binding.node_type, tool_params,
                                          exclude=role_keys),
                     **_role_params(binding, channel_map)}

    if anchor_graph is not None:
        # 跨源扇入（ADR-011）：克隆锚子图 + 本源 apick → 解码节点(analog, sync)
        anchor_nodes, anchor_edges = clone_anchor_graph(anchor_graph)
        for nid, ntype, params in anchor_nodes:
            g.add_node(nid, ntype, **params)
        for src, sp, dst, dp in anchor_edges:
            g.add_edge(src, sp, dst, dp)
        has_in = {e.dst for e in anchor_graph.edges}
        has_out = {e.src for e in anchor_graph.edges}
        roots = [nid for nid in anchor_graph.nodes if nid not in has_in]
        sinks = [nid for nid in anchor_graph.nodes if nid not in has_out]
        if len(roots) != 1 or len(sinks) != 1:
            raise ValueError(
                f"锚子图应有唯一注入点（根）与唯一 sync 抽头（汇），"
                f"实际根 {roots}、汇 {sinks}"
            )
        input_nodes["anchor"] = f"{UL_PREFIX}{roots[0]}"  # 锚源 Capture 注入点
        sync_src = f"{UL_PREFIX}{sinks[0]}"               # 锚解码输出抽头
        g.add_node("apick", "analog_pick")
        g.add_node(binding.node_type, binding.node_type, **decode_params)
        g.add_edge("apick", "out", binding.node_type, "in")
        g.add_edge(sync_src, "out", binding.node_type, "sync")
        input_nodes["main"] = "apick"
        return g, input_nodes

    if binding.analog_direct:
        g.add_node("apick", "analog_pick")
        upstream = "apick"
        if binding.precond_node_type:
            pre_params = {**node_routed_params(reg, binding.precond_node_type,
                                               tool_params, exclude=role_keys),
                          **_role_params(binding, channel_map)}
            g.add_node(binding.precond_node_type, binding.precond_node_type, **pre_params)
            g.add_edge(upstream, "out", binding.precond_node_type, "in")
            upstream = binding.precond_node_type
        g.add_node(binding.node_type, binding.node_type, **decode_params)
        g.add_edge(upstream, "out", binding.node_type, "in")
        input_nodes["main"] = "apick"
        return g, input_nodes

    if source_kind == "digital":
        g.add_node("pick", "digital_pick")
        g.add_node(binding.node_type, binding.node_type, **decode_params)
        g.add_edge("pick", "out", binding.node_type, "in")
        input_nodes["main"] = "pick"
        return g, input_nodes

    # 模拟源上的数字协议：显式切片（ADR-002——模拟→数字唯一合法路径）
    g.add_node("apick", "analog_pick")
    g.add_node("slice", "slicer", **node_routed_params(reg, "slicer", tool_params))
    g.add_node(binding.node_type, binding.node_type, **decode_params)
    g.add_edge("apick", "out", "slice", "in")
    g.add_edge("slice", "out", binding.node_type, "in")
    input_nodes["main"] = "apick"
    return g, input_nodes
