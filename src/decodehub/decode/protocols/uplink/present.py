"""上行 DSSS 呈现约定（ADR-013）：kind 中文名 / 表格内容列 / CSV 专有列。"""

from __future__ import annotations

from ...events import UplinkEvent
from ...presentation import Presentation, register_presentation


def _detail(ev: UplinkEvent) -> str:
    if ev.kind == "uplink.frame":
        bits = "".join(str(b) for b in ev.data_bits)
        return f"{ev.label}  bits={bits}"
    return ev.label


def _value(ev: UplinkEvent) -> int:
    return ev.value


def _pream_ok(ev: UplinkEvent):
    return ev.pream_ok


def _confidence(ev: UplinkEvent) -> str:
    return f"{ev.confidence:.4g}"


register_presentation(Presentation(
    protocol="uplink",
    kind_cn={"uplink.frame": "上行·帧", "uplink.warn": "上行!"},
    detail_fn=_detail,
    event_fields=("value", "data_bits", "pream_ok", "confidence", "burst"),
    csv_columns=(("value_or_address", _value), ("pream_ok", _pream_ok),
                 ("confidence", _confidence)),
    preview_kinds=("uplink.frame",),
    plot_family=False,  # 模拟直达协议：span 走 analog_plot 的 events 通道（ADR-010）
))
