---
type: "query"
date: "2026-08-28T21:08:07.408686+00:00"
question: "Calcolami il portafoglio ideale con solo world momentum e quality. Voglio sapere l'allocazione efficiente"
contributor: "graphify"
source_nodes: ["Constrained PIR Optimization", "MSCI World EUR Return Reconstruction", "Walk-Forward Optimization"]
---

# Q: Calcolami il portafoglio ideale con solo world momentum e quality. Voglio sapere l'allocazione efficiente

## Answer

Expanded from graph vocabulary: [portfolio, world, optimization, constrained, return, annual, metrics]. On the frozen EUR 240-month sample, long-only maximum Sharpe is 100% Quality; minimum variance is 10.78% World, 6.86% Momentum, 82.36% Quality. Because this is concentrated and return-estimate-sensitive, the robust equal-risk allocation is 33.48% World, 32.38% Momentum, 34.13% Quality; its first-ten-year estimate was 32.90/31.85/35.25, showing stability. Full-sample ERC backtest after simulated trading costs: CAGR 10.44%, annualized volatility 13.19%, max drawdown -44.18%. The three equity sleeves remain highly correlated, so they cannot reproduce the defensive benefit of the Trend sleeve.

## Source Nodes

- Constrained PIR Optimization
- MSCI World EUR Return Reconstruction
- Walk-Forward Optimization