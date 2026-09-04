---
type: "implementation"
date: "2026-08-29T09:15:22.859079+00:00"
question: "portfolio world return metrics implementation test constrained"
contributor: "graphify"
outcome: "useful"
source_nodes: ["Constrained PIR Optimization", "MSCI World EUR Return Reconstruction", "VI Portfolio", "RCCR and Real Capital Buffer Metrics"]
---

# Q: portfolio world return metrics implementation test constrained

## Answer

Changed the operational structural candidate to THREE_EQUITY_FIXED with 33.0470588235% World, 26.6352941176% Momentum, 40.3176470589% Quality and zero Trend. This preserves the prior equity mix and avoids a new retrospective optimization. Kept Trend candidates as research comparators. Regenerated outputs: 2006-06 through 2026-05 CAGR 10.4902%, volatility 13.1541%, max drawdown -43.8256%. Full 225-test suite passed and manifest hashes reconcile.

## Outcome

- Signal: useful

## Source Nodes

- Constrained PIR Optimization
- MSCI World EUR Return Reconstruction
- VI Portfolio
- RCCR and Real Capital Buffer Metrics