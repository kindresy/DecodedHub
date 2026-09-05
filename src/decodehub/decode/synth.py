"""合成与导出助手（兼容门面，ADR-012）。

各协议的合成编码器已拆分至 decodehub.decode.protocols/<协议>/encode.py
（与解码器同目录，原理见各协议 README.md）；此处保留通用助手（analogify /
save_kingst_csv）并对全部编码器做兼容再导出。
"""

from __future__ import annotations

import numpy as np

from ..shared.waves import AnalogChannel, DigitalWave
from .protocols.uart.encode import encode_uart  # noqa: F401
from .protocols.i2c.encode import encode_i2c  # noqa: F401
from .protocols.spi.encode import encode_spi  # noqa: F401
from .protocols.i3c.encode import encode_i3c  # noqa: F401
from .protocols.avsbus.encode import encode_avsbus  # noqa: F401
from .protocols.uplink.encode import encode_uplink  # noqa: F401
from .protocols.downlink.encode import encode_downlink  # noqa: F401

__all__ = ["encode_uart", "encode_i2c", "encode_spi", "encode_i3c", "encode_avsbus", "encode_uplink",
           "encode_downlink", "analogify", "save_kingst_csv"]


# -------------------------------------------------------------- 模拟化 ---

def analogify(
    wave: DigitalWave,
    name: str,
    fs: float,
    v_low: float = 0.0,
    v_high: float = 3.3,
    rise_s: float | None = None,
    noise_sigma: float = 0.0,
    seed: int | None = None,
) -> AnalogChannel:
    """数字波形 → 模拟通道（带摆率与噪声），供 slicer 路径测试。"""
    rng = np.random.default_rng(seed)
    n = max(2, int(np.ceil((wave.t_end - wave.t_start) * fs)) + 1)
    t = wave.t_start + np.arange(n) / fs
    logical = np.zeros(n, dtype=np.float64)
    for i, ti in enumerate(t):
        logical[i] = wave.level_at(name, float(ti))
    v = v_low + (v_high - v_low) * logical
    if rise_s and rise_s > 0:
        r = max(1, int(rise_s * fs))
        kernel = np.ones(r) / r
        v = np.convolve(v, kernel, mode="same")
    if noise_sigma > 0:
        v = v + rng.normal(0, noise_sigma, n)
    return AnalogChannel(name=name, samples=v.astype(np.float32), units="V",
                         t0=wave.t_start, dt=1.0 / fs)


def save_kingst_csv(wave: DigitalWave, path, channels: list[str] | None = None) -> None:
    """把 DigitalWave 存为 Kingst CSV 格式（examples / MCP 冒烟测试造数据用）。"""
    chs = channels or list(wave.channels)
    bits = [wave.channels.index(c) for c in chs]
    with open(path, "w", encoding="utf-8") as f:
        f.write("Time[s], " + ", ".join(chs) + "\n")
        f.write(f"{wave.t_start:.9f}, " + ", ".join(str((wave.initial >> b) & 1) for b in bits) + "\n")
        for t, snap in zip(wave.edges_t, wave.edges_levels):
            f.write(f"{t:.9f}, " + ", ".join(str((int(snap) >> b) & 1) for b in bits) + "\n")
