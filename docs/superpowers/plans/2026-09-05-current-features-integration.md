# Current DecodeHub Features Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port real KingstVIS KVDAT support, Sigrok sessions, runtime plugin/contracts/capabilities, I3C SDR and AVSBus from the local DecodeHub into the upstream-tracking v0.2.0 repository while preserving all v0.2.0 features.

**Architecture:** `third_part` remains authoritative. Platform features are adapted to its `AdapterSpec`, payload field, pipeline, named-lock, render-route and incremental-run architecture. Protocols register through a versioned plugin descriptor and continue to consume the common `DigitalWave`/`DecodedEvent` contracts.

**Tech Stack:** Python 3.11+, NumPy, SciPy, matplotlib, MCP 1.29.x, pytest, Git/GitHub.

## Global Constraints

- Local `main` tracks `upstream/main`; all work lands on `integration/kingst-features`.
- Published integration commits are never rebased or force-pushed.
- Each task ends with targeted tests, the full suite, a detailed commit and an immediate push.
- Preserve v0.2.0 payload fields, pipelines, named locks, render routes, incremental runs and output naming.
- Do not replace `services.py`, `runner.py`, `config.py`, the adapter registry or render layer wholesale.
- Explicit user parameters override saved capture defaults.
- Test assets include provenance, redistribution notes, SHA-256, channel maps and expected results.
- Python source remains compatible with the project requirement `requires-python = ">=3.11"`.

---

### Task 1: External Assets and Baseline

**Files:**
- Create: `docs/test-assets.md`
- Create: `tests/data/external/README.md`
- Create: `tests/data/external/{uart,i2c,spi,i3c,avsbus}/`
- Create: `tests/unit/test_external_asset_manifest.py`

**Interfaces:**
- Consumes: downloaded assets in `../decodehub-code-e127559/tests/data/external/`
- Produces: canonical asset tree consumed by all later adapter/protocol tests

- [ ] **Step 1: Copy the original assets without modifying bytes**

```bash
mkdir -p tests/data/external/{uart,i2c,spi,i3c,avsbus}
cp ../decodehub-code-e127559/tests/data/external/uart/hello_world_8n1_115200.sr tests/data/external/uart/
cp ../decodehub-code-e127559/tests/data/external/i2c/rtc_ds1307_200khz.sr tests/data/external/i2c/
cp ../decodehub-code-e127559/tests/data/external/spi/*.sr tests/data/external/spi/
cp ../decodehub-code-e127559/tests/data/external/i3c/* tests/data/external/i3c/
cp ../decodehub-code-e127559/tests/data/external/avsbus/avsbus_smoke.csv tests/data/external/avsbus/
```

- [ ] **Step 2: Write the manifest integrity test**

```python
from pathlib import Path
import hashlib

ROOT = Path(__file__).parents[1] / "data" / "external"

EXPECTED = {
    "uart/hello_world_8n1_115200.sr": "e72b96efdb30ef33e1a7a2a4e1bd814cd47d5474b67a1d5adb68d4c5ad47ab24",
    "i2c/rtc_ds1307_200khz.sr": "963b2d1d34a7a1dedbbe04c59b70de305d4c19f8ae9789a8029ce2495795b96d",
    "spi/spi_0x5a_cpol0_cpha0.sr": "bf27e3dcf9dec7455aa28aa2766fdc7edfdc9c07014e9ad6d134f0229a6a5dff",
    "spi/max7219.sr": "fb740e70746d268ee697d86eaaa2502727c4d5abdfaeb6a5f7dd0df37fb26325",
    "i3c/ExampleWaveform.sr": "2429e287671243a39ae26b12aff062b3c82944d0527a4830b5c40db72c3f7ca9",
    "i3c/ExampleWaveform.csv": "42c2ec35d7da641467f941c66f3e20362e53e8fd0f3103655b29e880233332ca",
    "i3c/i3c_sdr_smoke.csv": "1dd3e28520b3652c47fb41a56f69d56a94c16453863a6a800babb3712aefca6b",
    "avsbus/avsbus_smoke.csv": "4b7e66f9fbcf39bbae589595c6ec7e3e791a6af724f1906e2e0adc9e90cf819c",
}

def test_external_assets_match_manifest():
    for relative, expected in EXPECTED.items():
        blob = (ROOT / relative).read_bytes()
        assert hashlib.sha256(blob).hexdigest() == expected
```

