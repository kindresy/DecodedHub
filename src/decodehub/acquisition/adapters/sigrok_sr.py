"""Sigrok ``.sr`` logic capture 适配器（只读、unitsize 1–4）。"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import numpy as np

from ...shared.errors import IngestError
from ...shared.waves import Capture, CaptureMeta, DigitalWave
from .spec import AdapterSpec, SniffCtx

_RATE = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*([kKmMgG]?)\s*(?:Hz)?")
_PROBE = re.compile(r"probe(\d+)\s*=\s*(.+)", re.IGNORECASE)


def _metadata(text: str) -> tuple[float, int, dict[int, str], str]:
    rate = None
    unitsize = 1
    probes: dict[int, str] = {}
    capturefiles: list[str] = []
    device_sections = 0
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.lower().startswith("[device "):
            device_sections += 1
            continue
        if line.startswith("["):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key, value = key.strip().lower(), value.strip()
        if key == "samplerate":
            m = _RATE.search(value)
            if not m:
                raise IngestError(f"Sigrok samplerate 无法解析: {value!r}")
            mult = {"": 1.0, "k": 1e3, "m": 1e6, "g": 1e9}[m.group(2).lower()]
            rate = float(m.group(1)) * mult
        elif key == "unitsize":
            unitsize = int(value)
        elif key == "capturefile":
            capturefiles.append(value)
        else:
            m = _PROBE.fullmatch(raw.strip())
            if m:
                probes[int(m.group(1)) - 1] = m.group(2).strip()
    if rate is None or rate <= 0:
        raise IngestError("Sigrok metadata 缺少有效 samplerate")
    if unitsize not in (1, 2, 3, 4):
        raise IngestError(f"Sigrok unitsize={unitsize} 暂不支持（仅 1–4）")
    if device_sections > 1 or len(set(capturefiles)) > 1:
        raise IngestError("Sigrok .sr 包含多个 device/capturefile；当前适配器要求单设备")
    return rate, unitsize, probes, (capturefiles[0] if capturefiles else "logic-1")


def load(path: str | Path, options: dict | None = None) -> Capture:
    p = Path(path)
    try:
        with zipfile.ZipFile(p) as z:
            members = z.namelist()
            metadata_name = next((n for n in members if n.split("/")[-1] == "metadata"), None)
            if metadata_name is None:
                raise IngestError(f"{p.name} 不是带 metadata 的 Sigrok .sr")
            rate, unitsize, probes, capturefile = _metadata(
                z.read(metadata_name).decode("utf-8", "replace")
            )
            logic_names = [
                n for n in members
                if n.split("/")[-1] == capturefile
                or n.split("/")[-1].startswith(capturefile + "-")
            ]
            if not logic_names:
                raise IngestError(f"{p.name} 缺少 logic-* 数据成员")
            def chunk_key(name: str):
                base = name.split("/")[-1]
                suffix = base[len(capturefile):].lstrip("-")
                return tuple(int(x) for x in suffix.split("-") if x.isdigit())
            raw = b"".join(z.read(n) for n in sorted(logic_names, key=chunk_key))
    except zipfile.BadZipFile as exc:
        raise IngestError(f"{p.name} 不是有效 Sigrok ZIP") from exc
    if len(raw) % unitsize:
        raise IngestError(f"{p.name} logic 数据长度不是 unitsize={unitsize} 的整数倍")
    n = len(raw) // unitsize
    if n == 0:
        raise IngestError(f"{p.name} logic 数据为空")
    samples = np.frombuffer(raw, dtype=np.uint8).reshape(-1, unitsize)
    if unitsize > 1:
        vals = np.zeros(n, dtype=np.uint32)
        for i in range(unitsize):
            vals |= samples[:, i].astype(np.uint32) << (8 * i)
    else:
        vals = samples[:, 0].astype(np.uint32)
    if not probes:
        probes = {i: f"Channel {i}" for i in range(max(1, unitsize * 8))}
    idxs = sorted(probes)
    channels = tuple(probes[i] for i in idxs)
    initial = sum(int((int(vals[0]) >> source_bit) & 1) << out_bit
                  for out_bit, source_bit in enumerate(idxs))
    edge_t: list[float] = []
    edge_lv: list[int] = []
    prev = initial
    for i in range(1, n):
        snap = sum(int((int(vals[i]) >> source_bit) & 1) << out_bit
                   for out_bit, source_bit in enumerate(idxs))
        if snap != prev:
            edge_t.append(i / rate)
            edge_lv.append(snap)
            prev = snap
    return Capture(
        meta=CaptureMeta(source_kind="sigrok", format_key="sigrok_sr", device="sigrok",
                          source_files=[str(p)], sample_rate=rate,
                          extra={"unitsize": unitsize, "probes": dict(probes)}),
        digital=DigitalWave(channels=channels, initial=initial, t_start=0.0,
                            edges_t=np.asarray(edge_t), edges_levels=np.asarray(edge_lv),
                            t_end=n / rate, sample_rate=rate, n_samples=n),
    )


def _sniff(ctx: SniffCtx) -> bool:
    return ctx.name.endswith(".sr") and ctx.head.startswith(b"PK")


SPEC = AdapterSpec(
    key="sigrok_sr",
    description="Sigrok .sr 逻辑会话（metadata、采样率、探针和多段 logic 数据）",
    load=load,
    sniff=_sniff,
    sniff_hint=".sr ZIP + metadata",
)
