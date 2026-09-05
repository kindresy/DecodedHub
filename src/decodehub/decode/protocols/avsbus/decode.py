"""PMBus/SMIF Adaptive Voltage Scaling Bus 被动解码器。

AVSBus 是三线、时钟同步的 32-bit controller/target 子帧。此实现消费
``DigitalWave`` 的 clock 上升沿，在每 32 个时钟上采样 MData/SData，解析
公开 Quick Guide/PMBus 资料定义的字段和 CRC-3（x³+x+1）。它不假设电气
所有权，因此对逻辑分析仪或离线波形同样适用；未能从逻辑波形证明的 HDR
电气语义会以告警保留。
"""

from __future__ import annotations

from typing import Any

from ....shared.waves import DigitalWave
from ...events import AvsBusEvent
from ...graph import Param
from ...registry import register
from ...schema import register_error_codes


register_error_codes({
    "start-code", "main-crc", "response-crc", "main-reserved",
    "response-reserved", "unsupported-cmd", "slave-ack", "status-response",
    "resync", "truncated",
})

_COMMANDS = {0: "write_commit", 1: "write_hold", 2: "reserved", 3: "read"}
_DATA_TYPES = {
    0x0: "voltage", 0x1: "transition_rate", 0x2: "current",
    0x3: "temperature", 0x4: "reset_voltage", 0x5: "power_mode",
    0xE: "status", 0xF: "version",
}


def crc3(word: int) -> int:
    """AVSBus CRC remainder for the upper 29 bits (polynomial 0b1011)."""
    value = int(word) & 0xFFFFFFF8
    polynomial = 0xB0000000
    msb = 0x80000000
    while value & 0xFFFFFFF8:
        if value & msb:
            value ^= polynomial
        polynomial >>= 1
        msb >>= 1
    return value & 0x7


def _bits_to_int(bits: list[int], start: int, end: int) -> int:
    value = 0
    for bit in bits[start:end]:
        value = (value << 1) | int(bit)
    return value


def _bits_word(bits: list[int]) -> int:
    return _bits_to_int(bits, 0, len(bits))


