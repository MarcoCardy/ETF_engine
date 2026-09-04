# Perpetual Engine — Reproducible Core Design

## Purpose

Build the first auditable component of the Perpetual Engine: an exact implementation of Distribution Rule E, Funding Protocol G, real-capital accounting, and the VI portfolio state. This component must produce the same result from the same inputs, fail closed when required financial or tax data are missing, and expose outputs that a weekly report can consume.

The historical backtest, paired bootstrap, Monte Carlo, and walk-forward optimizer are subsequent components. No prior Monte Carlo number is accepted as a result of this core.

## Chosen approach

Use a small Python package with pure calculation functions and explicit input files. Keep market-data downloads outside the policy calculations so tests never depend on the network.

Alternatives rejected:

- A single notebook would be faster initially but would mix data acquisition, state, and calculations, making look-ahead and state errors hard to audit.
- Building the complete v3.3 Monte Carlo immediately would conceal policy errors behind simulation output and make failures difficult to localize.

## Units and base state

- Accounting currency: EUR.
- Policy base date: 2026-08-19.
- VI real capital at the base date: EUR 614,590.86.
- Initial real high-water mark: EUR 614,590.86.
- Real hard floor: EUR 600,000.
- Real distribution activation threshold: EUR 800,000.
- CPI base value is a required input identified by series, observation date, value, and publication date. The base portfolio value is real by definition; later nominal observations are converted to base-date euros.
- Italian CPI series: `ITACPALTT01IXNBM`, unless a later design explicitly replaces it with a documented Italian or euro-area series.

Real capital is:

```text
real_capital_t = nominal_capital_t * cpi_base / cpi_t
```

Missing or stale CPI causes policy output `DATA_INCOMPLETE` and operational payout zero.

## Policy lifecycle

The policy has two lifecycle states:

1. `GROWTH`: payout is always zero; E+G is calculated only in shadow mode.
2. `DISTRIBUTION`: entered only after an explicit activation instruction recorded with a date and only if real capital is at least EUR 800,000 on that date.

After activation, a fall below EUR 800,000 does not return the portfolio to `GROWTH`. Rule E manages the subsequent distribution state. Deactivation requires another explicit instruction and is not inferred from market movements.

## High-water mark and ratcheted floor

The high-water mark is updated from the latest end-of-week real portfolio value before any distribution outflow. It never decreases.

```text
hwm_t = max(hwm_previous, real_capital_pre_distribution_t)
steps_t = floor(max(hwm_t - 614590.86, 0) / 100000)
ratcheted_floor_t = 600000 + 50000 * steps_t
```

Weekly sampling is the official observation convention. Intrawweek highs do not alter the HWM. A historical test must reproduce the same weekly calendar.

## Distribution Rule E

All targets are real net amounts received by the investor. Protocol G converts the net target into the required gross portfolio outflow, including taxes, fees, and slippage.

At the monthly distribution evaluation:

```text
if lifecycle == GROWTH:
    state = GROWTH
    target_net_real = 0
elif real_capital < 600000:
    state = HARD_STOP
    target_net_real = 0
elif real_capital < ratcheted_floor:
    state = PROTECTED
    target_net_real = 1800
else:
    state = NORMAL
    target_net_real = real_capital * 0.03 / 12
```

The hard floor constrains total portfolio outflow, not only the investor's net receipt:

```text
maximum_total_outflow_real = max(real_capital - applicable_floor, 0)
```

In `NORMAL`, the applicable floor is the ratcheted floor. In `PROTECTED`, it is EUR 600,000. Protocol G must reduce the delivered net amount if taxes, fees, and payout together would exceed the permitted total outflow.

Examples:

- EUR 614,590.86 in `GROWTH`: payout zero.
- EUR 800,000 after activation with a EUR 650,000 ratcheted floor: normal target EUR 2,000 net real per month.
- EUR 640,000 with a EUR 650,000 ratcheted floor: protected target EUR 1,800 net real per month.
- EUR 600,500 with a EUR 650,000 ratcheted floor: at most EUR 500 total real outflow; net received is lower after any tax or fee.
- EUR 599,999: hard stop and payout zero.

## Funding Protocol G

G receives the net target, allowed total outflow, current holdings, prices, weighted-average purchase costs, tax metadata, cash, transaction costs, and strategic allocation constraints. It returns proposed sales, estimated taxes and costs, delivered net amount, and post-trade weights.

The priority is lexicographic:

1. Never breach the applicable real floor.
2. Deliver as much of the net target as possible.
3. Respect instrument-level minimum holdings and portfolio allocation constraints.
4. Minimize current tax due.
5. Minimize commissions, spread, and unnecessary turnover.

