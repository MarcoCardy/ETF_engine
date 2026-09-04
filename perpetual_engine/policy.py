from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal, ROUND_DOWN

from perpetual_engine.models import (
    CENT,
    ZERO,
    DistributionState,
    Lifecycle,
    PolicyConfig,
    PolicyDecision,
    PortfolioSnapshot,
)


def _cents(value: Decimal) -> Decimal:
    return max(value, ZERO).quantize(CENT, rounding=ROUND_DOWN)


def real_capital(nominal: Decimal, cpi_base: Decimal, cpi_current: Decimal) -> Decimal:
    if cpi_base <= ZERO or cpi_current <= ZERO:
        raise ValueError("CPI values must be positive")
    return nominal * cpi_base / cpi_current


def update_hwm(previous_hwm: Decimal, pre_distribution_capital: Decimal) -> Decimal:
    return max(previous_hwm, pre_distribution_capital)


def ratcheted_floor(hwm: Decimal, config: PolicyConfig) -> Decimal:
    gain = max(hwm - config.initial_hwm, ZERO)
    steps = gain // config.ratchet_hwm_step
    return config.hard_floor + steps * config.ratchet_floor_increment


def target_gross_real(
    capital: Decimal,
    floor: Decimal,
    lifecycle: Lifecycle,
    config: PolicyConfig,
) -> PolicyDecision:
    common = {
        "lifecycle": lifecycle,
        "real_capital": capital,
        "hwm_real": capital,
        "ratcheted_floor_real": floor,
        "errors": (),
    }
    if lifecycle is Lifecycle.GROWTH:
        return PolicyDecision(
            state=DistributionState.GROWTH,
            target_gross_real=ZERO,
            maximum_total_outflow_real=ZERO,
            **common,
        )
    if capital < config.hard_floor:
        return PolicyDecision(
            state=DistributionState.HARD_STOP,
            target_gross_real=ZERO,
            maximum_total_outflow_real=ZERO,
            **common,
        )
    if capital < floor:
        return PolicyDecision(
            state=DistributionState.PROTECTED,
            target_gross_real=_cents(config.protected_monthly_gross),
            maximum_total_outflow_real=_cents(capital - config.hard_floor),
            **common,
        )
    return PolicyDecision(
        state=DistributionState.NORMAL,
        target_gross_real=_cents(capital * config.normal_annual_rate / Decimal("12")),
        maximum_total_outflow_real=_cents(capital - floor),
        **common,
    )


def _incomplete(snapshot: PortfolioSnapshot, config: PolicyConfig, message: str, lifecycle: Lifecycle) -> PolicyDecision:
    floor = ratcheted_floor(snapshot.previous_hwm_real, config)
    return PolicyDecision(
        lifecycle=lifecycle,
        state=DistributionState.DATA_INCOMPLETE,
        real_capital=ZERO,
        hwm_real=snapshot.previous_hwm_real,
        ratcheted_floor_real=floor,
        target_gross_real=ZERO,
        maximum_total_outflow_real=ZERO,
        errors=(message,),
    )


def evaluate_policy(
    snapshot: PortfolioSnapshot,
    config: PolicyConfig,
    activation_date: date | None,
) -> PolicyDecision:
    lifecycle = Lifecycle.DISTRIBUTION if activation_date else Lifecycle.GROWTH
    if snapshot.cpi_base is None or snapshot.cpi_current is None:
        return _incomplete(snapshot, config, "CPI base and current observations are required", lifecycle)
    if snapshot.cpi_base.series != config.cpi_series or snapshot.cpi_current.series != config.cpi_series:
        return _incomplete(snapshot, config, "CPI series does not match policy configuration", lifecycle)
    if snapshot.cpi_current.publication_date > snapshot.valuation_date:
        return _incomplete(snapshot, config, "CPI publication date is after valuation date", lifecycle)

    capital = real_capital(
        snapshot.nominal_capital_eur,
        snapshot.cpi_base.value,
        snapshot.cpi_current.value,
    )
    hwm = update_hwm(snapshot.previous_hwm_real, capital)
    floor = ratcheted_floor(hwm, config)

    if activation_date is not None:
        if activation_date > snapshot.valuation_date:
            return _incomplete(snapshot, config, "activation date is after valuation date", lifecycle)
        if activation_date == snapshot.valuation_date and capital < config.activation_threshold:
            return _incomplete(snapshot, config, "activation threshold was not reached", lifecycle)

    decision = target_gross_real(capital, floor, lifecycle, config)
    return replace(decision, hwm_real=hwm, real_capital=capital, ratcheted_floor_real=floor)
