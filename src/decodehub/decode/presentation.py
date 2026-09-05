"""呈现注册表：protocol 族 → 表格/CSV/时序图呈现约定（ADR-013）。

render 只依赖 DecodedEvent 基础字段与本注册表——协议特化的中文名、内容列、
CSV 专有列在协议侧注册（protocols/<p>/present.py），render/app 零改动
（仿 registry.py 先例；导入 decode 包即触发全部注册）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from .schema import register_event_fields, register_kinds

_PRESENTATIONS: dict[str, Presentation] = {}


def _label_detail(ev: Any) -> str:
    """默认内容列：label 原文（未注册 detail_fn 时）。"""
    return ev.label


def ascii_byte(byte: int) -> str:
    """字节 → 可打印 ASCII 字符；不可打印显示 '·'（各协议 detail_fn 共用）。"""
    c = chr(byte)
    return c if c.isprintable() and 32 <= byte < 127 else "·"


@dataclass(frozen=True)
class Presentation:
    """一个协议族（kind 前缀）的呈现约定。"""

    protocol: str                                        # 族前缀，如 "uart"
    kind_cn: Mapping[str, str] = field(default_factory=dict)  # kind → 中文短名
    detail_fn: Callable[[Any], str] = _label_detail      # 事件 → 表格"内容"列
    csv_columns: tuple[tuple[str, Callable], ...] = ()   # 协议专有 CSV 列 (列名, 取值fn)
    event_fields: tuple[str, ...] = ()                    # 事件扩展字段 schema
    plot_family: bool = True                             # timing_plot 是否画该族 span
    preview_kinds: tuple[str, ...] = ()                  # run_decode 摘要预览包含的 kind


def register_presentation(p: Presentation) -> None:
    """注册一个协议族的呈现约定；重复 protocol 抛 ValueError。"""
    if p.protocol in _PRESENTATIONS:
        raise ValueError(f"协议呈现重复注册: {p.protocol}")
    register_kinds(p.kind_cn)
    fields = list(p.event_fields)
    fields.extend(name for name, _fn in p.csv_columns if name not in fields)
    register_event_fields(p.protocol, fields)
    _PRESENTATIONS[p.protocol] = p


def presentation_of(kind: str) -> Presentation | None:
    """kind → 所属协议族的约定（kind.split(".")[0] == protocol）；未注册返回 None。"""
    return _PRESENTATIONS.get(kind.split(".")[0])


def all_presentations() -> tuple[Presentation, ...]:
    """全部已注册约定（注册顺序——CSV 并集列序依赖它的稳定性）。"""
    return tuple(_PRESENTATIONS.values())


def all_preview_kinds() -> tuple[str, ...]:
    """全部协议 preview_kinds 的并集（注册顺序去重）。"""
    out: list[str] = []
    for p in _PRESENTATIONS.values():
        for k in p.preview_kinds:
            if k not in out:
                out.append(k)
    return tuple(out)
