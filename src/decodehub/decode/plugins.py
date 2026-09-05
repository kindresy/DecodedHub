"""显式协议插件发现（阶段 B）。

内置协议走固定 descriptor，外部包可通过 ``decodehub.protocols`` entry point
提供模块或 ``PluginDescriptor``。旧的 ``protocols`` 包仍可被直接导入，兼容
既有调用方；正常初始化不再依赖中心包的全量 import side effect。
"""

from __future__ import annotations

import importlib
from types import ModuleType
from dataclasses import dataclass
from importlib import metadata
from typing import Any, Iterable


@dataclass(frozen=True)
class PluginDescriptor:
    protocol: str
    module: str
    node_type: str
    version: str = "1"


PLUGIN_API_VERSION = "1"

BUILTIN_PLUGINS: tuple[PluginDescriptor, ...] = (
    PluginDescriptor("uart", "decodehub.decode.protocols.uart", "uart_decode"),
    PluginDescriptor("i2c", "decodehub.decode.protocols.i2c", "i2c_decode"),
    PluginDescriptor("spi", "decodehub.decode.protocols.spi", "spi_decode"),
    PluginDescriptor("uplink", "decodehub.decode.protocols.uplink", "uplink_decode"),
    PluginDescriptor("downlink", "decodehub.decode.protocols.downlink", "downlink_decode"),
)

_loaded: dict[str, PluginDescriptor] = {}


def _coerce_external_descriptor(ep, obj: Any) -> PluginDescriptor:
    """将 entry point 返回的 module/class/descriptor 归一化。"""
    if isinstance(obj, PluginDescriptor):
        return obj
    if isinstance(obj, dict):
        return PluginDescriptor(**obj)
    candidate = getattr(obj, "PLUGIN", None) or getattr(obj, "PLUGIN_DESCRIPTOR", None)
    if isinstance(candidate, PluginDescriptor):
        return candidate
    if isinstance(candidate, dict):
        return PluginDescriptor(**candidate)
    if isinstance(obj, ModuleType):
        module = obj.__name__
        protocol = getattr(obj, "PROTOCOL", ep.name)
        node_type = getattr(obj, "NODE_TYPE", "")
    elif isinstance(obj, type):
        module = obj.__module__
        protocol = getattr(obj, "PROTOCOL", ep.name)
        node_type = getattr(obj, "NODE_TYPE", "")
    elif isinstance(obj, str):
        module, protocol, node_type = obj, ep.name, ""
    else:
        raise ValueError(f"插件 entry point {ep.name!r} 必须返回 PluginDescriptor/dict/module/class")
    return PluginDescriptor(protocol=protocol, module=module, node_type=node_type)


def _external_descriptors() -> Iterable[PluginDescriptor]:
    try:
        eps = metadata.entry_points()
        selected = eps.select(group="decodehub.protocols") if hasattr(eps, "select") else eps.get("decodehub.protocols", ())
    except Exception:
        return ()
    out: list[PluginDescriptor] = []
    for ep in selected:
        try:
            out.append(_coerce_external_descriptor(ep, ep.load()))
        except TypeError as exc:
            raise ValueError(f"插件 entry point {ep.name!r} descriptor 无效: {exc}") from exc
    return out


def _validate_loaded(desc: PluginDescriptor) -> None:
    from .bindings import all_bindings
    from .presentation import all_presentations
    from .registry import get_registry
    from .contracts import validate_node_contract

    bindings = {b.protocol: b for b in all_bindings()}
    presentations = {p.protocol: p for p in all_presentations()}
    if desc.protocol not in bindings:
        raise ValueError(f"插件 {desc.protocol} 未注册 ProtocolBinding")
    binding = bindings[desc.protocol]
    if desc.node_type and binding.node_type != desc.node_type:
        raise ValueError(f"插件 {desc.protocol} descriptor node_type={desc.node_type!r} 与 binding={binding.node_type!r} 不一致")
    if desc.protocol not in presentations:
        raise ValueError(f"插件 {desc.protocol} 未注册 Presentation")
    node_type = desc.node_type or binding.node_type
    if node_type not in get_registry():
        raise ValueError(f"插件 {desc.protocol} 节点 {node_type!r} 未注册")
    validate_node_contract(get_registry()[node_type])


def load_plugins(extra: Iterable[PluginDescriptor] | None = None) -> tuple[PluginDescriptor, ...]:
    """加载并校验内置/外部协议，重复调用幂等。"""
    descriptors = tuple(BUILTIN_PLUGINS) + tuple(extra or ()) + tuple(_external_descriptors())
    seen: set[str] = set()
    for desc in descriptors:
        if desc.protocol in seen:
            raise ValueError(f"协议插件重复发现: {desc.protocol}")
        seen.add(desc.protocol)
        if desc.version != PLUGIN_API_VERSION:
            raise ValueError(
                f"插件 {desc.protocol} descriptor 版本 {desc.version!r} 不兼容；"
                f"当前 API 版本 {PLUGIN_API_VERSION!r}"
            )
        if desc.protocol in _loaded:
            continue
        importlib.import_module(desc.module)
        _validate_loaded(desc)
        _loaded[desc.protocol] = desc
    return tuple(_loaded[p.protocol] for p in descriptors if p.protocol in _loaded)
