---
type: "query"
date: "2026-08-28T21:26:55.802389+00:00"
question: "Si, ricalcola il portafoglio a 4, AQR Time-Series Momentum, mi sembra dia poco contributo"
contributor: "graphify"
source_nodes: ["Constrained PIR Optimization", "MSCI World EUR Return Reconstruction", "Walk-Forward Optimization"]
---

# Q: Si, ricalcola il portafoglio a 4, AQR Time-Series Momentum, mi sembra dia poco contributo

## Answer

Expanded from graph vocabulary: [portfolio, world, return, metrics, optimization, constrained, historical]. Recalculated a frozen sensitivity grid holding the World/Momentum/Quality mix constant and varying AQR Trend from 0% to 30%. Over 2006-06 to 2026-05, moving from 0% to 15% Trend changed CAGR by only -0.0226 percentage points, but reduced annualized volatility by 1.9018 points and improved maximum drawdown by 11.6862 points. In the most recent ten years it cost 1.2901 points of CAGR while reducing volatility by 2.3042 points and improving drawdown by 4.4529 points. A prudent non-optimized candidate is 29.74% World, 23.97% Momentum, 36.29% Quality, 10% Trend; full-sample CAGR 10.50%, volatility 11.79%, max drawdown -36.08%. Trend contribution is therefore defensive, not return-generating.

## Source Nodes

- Constrained PIR Optimization
- MSCI World EUR Return Reconstruction
- Walk-Forward Optimization