These digests were calculated from the immutable source files before the plan
was committed. The manifest test guarantees byte-for-byte preservation. The
three real KVDAT forum attachments have no stated redistribution license, so
their URLs and hashes are documented but their tests consume the local copies
from the sibling repository and skip when those files are unavailable.

- [ ] **Step 3: Document provenance and expected decodes**

Write `docs/test-assets.md` with one table row per file containing source URL,
license note, SHA-256, channels, decoder parameters and expected result. Copy the
per-protocol provenance details from the local repository READMEs, preserving
their URLs and distinguishing downloaded from generated assets.

- [ ] **Step 4: Verify the unmodified v0.2.0 baseline**

Run:

```bash
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest tests
```

Expected: `380 passed, 2 skipped` before adding the new manifest test, then
`381 passed, 2 skipped` after it. The KVDAT files are not part of this count.

- [ ] **Step 5: Commit and push**

```bash
git add docs/test-assets.md tests/data/external tests/unit/test_external_asset_manifest.py
git commit -m "test(assets): establish external waveform regression baseline" \
  -m "Why: preserve downloaded protocol captures as reproducible regression inputs." \
  -m "What: add provenance, checksums, channel maps and expected decode results." \
  -m "Tests: full pytest baseline and asset SHA-256 verification."
git push origin integration/kingst-features
```

### Task 2: Real KingstVIS KVDAT 3.6.x

**Files:**
- Modify: `src/decodehub/acquisition/adapters/kingst_kvdat.py`
- Modify: `src/decodehub/app/services.py`
- Create: `tests/unit/test_kingst_kvdat_real.py`
- Create: `tests/unit/test_kingst_saved_protocol.py`

**Interfaces:**
- Consumes: `AdapterSpec`, `Capture`, `DigitalWave`, `ProtocolBinding`
- Produces: `load(path, options) -> Capture` with `meta.extra["protocol_defaults"]`

- [ ] **Step 1: Add failing real-capture tests**

Copy the corruption, metadata and real-capture cases from
`../decodehub-code-e127559/tests/unit/test_kingst_kvdat.py` into
`tests/unit/test_kingst_kvdat_real.py`. Change fixture paths to
`tests/data/external/spi/`. Add this application assertion:

```python
def test_real_capture_uses_saved_spi_defaults(external_dir):
    state = SessionState()
    services.ingest(state, str(external_dir / "spi" / "spi_bootloader_good.kvdat"), None, None)
    services.lock_protocol(state, "spi", {}, source=None)
    services.run_decode(state, {}, source=None)
    report = next(iter(state.reports.values()))
    words = [(e.mosi, e.miso) for e in report.events if e.kind == "spi.word" and not e.errors]
    assert words[0] == (0x5A, 0xA5)
```

- [ ] **Step 2: Verify tests fail on the old parser**

```bash
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest tests/unit/test_kingst_kvdat_real.py -q
```

Expected: failures include structural read-past-end and missing XML metadata.

- [ ] **Step 3: Port the parser and retain AdapterSpec**

Use the implementation body from
`../decodehub-code-e127559/src/decodehub/acquisition/adapters/kingst_kvdat.py`.
Retain the v0.2.0 adapter declaration at the bottom:

```python
def _sniff(ctx) -> bool:
    return ctx.head.find(_MAGIC) >= 0

SPEC = AdapterSpec(
    key="kingst_kvdat",
    description="KingstVIS .kvdat 原始采集（含通道名和已保存 SPI 设置）",
    load=load,
    sniff=_sniff,
)
```

- [ ] **Step 4: Merge saved defaults in named-lock services**

Immediately after resolving `cap`, `alias` and `params`, add:

```python
defaults = cap.meta.extra.get("protocol_defaults", {})
saved = defaults.get(protocol, {}) if isinstance(defaults, dict) else {}
saved = saved if isinstance(saved, dict) else {}
params = {**saved, **params}
```

Keep the existing named-lock collision checks, derived parameter routing and
pipeline behavior unchanged.

- [ ] **Step 5: Run targeted and full tests**

