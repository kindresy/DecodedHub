"""I3C presentation contract for Markdown, CSV, and timing output."""

from __future__ import annotations

from ...events import I3cEvent
from ...presentation import Presentation, register_presentation


def _detail(ev: I3cEvent) -> str:
    # Transfer labels already carry the compact byte preview. Do not append
    # the same payload a second time in Markdown/GUI detail cells.
    return ev.label


def _address(ev: I3cEvent):
    return ev.address


def _read(ev: I3cEvent):
    return ev.read


def _mode(ev: I3cEvent):
    return ev.mode


def _data_bytes(ev: I3cEvent) -> str:
    return " ".join(f"{b:02X}" for b in ev.data_bytes or [])


def _acks(ev: I3cEvent) -> str:
    return "".join("-" if a is None else ("A" if a else "N") for a in ev.acks or [])


def _parity(ev: I3cEvent) -> str:
    return "".join("-" if p is None else ("OK" if p else "BAD")
                   for p in ev.parity_ok or [])


def _t_bits(ev: I3cEvent) -> str:
    return "".join("-" if bit is None else str(bit) for bit in ev.t_bits or [])


def _ccc(ev: I3cEvent):
    return "" if ev.ccc is None else f"0x{ev.ccc:02X}"


def _ccc_name(ev: I3cEvent):
    return ev.ccc_name or ""


def _pid(ev: I3cEvent):
    return "" if ev.pid is None else f"0x{ev.pid:012X}"


def _bcr(ev: I3cEvent):
    return "" if ev.bcr is None else f"0x{ev.bcr:02X}"


def _dcr(ev: I3cEvent):
    return "" if ev.dcr is None else f"0x{ev.dcr:02X}"


register_presentation(Presentation(
    protocol="i3c",
    kind_cn={
        "i3c.start": "I3C·S",
        "i3c.repeat-start": "I3C·Sr",
        "i3c.stop": "I3C·P",
        "i3c.addr": "I3C·地址",
        "i3c.data": "I3C·数据",
        "i3c.transfer": "I3C·传输",
        "i3c.ccc": "I3C·CCC",
        "i3c.daa": "I3C·DAA",
        "i3c.warn": "I3C!",
        "i3c.unsupported": "I3C·不支持",
    },
    detail_fn=_detail,
    event_fields=("mode", "address", "read", "data_bytes", "acks", "parity_ok", "t_bits",
                  "ccc", "ccc_name", "pid", "bcr", "dcr"),
    csv_columns=(
        ("value_or_address", _address),
        ("read", _read),
        ("mode", _mode),
        ("data_bytes", _data_bytes),
        ("acks", _acks),
        ("parity", _parity),
        ("t_bits", _t_bits),
        ("ccc", _ccc),
        ("ccc_name", _ccc_name),
        ("pid", _pid),
        ("bcr", _bcr),
        ("dcr", _dcr),
    ),
    preview_kinds=("i3c.transfer", "i3c.addr", "i3c.ccc", "i3c.daa"),
))
