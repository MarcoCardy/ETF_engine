# Graph Report - ETF_infazione  (2026-08-21)

## Corpus Check
- Corpus is ~5,077 words - fits in a single context window. You may not need a graph.

## Summary
- 43 nodes · 41 edges · 9 communities (7 shown, 2 thin omitted)
- Extraction: 85% EXTRACTED · 15% INFERRED · 0% AMBIGUOUS · INFERRED: 6 edges (avg confidence: 0.8)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Architettura fiscale e beta
- Capitale reale e rendita
- Validazione della perpetuità
- Damodaran senza look-ahead
- Integrazione operativa VI
- Regola di distribuzione E
- Crisi e recupero
- Rendimenti World in EUR
- Veto Treasury e TIPS

## God Nodes (most connected - your core abstractions)
1. `Distribution Rule E` - 6 edges
2. `Funding Protocol G` - 5 edges
3. `E+G Workbook Implementation Transcript` - 4 edges
4. `Shadow Monitoring During Growth` - 3 edges
5. `Perpetual Engine v3.3` - 3 edges
6. `EUR Real-Capital Accounting` - 3 edges
7. `Damodaran Implied ERP Engine` - 3 edges
8. `Tax-Aware 60/20/20 Architecture` - 3 edges
9. `Real-Income Payout Guardrail` - 3 edges
10. `Paired Monthly Block Bootstrap` - 3 edges

## Surprising Connections (you probably didn't know these)
- `Distribution Rule E` --semantically_similar_to--> `Real-Income Payout Guardrail`  [INFERRED] [semantically similar]
  eg_policy_workbook_transcript.txt → perpetual_engine_v3_3.txt
- `Funding Protocol G` --semantically_similar_to--> `Italian Tax-Lot Ledger`  [INFERRED] [semantically similar]
  eg_policy_workbook_transcript.txt → perpetual_engine_v3_3.txt
- `Paired Monte Carlo Policy Test` --semantically_similar_to--> `Paired Monthly Block Bootstrap`  [INFERRED] [semantically similar]
  eg_policy_workbook_transcript.txt → perpetual_engine_v3_3.txt
- `Shadow Monitoring During Growth` --conceptually_related_to--> `Walk-Forward Optimization`  [INFERRED]
  eg_policy_workbook_transcript.txt → perpetual_engine_v3_3.txt
- `€600,000 Real Hard Floor` --conceptually_related_to--> `Strict Perpetuity Constraints`  [INFERRED]
  eg_policy_workbook_transcript.txt → perpetual_engine_v3_3.txt

## Hyperedges (group relationships)
- **Real Capital Protection System** — motor_docs_perpetual_engine_v3_3_real_capital_accounting, motor_docs_perpetual_engine_v3_3_italian_cpi, motor_docs_perpetual_engine_v3_3_payout_guardrail, motor_docs_perpetual_engine_v3_3_strict_perpetuity_constraints, motor_docs_eg_policy_workbook_transcript_hard_floor [INFERRED 0.85]
- **Dynamic Beta Control System** — motor_docs_perpetual_engine_v3_3_damodaran_erp, motor_docs_perpetual_engine_v3_3_treasury_tips_veto, motor_docs_perpetual_engine_v3_3_crisis_engine, motor_docs_perpetual_engine_v3_3_recovery_engine, motor_docs_perpetual_engine_v3_3_morin_bayes_control_layer [EXTRACTED 1.00]
- **Tax-Aware Portfolio and Funding Architecture** — motor_docs_perpetual_engine_v3_3_swda_core, motor_docs_perpetual_engine_v3_3_lwld_portable_beta, motor_docs_perpetual_engine_v3_3_tax_aware_60_20_20, motor_docs_perpetual_engine_v3_3_italian_tax_ledger, motor_docs_eg_policy_workbook_transcript_funding_protocol_g [INFERRED 0.85]

## Communities (9 total, 2 thin omitted)

### Community 0 - "Architettura fiscale e beta"
Cohesion: 0.25
Nodes (8): Funding Protocol G, Tax-Aware Withdrawal Waterfall, Tax-Lot-Based Funding Capacity, Daily Leveraged Return Reconstruction, Italian Tax-Lot Ledger, LWLD Portable-Beta Overlay, SWDA Strategic Core, Tax-Aware 60/20/20 Architecture

### Community 1 - "Capitale reale e rendita"
Cohesion: 0.29
Nodes (7): Income Reserve Policy, Italian CPI ITACPALTT01IXNBM, Real-Income Payout Guardrail, Perpetual Engine v3.3, RCCR and Real Capital Buffer Metrics, EUR Real-Capital Accounting, Perpetual Engine v3.3 Specification

### Community 2 - "Validazione della perpetuità"
Cohesion: 0.33
Nodes (6): €600,000 Real Hard Floor, Paired Monte Carlo Policy Test, Paired Monthly Block Bootstrap, Constrained PIR Optimization, Regime-Conditional Bootstrap, Strict Perpetuity Constraints

### Community 3 - "Damodaran senza look-ahead"
Cohesion: 0.33
Nodes (6): Anti-Look-Ahead Signal Timing, Damodaran Implied ERP Engine, Damodaran Historical and Monthly ERP Data, Beta No-Trade Band, Parameter Perturbation Testing, Walk-Forward Optimization

### Community 4 - "Integrazione operativa VI"
Cohesion: 0.40
Nodes (5): VI Growth Phase, E+G Workbook Implementation Transcript, Shadow Monitoring During Growth, VI Portfolio, Weekly Workbook Policy Integration

### Community 5 - "Regola di distribuzione E"
Cohesion: 0.50
Nodes (4): High-Water-Mark Floor Ratchet, €1,800 Monthly Protected Income, Distribution Rule E, 3% Annual Distribution

### Community 6 - "Crisi e recupero"
Cohesion: 1.00
Nodes (3): Crisis Engine, Morin–Bayes Control Layer, Recovery Engine

## Knowledge Gaps
- **18 isolated node(s):** `VI Portfolio`, `3% Annual Distribution`, `€1,800 Monthly Protected Income`, `Tax-Lot-Based Funding Capacity`, `VI Growth Phase` (+13 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **2 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `E+G Workbook Implementation Transcript` connect `Integrazione operativa VI` to `Architettura fiscale e beta`, `Regola di distribuzione E`?**
  _High betweenness centrality (0.425) - this node is a cross-community bridge._
- **Why does `Distribution Rule E` connect `Regola di distribuzione E` to `Capitale reale e rendita`, `Validazione della perpetuità`, `Integrazione operativa VI`?**
  _High betweenness centrality (0.366) - this node is a cross-community bridge._
- **Why does `Funding Protocol G` connect `Architettura fiscale e beta` to `Validazione della perpetuità`, `Integrazione operativa VI`?**
  _High betweenness centrality (0.292) - this node is a cross-community bridge._
- **What connects `VI Portfolio`, `3% Annual Distribution`, `€1,800 Monthly Protected Income` to the rest of the system?**
  _18 weakly-connected nodes found - possible documentation gaps or missing edges._