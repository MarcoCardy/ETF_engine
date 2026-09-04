---
type: "implementation"
date: "2026-08-29T08:52:33.290720+00:00"
question: "portfolio world return metrics implementation test constrained"
contributor: "graphify"
outcome: "useful"
source_nodes: ["Constrained PIR Optimization", "MSCI World EUR Return Reconstruction", "VI Portfolio", "RCCR and Real Capital Buffer Metrics"]
---

# Q: portfolio world return metrics implementation test constrained

## Answer

Implemented PRUDENT_TREND_10 as a separate non-optimized sensitivity candidate: 29.7423529412% World, 23.9717647059% Momentum, 36.2858823529% Quality, 10% Trend. Preserved ERC_TREND_15. Regenerated audited outputs; 2006-06 through 2026-05 results after simulated trading costs: CAGR 10.4969%, volatility 11.7857%, max drawdown -36.0847%. Full 224-test suite passed and manifest hashes reconcile.

## Outcome

- Signal: useful

## Source Nodes

- Constrained PIR Optimization
- MSCI World EUR Return Reconstruction
- VI Portfolio
- RCCR and Real Capital Buffer Metrics