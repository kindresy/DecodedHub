"""Deterministic I3C SDR waveform synthesis for tests and examples."""

from __future__ import annotations

from collections.abc import Iterable

from ....shared.waves import DigitalWave


def _odd_parity(byte: int) -> int:
    return 1 ^ (int(byte).bit_count() & 1)


_READ_CCCS = {0x8B, 0x8C, 0x8D, 0x8E, 0x8F, 0x90, 0x91}


def encode_i3c(
    transactions: Iterable[dict],
    freq: float = 100e3,
    gap_s: float = 5e-5,
) -> DigitalWave:
    """Encode I3C SDR/legacy-I2C transactions on SCL and SDA.

    A transaction contains addr, read, and data. Optional fields are mode
    (sdr or legacy_i2c), acks, t_bits, bad_parity, repeat_next, ccc and daa.
    daa is a mapping with pid (six bytes/int), bcr, dcr and optional address.
    """
    txs = [dict(tx) for tx in transactions]
    if freq <= 0:
        raise ValueError("freq must be positive")
    bit_t = 1.0 / float(freq)
    half = bit_t / 2.0
    t = float(gap_s)
    cur = [1, 1]  # SCL, SDA
    snaps: list[tuple[float, int]] = []

    def set_(dt: float, scl: int | None = None, sda: int | None = None) -> None:
        nonlocal t
        t += float(dt)
        if scl is not None:
            cur[0] = int(bool(scl))
        if sda is not None:
            cur[1] = int(bool(sda))
        snaps.append((t, cur[0] | (cur[1] << 1)))

    def start(repeated: bool) -> None:
        if repeated:
            set_(half, 0, 1)
            set_(half, 1, 1)
            set_(half, 1, 0)
        else:
            set_(half, 1, 1)
            set_(half, 1, 0)
        set_(half, 0, 0)

    def stop() -> None:
        set_(half, 0, 0)
        set_(half, 1, 0)
        set_(half, 1, 1)

    def bit(value: int) -> None:
        set_(half, 0, value)
        set_(half, 1, value)

    def byte(value: int, ninth: int) -> None:
        for shift in range(7, -1, -1):
            bit((int(value) >> shift) & 1)
        bit(ninth)

    previous: dict | None = None
    for tx in txs:
        addr = int(tx.get("addr", 0)) & 0x7F
        read = bool(tx.get("read", False))
        mode = str(tx.get("mode", "sdr"))
        if mode not in {"sdr", "legacy_i2c", "hdr-ddr", "hdr-tsp", "hdr-tsl", "hdr-bt"}:
            raise ValueError(f"unsupported I3C synth mode: {mode}")
        hdr_mode = mode if mode.startswith("hdr-") else None
        if hdr_mode:
            addr = 0x7E
            mode = "sdr"
        data = [int(v) & 0xFF for v in tx.get("data", [])]
        repeated = previous is not None and bool(previous.get("repeat_next"))
        start(repeated)

        acks = list(tx.get("acks", []))
        address_ack = bool(acks[0]) if acks else True
        ccc = tx.get("ccc")
        if hdr_mode:
            ccc = {"hdr-ddr": 0x20, "hdr-tsp": 0x21,
                   "hdr-tsl": 0x22, "hdr-bt": 0x23}[hdr_mode]
        daa = tx.get("daa")
        byte((addr << 1) | int(read and ccc is None and daa is None),
             0 if address_ack else 1)

        bad_parity = list(tx.get("bad_parity", []))
        if daa is not None:
            ccc = 0x07
            byte(ccc, _odd_parity(ccc))
            targets = daa if isinstance(daa, list) else [daa]
            for target in targets:
                # ENTDAA arbitration: repeated START, 7Eh/R + ACK, then
                # 64 uninterrupted PID/BCR/DCR bits (no ninth clocks).
                start(True)
                byte(0xFD, 0 if target.get("address_ack", True) else 1)
                pid = target.get("pid", 0)
                if isinstance(pid, (bytes, bytearray, list, tuple)):
                    pid_bytes = [int(v) & 0xFF for v in pid]
                else:
                    pid_bytes = [(int(pid) >> shift) & 0xFF for shift in range(40, -1, -8)]
                pid_bytes = (pid_bytes + [0] * 6)[:6]
                daa_data = pid_bytes + [int(target.get("bcr", 0)) & 0xFF,
                                        int(target.get("dcr", 0)) & 0xFF]
                for value in daa_data:
                    for shift in range(7, -1, -1):
                        bit((value >> shift) & 1)
                address = int(target.get("address", 0)) & 0x7F
                for shift in range(6, -1, -1):
                    bit((address >> shift) & 1)
                bit(_odd_parity(address))
                bit(0 if target.get("assigned_ack", True) else 1)
            # A final 7Eh/R NACK terminates the arbitration rounds.
            start(True)
            byte(0xFD, 1)
        elif ccc is not None:
            ccc = int(ccc) & 0xFF
            byte(ccc, _odd_parity(ccc))
            if read or ccc in _READ_CCCS:
                t_bits = list(tx.get("t_bits", []))
                if not t_bits:
                    t_bits = [1] * max(0, len(data) - 1) + ([0] if data else [])
                for i, value in enumerate(data):
                    transition = int(t_bits[i]) if i < len(t_bits) else (0 if i == len(data) - 1 else 1)
                    byte(value, transition)
            elif mode == "legacy_i2c":
                for i, value in enumerate(data):
                    ack = bool(acks[i + 1]) if i + 1 < len(acks) else True
                    byte(value, 0 if ack else 1)
            else:
                for i, value in enumerate(data):
                    parity = _odd_parity(value)
                    if i < len(bad_parity) and bad_parity[i]:
                        parity ^= 1
                    byte(value, parity)
        elif mode == "legacy_i2c":
            for i, value in enumerate(data):
                ack = bool(acks[i + 1]) if i + 1 < len(acks) else True
                byte(value, 0 if ack else 1)
        elif read:
            t_bits = list(tx.get("t_bits", []))
            if not t_bits:
                t_bits = [1] * max(0, len(data) - 1) + ([0] if data else [])
            for i, value in enumerate(data):
                transition = int(t_bits[i]) if i < len(t_bits) else (0 if i == len(data) - 1 else 1)
                byte(value, transition)
        else:
            for i, value in enumerate(data):
                parity = _odd_parity(value)
                if i < len(bad_parity) and bad_parity[i]:
                    parity ^= 1
                byte(value, parity)
        previous = tx
        if not tx.get("repeat_next"):
            stop()
            t += float(gap_s)

    if txs and txs[-1].get("repeat_next"):
        stop()
    elif not txs:
        t += float(gap_s)

    return DigitalWave.from_segments(
        ["SCL", "SDA"],
        0b11,
        snaps,
        t_end=t,
    )
