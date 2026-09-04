# Deterministic Ranking Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a reproducible monthly deterministic ranking series from January 2020 through July 2026 plus a separately labelled partial observation through 28 August 2026.

**Architecture:** Reuse the existing source-manifest, point-in-time, metric and deterministic-output conventions. Add one small DRO module that consumes already-normalized monthly total-return levels; keep network refresh separate from offline calculation and keep every DRO result separate from TCE-MA.

**Tech Stack:** Python standard library, existing yfinance dependency for declared market proxies, existing project test framework.

**Spec:** `docs/superpowers/specs/2026-08-27-deterministic-ranking-overlay-design.md`

## Global Constraints

- DRO and news-based TCE-MA are different systems and never share scores or performance claims.
- Fixed ten-exposure universe; SWDA is benchmark only.
- Ranking is 12–1 total return; absolute return must be positive; current total-return level must exceed its 10-month mean.
- No optimization, fitted coefficients, volatility scaling, discretionary overrides or post-result proxy substitutions.
- Decisions use only available data and execute no earlier than the next complete session.
- Missing/stale data make the candidate ineligible; no silent truncation.
- Complete history is January 2020–July 2026; August is `PARTIAL_AS_OF_2026-08-28` and excluded from full-month statistics.

---

### Task 1: Freeze sources and normalized input contract

**Files:**
- Create: `config/dro_v1.json`
- Create: `tests/test_dro.py`
- Create: `perpetual_engine/dro.py`

**Interfaces:**
- Produce immutable monthly observation and source definitions consumed by Task 2.

- [x] Add failing tests for exact universe, explicit start/end/partial dates, unique candidates, source inception, total-return requirement, staleness, hashes and the separation from TCE.
- [x] Run the focused tests and confirm failure because DRO does not exist.
- [x] Implement the minimum dataclasses/config validation required by the tests.
- [x] Run the focused tests and confirm they pass.

### Task 2: Implement the monthly selector and accounting

**Files:**
- Modify: `tests/test_dro.py`
- Modify: `perpetual_engine/dro.py`

**Interfaces:**
- Consume validated monthly total-return levels.
- Produce candidate diagnostics, monthly decisions and gross/net strategy returns.

- [x] Add failing tests for 12–1 ranking, 10-month trend, positive absolute filter, frozen-order tie break, cash fallback, next-period execution, transaction costs and partial-month exclusion.
- [x] Run focused tests and verify the intended failures.
- [x] Implement only the selector and accounting needed by those tests.
- [x] Run focused and regression tests.

### Task 3: Refresh declared histories and freeze a vintage

**Files:**
- Modify: `config/dro_v1.json`
- Create: `data/dro_v1/raw/`
- Create: `data/dro_v1/manifest.json`

**Interfaces:**
- Produce one frozen monthly total-return table with source, observation and retrieval metadata.

- [x] Establish each declared exposure proxy before calculating strategy returns; record any unavailable candidate rather than substituting after inspection.
- [x] Download sufficient history for the December 2018 warm-up, complete months through July 2026 and the 28 August partial observation.
- [x] Freeze raw bytes and hashes in the manifest.
- [x] Validate coverage, inception, last observation and staleness before calculation.

### Task 4: Run the monthly study and publish outputs

**Files:**
- Create: `perpetual_engine/dro_report.py`
- Modify: `tests/test_dro.py`
- Create: `outputs/dro_v1/monthly.csv`
- Create: `outputs/dro_v1/result.json`
- Create: `outputs/dro_v1/report.md`

**Interfaces:**
- Consume the frozen vintage and configuration.
- Produce deterministic CSV/JSON/Markdown artifacts.

- [x] Add a failing end-to-end fixture test covering exact month count, no truncation, partial-label handling and deterministic byte-identical output.
- [x] Run the test and verify the expected failure.
- [x] Implement the minimal offline runner and report writer.
- [x] Run the real study twice and verify identical outputs.
- [x] Inspect selections, gaps, returns, turnover, costs and benchmark alignment.

### Task 5: Independent review and final verification

**Files:**
- Review all Task 1–4 changes and outputs.

- [x] Have a non-authoring agent audit signal timing, proxy declarations, survivorship labels, missing-data handling, transaction costs and August exclusion.
- [x] Correct every load-bearing finding with a covering test.
- [x] Run the complete test suite and inspect the final monthly table.
