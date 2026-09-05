"""SPI 呈现约定（ADR-013）：kind 中文名 / 表格内容列 / CSV 专有列。"""

from __future__ import annotations

from ...events import SpiEvent
from ...presentation import Presentation, register_presentation


def _detail(ev: SpiEvent) -> str:
    if ev.kind == "spi.transfer" and ev.words:
        hexes = " ".join("--" if m is None else f"{m:02X}" for m, _ in ev.words[:8])
        more = " …" if len(ev.words) > 8 else ""
        return f"{len(ev.words)} 词: [{hexes}{more}]"
    return ev.label


def _mosi(ev: SpiEvent) -> str:
    return "" if ev.mosi is None else f"{ev.mosi:02X}"


def _miso(ev: SpiEvent) -> str:
    return "" if ev.miso is None else f"{ev.miso:02X}"


def _word_bits(ev: SpiEvent):
    return ev.word_bits


register_presentation(Presentation(
    protocol="spi",
    kind_cn={"spi.word": "SPI·词", "spi.transfer": "SPI·传输", "spi.warn": "SPI!"},
    detail_fn=_detail,
    event_fields=("mosi", "miso", "word_bits", "words"),
    csv_columns=(("mosi", _mosi), ("miso", _miso), ("word_bits", _word_bits)),
    preview_kinds=("spi.transfer", "spi.word"),
))