```bash
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest tests/unit/test_kingst_kvdat_real.py tests/unit/test_kingst_saved_protocol.py -q
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest tests
git diff --check
```

Expected: all three KVDAT assets ingest; the known-good first SPI word is
`0x5A/0xA5`; full suite green.

- [ ] **Step 6: Commit and push**

```bash
git add src/decodehub/acquisition/adapters/kingst_kvdat.py src/decodehub/app/services.py tests/unit/test_kingst_kvdat_real.py tests/unit/test_kingst_saved_protocol.py
git commit -m "feat(kingst): ingest real kvdat captures and saved spi settings" \
  -m "Parse KingstVIS 3.6.x metadata, sparse physical channels and saved SPI analyzer settings." \
  -m "Preserve AdapterSpec registration, named locks, pipelines and explicit parameter precedence." \
  -m "Verify corrupt-file diagnostics, three real captures, saved SPI defaults and the full pytest suite."
git push origin integration/kingst-features
```

### Task 3: Native Sigrok Session Adapter

**Files:**
- Create: `src/decodehub/acquisition/adapters/sigrok_sr.py`
- Modify: `src/decodehub/acquisition/adapters/__init__.py`
- Create: `tests/unit/test_sigrok_external_waveforms.py`

**Interfaces:**
- Consumes: `AdapterSpec`, `SniffCtx`, ZIP `metadata` and `logic-*`
- Produces: `sigrok_sr` capture with probe names and sample rate

- [ ] **Step 1: Add failing adapter/decoder tests**

Copy `../decodehub-code-e127559/tests/unit/test_sigrok_external_waveforms.py`.
Point `ROOT` to `tests/data/external`, and retain UART/I2C/SPI cases while
temporarily removing I3C/AVSBus imports until their tasks land.

- [ ] **Step 2: Verify the missing format failure**

```bash
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest tests/unit/test_sigrok_external_waveforms.py -q
```

Expected: FAIL because `sigrok_sr` is not registered.

- [ ] **Step 3: Add the adapter**

Copy the parser from
`../decodehub-code-e127559/src/decodehub/acquisition/adapters/sigrok_sr.py`, then
declare its v0.2.0 registration:

```python
def _sniff(ctx: SniffCtx) -> bool:
    return ctx.suffix == ".sr" and ctx.is_zip

SPEC = AdapterSpec(
    key="sigrok_sr",
    description="Sigrok .sr 逻辑会话（metadata、采样率、探针和 logic 数据）",
    load=load,
    sniff=_sniff,
)
```

Import `SPEC as _sigrok_sr` and insert it before generic CSV in the ordered
`SPECS` tuple.

- [ ] **Step 4: Run tests and commit**

```bash
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest tests/unit/test_sigrok_external_waveforms.py tests/unit/test_adapter_registry.py -q
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest tests
git diff --check
git add src/decodehub/acquisition/adapters tests/unit/test_sigrok_external_waveforms.py
git commit -m "feat(acquisition): add native sigrok session adapter" \
  -m "Read Sigrok ZIP metadata, sample rate, probes and logic streams directly into DigitalWave." \
  -m "Register before generic CSV sniffing without changing existing adapter behavior." \
  -m "Decode the external UART, I2C and SPI sessions and run the full pytest suite."
git push origin integration/kingst-features
```

### Task 4: Binding Alternative Roles

**Files:**
- Modify: `src/decodehub/decode/bindings.py`
- Modify: `src/decodehub/decode/protocols/spi/binding.py`
- Modify: `tests/unit/test_param_routing.py`

**Interfaces:**
- Produces: `ProtocolBinding.require_any: tuple[tuple[str, ...], ...]`
- Preserves: named locks and `node_routed_params()`

- [ ] **Step 1: Add MOSI-only, MISO-only and tokenized-name tests**

```python
def test_spi_accepts_miso_only_and_prefixed_names():
    binding = get_binding("spi")
    assert auto_map_channels(["la:SPI 0 SCK", "la:SPI 0 MISO"], binding, {}) == {
        "clk": "la:SPI 0 SCK", "miso": "la:SPI 0 MISO"
    }

def test_spi_rejects_clock_without_data():
    with pytest.raises(ProtocolLockError, match="至少需要其一"):
        auto_map_channels(["CLK"], get_binding("spi"), {})
```

