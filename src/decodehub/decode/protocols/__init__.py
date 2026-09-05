"""协议模块注册表（ADR-012）：一协议一目录（decode.py + encode.py + README.md）。

内置与外部协议由 decode.plugins 显式加载；这里仅保留可发现的包名。
"""

__all__ = ("uart", "i2c", "spi", "uplink", "downlink", "i3c", "avsbus")
