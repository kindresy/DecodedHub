"""解码事件模型（发布语言，呈现层只读消费）。

解码错误是事件字段而非异常（ADR-004）。所有事件全局时间有序（Saleae 不变量）。
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

from .schema import EVENT_SCHEMA_VERSION, REPORT_SCHEMA_VERSION, validate_event


@dataclass
class DecodedEvent:
    kind: str
    t_start: float
    t_end: float
    label: str
    errors: list[str] = field(default_factory=list)
    ann_class: str = "data"  # start/stop/data/ack/warn/err
    schema_version: str = field(default=EVENT_SCHEMA_VERSION, kw_only=True)

    def to_dict(self) -> dict:
        validate_event(self)
        return asdict(self)


@dataclass
class UartEvent(DecodedEvent):
    value: int = 0
    parity: str = "N"
    data_bits: int = 8


@dataclass
class I2cEvent(DecodedEvent):
    address: int | None = None
    is_10bit: bool = False
    read: bool | None = None
    data_bytes: list[int] = field(default_factory=list)
    acks: list[bool] = field(default_factory=list)  # True = ACK
    byte_index: int = 0


@dataclass
class I3cEvent(DecodedEvent):
    """I3C SDR/legacy-I2C event with explicit ninth-bit semantics."""

    mode: str = "unknown"
    address: int | None = None
    read: bool | None = None
    data_bytes: list[int] = field(default_factory=list)
    acks: list[bool | None] = field(default_factory=list)
    parity_ok: list[bool | None] = field(default_factory=list)
    t_bits: list[int | None] = field(default_factory=list)
    ccc: int | None = None
    ccc_name: str | None = None
    pid: int | None = None
    bcr: int | None = None
    dcr: int | None = None


@dataclass
class SpiEvent(DecodedEvent):
    mosi: int | None = None
    miso: int | None = None
    word_bits: int = 8
    words: list[tuple[int | None, int | None]] = field(default_factory=list)  # transfer 级；缺侧 = None（与 word 事件一致）


@dataclass
class AvsBusEvent(DecodedEvent):
    """PMBus/SMIF AVSBus controller/target frame."""

    mode: str = "auto"
    raw_mdata: int = 0
    raw_sdata: int = 0
    start_code: int = 0
    cmd: int = 0
    command: str = "reserved"
    cmd_group: int = 0
    cmd_data_type: int = 0
    select: int = 0
    cmd_data: int = 0
    response_data: int = 0
    slave_ack: int = 0
    status_resp: int = 0
    main_crc: int = 0
    response_crc: int = 0
    main_crc_ok: bool = False
    response_crc_ok: bool = False


@dataclass
class UplinkEvent(DecodedEvent):
    """上行 DSSS 帧（kind ∈ uplink.frame / uplink.warn）。

    t_start = 帧首符号（前导第一符号）的解扩相关峰时刻；value = 数据 bit 组装值。
    """

    value: int = 0
    data_bits: list[int] = field(default_factory=list)
    pream_ok: bool = True
    confidence: float = 0.0
    burst: int = 0


@dataclass
class DownlinkEvent(DecodedEvent):
    """下行 DBPSK 包（kind ∈ downlink.packet / downlink.warn）。

    槽位锚定在上行帧网格（delta 自校准，ADR-011）；value = 16 差分数据位
    （1 = 相对前符号相位翻转）组装值；value_inv 为反相解读（排查用）。
    """

    value: int = 0
    value_inv: int = 0
    bits: list[int] = field(default_factory=list)
    slot: int = 0
    frame: int = 0
    fc_est: float = 0.0
    confidence: float = 0.0


@dataclass
class DecodeReport:
    protocol: str
    params: dict
    events: list[DecodedEvent]
    node_id: str = ""
    wall_ms: float = 0.0
    schema_version: str = field(default=REPORT_SCHEMA_VERSION, kw_only=True)

    def counts(self) -> dict:
        by_kind: dict[str, int] = {}
        n_err = 0
        for ev in self.events:
            by_kind[ev.kind] = by_kind.get(ev.kind, 0) + 1
            n_err += len(ev.errors)
        return {"total": len(self.events), "by_kind": by_kind, "errors": n_err}

    def to_json(self) -> dict:
        if self.schema_version != REPORT_SCHEMA_VERSION:
            raise ValueError(
                f"报告 schema_version={self.schema_version!r} 不兼容；"
                f"当前为 {REPORT_SCHEMA_VERSION!r}"
            )
        return {
            "schema_version": self.schema_version,
            "protocol": self.protocol,
            "params": self.params,
            "counts": self.counts(),
            "node_id": self.node_id,
            "wall_ms": round(self.wall_ms, 3),
            "events": [e.to_dict() for e in self.events],
        }