- [ ] **Step 2: Implement the declarative constraint**

Add to `ProtocolBinding`:

```python
require_any: tuple[tuple[str, ...], ...] = ()
```

Port `_ROLE_TOKEN`, explicit-first mapping and missing-group validation from the
local `bindings.py`. Set SPI to:

```python
optional_roles=("mosi", "miso", "cs"),
require_any=(("mosi", "miso"),),
```

- [ ] **Step 3: Test, commit and push**

```bash
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest tests/unit/test_param_routing.py tests/property/test_i2c_spi_roundtrip.py -q
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest tests
git diff --check
git add src/decodehub/decode/bindings.py src/decodehub/decode/protocols/spi/binding.py tests/unit/test_param_routing.py
git commit -m "feat(bindings): support alternative roles and robust channel matching" \
  -m "Declare require-any role groups and match tokenized analyzer channel names." \
  -m "Allow MOSI-only or MISO-only SPI while preserving named locks and routed parameters." \
  -m "Verify the SPI role matrix, compatibility cases and the full pytest suite."
git push origin integration/kingst-features
```

### Task 5: Versioned Event Publication Schema

**Files:**
- Create: `src/decodehub/decode/schema.py`
- Modify: `src/decodehub/decode/events.py`
- Modify: `src/decodehub/decode/presentation.py`
- Modify: each existing protocol `present.py` and error registration site
- Create: `tests/unit/test_event_schema.py`

**Interfaces:**
- Produces: `register_kinds`, `register_event_fields`, `register_error_codes`, `validate_event`
- Produces: `DecodedEvent.schema_version`, `DecodeReport.schema_version`
- Preserves: `decode.fields.register_fields()` for payload specs

- [ ] **Step 1: Add serialization contract tests**

```python
def test_report_and_events_publish_schema_version():
    event = UartEvent("uart.frame", 0.0, 1.0, "A", value=65)
    report = DecodeReport("uart", {}, [event])
    assert event.to_dict()["schema_version"] == "1.0"
    assert report.to_json()["schema_version"] == "1.0"

def test_unknown_kind_and_error_are_rejected():
    with pytest.raises(ValueError, match="kind"):
        DecodedEvent("typo.kind", 0.0, 1.0, "x").to_dict()
    with pytest.raises(ValueError, match="错误码"):
        DecodedEvent("uart.frame", 0.0, 1.0, "x", errors=["typo"]).to_dict()
```

- [ ] **Step 2: Add a namespace-safe schema registry**

Port local `schema.py`, renaming its event-field function to avoid collision:

```python
def register_event_fields(protocol: str, names: Iterable[str]) -> None: ...
def known_event_fields(protocol: str | None = None): ...
```

Update `presentation.register_presentation()` to call `register_kinds()` and
`register_event_fields()`. Do not change imports or behavior in
`decode/fields.py`.

- [ ] **Step 3: Add keyword-only schema fields**

```python
schema_version: str = field(default=EVENT_SCHEMA_VERSION, kw_only=True)
```

Add the equivalent report field and validate in `to_dict()`/`to_json()` so
existing positional constructors remain compatible.

- [ ] **Step 4: Register every existing protocol/field error code**

Collect literal `errors=[...]` values with:

```bash
rg -n 'errors=|errors\.append|errors\.add' src/decodehub/decode
```

Register the exact finite set for UART/I2C/SPI/uplink/downlink/field events.

- [ ] **Step 5: Test, commit and push**

```bash
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest tests/unit/test_event_schema.py tests/unit/test_fields.py tests/property/test_payload_fields.py -q
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest tests
git diff --check
git add src/decodehub/decode tests/unit/test_event_schema.py
git commit -m "feat(schema): version and validate decoded event contracts" \
  -m "Add protocol kind, field and error registries with schema-version validation." \
  -m "Keep v0.2.0 payload fields available through their existing event namespace." \
  -m "Verify registry validation, every built-in decoder and the full pytest suite."
git push origin integration/kingst-features
```

### Task 6: Protocol Plugin Discovery and Contracts

**Files:**
- Create: `src/decodehub/decode/plugins.py`
- Create: `src/decodehub/decode/contracts.py`
- Modify: `src/decodehub/decode/__init__.py`
- Modify: `src/decodehub/decode/protocols/__init__.py`
- Create: `tests/unit/test_plugin_contracts.py`

