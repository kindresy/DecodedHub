from .events import (
    DecodeReport,
    DecodedEvent,
    I2cEvent,
    SpiEvent,
    UartEvent,
)
from .graph import Edge, Graph, NodeSpec, Param, evaluate, validate
from .registry import NODE_REGISTRY, get_registry, node_catalog, register
from .plugins import BUILTIN_PLUGINS, PluginDescriptor, load_plugins

# 通用节点静态注册；协议包通过显式插件描述符加载。
from . import nodes as _nodes  # noqa: F401,E402
load_plugins()

# fields 呈现在协议族之后注册（ADR-013 CSV 并集列序契约：新族列追加在尾）
from .fields import register_fields_presentation as _register_fields_presentation
_register_fields_presentation()

__all__ = [
    "DecodeReport", "DecodedEvent", "I2cEvent", "SpiEvent", "UartEvent",
    "Edge", "Graph", "NodeSpec", "Param", "evaluate", "validate",
    "NODE_REGISTRY", "node_catalog", "register",
    "PluginDescriptor", "BUILTIN_PLUGINS", "load_plugins",
]
