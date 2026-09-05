from __future__ import annotations

import struct
from pathlib import Path

import pytest

from decodehub.acquisition.adapters.kingst_kvdat import load
from decodehub.shared.errors import IngestError


MAGIC = b"kvdat\x00\x00\x00"
CHANNEL = struct.Struct("<IBBHQ")
RECORD = struct.Struct("<IB")


def _write_kvdat(
    tmp_path: Path,
    *,
    blocks: list[tuple[int, int, list[tuple[int, int]]]],
    capacity: int = 16,
    n_samples: int = 8,
    sample_rate: int = 10,
    xml: str = "<settings />\n",
) -> Path:
    data = bytearray(xml.encode() + MAGIC)
    data += struct.pack("<QQQQ", n_samples, sample_rate, 0, capacity)
    for channel, initial, records in blocks:
        data += CHANNEL.pack(0x00442323, channel, initial, 0, len(records))
        for position, flag in records:
            data += RECORD.pack(position, flag)
    path = tmp_path / "capture.kvdat"
    path.write_bytes(data)
    return path


def _settings() -> str:
    return """<settings><global>
      <version>3.6.2</version>
      <chnShowName1>CLK</chnShowName1>
      <chnShowName3>MOSI</chnShowName3>
    </global><devices><LA2016><chnEnable>0,1,0,1</chnEnable></LA2016></devices>
    <analyzers><item0><fileName>libSPI.dylib</fileName>
      <parameters>SpiAnalyzer,3,0,18446744073709551615,0,1,0,18446744073709551615,0,0,8,0,0,0,1,</parameters>
      <format>2</format>
    </item0></analyzers></settings>"""


def _real_fixture(filename: str) -> Path:
    root = Path(__file__).resolve().parents[3]
    candidates = (
        root / "decodehub-code-e127559" / "tests" / "data" / "external" / filename,
        root / "third_part" / "tests" / "data" / "external" / filename,
    )
    for path in candidates:
        if path.is_file():
            return path
    pytest.skip(f"optional unredistributed KVDAT fixture absent: {filename}")


def test_sparse_physical_channels_restore_xml_and_saved_spi(tmp_path: Path) -> None:
    path = _write_kvdat(
        tmp_path,
        xml=_settings(),
        blocks=[(1, 0, [(2, 0), (8, 0)]), (3, 1, [(4, 0), (8, 0)])],
    )

    capture = load(path)

    assert capture.digital.channels == ("CLK", "MOSI")
    assert capture.digital.initial == 0b10
    assert capture.digital.edges_levels.tolist() == [0b11, 0b01]
    assert capture.meta.device == "LA2016"
    assert capture.meta.extra["physical_channels"] == [1, 3]
    assert capture.meta.extra["protocol_defaults"]["spi"] == {
        "clk": "CLK",
        "mosi": "MOSI",
        "miso": None,
        "cs": None,
        "bit_order": "msb",
        "word_bits": 8,
        "cpol": 0,
        "cpha": 0,
        "cs_active": "low",
    }


@pytest.mark.parametrize(
    ("blocks", "message"),
    [
        ([(0, 0, [(9, 0)])], "超出 n_samples"),
        ([(0, 0, [(8, 1)])], "记录标志"),
        ([(0, 0, [(8, 0)]), (0, 0, [(8, 0)])], "重复"),
        ([(16, 0, [(8, 0)])], "超出设备容量"),
        ([(0, 2, [(8, 0)])], "初始电平"),
    ],
)
def test_rejects_corrupt_channel_metadata(
    tmp_path: Path,
    blocks: list[tuple[int, int, list[tuple[int, int]]]],
    message: str,
) -> None:
    with pytest.raises(IngestError, match=message):
        load(_write_kvdat(tmp_path, blocks=blocks))


@pytest.mark.parametrize(
    ("filename", "samples"),
    [
        ("spi_bootloader_good.kvdat", 199_920),
        ("spi_bootloader_bad.kvdat", 1_865_532),
        ("spi_bootloader_with_init.kvdat", 5_000_000),
    ],
)
def test_loads_real_kingstvis_36_capture(filename: str, samples: int) -> None:
    capture = load(_real_fixture(filename))

    assert capture.digital.n_samples == samples
    assert capture.digital.channels == (
        "SPI 0 NSS",
        "SPI 0 SCK",
        "SPI 0 MISO",
        "SPI 0 MOSI",
        "GPIO 22 BOOT0",
        "GPIO 27 NRST",
    )
    assert capture.meta.extra["channel_capacity"] == 16
    assert capture.meta.extra["physical_channels"] == [0, 1, 2, 3, 4, 5]
    assert capture.meta.extra["kingst"]["version"] == "3.6.2"
