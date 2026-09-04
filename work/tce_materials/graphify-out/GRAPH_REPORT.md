# Graph Report - tce_materials  (2026-08-26)

## Corpus Check
- Corpus is ~10,306 words - fits in a single context window. You may not need a graph.

## Summary
- 27 nodes · 24 edges · 6 communities (5 shown, 1 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- Monthly GDI Evolution
- Live TCE Paper Tests
- TCE Theme Scoring
- Confirmed Debasement Inputs
- TCE Multi-Asset Rotation
- Damodaran Valuation Context

## God Nodes (most connected - your core abstractions)

## Surprising Connections (you probably didn't know these)
- `GDI Monthly v2.0` --semantically_similar_to--> `GDI Confirmed`  [EXTRACTED] [semantically similar]
   →   _Bridges community 3 → community 0_
- `TCE-MA v1.0` --semantically_similar_to--> `Thematic Confirmation Engine v1.0`  [EXTRACTED] [semantically similar]
   →   _Bridges community 2 → community 4_
- `TCE-MA v1.0` --implements--> `Invesco Physical Gold ETC (SGLD)`  [EXTRACTED]
   →   _Bridges community 4 → community 1_

## Hyperedges (group relationships)
- **gdi_debasement_signal_components** —  [INFERRED]
- **tce_multi_asset_selection** —  [INFERRED]
- **tce_basket_paper_test** —  [INFERRED]

## Communities (6 total, 1 thin omitted)

### Community 0 - "Monthly GDI Evolution"
Cohesion: 0.25
Nodes (8): GDI Pressure, Gold/SDR, Conditional Financial Repression, Debasement Early Warning, GDI, GDI Monthly v2.0, GDI-RT v1.1, Global Money Nowcast

### Community 1 - "Live TCE Paper Tests"
Cohesion: 0.33
Nodes (6): Invesco Physical Gold ETC (SGLD), Tce weekly, NUCL Starting Position, Invesco Physical Gold ETC (SGLD), TCE Basket, WIRE Starting Position

### Community 2 - "TCE Theme Scoring"
Cohesion: 0.40
Nodes (5): Bubble Veto, Narrative Veto, VanEck Uranium and Nuclear Technologies UCITS ETF (NUCL), Nuclear Energy Theme, Thematic Confirmation Engine v1.0

### Community 3 - "Confirmed Debasement Inputs"
Cohesion: 0.67
Nodes (3): Financial Repression, GDI Confirmed, Global Excess Money Growth

### Community 4 - "TCE Multi-Asset Rotation"
Cohesion: 0.67
Nodes (3): Avviare test, TCE-MA Rotation Rule, TCE-MA v1.0

## Knowledge Gaps
- **1 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Not enough signal to generate questions. This usually means the corpus has no AMBIGUOUS edges, no bridge nodes, no INFERRED relationships, and all communities are tightly cohesive. Add more files or run with --mode deep to extract richer edges._