from __future__ import annotations

import importlib
import importlib.util


def _capabilities():
    assert importlib.util.find_spec("decodehub.decode.capabilities") is not None
    return importlib.import_module("decodehub.decode.capabilities")


def test_runtime_matrix_matches_live_registries() -> None:
    capabilities = _capabilities()
    from decodehub.acquisition.adapters import SUPPORTED_FORMATS

    matrix = capabilities.capability_matrix()
    assert {item["protocol"] for item in matrix["protocols"]} == {
        "uart", "i2c", "spi", "uplink", "downlink"
    }
    assert set(matrix["formats"]) == set(SUPPORTED_FORMATS)
    assert all(item["node_type"] and item["presentation"] for item in matrix["protocols"])
    assert all(item["schema_version"] == "1.0" for item in matrix["protocols"])


def test_runtime_formats_include_adapter_options() -> None:
    matrix = _capabilities().capability_matrix()
    assert matrix["formats"]["kingst_bin"]["options"]["sample_rate"]["required"] is True
    assert matrix["formats"]["sigrok_sr"]["options"] == {}


def test_mcp_lock_protocol_enum_is_runtime_derived() -> None:
    capabilities = _capabilities()
    from decodehub.mcp_server.tools import TOOLS

    lock = next(tool for tool in TOOLS if tool.name == "lock_protocol")
    assert lock.schema["properties"]["protocol"]["enum"] == [
        item["protocol"] for item in capabilities.protocol_capabilities()
    ]
