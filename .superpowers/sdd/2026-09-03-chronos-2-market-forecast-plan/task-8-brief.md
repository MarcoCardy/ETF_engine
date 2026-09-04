# Task 8 brief — Portfolio P monitoring

Implement the revised approved realized monitor for `P_WORLD_FACTOR_TREND` under TDD and Ponytail full. It monitors; it does not forecast, optimize, trade, or alter weights. The user explicitly replaced the earlier QDVA/QDVB/DBMG candidate; remove it completely from active configuration, code, tests, and reports.

## Files

- Create `config/portfolio_p_v1.json`.
- Create `perpetual_engine/portfolio_monitor.py`.
- Create `tests/test_portfolio_monitor.py`.
- Modify `perpetual_engine/cli.py` only to add the two portfolio commands below.

## Frozen identity and arithmetic

- Base currency EUR, monthly target-weight rebalance, starting value 100, weights exactly summing to one.
- Exact ordered components:
  - `SWDA`: ticker `SWDA.MI`, ISIN `IE00B4L5Y983`, quote currency EUR, weight 0.60.
  - `IWMO`: ticker `IWMO.MI`, ISIN `IE00BP3QZ825`, quote currency EUR, weight 0.15.
  - `IWQU`: ticker `IWQU.MI`, ISIN `IE00BP3QZ601`, quote currency EUR, weight 0.15.
  - `DBMFE`: ticker `DBMFE.PA`, ISIN `LU2951555403`, quote currency EUR, weight 0.10.
- No FX conversion: all four selected listings are quoted in EUR.
- For each month end use the last adjusted close on or before month end, at most seven calendar days stale.
- Never synthesize or backfill DBMFE before launch and never substitute DBMG, DBMF, or an index.
- Start at the first real common **return** month, after two consecutive eligible common monthly price observations. Portfolio return is the ordinary weighted sum of the four component EUR returns, representing monthly target-weight rebalancing. Reject non-finite/non-positive prices and component returns at or below -100%.
- Cumulative value starts from 100 at the first return and compounds. Drawdown is versus the running peak.
- Trailing volatility is sample standard deviation of up to the exact last 12 monthly portfolio returns times `sqrt(12)`, available only with 12 observations. Overall annualized volatility is likewise unavailable until 12 returns. Maximum drawdown and cumulative values remain available earlier.
- Rolling component correlations use the exact last 12 common component returns and are unavailable before 12. Emit all six unordered pairs in this order: `SWDA-IWMO`, `SWDA-IWQU`, `SWDA-DBMFE`, `IWMO-IWQU`, `IWMO-DBMFE`, `IWQU-DBMFE`.
- `SHORT_LIVE_HISTORY` is true until 36 complete common monthly returns exist.
- Bind the investable mapping `WORLD=SWDA`, `MOMENTUM=IWMO`, `QUALITY=IWQU`, and `TREND=DBMFE` in configuration and manifest. Chronos' long historical targets remain factor proxies; the exact ETF series are the realized investable monitor.

## Config and interfaces

- Strict frozen config below project root, exact identifiers/order/weights/rules and a portfolio data root below the project.
- Required interfaces:
  - `load_portfolio_config(path: Path) -> PortfolioConfig`;
  - `refresh_portfolio_prices(path: Path, *, downloader: Callable | None = None, retrieved_at: datetime | None = None) -> str`;
  - `portfolio_rows(config: PortfolioConfig, eur_prices: Mapping[str, Mapping[date, float]]) -> tuple[PortfolioRow, ...]`;
  - `write_portfolio_report(path: Path, output: Path) -> Path`.
- Reuse the existing yfinance adjusted-close extraction pattern only inside `refresh_portfolio_prices`; refresh is the sole networked portfolio path. Tests inject a downloader and remain offline.
- Freeze exact daily adjusted-close input bytes with hashes and UTC retrieval provenance. Publish a new immutable vintage and atomically replace a validated current pointer only after every symbol succeeds. A failed refresh leaves the prior current pointer/vintage usable. Reject missing/duplicate dates, duplicate symbols, non-finite/non-positive prices, unexpected identifiers, corrupt hashes, and path escape/link traversal.
- The offline report loads only the frozen current vintage and validates all hashes before calculation.

## Outputs

Publish the report atomically and reuse only byte-identical output:

- `portfolio_monthly.csv`: `month,SWDA,IWMO,IWQU,DBMFE,portfolio_return,cumulative_value,drawdown,trailing_volatility_12m` where component columns are monthly EUR returns.
- `portfolio_metrics.csv`: `first_month,last_month,count,cumulative_return,annualized_volatility,max_drawdown`.
- `portfolio_correlations.csv`: `month,pair,count,rolling_correlation_12m`; emit six rows per portfolio month, blank correlation until count 12.
- `manifest.json`: schema/portfolio ID, config/input/output hashes, vintage/retrieval provenance, exact identifiers/order/weights, investable target mapping, EUR-listing rule, first common return month, proxy caveat, and boolean `SHORT_LIVE_HISTORY`.

## CLI

- `chronos portfolio-refresh --config PATH` -> print vintage ID, return 0.
- `chronos portfolio-report --config PATH --output PATH` -> print output path, return 0.
- Preserve the four Task 7 commands and all legacy behavior. Imports remain lazy and errors use existing `Input error`/exit 2 handling.

## TDD and verification

- Use tiny deterministic fixtures to cover strict config and target mapping, first-common truncation, weights, returns/value/drawdown, volatility availability, all six rolling correlations, staleness/missing/duplicate/non-finite rejection, frozen refresh rollback, output hashes/atomic collision, and CLI dispatch. Assert that no FX or withdrawn QDVA/QDVB/DBMG identifiers are requested or emitted.
- Capture RED, run focused portfolio tests, all Chronos tests, the full suite, and `pip check`.
- No live download during ordinary tests. Do not add dependencies.
- Produce `task-8-head`, `task-8-review.diff`, `task-8-report.md`, update ledger review-pending, and do not use Git.
