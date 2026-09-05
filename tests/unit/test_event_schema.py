from __future__ import annotations

from dataclasses import dataclass
import importlib
import importlib.util

import pytest

from decodehub.decode.events import DecodeReport, DecodedEvent, UartEvent
from decodehub.render.format import report_csv_rows


def _schema():
    assert importlib.util.find_spec("decodehub.decode.schema") is not None
    return importlib.import_module("decodehub.decode.schema")


def test_report_and_events_publish_schema_version() -> None:
    event = UartEvent("uart.frame", 0.0, 1.0, "A", value=65)
    report = DecodeReport("uart", {}, [event])

    assert event.to_dict()["schema_version"] == "1.0"
    assert report.to_json()["schema_version"] == "1.0"
    assert report_csv_rows(report).splitlines()[0].endswith(",schema_version")
    assert report_csv_rows(report).splitlines()[1].endswith(",1.0")


def test_schema_rejects_unknown_kind_and_error_code() -> None:
    with pytest.raises(ValueError, match="kind"):
        DecodedEvent("typo.kind", 0.0, 1.0, "x").to_dict()
    with pytest.raises(ValueError, match="错误码"):
        DecodedEvent("uart.frame", 0.0, 1.0, "x", errors=["typo"]).to_dict()


def test_event_schema_version_is_keyword_only() -> None:
    event = UartEvent("uart.frame", 0.0, 1.0, "A", [], "data", 65, "E", 7)
    assert event.value == 65
    assert event.parity == "E"
    assert event.schema_version == "1.0"


def test_schema_rejects_unregistered_subclass_field() -> None:
    @dataclass
    class BrokenUartEvent(UartEvent):
        typo_field: int = 1

    with pytest.raises(ValueError, match="字段未注册"):
        BrokenUartEvent("uart.frame", 0.0, 1.0, "x").to_dict()


def test_builtin_kind_and_field_registries_are_complete() -> None:
    schema = _schema()
    assert {"uart.frame", "i2c.transfer", "spi.word", "i3c.daa", "fields.split"} <= schema.known_kinds()
    assert {"value", "parity", "data_bits"} <= schema.known_event_fields("uart")
    assert {"spec", "source_kind", "fields"} <= schema.known_event_fields("fields")
