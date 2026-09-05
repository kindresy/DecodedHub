"""AVSBus 三线测试波形合成器。"""

from __future__ import annotations

from collections.abc import Iterable

from ....shared.waves import DigitalWave
from .decode import crc3


def _controller_word(tx: dict) -> int:
    cmd = int(tx.get("cmd", 1)) & 0x3
    group = int(tx.get("cmd_group", 0)) & 1
    dtype = int(tx.get("cmd_data_type", 0)) & 0xF
    select = int(tx.get("select", 0)) & 0xF
    data = int(tx.get("cmd_data", 0xFFFF if cmd == 3 else 0)) & 0xFFFF
    word = (1 << 30) | (cmd << 28) | (group << 27) | (dtype << 23) | (select << 19) | (data << 3)
    return word | crc3(word)


def _target_word(tx: dict) -> int:
    ack = int(tx.get("slave_ack", 0)) & 0x3
    status = int(tx.get("status_resp", 0)) & 0x1F
    cmd = int(tx.get("cmd", 1)) & 0x3
    data = int(tx.get("response_data", 0 if cmd == 3 else 0xFFFF)) & 0xFFFF
    word = (ack << 30) | (status << 24) | (data << 8) | (0x1F << 3)
    return word | crc3(word)


def _word_bits(word: int) -> list[int]:
    return [(int(word) >> (31 - i)) & 1 for i in range(32)]


def encode_avsbus(transactions: Iterable[dict], fs: float = 1e6,
                  idle_clocks: int = 2) -> DigitalWave:
    """生成 clock/mdata/sdata 三通道波形；每个 transaction 对应一对 32-bit 子帧。"""
    if fs <= 0 or idle_clocks < 0:
        raise ValueError("fs 必须为正数且 idle_clocks 不能为负")
    bit_t = 1.0 / float(fs)
    half = bit_t / 2
    # bits: clock=0, mdata=1, sdata=2; 两条数据线空闲为 1。
    initial = 0b110
    cur = initial
    t = 4 * bit_t
    segments: list[tuple[float, int]] = []

    def set_level(clock: int | None = None, mdata: int | None = None,
                  sdata: int | None = None, dt: float = half) -> None:
        nonlocal cur, t
        t += dt
        if clock is not None:
            cur = (cur & ~1) | (int(clock) & 1)
        if mdata is not None:
            cur = (cur & ~2) | ((int(mdata) & 1) << 1)
        if sdata is not None:
            cur = (cur & ~4) | ((int(sdata) & 1) << 2)
        segments.append((t, cur))

    for tx in transactions:
        mbits = _word_bits(_controller_word(tx))
        sbits = _word_bits(_target_word(tx))
        for mb, sb in zip(mbits, sbits):
            set_level(clock=0, mdata=mb, sdata=sb, dt=0)
            set_level(clock=1)
            set_level(clock=0)
        # AVS_Clock is normally stopped low between transactions.
        set_level(mdata=1, sdata=1, dt=idle_clocks * bit_t)
    return DigitalWave.from_segments(
        ["clock", "mdata", "sdata"], initial, segments, t_end=t,
        sample_rate=float(fs), n_samples=int(round(t * fs)),
    )