**Interfaces:**
- Produces: `PluginDescriptor`, `PLUGIN_API_VERSION`, `BUILTIN_PLUGINS`, `load_plugins()`
- External interface: Python entry points in group `decodehub.protocols`

- [ ] **Step 1: Add plugin loading tests**

```python
def test_all_existing_protocols_load_through_descriptors():
    assert [d.protocol for d in load_plugins()] == [
        "uart", "i2c", "spi", "uplink", "downlink"
    ]

def test_plugin_api_mismatch_fails():
    bad = PluginDescriptor("bad", "decodehub.decode.protocols.uart", "uart_decode", version="2")
    with pytest.raises(ValueError, match="版本"):
        load_plugins(extra=[bad])
```

- [ ] **Step 2: Port and adapt the plugin loader**

Copy local `plugins.py`, defining the v0.2.0 built-ins as:

```python
BUILTIN_PLUGINS = (
    PluginDescriptor("uart", "decodehub.decode.protocols.uart", "uart_decode"),
    PluginDescriptor("i2c", "decodehub.decode.protocols.i2c", "i2c_decode"),
    PluginDescriptor("spi", "decodehub.decode.protocols.spi", "spi_decode"),
    PluginDescriptor("uplink", "decodehub.decode.protocols.uplink", "uplink_decode"),
    PluginDescriptor("downlink", "decodehub.decode.protocols.downlink", "downlink_decode"),
)
```

Port node/presentation contracts. Validate `AdapterSpec` objects instead of
the local repository's raw loader callables.

- [ ] **Step 3: Replace normal static discovery and retain compatibility**

In `decode/__init__.py`, import generic nodes, call `load_plugins()`, then call
`register_fields_presentation()`. Change `protocols/__init__.py` to expose
names via `__all__` without eagerly importing all modules. Direct imports such
as `decodehub.decode.protocols.uart.decode` remain valid.

- [ ] **Step 4: Test, commit and push**

```bash
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest tests/unit/test_plugin_contracts.py tests/unit/test_presentation.py tests/unit/test_pipelines.py -q
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest tests
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python scripts/stdio_smoke.py
git diff --check
git add src/decodehub/decode tests/unit/test_plugin_contracts.py
git commit -m "feat(plugins): add versioned external protocol discovery" \
  -m "Load versioned decodehub.protocols entry points through explicit plugin contracts." \
  -m "Expose all existing built-ins through the same descriptor while preserving registry semantics." \
  -m "Verify malformed-plugin isolation, all five existing protocols, MCP startup and the full suite."
git push origin integration/kingst-features
```

### Task 7: Runtime Capability Matrix

**Files:**
- Create: `src/decodehub/decode/capabilities.py`
- Modify: `src/decodehub/app/services.py`
- Modify: `src/decodehub/mcp_server/tools.py`
- Modify: `src/decodehub/cli/main.py`
- Create: `tests/unit/test_runtime_capabilities.py`

**Interfaces:**
- Produces: `protocol_capabilities() -> list[dict]`
- Produces: `capability_matrix() -> dict`

- [ ] **Step 1: Add the single-source-of-truth test**

```python
def test_runtime_matrix_matches_all_registries():
    matrix = capability_matrix()
    assert {p["protocol"] for p in matrix["protocols"]} == {
        "uart", "i2c", "spi", "uplink", "downlink"
    }
    assert set(matrix["formats"]) == set(SUPPORTED_FORMATS)
    assert all(p["node_type"] and p["presentation"] for p in matrix["protocols"])
```

- [ ] **Step 2: Adapt local capabilities to AdapterSpec and event schema**

Construct format entries from `acquisition.adapters.SPECS`, including option
names, required flags and descriptions. Construct protocol entries from
`all_bindings()`, node `PARAMS`, presentation fields and preview kinds.

- [ ] **Step 3: Remove the MCP hard-coded protocol enum**

Replace the five-element literal with:

```python
_PROTOCOL_NAMES = [item["protocol"] for item in protocol_capabilities()]
_LOCK_PROTOCOL = {"type": "string", "enum": _PROTOCOL_NAMES}
```

