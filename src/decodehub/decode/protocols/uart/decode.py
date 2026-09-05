"""UART 解码器（docs/41-decode.md §3.1；essence 参照 sigrok，独立实现）。

要点：
- 空闲高电平；起始位锚定（起始位中点）+ 位中点纯算术采样（ADR-005）；
- 自动波特率 = 最短低脉冲集合的中值（起始位是唯一保证的单 bit 低脉冲）；
- 错误是事件字段（parity/framing/break/truncated/spurious-start），恢复点 = 下一个下降沿。
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ....shared.waves import DigitalWave
from ...events import UartEvent
from ...graph import Param
from ...registry import register


@register
class UartDecodeNode:
    TYPE = "uart_decode"
    INPUTS = {"in": "digital"}
    OUTPUTS = {"out": "events"}
    PARAMS = {
        "rx": Param("str", default="", doc="RX 通道名（空 = 第一个通道）"),
        "baud": Param("float_auto", default="auto", doc="波特率；'auto' = 自动估计"),
        "data_bits": Param("int", default=8, lo=5, hi=9, doc="数据位 5–9"),
        "parity": Param("enum", default="N", choices=("N", "O", "E"), doc="校验 N/O/E"),
        "stop_bits": Param("float", default=1.0, doc="停止位长度 1/1.5/2"),
        "invert": Param("bool", default=False, doc="线路反相（空闲低）"),
        "bit_order": Param("enum", default="lsb", choices=("lsb", "msb"), doc="位序 lsb/msb"),
    }

    def run(self, inputs: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        wave: DigitalWave = inputs["in"]
        rx = params["rx"] or wave.channels[0]
        if rx not in wave.channels:
            raise ValueError(f"通道 {rx!r} 不存在；可用: {list(wave.channels)}")
        inv = 1 if params["invert"] else 0

        def lv(t: float) -> int:
            return wave.level_at(rx, t) ^ inv

        times, levels = wave.edge_stream(rx)
        bit_t: float
        baud_warn = False
        if params["baud"] == "auto":
            lows: list[float] = []
            last_fall: float | None = None
            logical = levels ^ np.uint8(inv)
            for t, lv_ in zip(times, logical):
                if lv_ == 0:
                    last_fall = t
                elif last_fall is not None:
                    lows.append(t - last_fall)
                    last_fall = None
            if not lows:
                raise ValueError("自动波特率失败: 无低脉冲")
            m = min(lows)
            cands = [w for w in lows if w < 1.5 * m]
            bit_t = float(np.median(cands if cands else lows))
            # 候选离散度大 → 估计不确定
            if len(cands) >= 3 and float(np.std(cands)) > 0.1 * bit_t:
                baud_warn = True
        else:
            bit_t = 1.0 / float(params["baud"])

        nd = int(params["data_bits"])
        parity = params["parity"]
        nsb = float(params["stop_bits"])
        lsb_first = params["bit_order"] == "lsb"
        frame_bits = 1 + nd + (nsb if parity == "N" else 1 + nsb)

        # 起始候选沿：逻辑下降沿（物理上 after-level == inv）
        cand = times[levels == np.uint8(inv)] if times.size else np.array([])
        events: list[UartEvent] = []
        if baud_warn:
            events.append(UartEvent("uart.warn", 0.0, bit_t, "波特率估计不确定", errors=["baud-uncertain"], ann_class="warn"))

        i = 0
        prev_end = float("-inf")
        n = len(cand)
        while i < n:
            ts = float(cand[i])
            # Sampled edges can be quantized up to about 0.2 UI before the
            # ideal boundary; retain such a legitimate next start edge.
            if ts < prev_end - 0.2 * bit_t:
                i += 1
                continue

            # BREAK 检测：起始电平持续 ≥ 整帧
            t_rise = _next_rise(cand, times, levels, inv, i)
            if t_rise is not None and (t_rise - ts) >= frame_bits * bit_t:
                events.append(UartEvent(
                    "uart.frame", ts, t_rise, "BREAK", errors=["break"], ann_class="warn",
                    data_bits=nd, parity=parity,
                ))
                prev_end = t_rise
                i = _advance_past(cand, t_rise)
                continue

            # 起始位校验（50% 处仍为低）
            if lv(ts + bit_t / 2) != 0:
                events.append(UartEvent(
                    "uart.frame", ts, ts + bit_t, "伪起始", errors=["spurious-start"],
                    ann_class="warn", data_bits=nd, parity=parity,
                ))
                i += 1
                continue

            errors: list[str] = []
            t = ts + bit_t / 2  # 起始位中点锚
            val = 0
            truncated = False
            for k in range(nd):
                t += bit_t
                if t > wave.t_end:
                    truncated = True
                bit = lv(t)
                val |= bit << (k if lsb_first else nd - 1 - k)

            if parity != "N":
                t += bit_t
                if t > wave.t_end:
                    truncated = True
                p = lv(t)
                ones = bin(val).count("1") + p
                if (parity == "O" and ones % 2 != 1) or (parity == "E" and ones % 2 != 0):
                    errors.append("parity")

            # 停止位采样：取首个停止位中点（+0.5），nsb>1 时加最后一停止位中点（+nsb−0.5）
            stop_offs: list[float] = sorted({0.5, nsb - 0.5}) if nsb > 1.0 else [0.5]
            for so in stop_offs:
                ts_pt = ts + (1 + nd + (0 if parity == "N" else 1) + so) * bit_t
                if ts_pt <= wave.t_end and lv(ts_pt) != 1:
                    errors.append("framing")
                    break
            if truncated:
                errors.append("truncated")

            frame_end = ts + frame_bits * bit_t
            mask = (1 << nd) - 1
            val &= mask
            events.append(UartEvent(
                "uart.frame", ts, frame_end, f"0x{val:0{max(1, (nd + 3) // 4)}X}",
                errors=errors, ann_class=("err" if errors else "data"),
                value=val, parity=parity, data_bits=nd,
            ))
            prev_end = frame_end
            i = _advance_past(cand, frame_end - 0.25 * bit_t)
        return {"out": events}


def _next_rise(cand, times, levels, inv, i):
    """cand[i] 之后第一个逻辑上升沿时刻（用于 BREAK 判定）。"""
    t0 = float(cand[i])
    idx = np.searchsorted(times, t0, side="right")
    for j in range(idx, len(times)):
        if levels[j] == np.uint8(1 ^ inv):
            return float(times[j])
    return None


def _advance_past(cand, t):
    return int(np.searchsorted(cand, t, side="right"))
