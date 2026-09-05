"""Kingst VIS 工程文件（.kvdat）适配器 —— 自描述的二进制跳变档案。

布局（逆向自真实样本，见 docs/40-acquisition.md）：
  [XML <settings> 前导（变长）] "\\n" b"kvdat\\0\\0\\0"
  u64 LE × 4: n_samples, sample_rate, trigger_pos, channel_capacity
  随后的实际序列化通道块（数量由文件末尾决定）:
      u32 常量 0x00442323 | u8 ch_index | u8 initial_level | u16 保留 | u64 record_count
      record_count × 5 字节记录: u32 位置索引 + u8 flag(≡0)
  末条记录 = (n_samples, 0) 为终结符（丢弃）。

记录按通道分块 → 通道归属直接可知；各通道跳变合并为全局时间序位域快照。
"""

from __future__ import annotations

import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from ...shared.errors import IngestError
from ...shared.waves import Capture, CaptureMeta, DigitalWave
from .spec import AdapterSpec, OptionField

_MAGIC = b"kvdat\x00\x00\x00"
_CH_STRUCT = struct.Struct("<IBBHQ")  # magic, ch_index, initial, res, record_count
_REC_DTYPE = np.dtype([("pos", "<u4"), ("flag", "u1")])


def _parse_settings(xml_bytes: bytes) -> tuple[dict, list[str]]:
    """Read the small, optional XML settings prefix without trusting it."""
    if not xml_bytes.strip():
        return {}, ["XML settings missing"]
    try:
        root = ET.fromstring(xml_bytes)
    except (ET.ParseError, UnicodeError, LookupError) as exc:
        # ElementTree raises LookupError when the XML declaration names an unknown codec.
        return {}, [f"XML parse failed: {exc}"]

    settings: dict = {}
    diagnostics: list[str] = []
    global_settings = root.find("global")
    if global_settings is not None:
        version = global_settings.findtext("version")
        if version:
            settings["version"] = version.strip()
        channel_names = {}
        for element in global_settings:
            suffix = element.tag.removeprefix("chnShowName")
            if not element.tag.startswith("chnShowName") or not suffix.isdigit():
                continue
            compact = suffix.lstrip("0") or "0"
            if len(compact) > 3 or (len(compact) == 3 and compact > "255"):
                diagnostics.append(
                    f"XML channel name index ignored: {len(suffix)}-digit value outside 0..255"
                )
                continue
            channel_names[int(compact)] = (element.text or "").strip()
        if channel_names:
            settings["channel_names"] = channel_names

    devices = root.find("devices")
    device = next(iter(devices), None) if devices is not None else None
    if device is not None:
        settings["device"] = device.tag
        enabled_element = device.find("chnEnable")
        if enabled_element is not None:
            enabled = [
                index for index, value in enumerate((enabled_element.text or "").split(","))
                if value.strip() == "1"
            ]
            settings["enabled_channels"] = enabled

    analyzers = root.find("analyzers")
    if analyzers is not None:
        raw_analyzers = [
            {field.tag: field.text or "" for field in analyzer}
            for analyzer in analyzers
        ]
        if raw_analyzers:
            settings["analyzers"] = raw_analyzers
    return settings, diagnostics


def _channel_names(settings: dict, physical_channels: list[int]) -> tuple[str, ...]:
    """Map global Kingst labels to the channels that were actually serialized."""
    saved_names = settings.get("channel_names", {})
    names: list[str] = []
    used: set[str] = set()
    for channel in physical_channels:
        base = saved_names.get(channel) or f"D{channel}"
        name = base
        attempt = 2
        while name in used:
            suffix = f" [D{channel}]" if attempt == 2 else f" [D{channel} #{attempt}]"
            name = f"{base}{suffix}"
            attempt += 1
        names.append(name)
        used.add(name)
    return tuple(names)