Use `_LOCK_PROTOCOL` only for creating new protocol locks; keep `_PRO` as the
free-form report/lock-instance selector so named locks and pipeline names work.

- [ ] **Step 4: Test, commit and push**

```bash
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest tests/unit/test_runtime_capabilities.py tests/mcp/test_progressive_disclosure.py tests/unit/test_adapter_registry.py -q
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest tests
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python scripts/stdio_smoke.py
git diff --check
git add src/decodehub/decode/capabilities.py src/decodehub/app/services.py src/decodehub/mcp_server/tools.py src/decodehub/cli/main.py tests/unit/test_runtime_capabilities.py
git commit -m "feat(capabilities): derive protocol and format matrix at runtime" \
  -m "Derive protocol, format and event-schema capabilities from live registries." \
  -m "Generate MCP protocol validation dynamically while preserving named-lock behavior." \
  -m "Verify capability output, tool validation, startup smoke and the full pytest suite."
git push origin integration/kingst-features
```

### Task 8: I3C SDR Protocol Plugin

**Files:**
- Create: `src/decodehub/decode/protocols/i3c/{__init__.py,decode.py,encode.py,binding.py,present.py,README.md}`
- Modify: `src/decodehub/decode/events.py`
- Modify: `src/decodehub/decode/plugins.py`
- Modify: `src/decodehub/decode/synth.py`
- Create: `tests/unit/test_i3c_decoder.py`
- Create: `tests/property/test_i3c_roundtrip.py`
- Create: `tests/mcp/test_i3c.py`
- Extend: `tests/unit/test_sigrok_external_waveforms.py`

**Interfaces:**
- Produces: `I3cDecodeNode`, `encode_i3c()`, `I3cEvent`
- Event kinds: `i3c.start`, `repeat-start`, `stop`, `addr`, `data`, `transfer`, `ccc`, `daa`, `warn`, `unsupported`

- [ ] **Step 1: Copy I3C tests and verify registration failure**

Copy the four I3C test files from the local repository, updating external
fixture paths to `tests/data/external/i3c/`. Run:

```bash
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest tests/unit/test_i3c_decoder.py -q
```

Expected: import/registration failure because the I3C package is absent.

- [ ] **Step 2: Port the self-contained protocol package**

Copy the six files from
`../decodehub-code-e127559/src/decodehub/decode/protocols/i3c/`. Preserve its
SDR/legacy/auto behavior, CCC name table, DAA state machine and HDR rejection.
Add this descriptor:

```python
PluginDescriptor("i3c", "decodehub.decode.protocols.i3c", "i3c_decode")
```

Add `I3cEvent` to `events.py` and export `encode_i3c` through `synth.py`.

- [ ] **Step 3: Register schema fields and errors**

Ensure `present.py` declares `mode`, address/data/ACK/parity/T-bit/CCC/DAA
fields and that all I3C error codes are registered before serialization.

- [ ] **Step 4: Run all I3C paths and full regression**

```bash
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest tests/unit/test_i3c_decoder.py tests/property/test_i3c_roundtrip.py tests/mcp/test_i3c.py tests/unit/test_sigrok_external_waveforms.py -q
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest tests
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python scripts/stdio_smoke.py
git diff --check
```

Expected: downloaded `ExampleWaveform.sr` reaches DAA and emits explicit HDR
unsupported events; generated SDR round trips remain exact.

- [ ] **Step 5: Commit and push**

```bash
git add src/decodehub/decode tests/unit/test_i3c_decoder.py tests/property/test_i3c_roundtrip.py tests/mcp/test_i3c.py tests/unit/test_sigrok_external_waveforms.py
git commit -m "feat(i3c): add passive basic sdr decoder" \
  -m "Decode I3C Basic SDR framing, parity and T bits, broadcast and directed CCCs, and DAA." \
  -m "Register the protocol through the plugin contract and report unsupported HDR modes explicitly." \
  -m "Verify synthetic vectors, external waveforms, MCP output and the full pytest suite."
git push origin integration/kingst-features
```

### Task 9: AVSBus Protocol Plugin

**Files:**
- Create: `src/decodehub/decode/protocols/avsbus/{__init__.py,decode.py,encode.py,binding.py,present.py,README.md}`
- Modify: `src/decodehub/decode/events.py`
- Modify: `src/decodehub/decode/plugins.py`
- Modify: `src/decodehub/decode/synth.py`
- Create: `tests/unit/test_avsbus_decoder.py`
- Extend: `tests/unit/test_sigrok_external_waveforms.py`

