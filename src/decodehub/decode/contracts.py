"""DecodeHub 扩展边界的静态/运行时契约（阶段 A）。

协议实现可以继续使用现有的轻量类约定；这些 ``Protocol`` 为第三方扩展、
IDE 和契约测试提供了明确的形状，不改变现有调用方式。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar, Mapping, Protocol, runtime_checkable

from ..acquisition.adapters.spec import AdapterSpec
from ..shared.waves import Capture, DigitalWave


@runtime_checkable
class DecoderNode(Protocol):
    TYPE: ClassVar[str]
    INPUTS: ClassVar[Mapping[str, str]]
    OUTPUTS: ClassVar[Mapping[str, str]]
    PARAMS: ClassVar[Mapping[str, Any]]

    def run(self, inputs: Mapping[str, Any], params: Mapping[str, Any]) -> Mapping[str, Any]: ...


@runtime_checkable
class CaptureAdapter(Protocol):
    def __call__(self, path: str | Path, options: dict | None = None) -> Capture: ...


@runtime_checkable
class EventPresenter(Protocol):
    protocol: str


def validate_node_contract(node: type) -> None:
    """在扩展加载/能力发现时验证节点契约。"""
    key = getattr(node, "TYPE", None)
    if not isinstance(key, str) or not key:
        raise ValueError("节点契约要求非空 str TYPE")
    for attr in ("INPUTS", "OUTPUTS", "PARAMS"):
        if not isinstance(getattr(node, attr, None), Mapping):
            raise ValueError(f"节点 {key} 缺少 Mapping {attr}")
    if not callable(getattr(node, "run", None)):
        raise ValueError(f"节点 {key} 缺少 run(inputs, params)")
    for port_map in (node.INPUTS, node.OUTPUTS):
        for name, port_type in port_map.items():
            if not isinstance(name, str) or not isinstance(port_type, str):
                raise ValueError(f"节点 {key} 端口声明必须为 str: {name!r}={port_type!r}")


def validate_adapter_contract(spec: Any) -> None:
    if not isinstance(spec, AdapterSpec):
        raise ValueError("采集适配器契约要求 AdapterSpec")
    if not spec.key or not spec.description:
        raise ValueError("采集适配器契约要求非空 key 和 description")
    if spec.load is not None and not callable(spec.load):
        raise ValueError(f"采集适配器 {spec.key} 的 load 必须可调用")
    if spec.sniff is not None and not callable(spec.sniff):
        raise ValueError(f"采集适配器 {spec.key} 的 sniff 必须可调用")


def validate_presenter_contract(presenter: Any) -> None:
    if not isinstance(getattr(presenter, "protocol", None), str) or not presenter.protocol:
        raise ValueError("呈现契约要求非空 str protocol")
