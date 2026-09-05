# Upstream PR Series Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the downstream DecodeHub improvements to `andy-qingcai/DecodedHub` as a reviewable, dependency-ordered pull-request series.

**Architecture:** Each PR branch is stacked on the preceding branch and pushed to the `kindresy/DecodedHub` fork. GitHub PRs all target upstream `main`; merging them in order makes each later diff shrink to its own slice. Internal implementation plans, unredistributable KVDAT files, and GPL waveform fixtures stay out of upstream.

**Tech Stack:** Git worktrees, GitHub CLI, Python 3.11+, pytest, GitHub Actions, setuptools/uv.

## Global Constraints

- Preserve every upstream v0.2.0 feature and public API unless a versioned compatibility layer explicitly covers it.
- Use only MIT, public-domain, or project-generated material in upstream test assets.
- Keep commits independently testable and push each branch before opening its PR.
- Run the full test suite and process-level MCP smoke on every stacked branch.
- Never force-push or write directly to upstream `main`.
- Upstream version becomes `0.3.0` only in the final release PR.

---

### Task 1: Mainline CI and Packaging Hygiene

**Files:**
- Create: `.github/workflows/ci.yml`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: Python 3.11/3.12/3.13 test gate and standards-compliant MIT package metadata.

- [ ] Create `pr/01-mainline-hygiene` from `upstream/main` in `.worktrees/upstream-prs`.
- [ ] Verify the untouched upstream suite with `PYTHONPATH=src ../decodehub-code-e127559/.venv/bin/python -m pytest tests`.
- [ ] Add a GitHub Actions matrix that installs `.[dev]`, runs `pytest tests`, and runs `scripts/stdio_smoke.py`.
- [ ] Replace `license = { text = "MIT" }` with `license = "MIT"`.
- [ ] Run pytest, stdio smoke, `uv build --wheel`, and `git diff --check`.
- [ ] Commit, push `pr/01-mainline-hygiene`, and create PR 1 against upstream `main`.

### Task 2: Waveform Assets and Acquisition

**Files:**
- Cherry-pick: `491840a`, `bb4af54`, `c6e8328`
- Modify: `LICENSE`
- Modify: `docs/test-assets.md`
- Modify: `tests/data/external/README.md`
- Modify: `tests/unit/test_external_asset_manifest.py`
- Modify: `tests/unit/test_kingst_kvdat_real.py`
- Delete before commit: `tests/data/external/i3c/ExampleWaveform.csv`
- Delete before commit: `tests/data/external/i3c/ExampleWaveform.sr`
- Delete before commit: `tests/data/external/i3c/LICENSE.GPL-3.0`

**Interfaces:**
- Produces: KVDAT 3.6.x saved-settings ingestion, Sigrok `.sr`, public-domain UART/I2C/SPI fixtures, and generated I3C/AVSBus fixtures.

- [ ] Create `pr/02-acquisition` from PR 1 head and cherry-pick the three commits.
- [ ] Remove the GPL I3C files and their manifest/documentation rows.
- [ ] Preserve the imported MIT source notice by adding `Copyright (c) 2026 luyuan` to `LICENSE`.
- [ ] Replace the hard-coded sibling KVDAT lookup with optional `DECODEHUB_KVDAT_FIXTURES`; absent files skip with a clear reason.
- [ ] Run asset manifest, KVDAT, Sigrok, full pytest, stdio smoke, and whitespace checks.
- [ ] Commit the upstream hygiene delta, push `pr/02-acquisition`, and create PR 2.

### Task 3: Platform Contracts and Plugin Runtime

**Files:**
- Cherry-pick: `c191af9`, `e51c3d4`, `89e50f2`, `cef830f`

**Interfaces:**
- Produces: `ProtocolBinding.require_any`, event/report schema 1.0, plugin API 1, and registry-derived capability output.

- [ ] Create `pr/03-plugin-runtime` from PR 2 head and cherry-pick all four commits.
- [ ] Run binding, schema, plugin, capability, MCP, full pytest, stdio smoke, and whitespace checks.
- [ ] Push `pr/03-plugin-runtime` and create PR 3.

### Task 4: I3C Basic SDR Decoder

**Files:**
- Cherry-pick: `9274c61`
- Modify: `tests/unit/test_i3c_external_waveform.py`
- Modify: `tests/unit/test_sigrok_external_waveforms.py`

**Interfaces:**
- Produces: passive I3C Basic SDR/legacy-I2C decoder, CCC/DAA events, explicit ambiguity/HDR diagnostics, and generated-vector regression coverage.

- [ ] Create `pr/04-i3c` from PR 3 head and cherry-pick the I3C commit.
- [ ] Point the external CSV test at generated `i3c_sdr_smoke.csv` and assert address/data/no-error semantics.
- [ ] Remove the GPL `ExampleWaveform.sr` Sigrok test; Sigrok protocol interoperability remains covered by public-domain UART/I2C/SPI sessions.
- [ ] Run I3C unit/property/MCP, full pytest, stdio smoke, and whitespace checks.
- [ ] Commit the generated-fixture adaptation, push `pr/04-i3c`, and create PR 4.

### Task 5: AVSBus Decoder

**Files:**
- Cherry-pick: `61b2b6b`

**Interfaces:**
- Produces: passive AVSBus controller/target decoding, CRC-3 validation, status/error events, and generated CSV coverage.

- [ ] Create `pr/05-avsbus` from PR 4 head and cherry-pick the AVSBus commit.
- [ ] Run AVSBus/Sigrok, full pytest, stdio smoke, and whitespace checks.
- [ ] Push `pr/05-avsbus` and create PR 5.

### Task 6: Public Documentation and v0.3.0

**Files:**
- Import selected paths from: `6852724`
- Modify: `README.md`
- Modify: `docs/30-architecture.md`
- Modify: `docs/40-acquisition.md`
- Modify: `docs/41-decode.md`
- Modify: `docs/50-mcp-gateway.md`
- Modify: `docs/60-testing.md`
- Modify: `docs/test-assets.md`
- Modify: `pyproject.toml`
- Modify: `src/decodehub/__init__.py`
- Create: `CHANGELOG.md`

**Interfaces:**
- Produces: public 7-protocol/10-format documentation and release version `0.3.0`.

- [ ] Create `pr/06-release-docs` from PR 5 head and import only public docs from `6852724`; do not import `docs/integration-test-summary.md` or any `docs/superpowers` file.
- [ ] Remove local sibling-path references and describe optional KVDAT fixtures via `DECODEHUB_KVDAT_FIXTURES`.
- [ ] Set both package version declarations to `0.3.0` and add a concise changelog.
- [ ] Run full pytest, stdio smoke, CLI version, wheel build, and whitespace checks.
- [ ] Push `pr/06-release-docs` and create PR 6.

### Task 7: Remote and PR Verification

**Files:** None.

**Interfaces:**
- Produces: auditable PR URLs, branch heads, CI state, and merge order.

- [ ] Confirm every fork branch exists and matches its local head.
- [ ] Confirm six open PRs target `andy-qingcai/DecodedHub:main` in dependency order.
- [ ] Check GitHub Actions status and report any jobs still queued separately from failures.
- [ ] Leave all PR worktrees and remote branches intact for review; do not merge without upstream maintainer action.
