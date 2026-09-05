from __future__ import annotations

from pathlib import Path
import zipfile

import pytest

from decodehub.acquisition.service import load_capture
from decodehub.decode.protocols.i2c.decode import I2cDecodeNode
from decodehub.decode.protocols.i3c.decode import I3cDecodeNode
from decodehub.decode.protocols.spi.decode import SpiDecodeNode
from decodehub.decode.protocols.uart.decode import UartDecodeNode
from decodehub.shared.errors import IngestError


ROOT = Path(__file__).resolve().parents[1] / "data" / "external"


def _params(node_type, **overrides):
    params = {name: field.default for name, field in node_type.PARAMS.items()}
    params.update(overrides)
    return params


def test_sigrok_uart_fixture_decodes_hello() -> None:
    capture = load_capture(ROOT / "uart" / "hello_world_8n1_115200.sr")

    assert capture.meta.format_key == "sigrok_sr"
    assert capture.digital.channels == ("TX",)
    events = UartDecodeNode().run(
        {"in": capture.digital}, _params(UartDecodeNode, rx="TX", baud=115200)
    )["out"]
    values = [event.value for event in events
              if event.kind == "uart.frame" and not event.errors]
    assert bytes(values).startswith(b"Hello")


def test_sigrok_i2c_fixture_decodes_transfer() -> None:
    capture = load_capture(ROOT / "i2c" / "rtc_ds1307_200khz.sr")

    assert capture.digital.channels == ("SCL", "SDA")
    events = I2cDecodeNode().run(
        {"in": capture.digital}, _params(I2cDecodeNode, scl="SCL", sda="SDA")
    )["out"]
    assert any(event.kind == "i2c.start" for event in events)
    assert any(event.kind == "i2c.transfer" for event in events)


def test_sigrok_spi_fixture_decodes_5a() -> None:
    capture = load_capture(ROOT / "spi" / "spi_0x5a_cpol0_cpha0.sr")

    events = SpiDecodeNode().run(
        {"in": capture.digital},
        _params(SpiDecodeNode, clk="CLK", mosi="MOSI", miso="MISO", cs="CS#"),
    )["out"]
    assert 0x5A in [event.mosi for event in events if event.kind == "spi.word"]


def test_sigrok_multichunk_session_is_concatenated() -> None:
    capture = load_capture(ROOT / "spi" / "max7219.sr")

    assert capture.digital.n_samples == 5_000_000
    assert capture.digital.channels == ("MISO", "CS#", "MOSI", "CLK")


def test_sigrok_i3c_example_reaches_daa_and_hdr_boundaries() -> None:
    capture = load_capture(ROOT / "i3c" / "ExampleWaveform.sr")

    events = I3cDecodeNode().run(
        {"in": capture.digital},
        _params(I3cDecodeNode, scl="scl", sda="sda", mode="auto"),
    )["out"]
    assert any(event.kind == "i3c.daa" for event in events)
    assert any(event.kind == "i3c.unsupported" and "hdr" in event.errors
               for event in events)


def test_sigrok_empty_logic_is_an_ingest_error(tmp_path: Path) -> None:
    metadata = """[global]\nsigrok version=0.4\n[device 1]\ncapturefile=logic-1\nunitsize=1\nsamplerate=1 MHz\nprobe1=TX\n"""
    path = tmp_path / "empty.sr"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("version", "1")
        archive.writestr("metadata", metadata)
        archive.writestr("logic-1", b"")

    with pytest.raises(IngestError, match="空"):
        load_capture(path)


def test_sigrok_rejects_multiple_devices(tmp_path: Path) -> None:
    metadata = """[global]\nsigrok version=0.4\n[device 1]\ncapturefile=logic-1\nunitsize=1\nsamplerate=1 MHz\nprobe1=TX\n[device 2]\ncapturefile=logic-2\n"""
    path = tmp_path / "multiple.sr"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("version", "1")
        archive.writestr("metadata", metadata)
        archive.writestr("logic-1", b"\x01")
        archive.writestr("logic-2", b"\x01")

    with pytest.raises(IngestError, match="多个 device"):
        load_capture(path)