**Interfaces:**
- Produces: `AvsBusDecodeNode`, `encode_avsbus()`, `crc3()`, `AvsBusEvent`
- Event kinds: `avsbus.frame`, `avsbus.resync`, `avsbus.warn`

- [ ] **Step 1: Copy AVSBus tests and verify import failure**

Copy local `tests/unit/test_avsbus_decoder.py` and its CSV fixture test. Point
the asset at `tests/data/external/avsbus/avsbus_smoke.csv`.

- [ ] **Step 2: Port the package and event model**

Copy the six AVSBus protocol files from the local repository. Add:

```python
PluginDescriptor("avsbus", "decodehub.decode.protocols.avsbus", "avsbus_decode")
```

Add `AvsBusEvent` to `events.py`, export `encode_avsbus`, and register all frame
fields and error codes.

- [ ] **Step 3: Run targeted/full tests and commit**

```bash
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest tests/unit/test_avsbus_decoder.py tests/unit/test_sigrok_external_waveforms.py -q
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest tests
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python scripts/stdio_smoke.py
git diff --check
git add src/decodehub/decode tests/unit/test_avsbus_decoder.py tests/unit/test_sigrok_external_waveforms.py
git commit -m "feat(avsbus): add passive controller and target decoder" \
  -m "Decode AVSBus controller and target frames with CRC validation and resynchronization." \
  -m "Register AVSBus through the common plugin, binding, schema and capability interfaces." \
  -m "Verify valid and invalid frames, external CSV decode and the full pytest suite."
git push origin integration/kingst-features
```

### Task 10: Documentation, Upstream Compatibility and Final Gate

**Files:**
- Modify: `README.md`
- Modify: `docs/30-architecture.md`
- Modify: `docs/40-acquisition.md`
- Modify: `docs/41-decode.md`
- Modify: `docs/50-mcp-gateway.md`
- Modify: `docs/60-testing.md`
- Modify: `docs/test-assets.md`
- Create: `docs/integration-test-summary.md`

**Interfaces:**
- Produces: operator documentation and final auditable test report

- [ ] **Step 1: Update public documentation**

Document seven built-in protocols, ten acquisition formats, external entry
points, runtime capability derivation, real KVDAT settings and all external
assets. Keep existing v0.2.0 pipeline, fields and incremental CLI sections.

- [ ] **Step 2: Verify upstream mergeability without changing the branch**

```bash
git fetch upstream
git merge-tree "$(git merge-base HEAD upstream/main)" HEAD upstream/main > /tmp/decodehub-merge-tree.txt
! rg -n '^<<<<<<<|^=======$|^>>>>>>>' /tmp/decodehub-merge-tree.txt
```

Expected: no unresolved conflict markers. If upstream advanced and conflicts
exist, merge `upstream/main`, resolve them, run the complete gate, commit the
merge and push before continuing.

- [ ] **Step 3: Run the final gate**

```bash
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest tests
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python scripts/stdio_smoke.py
PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m decodehub.cli.main --version
git diff --check
git status --short
```

Expected: all tests green, stdio smoke successful, CLI reports v0.2.0 or the
documented downstream version, no whitespace errors and only intended docs
changes before commit.

- [ ] **Step 4: Write the exact final summary**

`docs/integration-test-summary.md` records every integration commit, targeted
test command/result, full-suite count, asset result, stdio smoke result,
upstream HEAD and integration HEAD.

- [ ] **Step 5: Commit and push**

```bash
git add README.md docs
git commit -m "docs(release): document integrated decoders and regression assets" \
  -m "Document setup, supported formats, protocol coverage and reproducible offline examples." \
  -m "Record the upstream synchronization workflow and all external asset provenance." \
  -m "Verify the final full suite, CLI and MCP smoke paths, assets and upstream merge compatibility."
git push origin integration/kingst-features
```

- [ ] **Step 6: Confirm remote state**

```bash
git status --short --branch
git rev-parse HEAD
git ls-remote origin refs/heads/integration/kingst-features
```

Expected: clean branch and identical local/remote commit identifiers.