def _spi_defaults(
    analyzer: dict, version: str | None, physical_to_name: dict[int, str],
) -> tuple[dict | None, str | None]:
    """Decode the one KingstVIS SPI vector whose layout is verified."""
    parts = version.split(".") if version else []
    if len(parts) != 3 or parts[:2] != ["3", "6"] or not parts[2].isdigit():
        return None, f"SPI defaults skipped: unsupported KingstVIS version {version!r}"

    parameters = analyzer.get("parameters")
    if not isinstance(parameters, str):
        return None, "SPI defaults skipped: missing parameters"
    tokens = [token.strip() for token in parameters.split(",")]
    if tokens[-1:] == [""]:
        tokens.pop()
    if len(tokens) != 15 or tokens[0] != "SpiAnalyzer":
        return None, "SPI defaults skipped: unsupported analyzer or parameter vector"
    try:
        values = [int(token, 10) for token in tokens[1:]]
    except ValueError:
        return None, "SPI defaults skipped: non-integer parameter"

    channels = values[:8]
    if any(not 0 <= value <= 0xFFFFFFFFFFFFFFFF for value in channels[::2]) or any(
        not 0 <= value <= 0xFFFFFFFF for value in channels[1::2]
    ):
        return None, "SPI defaults skipped: unsupported channel value"

    channel_ids = channels[::2]
    companions = channels[1::2]
    if any(companions):
        return None, "SPI defaults skipped: nonzero Channel companion is unsupported"

    mosi_id, miso_id, clk_id, enable_id = channel_ids
    missing = 0xFFFFFFFFFFFFFFFF
    def name_for(channel: int) -> str | None:
        return None if channel == missing else physical_to_name.get(channel)

    mosi, miso, clk, enable = (
        name_for(channel) for channel in (mosi_id, miso_id, clk_id, enable_id)
    )
    for role, channel, name in (
        ("MOSI", mosi_id, mosi), ("MISO", miso_id, miso),
        ("CLOCK", clk_id, clk), ("ENABLE", enable_id, enable),
    ):
        if channel != missing and name is None:
            return None, f"SPI defaults skipped: {role} channel D{channel} is unserialized"
    if clk is None:
        return None, "SPI defaults skipped: CLOCK channel is missing or unserialized"
    if mosi is None and miso is None:
        return None, "SPI defaults skipped: both MOSI and MISO channels are missing or unserialized"

    shift_order, word_bits, cpol, cpha, enable_active, _show_decode_marker = values[8:]
    bit_orders = {0: "msb", 1: "lsb"}
    active_states = {0: "low", 1: "high"}
    if shift_order not in bit_orders:
        return None, "SPI defaults skipped: unsupported shift order"
    if not 1 <= word_bits <= 32:
        return None, "SPI defaults skipped: unsupported word size"
    if cpol not in (0, 1) or cpha not in (0, 1):
        return None, "SPI defaults skipped: unsupported clock mode"
    if enable_active not in active_states:
        return None, "SPI defaults skipped: unsupported enable polarity"

    defaults = {
        "clk": clk,
        "mosi": mosi,
        "miso": miso,
        "cs": enable,
        "bit_order": bit_orders[shift_order],
        "word_bits": word_bits,
        "cpol": cpol,
        "cpha": cpha,
        "cs_active": active_states[enable_active],
    }
    return defaults, None


