# Downstream integration test summary

## Scope

This branch preserves upstream DecodedHub v0.2.0 and layers the reusable
decodehub features in dependency order: licensed waveform assets, KingstVIS
KVDAT compatibility, Sigrok ingestion, channel-binding contracts, event schema,
protocol plugins, runtime capabilities, I3C, and AVSBus.

Base upstream revision: `92267967c5d89babaa2738915312fc7be56baaac`.
Feature implementation revision: `61b2b6bd4c03139e9690388836ed7fa6194d790f`.

## Commit-by-commit evidence

| Commit | Integration slice | Targeted verification recorded before push |
|---|---|---|
| `491840a` | Redistributable external waveform baseline and manifest | Manifest/integrity tests; full suite 381 passed, 2 optional KVDAT skips |
| `bb4af54` | Real KingstVIS 3.6.x KVDAT metadata and saved SPI defaults | 53 targeted tests; full suite 393 passed, 2 skipped; real capture MOSI `0x5A`, MISO `0xA5` |
| `c6e8328` | Native Sigrok `.sr` adapter and interpreter-safe stdio smoke | 35 targeted tests; full suite 400 passed, 2 skipped; process MCP smoke passed |
| `c191af9` | Alternative roles and robust channel matching | 44 targeted tests; full suite 404 passed, 2 skipped; process MCP smoke passed |
| `e51c3d4` | Event/report schema version 1.0 and export validation | 77 targeted tests, 1 optional skip; full suite 410 passed, 2 skipped; smoke passed |
| `89e50f2` | Versioned `decodehub.protocols` plugin discovery | 28 targeted tests; full suite 414 passed, 2 skipped; smoke passed |
| `cef830f` | Registry-derived runtime capability matrix | 35 targeted tests; full suite 417 passed, 2 skipped; smoke passed |
| `9274c61` | Passive I3C Basic SDR, CCC/DAA, legacy I2C and HDR honesty | 49 targeted tests; full suite 440 passed, 2 skipped; external CSV/SR and smoke passed |
| `61b2b6b` | Passive AVSBus controller/target decoder and CRC-3 | 35 targeted tests; full suite 448 passed, 2 skipped; process MCP smoke passed |

The targeted rows above used this common prefix:
`PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest`.
The exact test selections were:

| Commit | Arguments after the common prefix |
|---|---|
| `491840a` | `tests/unit/test_external_asset_manifest.py -q` |
| `bb4af54` | `tests/unit/test_kingst_kvdat_real.py tests/unit/test_kingst_saved_protocol.py -q` |
| `c6e8328` | `tests/unit/test_sigrok_external_waveforms.py tests/unit/test_adapter_registry.py -q` |
| `c191af9` | `tests/unit/test_param_routing.py tests/property/test_i2c_spi_roundtrip.py -q` |
| `e51c3d4` | `tests/unit/test_event_schema.py tests/unit/test_fields.py tests/property/test_payload_fields.py -q` |
| `89e50f2` | `tests/unit/test_plugin_contracts.py tests/unit/test_presentation.py tests/unit/test_pipelines.py -q` |
| `cef830f` | `tests/unit/test_runtime_capabilities.py tests/mcp/test_progressive_disclosure.py tests/unit/test_adapter_registry.py -q` |
| `9274c61` | `tests/unit/test_i3c_decoder.py tests/property/test_i3c_roundtrip.py tests/mcp/test_i3c.py tests/unit/test_sigrok_external_waveforms.py -q` |
| `61b2b6b` | `tests/unit/test_avsbus_decoder.py tests/unit/test_sigrok_external_waveforms.py -q` |

The two skips are deliberate local-only KVDAT checks. Their source pages and
hashes are documented in [`test-assets.md`](test-assets.md); no redistribution
license was found, so the files are not committed.

## Final branch gate

Run from the repository root with the integration environment:

```bash
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest tests
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python scripts/stdio_smoke.py
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m decodehub.cli.main --version
git diff --check
```

Final observed result on 2026-09-05: **448 passed, 2 skipped**; stdio MCP smoke
printed `STDIO SMOKE PASSED`; the CLI printed `decodehub 0.2.0`; whitespace
validation passed. `git fetch upstream` found upstream `main` still at the base
revision above, and `git merge-tree` reported no conflict markers.
