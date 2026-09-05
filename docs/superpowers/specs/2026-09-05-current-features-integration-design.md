# Current DecodeHub Features Integration Design

Date: 2026-09-05  
Status: Approved for implementation  
Target: `integration/kingst-features` based on `andy-qingcai/DecodedHub` v0.2.0

## 1. Objective

Integrate the capabilities that currently exist only in the local DecodeHub
repository into the upstream-tracking `third_part` repository without
regressing its v0.2.0 functionality.

The target must retain:

- regular updates from `andy-qingcai/DecodedHub`;
- the existing UART, I2C, SPI, uplink DSSS and downlink DBPSK decoders;
- payload field specifications, pipeline composition, named locks, render
  routes, incremental execution and output naming;
- one tested, reviewable commit and push for each integrated feature.

## 2. Repository and Branch Topology

```text
andy-qingcai/DecodedHub
        |
        v
upstream/main                  pristine upstream line
        |
        | merge periodically
        v
integration/kingst-features    downstream integration line
        |
        v
origin = kindresy/DecodedHub   writable fork
```

Local `main` tracks `upstream/main`. Development occurs only on
`integration/kingst-features`. Published integration commits are never
rebased; upstream updates are merged so pushed commit identifiers remain
stable and no force push is required.

## 3. Integration Strategy

The repositories have unrelated Git histories, so the implementation uses a
semantic port rather than a Git history merge or whole-file replacement.

For every feature:

1. identify the local implementation and tests;
2. write failing tests against the `third_part` architecture;
3. adapt the implementation to v0.2.0 extension points;
4. run targeted and full regression tests;
5. record exact tests in the commit body;
6. commit and push immediately.

Existing `third_part` modules are authoritative when both repositories contain
the same subsystem. In particular, the port must preserve `AdapterSpec`,
payload field specs, pipelines, named locks, render routes, incremental runs
and output naming templates.

## 4. Test Asset Policy

Downloaded and deterministic waveform assets live under:

```text
tests/data/external/<protocol>/
```

The canonical manifest is `docs/test-assets.md`. Each entry records:

- original source URL and project;
- download date and redistribution/license information;
- SHA-256 digest;
- original versus generated/converted status;
- file format and channel map;
- decoder parameters;
- expected events and decoded values;
- tests that consume the asset.

Original `.sr`, `.kvdat` and `.csv` files are retained. Derived files are
clearly labelled and include a reproducible conversion command. Generated
smoke captures are not described as externally sourced captures.

## 5. Baseline

Before feature changes, the upstream v0.2.0 suite produced:

```text
380 passed, 2 skipped
```

The downloaded real KingstVIS captures currently fail in the v0.2.0 KVDAT
adapter because the fourth header field is treated as the number of serialized
channels rather than device channel capacity:

```text
spi_bootloader_good.kvdat       FAIL: structural read past end
spi_bootloader_bad.kvdat        FAIL: structural read past end
spi_bootloader_with_init.kvdat  FAIL: structural read past end
```

The downloaded UART, I2C and SPI `.sr` captures cannot yet be ingested because
v0.2.0 has no Sigrok session adapter. These are capability gaps, not decoder
algorithm failures. Existing property tests remain the baseline for uplink and
downlink because no downloaded external assets for those two protocols are
currently available.

## 6. Ordered Feature Plan

### Phase 0: Assets and baseline tests

- Add `docs/test-assets.md`.
- Copy downloaded UART, I2C, SPI, I3C and AVSBus fixtures into
  `tests/data/external/`.
- Add checksums and provenance.
- Record the clean v0.2.0 baseline and known ingestion gaps.
- Validate existing UART/I2C/SPI algorithms using converted temporary input
  where native `.sr` ingestion is not yet available.
- Re-run all existing uplink/downlink tests.

Planned commit:

```text
test(assets): establish external waveform regression baseline
```

### Phase 1: Real KingstVIS KVDAT 3.6.x support

Adapt the local KVDAT implementation to the v0.2.0 `AdapterSpec` registry:

- parse device channel capacity and serialized channel blocks separately;
- preserve sparse physical channels;
- restore XML channel names, KingstVIS version and device metadata;
- recover verified KingstVIS 3.6.x SPI analyzer defaults;
- merge saved defaults before explicit protocol parameters;
- explicit user parameters and cleared optional roles win;
- reject corrupt channel blocks and preserve diagnostics for malformed XML or
  unsupported analyzer metadata.

Acceptance includes successful ingestion of all three downloaded KVDAT files
and decoding the known-good capture as MOSI `0x5A`, MISO `0xA5` without manual
channel or SPI mode parameters.

Planned commit:

```text
feat(kingst): ingest real kvdat captures and saved spi settings
```

### Phase 2: Sigrok `.sr` acquisition

Implement a native `sigrok_sr` `AdapterSpec`:

- detect the ZIP session layout;
- read metadata, sample rate, probe names and packed `logic-*` data;
- construct the existing `DigitalWave` representation;
- reject unsupported/corrupt sessions with actionable errors;
- expose the format through derived capabilities and option schemas.

Acceptance uses the downloaded UART, I2C and SPI sessions directly, without
pre-conversion.

Planned commit:

```text
feat(acquisition): add native sigrok session adapter
```

### Phase 3: Binding robustness

Port the local binding improvements while retaining v0.2.0 named locks and
derived parameter routing:

- add `require_any` role groups;
- allow SPI MOSI-only or MISO-only operation;
- match role aliases within prefixed and tokenized channel names;
- process explicit role overrides before heuristic allocation;
- preserve explicit clearing of optional roles.

