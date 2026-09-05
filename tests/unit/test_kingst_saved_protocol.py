from __future__ import annotations

from pathlib import Path

from decodehub.app import services
from decodehub.app.session import SessionState

from test_kingst_kvdat_real import _real_fixture, _settings, _write_kvdat


def test_saved_spi_defaults_flow_through_named_lock(tmp_path: Path) -> None:
    path = _write_kvdat(
        tmp_path,
        n_samples=20,
        xml=_settings(),
        blocks=[
            (1, 0, [(position, 0) for position in range(1, 17)] + [(20, 0)]),
            (3, 1, [(position, 0) for position in (2, 4, 6, 10, 12, 14, 20)]),
        ],
    )
    state = SessionState()

    services.ingest(state, str(path), "kingst_kvdat", None)
    services.lock_protocol(state, "spi", {}, source=None)
    services.run_decode(state, None, source=None)

    lock = state.locks[f"{state.single_alias()}|spi"]
    assert lock.channel_map == {"clk": "CLK", "mosi": "MOSI"}
    words = [event for report in state.reports.values() for event in report.events
             if event.kind == "spi.word" and not event.errors]
    assert [(word.mosi, word.miso) for word in words] == [(0xA5, None)]


def test_real_capture_decodes_saved_spi_without_manual_params() -> None:
    state = SessionState()
    services.ingest(state, str(_real_fixture("spi_bootloader_good.kvdat")), None, None)

    services.lock_protocol(state, "spi", {}, source=None)
    services.run_decode(state, None, source=None)

    lock = next(iter(state.locks.values()))
    assert lock.channel_map == {
        "clk": "SPI 0 SCK",
        "mosi": "SPI 0 MOSI",
        "miso": "SPI 0 MISO",
        "cs": "SPI 0 NSS",
    }
    report = next(iter(state.reports.values()))
    words = [event for event in report.events if event.kind == "spi.word" and not event.errors]
    assert (words[0].mosi, words[0].miso) == (0x5A, 0xA5)


def test_explicit_spi_parameters_override_saved_defaults() -> None:
    state = SessionState()
    services.ingest(state, str(_real_fixture("spi_bootloader_good.kvdat")), None, None)

    services.lock_protocol(
        state,
        "spi",
        {"cpha": 1, "word_bits": 16, "cs": "GPIO 22 BOOT0"},
        source=None,
    )

    lock = next(iter(state.locks.values()))
    assert lock.params["cpol"] == 0
    assert lock.params["cpha"] == 1
    assert lock.params["word_bits"] == 16
    assert lock.channel_map["cs"] == "GPIO 22 BOOT0"
