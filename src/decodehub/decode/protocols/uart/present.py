"""UART 呈现约定（ADR-013）：kind 中文名 / 表格内容列 / CSV 专有列。"""

from __future__ import annotations

from ...events import UartEvent
from ...presentation import Presentation, ascii_byte as _ascii, register_presentation


def _detail(ev: UartEvent) -> str:
    if ev.kind == "uart.frame" and not ev.errors and ev.data_bits == 8:
        return f"{ev.label} '{_ascii(ev.value)}'"
    return ev.label


def _value(ev: UartEvent) -> int:
    return ev.value


register_presentation(Presentation(
    protocol="uart",
    kind_cn={"uart.frame": "UART", "uart.warn": "UART!"},
    detail_fn=_detail,
    event_fields=("value", "parity", "data_bits"),
    csv_columns=(("value_or_address", _value),),
    preview_kinds=("uart.frame",),
))
