from __future__ import annotations

import importlib
import importlib.util

import pytest


def _module(name: str):
    assert importlib.util.find_spec(name) is not None
    return importlib.import_module(name)


def test_all_existing_protocols_load_through_descriptors() -> None:
    plugins = _module("decodehub.decode.plugins")
    assert [item.protocol for item in plugins.load_plugins()] == [
        "uart", "i2c", "spi", "uplink", "downlink", "i3c"
    ]


def test_plugin_api_version_mismatch_is_rejected() -> None:
    plugins = _module("decodehub.decode.plugins")
    bad = plugins.PluginDescriptor(
        "bad", "decodehub.decode.protocols.uart", "uart_decode", version="2"
    )
    with pytest.raises(ValueError, match="版本"):
        plugins.load_plugins(extra=[bad])


def test_builtin_plugins_register_complete_runtime_contracts() -> None:
    contracts = _module("decodehub.decode.contracts")
    plugins = _module("decodehub.decode.plugins")
    from decodehub.decode.bindings import all_bindings
    from decodehub.decode.presentation import all_presentations
    from decodehub.decode.registry import get_registry

    bindings = {item.protocol: item for item in all_bindings()}
    presentations = {item.protocol: item for item in all_presentations()}
    for descriptor in plugins.load_plugins():
        binding = bindings[descriptor.protocol]
        assert binding.node_type == descriptor.node_type
        contracts.validate_node_contract(get_registry()[descriptor.node_type])
        contracts.validate_presenter_contract(presentations[descriptor.protocol])


def test_adapter_contract_accepts_adapter_spec() -> None:
    contracts = _module("decodehub.decode.contracts")
    from decodehub.acquisition.adapters import SPECS

    for spec in SPECS.values():
        contracts.validate_adapter_contract(spec)