def load(path: str | Path, options: dict | None = None) -> Capture:
    opts = options or {}
    data = Path(path).read_bytes()
    magic_off = data.find(_MAGIC)
    if magic_off < 0:
        raise IngestError(f"{path}: 未找到 kvdat 魔数")
    xml_prefix = data[:magic_off]
    settings, xml_diagnostics = _parse_settings(xml_prefix)
    off = magic_off + len(_MAGIC)
    if len(data) - off < 32:
        raise IngestError(f"{path}: kvdat 固定头截断（剩余 {len(data) - off} 字节）")
    n_samples, sample_rate, trigger_pos, channel_capacity = struct.unpack_from("<QQQQ", data, off)
    off += 32
    if sample_rate == 0:
        raise IngestError(f"{path}: kvdat 采样率不能为零")

    position_groups: list[np.ndarray] = []
    position_counts: list[int] = []
    initial_mask = 0
    names: list[str] = []
    physical_channels: list[int] = []
    seen_channels: set[int] = set()
    while off < len(data):
        if len(data) - off < _CH_STRUCT.size:
            raise IngestError(f"{path}: 通道块头截断（偏移 {off}，剩余 {len(data) - off} 字节）")
        magic, ch_index, initial_level, _res, record_count = _CH_STRUCT.unpack_from(data, off)
        off += _CH_STRUCT.size
        if magic != 0x00442323:
            raise IngestError(f"{path}: 通道描述头魔数不符（物理通道 D{ch_index}，0x{magic:08X}）")
        if ch_index in seen_channels:
            raise IngestError(f"{path}: 物理通道 D{ch_index} 重复")
        if ch_index >= channel_capacity:
            raise IngestError(f"{path}: 物理通道 D{ch_index} 超出设备容量 {channel_capacity}")
        if initial_level not in (0, 1):
            raise IngestError(f"{path}: 物理通道 D{ch_index} 初始电平无效: {initial_level}")
        if len(names) >= 32:
            raise IngestError(f"{path}: 序列化通道最多 32 个（DigitalWave uint32）")
        records_bytes = record_count * _REC_DTYPE.itemsize
        if len(data) - off < records_bytes:
            raise IngestError(
                f"{path}: 物理通道 D{ch_index} 记录截断"
                f"（需要 {records_bytes} 字节，剩余 {len(data) - off} 字节）"
            )

        dense_index = len(names)
        seen_channels.add(ch_index)
        physical_channels.append(ch_index)
        names.append(f"D{ch_index}")
        initial_mask |= initial_level << dense_index
        records = np.frombuffer(data, dtype=_REC_DTYPE, count=record_count, offset=off)
        off += records_bytes
        flags = records["flag"]
        bad_flags = np.flatnonzero(flags)
        if bad_flags.size:
            record_index = int(bad_flags[0])
            raise IngestError(
                f"{path}: 物理通道 D{ch_index} 记录 {record_index} "
                f"不支持的记录标志: {int(flags[record_index])}"
            )
        positions = records["pos"]
        past_end = np.flatnonzero(positions > n_samples)
        if past_end.size:
            record_index = int(past_end[0])
            raise IngestError(
                f"{path}: 物理通道 D{ch_index} 记录 {record_index} 位置 "
                f"{int(positions[record_index])} 超出 n_samples {n_samples}"
            )
        terminators = np.flatnonzero(positions == n_samples)
        if terminators.size and (terminators.size != 1 or int(terminators[0]) != record_count - 1):
            raise IngestError(
                f"{path}: 物理通道 D{ch_index} terminator must be the final record"
            )
        if terminators.size:
            positions = positions[:-1]
        if positions.size >= 2 and np.any(positions[1:] <= positions[:-1]):
            bad = int(np.flatnonzero(positions[1:] <= positions[:-1])[0]) + 1
            raise IngestError(
                f"{path}: 物理通道 D{ch_index} record positions must be strictly increasing "
                f"(record {bad})"
            )
        position_groups.append(positions)
        position_counts.append(int(positions.size))

    if sum(position_counts):
        positions = np.concatenate(position_groups).astype(np.uint32, copy=False)
        bits = np.repeat(np.arange(len(position_counts), dtype=np.uint8), position_counts)
        order = np.argsort(positions, kind="stable")
        positions = positions[order]
        masks = np.left_shift(np.uint32(1), bits[order].astype(np.uint32))

        starts_mask = np.empty(positions.size, dtype=bool)
        starts_mask[0] = True
        starts_mask[1:] = positions[1:] != positions[:-1]
        starts = np.flatnonzero(starts_mask)
        xor_masks = np.bitwise_xor.reduceat(masks, starts)
        positions = positions[starts]
        changed = xor_masks != 0
        positions = positions[changed]
        xor_masks = xor_masks[changed]
        edges_lv = np.bitwise_xor.accumulate(xor_masks) ^ np.uint32(initial_mask)
        edges_t = positions.astype(np.float64)
        edges_t /= sample_rate
    else:
        edges_t = np.empty(0, dtype=np.float64)
        edges_lv = np.empty(0, dtype=np.uint32)

    wave = DigitalWave(
        channels=_channel_names(settings, physical_channels),
        initial=initial_mask,
        t_start=0.0,
        edges_t=edges_t,
        edges_levels=edges_lv,
        t_end=n_samples / sample_rate,
        sample_rate=float(sample_rate),
        n_samples=int(n_samples),
    )
    protocol_defaults: dict = {}
    analyzer_diagnostics: list[str] = []
    physical_to_name = dict(zip(physical_channels, wave.channels, strict=True))
    for analyzer in settings.get("analyzers", []):
        spi_defaults, diagnostic = _spi_defaults(
            analyzer, settings.get("version"), physical_to_name,
        )
        if diagnostic:
            analyzer_diagnostics.append(diagnostic)
        elif spi_defaults is not None and "spi" not in protocol_defaults:
            protocol_defaults["spi"] = spi_defaults
    kingst = {
        key: settings[key]
        for key in ("version", "device", "enabled_channels", "analyzers")
        if key in settings
    }
    if xml_diagnostics:
        kingst["xml_diagnostics"] = xml_diagnostics
    if analyzer_diagnostics:
        kingst["analyzer_diagnostics"] = analyzer_diagnostics
    meta = CaptureMeta(
        source_kind="kingst",
        format_key="kingst_kvdat",
        device=opts["device"] if "device" in opts else settings.get("device", "Kingst LA"),
        source_files=[str(path)],
        sample_rate=float(sample_rate),
        trigger_t=float(trigger_pos) / sample_rate if trigger_pos else 0.0,
        extra={
            "n_samples": int(n_samples),
            "channel_capacity": int(channel_capacity),
            "serialized_channels": len(physical_channels),
            "physical_channels": physical_channels,
            **({"protocol_defaults": protocol_defaults} if protocol_defaults else {}),
            **({"kingst": kingst} if kingst else {}),
        },
    )
    return Capture(meta=meta, digital=wave)


def _sniff(ctx) -> bool:
    return _MAGIC in ctx.head


SPEC = AdapterSpec(
    key="kingst_kvdat",
    description="KingstVIS 3.6.x 工程文件（通道名、稀疏通道和已保存 SPI 设置）",
    load=load,
    sniff=_sniff,
    sniff_hint="kvdat 魔数",
    options=(OptionField("device", doc="设备显示名（缺省使用工程内设备名）"),),
)