@register
class AvsBusDecodeNode:
    TYPE = "avsbus_decode"
    INPUTS = {"in": "digital"}
    OUTPUTS = {"out": "events"}
    PARAMS = {
        "clock": Param("str", default="", doc="AVS_Clock 通道名（空 = 第一个通道）"),
        "mdata": Param("str", default="", doc="AVS_MData 通道名（空 = 第二个通道）"),
        "sdata": Param("str", default="", doc="AVS_SData 通道名（空 = 第三个通道）"),
        "mode": Param("enum", default="auto", choices=("auto", "controller", "target"),
                       doc="观察方向；auto 同时解析 controller/target 子帧"),
        "frame_bits": Param("int", default=32, lo=32, hi=32,
                             doc="每个 AVSBus 子帧的时钟数（固定 32）"),
    }

    def run(self, inputs: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        wave: DigitalWave = inputs["in"]
        names = list(wave.channels)
        clock = params["clock"] or (names[0] if names else "")
        mdata = params["mdata"] or (names[1] if len(names) > 1 else "")
        sdata = params["sdata"] or (names[2] if len(names) > 2 else "")
        for n in (clock, mdata, sdata):
            if n not in names:
                raise ValueError(f"通道 {n!r} 不存在；可用: {names}")
        if len({clock, mdata, sdata}) != 3:
            raise ValueError("AVS_Clock、AVS_MData、AVS_SData 必须是三个不同通道")

        clock_t, clock_lv = wave.edge_stream(clock)
        rises = [float(t) for t, lv in zip(clock_t, clock_lv) if int(lv) == 1]
        events: list[AvsBusEvent] = []
        if not rises:
            return {"out": events}

        # 连续 34 个 MData=1 时是公开 AVSBus 重同步序列；重同步会丢弃
        # 之前尚未完成的子帧，并让后续时钟从新的 32-bit 边界重新分组。
        resync_ranges: list[tuple[int, int]] = []
        i = 0
        while i < len(rises):
            if wave.level_at(mdata, rises[i]) != 1:
                i += 1
                continue
            j = i + 1
            while j < len(rises) and wave.level_at(mdata, rises[j]) == 1:
                j += 1
            if j - i >= 34:
                resync_ranges.append((i, j - 1))
                events.append(AvsBusEvent(
                    "avsbus.resync", rises[i], rises[j - 1], "重同步（34 个连续 1）",
                    errors=["resync"], ann_class="warn", mode=params["mode"],
                ))
            i = j

        frame_bits = int(params["frame_bits"])
        segments: list[tuple[list[float], bool]] = []
        cursor = 0
        for start, end in resync_ranges:
            if start > cursor:
                # 该段后面有重同步，尾部不足一帧的数据应丢弃而非告警。
                segments.append((rises[cursor:start], False))
            cursor = end + 1
        if cursor < len(rises):
            segments.append((rises[cursor:], True))
        for frame_rises, warn_truncated in segments:
            for offset in range(0, len(frame_rises), frame_bits):
                group = frame_rises[offset:offset + frame_bits]
                if len(group) < frame_bits:
                    if warn_truncated and group:
                        events.append(AvsBusEvent(
                            "avsbus.warn", group[0], group[-1], "不完整子帧",
                            errors=["truncated"], ann_class="warn", mode=params["mode"],
                        ))
                    break
                mbits = [wave.level_at(mdata, t) for t in group]
                sbits = [wave.level_at(sdata, t) for t in group]
                mword, sword = _bits_word(mbits), _bits_word(sbits)
                start_code = _bits_to_int(mbits, 0, 2)
                cmd = _bits_to_int(mbits, 2, 4)
                command = _COMMANDS[cmd]
                main_crc = mword & 0x7
                response_crc = sword & 0x7
                main_ok = crc3(mword) == main_crc
                response_ok = crc3(sword) == response_crc
                cmd_group = _bits_to_int(mbits, 4, 5)
                dtype = _bits_to_int(mbits, 5, 9)
                select = _bits_to_int(mbits, 9, 13)
                cmd_data = _bits_to_int(mbits, 13, 29)
                slave_ack = _bits_to_int(sbits, 0, 2)
                status_resp = _bits_to_int(sbits, 3, 8)
                response_data = _bits_to_int(sbits, 8, 24)
                errors: list[str] = []
                check_main = params["mode"] != "target"
                check_response = params["mode"] != "controller"
                if check_main and start_code != 1:
                    errors.append("start-code")
                if check_main and not main_ok:
                    errors.append("main-crc")
                if check_response and not response_ok:
                    errors.append("response-crc")
                if check_main and cmd == 2:
                    errors.append("unsupported-cmd")
                if check_main and cmd == 3 and cmd_data != 0xFFFF:
                    errors.append("main-reserved")
                response_reserved_bad = sbits[24:29] != [1] * 5
                if sbits[2] != 0:
                    response_reserved_bad = True
                if check_response and cmd != 3 and response_data != 0xFFFF:
                    response_reserved_bad = True
                if check_response and response_reserved_bad:
                    errors.append("response-reserved")
                if check_response and slave_ack:
                    errors.append("slave-ack")
                if check_response and status_resp:
                    errors.append("status-response")
                label = (
                    f"{command} type={_DATA_TYPES.get(dtype, f'0x{dtype:X}')} "
                    f"sel=0x{select:X} data=0x{cmd_data:04X} "
                    f"resp=0x{response_data:04X}"
                )
                events.append(AvsBusEvent(
                    "avsbus.frame", group[0], group[-1], label,
                    errors=errors, ann_class=("err" if errors else "data"),
                    mode=params["mode"], raw_mdata=mword, raw_sdata=sword,
                    start_code=start_code, cmd=cmd, command=command,
                    cmd_group=cmd_group, cmd_data_type=dtype, select=select,
                    cmd_data=cmd_data, response_data=response_data,
                    slave_ack=slave_ack, status_resp=status_resp,
                    main_crc=main_crc, response_crc=response_crc,
                    main_crc_ok=main_ok, response_crc_ok=response_ok,
                ))
        events.sort(key=lambda e: (e.t_start, e.t_end))
        return {"out": events}
