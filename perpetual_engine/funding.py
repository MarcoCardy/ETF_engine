from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal

from perpetual_engine.models import (
    ZERO,
    DistributionState,
    FundingResult,
    FundingTrade,
    Holding,
    PolicyDecision,
    PortfolioSnapshot,
    TaxCategory,
)


def marginal_sale(holding: Holding, units: Decimal, first_trade: bool) -> FundingTrade:
    if holding.weighted_average_cost_eur is None:
        raise ValueError(f"{holding.symbol}: weighted_average_cost_eur is required")
    gross = units * holding.price_eur * holding.price_multiplier
    raw_gain = units * (holding.price_eur - holding.weighted_average_cost_eur) * holding.price_multiplier
    tax = max(raw_gain, ZERO) * holding.tax_rate
    spread = gross * holding.spread_bps / Decimal("10000")
    commission = holding.commission_eur if first_trade else ZERO
    net = gross - tax - spread - commission
    return FundingTrade(
        symbol=holding.symbol,
        isin=holding.isin,
        units=units,
        gross_proceeds_real=gross,
        tax_real=tax,
        commission_real=commission,
        spread_real=spread,
        net_liquidity_real=net,
    )


def _sort_key(holding: Holding) -> tuple:
    if holding.weighted_average_cost_eur is None:
        return (holding.funding_priority, Decimal("Infinity"), holding.spread_bps, holding.symbol)
    trade = marginal_sale(holding, holding.sale_increment, True)
    gross = trade.gross_proceeds_real
    marginal_cost = (
        (trade.tax_real + trade.commission_real + trade.spread_real) / gross
        if gross > ZERO
        else Decimal("Infinity")
    )
    return (holding.funding_priority, marginal_cost, holding.spread_bps, holding.symbol)


def _empty(errors: tuple[str, ...] = ()) -> FundingResult:
    return FundingResult(
        delivered_net_real=ZERO,
        cash_used_real=ZERO,
        total_outflow_real=ZERO,
        errors=errors,
    )


def fund_withdrawal(snapshot: PortfolioSnapshot, decision: PolicyDecision) -> FundingResult:
    if decision.state not in (DistributionState.NORMAL, DistributionState.PROTECTED):
        return _empty()
    cap = max(decision.maximum_total_outflow_real, ZERO)
    gross_budget = min(max(decision.target_gross_real, ZERO), cap)
    if gross_budget <= ZERO:
        return _empty()

    cash_excess = max(snapshot.cash_eur - snapshot.operational_cash_minimum_eur, ZERO)
    cash_used = min(cash_excess, gross_budget)
    delivered = cash_used
    total_outflow = cash_used
    remaining_budget = gross_budget - total_outflow

    tax_total = ZERO
    commission_total = ZERO
    spread_total = ZERO
    retained_total = ZERO
    etf_losses = ZERO
    sold_units: dict[str, Decimal] = {}
    accumulators: OrderedDict[str, dict[str, Decimal | str]] = OrderedDict()
    missing_pmc: list[str] = []

    holdings = sorted((h for h in snapshot.holdings if h.sale_permitted), key=_sort_key)
    for holding in holdings:
        if remaining_budget <= ZERO:
            break
        if holding.weighted_average_cost_eur is None:
            missing_pmc.append(holding.symbol)
            continue
        sold_units[holding.isin] = ZERO
        while remaining_budget > ZERO and sold_units[holding.isin] + holding.sale_increment <= holding.quantity:
            first_trade = sold_units[holding.isin] == ZERO
            trade = marginal_sale(holding, holding.sale_increment, first_trade)
            costs = trade.tax_real + trade.commission_real + trade.spread_real
            room = remaining_budget
            if trade.net_liquidity_real <= ZERO or costs >= room:
                break
            payout = min(trade.net_liquidity_real, room - costs)
            if payout <= ZERO:
                break

            proposed_units = sold_units[holding.isin] + holding.sale_increment
            remaining_value = (
                holding.quantity - proposed_units
            ) * holding.price_eur * holding.price_multiplier
            post_portfolio = snapshot.nominal_capital_eur - (total_outflow + costs + payout)
            if post_portfolio <= ZERO or remaining_value / post_portfolio < holding.minimum_weight:
                break

            retained = trade.net_liquidity_real - payout
            sold_units[holding.isin] = proposed_units
            delivered += payout
            total_outflow += payout + costs
            remaining_budget -= payout + costs
            tax_total += trade.tax_real
            commission_total += trade.commission_real
            spread_total += trade.spread_real
            retained_total += retained
            raw_gain = holding.sale_increment * (
                holding.price_eur - holding.weighted_average_cost_eur
            ) * holding.price_multiplier
            if holding.tax_category is TaxCategory.ETF and raw_gain < ZERO:
                etf_losses += -raw_gain

            acc = accumulators.setdefault(
                holding.isin,
                {
                    "symbol": holding.symbol,
                    "units": ZERO,
                    "gross": ZERO,
                    "tax": ZERO,
                    "commission": ZERO,
                    "spread": ZERO,
                    "net": ZERO,
                    "retained": ZERO,
                },
            )
            acc["units"] += holding.sale_increment
            acc["gross"] += trade.gross_proceeds_real
            acc["tax"] += trade.tax_real
            acc["commission"] += trade.commission_real
            acc["spread"] += trade.spread_real
            acc["net"] += trade.net_liquidity_real
            acc["retained"] += retained

    trades = tuple(
        FundingTrade(
            symbol=str(acc["symbol"]),
            isin=isin,
            units=acc["units"],
            gross_proceeds_real=acc["gross"],
            tax_real=acc["tax"],
            commission_real=acc["commission"],
            spread_real=acc["spread"],
            net_liquidity_real=acc["net"],
            retained_cash_real=acc["retained"],
        )
        for isin, acc in accumulators.items()
    )
    errors = [f"{symbol}: weighted_average_cost_eur is required" for symbol in missing_pmc]
    if remaining_budget > ZERO:
        errors.append(f"gross target not fully funded; remaining real gross {remaining_budget}")
    return FundingResult(
        delivered_net_real=delivered,
        cash_used_real=cash_used,
        total_outflow_real=total_outflow,
        tax_real=tax_total,
        commission_real=commission_total,
        spread_real=spread_total,
        retained_cash_real=retained_total,
        realized_etf_losses_real=etf_losses,
        trades=trades,
        errors=tuple(errors),
    )
