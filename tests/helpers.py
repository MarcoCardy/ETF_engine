from dataclasses import replace
from datetime import date

from perpetual_engine.models import (
    CpiObservation,
    DistributionState,
    FundingResult,
    Holding,
    Lifecycle,
    PolicyDecision,
    PortfolioSnapshot,
    RunResult,
    TaxCategory,
    money,
)


def cpi(value="100", *, observation_date=date(2026, 7, 1), publication_date=date(2026, 8, 19)):
    return CpiObservation(
        series="ITACPALTT01IXNBM",
        observation_date=observation_date,
        publication_date=publication_date,
        value=money(value),
    )


def holding(**overrides) -> Holding:
    base = Holding(
        symbol="ETF",
        isin="IE0000000001",
        role="CORE",
        quantity=money("100"),
        price_eur=money("100"),
        weighted_average_cost_eur=money("90"),
        price_multiplier=money("1"),
        sale_increment=money("1"),
        tax_category=TaxCategory.ETF,
        tax_rate=money("0.26"),
        funding_priority=3,
        spread_bps=money("10"),
        commission_eur=money("19"),
        minimum_weight=money("0"),
        sale_permitted=True,
    )
    return replace(base, **overrides)


def snapshot(
    *,
    cash="1000",
    cash_minimum="500",
    holdings=(),
    cpi_base=None,
    cpi_current=None,
    previous_hwm="614590.86",
) -> PortfolioSnapshot:
    cash_value = money(cash)
    positions = tuple(holdings)
    total = cash_value + sum((item.market_value_eur for item in positions), money("0"))
    return PortfolioSnapshot(
        valuation_date=date(2026, 8, 19),
        price_date=date(2026, 8, 19),
        nominal_capital_eur=total,
        cash_eur=cash_value,
        operational_cash_minimum_eur=money(cash_minimum),
        holdings=positions,
        cpi_base=cpi_base,
        cpi_current=cpi_current,
        previous_hwm_real=money(previous_hwm),
    )


def taxable_snapshot(**holding_overrides) -> PortfolioSnapshot:
    return snapshot(cash="0", cash_minimum="0", holdings=(holding(**holding_overrides),))


def decision(*, target, cap, state=DistributionState.PROTECTED) -> PolicyDecision:
    return PolicyDecision(
        lifecycle=Lifecycle.DISTRIBUTION,
        state=state,
        real_capital=money("640000"),
        hwm_real=money("720000"),
        ratcheted_floor_real=money("650000"),
        target_gross_real=money(target),
        maximum_total_outflow_real=money(cap),
    )


def sample_result() -> RunResult:
    return RunResult(
        config_version="1.0",
        policy=decision(target="0", cap="0", state=DistributionState.GROWTH),
        funding=FundingResult(
            delivered_net_real=money("0"),
            cash_used_real=money("0"),
            total_outflow_real=money("0"),
        ),
        input_hashes={"config": "abc", "snapshot": "def"},
    )