Planned commit:

```text
feat(bindings): support alternative roles and robust channel matching
```

### Phase 4: Published event schema contracts

Add a versioned event publication contract without replacing the existing
payload field-spec subsystem:

- event and report schema version `1.0`;
- registered event kinds, protocol event fields and error codes;
- serialization-time validation;
- compatibility for all existing UART/I2C/SPI/uplink/downlink/field events;
- deterministic failures for misspelled kinds, fields and error codes.

The new event schema registry is separate from `decode/fields.py`, which
continues to own payload parsing specifications.

Planned commit:

```text
feat(schema): version and validate decoded event contracts
```

### Phase 5: Protocol plugin discovery and contracts

Introduce `PluginDescriptor` and plugin API version `1`:

- descriptors for all existing built-in protocols;
- external discovery through the `decodehub.protocols` Python entry-point
  group;
- idempotent loading;
- duplicate protocol and incompatible API rejection;
- node/binding/presentation consistency checks;
- node, adapter and presentation contract checks adapted to `AdapterSpec`;
- preserve direct protocol imports as a compatibility path.

Planned commit:

```text
feat(plugins): add versioned external protocol discovery
```

### Phase 6: Runtime capability matrix

Derive protocol and format capabilities from runtime registries:

- `AdapterSpec` supplies formats and options;
- decoder nodes supply parameter documentation;
- bindings supply roles and requirements;
- presentations supply event fields and preview kinds;
- the MCP protocol schema is no longer a hard-coded list;
- CLI parameter discovery and MCP capabilities share the same source.

Planned commit:

```text
feat(capabilities): derive protocol and format matrix at runtime
```

### Phase 7: I3C SDR

Port I3C Basic v1.1.1 SDR as a complete protocol plugin:

- decoder, deterministic encoder, binding, presentation and README;
- START, repeated START, STOP, address and transfer events;
- controller-write odd parity and target-read T-bit handling;
- `auto`, `sdr` and `legacy_i2c` modes;
- broadcast CCC recognition;
- ENTDAA PID/BCR/DCR and dynamic-address assignment;
- explicit ambiguity, malformed capture and unsupported HDR events;
- unit, property, external waveform and MCP end-to-end tests.

HDR-DDR/TSP/TSL/BT electrical decoding remains explicitly unsupported.

Planned commit:

```text
feat(i3c): add passive basic sdr decoder
```

### Phase 8: AVSBus

Port AVSBus as the second new plugin and use it to verify that the architecture
is not I3C-specific:

- 32-clock controller/target subframes;
- StartCode, Cmd, CmdGroup, CmdDataType, Select and data fields;
- target ACK, status and response data;
- CRC-3 `x^3+x+1` validation;
- controller, target and automatic observation modes;
- resynchronization, reserved-field and truncation diagnostics;
- unit, round-trip and tracked CSV waveform tests.

Planned commit:

```text
feat(avsbus): add passive controller and target decoder
```

### Phase 9: Documentation and release hardening

- update the root support matrix and architecture guide;
- document external plugin packaging;
- document KVDAT saved analyzer defaults and Sigrok sessions;
- finalize the test asset manifest and test summary;
- verify upstream merge procedure from a temporary branch;
- run all unit/property/integration/MCP tests and stdio smoke tests.

Planned commit:

```text
docs(release): document integrated decoders and regression assets
```

## 7. Per-Commit Quality Gate

Every feature commit must pass, in order:

1. feature-specific unit tests;
2. relevant property/round-trip tests;
3. relevant external waveform tests;
4. the complete `tests/` suite;
5. `scripts/stdio_smoke.py` when MCP-visible behavior changes;
6. `git diff --check`;
7. clean working tree after commit;
8. immediate push to `origin/integration/kingst-features`.

Commit messages use a Conventional Commit subject and contain:

```text
Why:
What:
Compatibility:
Tests:
```

The `Tests` section includes exact commands and pass/skip counts. Binary asset
commits also list asset checksums and provenance.

## 8. Compatibility and Conflict Rules

- Never replace v0.2.0 `services.py`, `runner.py`, `config.py`, adapter registry
  or render code wholesale.
- Preserve pipeline composition, payload field specs, named locks, incremental
  execution, render contributions and output naming templates.
- New capabilities must be derived from registries instead of adding another
  manually synchronized catalog.
- Existing public imports and event constructor positional arguments remain
  compatible.
- New protocols receive unique event kinds and registered fields/error codes.
- External assets never silently determine decoder parameters except verified
  capture metadata such as KingstVIS saved analyzer settings; explicit user
  parameters always take precedence.

## 9. Upstream Update Procedure

```text
git fetch upstream
git switch main
git merge --ff-only upstream/main
git switch integration/kingst-features
git merge main
run complete quality gate
git push origin main integration/kingst-features
```

Conflicts are resolved in favour of upstream for generic platform behavior and
in favour of the integration branch only for the explicitly listed features.
Each upstream synchronization receives its own merge commit and test summary.

## 10. Completion Criteria

The integration is complete when:

- all phases are committed and pushed individually;
- all seven built-in protocols are discoverable at runtime;
- external protocol entry-point discovery is covered by tests;
- all tracked waveform assets ingest and meet their documented expectations;
- real KingstVIS KVDAT defaults work through the application/MCP path;
- the complete suite and stdio smoke test are green;
- `third_part` retains every v0.2.0 capability and can merge the latest
  `upstream/main` without unresolved conflicts.