Funding sources are considered in this order subject to those constraints:

1. Cash above the configured operational cash minimum.
2. Direct government securities explicitly marked eligible for the preferential tax rate.
3. Other ISINs ranked by marginal total cost per euro of net liquidity.
4. Pro-rata sales only when the preceding sources cannot meet the target.

For an ETF position, taxable gain uses weighted-average purchase cost for the ISIN. The engine does not pretend that individual purchases of the same ETF are freely selectable tax lots.

Positive ETF proceeds and ETF losses are tracked in separate tax categories. An ETF loss is not assumed to offset a positive ETF income amount. Preferential government-security taxation is applied only when the exact security metadata marks it eligible.

The minimal sale calculation for each instrument includes:

```text
gross_proceeds = units_sold * sale_price
embedded_gain = max(units_sold * (sale_price - weighted_average_cost), 0)
tax = embedded_gain * applicable_tax_rate
total_outflow = net_to_investor + tax + commission + slippage
```

If quantity, price, weighted-average cost, tax classification, or transaction-cost data are missing for an asset needed to fund the payout, that asset is ineligible for automatic sale. If no eligible funding source remains, G returns a partial payout or zero and records the reason.

## Portfolio input contract

Each snapshot contains:

- valuation timestamp and price timestamp;
- instrument name and ISIN;
- ticker used only for market-data acquisition;
- asset class and policy role;
- quantity, currency, EUR price, and EUR market value;
- weighted-average purchase cost in EUR;
- accumulated realized loss category, when available;
- ordinary or preferential tax classification;
- estimated spread and commission rule;
- minimum strategic weight and whether sale is permitted;
- cash balance and operational cash minimum;
- CPI observation, observation date, and publication date.

The attachment's 2026-08-19 VI holdings become a versioned fixture. They are not silently replaced by later prices. A live weekly snapshot is a separate file referencing the fixture version.

## Separation from the allocation engine

Rule E and Protocol G do not decide target beta. Damodaran ERP, Treasury/TIPS veto, Crisis Engine, Recovery Engine, and Morin–Bayes belong to the allocation engine.

The core reports both:

- the accounting beta proxy used in the previous workbook; and
- an empirical risk estimate when sufficient historical data are available.

It labels the accounting beta as a heuristic and does not classify high-yield credit as risk-free or beta zero in empirical calculations.

## Reproducibility and audit trail

Every run records:

- configuration version;
- input-file hashes;
- data observation and publication dates;
- code version when available;
- lifecycle state, E state, HWM, floor, requested payout, delivered payout;
- proposed trades, tax, costs, and rejection reasons;
- warnings about proxies or incomplete data.

Calculations are deterministic. Randomness is not used in this component.

## Error handling

The engine fails closed for payout execution:

- missing CPI, stale valuation, inconsistent totals, negative quantities, absent tax metadata, or an outflow that could breach the floor produce a structured error;
- structured errors always imply operational payout zero;
- shadow calculations may still report hypothetical results when explicitly marked non-executable.

Downloaded prices are cached under the project workspace. Network access is never required by unit tests.

## Interfaces and outputs

The core exposes:

- a Python API for policy and funding calculations;
- a command that reads versioned JSON inputs and writes a JSON result;
- a flat CSV summary suitable for the weekly workbook;
- no direct modification of the legacy workbook in this component.

Workbook integration begins only after the JSON and CSV outputs pass the acceptance tests.

## Acceptance tests

The core is accepted when:

- all lifecycle and capital-boundary examples above pass exactly;
- gross outflow never exceeds the applicable real-capital buffer;
- the HWM never decreases and the floor changes only at completed EUR 100,000 steps;
- missing required data produces `DATA_INCOMPLETE` and payout zero;
- repeated runs with identical inputs produce byte-equivalent normalized JSON results;
- ETF sales use ISIN-level weighted-average cost;
- an ETF loss is not used to offset positive ETF income;
- cash and sale quantities never become negative;
- proposed trades reconcile from initial portfolio value to final portfolio value, taxes, fees, and delivered payout;
- package tests run without internet access.

## Subsequent components

After this core is accepted, separate designs and plans will cover:

1. historical data ingestion and point-in-time signal alignment;
2. deterministic historical backtest for benchmark, Damodaran, Crisis/Recovery, and tax-aware architectures;
3. paired block bootstrap and regime-conditional Monte Carlo;
4. walk-forward parameter selection and constrained PIR optimization;
5. weekly workbook/report integration.
