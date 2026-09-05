"""I2C 呈现约定（ADR-013）：kind 中文名 / 表格内容列 / CSV 专有列。"""

from __future__ import annotations

from ...events import I2cEvent
from ...presentation import Presentation, ascii_byte as _ascii, register_presentation


def _detail(ev: I2cEvent) -> str:
    if ev.kind == "i2c.transfer" and ev.data_bytes:
        asc = "".join(_ascii(b) for b in ev.data_bytes[:16])
        return f"{ev.label} '{asc}'"
    return ev.label


def _address(ev: I2cEvent):
    return ev.address


def _read(ev: I2cEvent):
    return ev.read


def _data_bytes(ev: I2cEvent) -> str:
    return " ".join(f"{b:02X}" for b in ev.data_bytes or [])


def _acks(ev: I2cEvent) -> str:
    return "".join("A" if a else "N" for a in ev.acks or [])


register_presentation(Presentation(
    protocol="i2c",
    kind_cn={"i2c.start": "I2C·S", "i2c.repeat-start": "I2C·Sr", "i2c.stop": "I2C·P",
             "i2c.addr": "I2C·地址", "i2c.data": "I2C·数据", "i2c.transfer": "I2C·传输",
             "i2c.warn": "I2C!"},
    detail_fn=_detail,
    event_fields=("address", "is_10bit", "read", "data_bytes", "acks", "byte_index"),
    csv_columns=(("value_or_address", _address), ("read", _read),
                 ("data_bytes", _data_bytes), ("acks", _acks)),
    preview_kinds=("i2c.transfer", "i2c.addr"),
))
