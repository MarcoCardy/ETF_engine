---
type: "query"
date: "2026-08-27T07:18:39.001629+00:00"
question: "Inspect the current workspace end to end for reusable point-in-time and backtest APIs and exact integration points for a minimal TCE module."
contributor: "graphify"
source_nodes: ["Anti-Look-Ahead Signal Timing", "Perpetual Engine v3.3", "Walk-Forward Optimization"]
---

# Q: Inspect the current workspace end to end for reusable point-in-time and backtest APIs and exact integration points for a minimal TCE module.

## Answer

Expanded from original query via vocab: [portfolio, engine, data, funding, core, implementation, test, historical, reconstruction, monthly]. Direct code verification found reusable ObservationRow validation/as-of selection, atomic frozen-vintage refresh and manifest hashing, deterministic monthly backtest costs and reporting. The legacy backtest is intentionally hard-coded to five strategies, three sleeves, and a January-1979 start, so a minimal TCE validator should be a separate module and CLI path that consumes frozen observations rather than extending BacktestRow. GDI and TCE runtime modules/tests are absent. Full suite: 162 tests pass.

## Source Nodes

- Anti-Look-Ahead Signal Timing
- Perpetual Engine v3.3
- Walk-Forward Optimization