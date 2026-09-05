"""下行 DBPSK 呈现约定（ADR-013）：kind 中文名 / CSV 专有列。

detail_fn 缺省 = label 原文（下行包的 label 已含槽位/帧号摘要）。
"""

from __future__ import annotations

from ...events import DownlinkEvent
from ...presentation import Presentation, register_presentation


def _value(ev: DownlinkEvent) -> int:
    return ev.value


def _fc_hz(ev: DownlinkEvent) -> str:
    return f"{ev.fc_est:.6g}"


def _slot(ev: DownlinkEvent) -> int:
    return ev.slot


def _frame(ev: DownlinkEvent) -> int:
    return ev.frame


def _confidence(ev: DownlinkEvent) -> str:
    return f"{ev.confidence:.4g}"


register_presentation(Presentation(
    protocol="downlink",
    kind_cn={"downlink.packet": "下行·包", "downlink.warn": "下行!"},
    event_fields=("value", "value_inv", "bits", "slot", "frame", "fc_est", "confidence"),
    csv_columns=(("value_or_address", _value), ("fc_hz", _fc_hz), ("slot", _slot),
                 ("frame", _frame), ("confidence", _confidence)),
    preview_kinds=("downlink.packet",),
    plot_family=False,  # 模拟直达协议：span 走 analog_plot 的 events 通道（ADR-011）
))
