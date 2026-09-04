from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
from typing import Any


ZERO = Decimal("0")
CENT = Decimal("0.01")


def money(value: str | int | Decimal) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("money must be finite")
    return result


class Lifecycle(StrEnum):
    GROWTH = "GROWTH"
    DISTRIBUTION = "DISTRIBUTION"


class DistributionState(StrEnum):
    GROWTH = "GROWTH"
    NORMAL = "NORMAL"
    PROTECTED = "PROTECTED"
    HARD_STOP = "HARD_STOP"
    DATA_INCOMPLETE = "DATA_INCOMPLETE"


class TaxCategory(StrEnum):
    CASH = "CASH"
    GOVERNMENT = "GOVERNMENT"
    ETF = "ETF"
    OTHER = "OTHER"


@dataclass(frozen=True)
class PolicyConfig:
    version: str = "1.0"
    base_date: date = date(2026, 8, 19)
    base_real_capital: Decimal = Decimal("614590.86")
    initial_hwm: Decimal = Decimal("614590.86")
    hard_floor: Decimal = Decimal("600000")
    activation_threshold: Decimal = Decimal("800000")
    ratchet_hwm_step: Decimal = Decimal("100000")
    ratchet_floor_increment: Decimal = Decimal("50000")
    protected_monthly_gross: Decimal = Decimal("1800")
    normal_annual_rate: Decimal = Decimal("0.03")
    cpi_series: str = "ITACPALTT01IXNBM"

    @classmethod
    def default(cls) -> "PolicyConfig":
        return cls()

    def __post_init__(self) -> None:
        amounts = (
            self.base_real_capital,
            self.initial_hwm,
            self.hard_floor,
            self.activation_threshold,
            self.ratchet_hwm_step,
            self.ratchet_floor_increment,
            self.protected_monthly_gross,
        )
        if any(value < ZERO for value in amounts):
            raise ValueError("policy amounts must be non-negative")
        if self.hard_floor >= self.activation_threshold:
            raise ValueError("hard_floor must be below activation_threshold")
        if self.ratchet_hwm_step <= ZERO:
            raise ValueError("ratchet_hwm_step must be positive")
        if not ZERO <= self.normal_annual_rate <= Decimal("1"):
            raise ValueError("normal_annual_rate must be between 0 and 1")


@dataclass(frozen=True)
class CpiObservation:
    series: str
    observation_date: date
    publication_date: date
    value: Decimal

    def __post_init__(self) -> None:
        if self.value <= ZERO:
            raise ValueError("CPI value must be positive")
        if self.publication_date < self.observation_date:
            raise ValueError("CPI publication_date precedes observation_date")


@dataclass(frozen=True)
class Holding:
    symbol: str
    isin: str
    role: str
    quantity: Decimal
    price_eur: Decimal
    weighted_average_cost_eur: Decimal | None
    price_multiplier: Decimal
    sale_increment: Decimal
    tax_category: TaxCategory
    tax_rate: Decimal
    funding_priority: int
    spread_bps: Decimal
    commission_eur: Decimal
    minimum_weight: Decimal
    sale_permitted: bool
    price_date: date | None = None
    price_currency: str = "EUR"

    def __post_init__(self) -> None:
        if self.quantity < ZERO:
            raise ValueError("quantity must be non-negative")
        if self.price_eur < ZERO:
            raise ValueError("price_eur must be non-negative")
        if self.weighted_average_cost_eur is not None and self.weighted_average_cost_eur < ZERO:
            raise ValueError("weighted_average_cost_eur must be non-negative")
        if self.price_multiplier <= ZERO:
            raise ValueError("price_multiplier must be positive")
        if self.sale_increment <= ZERO:
            raise ValueError("sale_increment must be positive")
        if not ZERO <= self.tax_rate <= Decimal("1"):
            raise ValueError("tax_rate must be between 0 and 1")
        if self.funding_priority < 1:
            raise ValueError("funding_priority must be positive")
        if self.spread_bps < ZERO or self.commission_eur < ZERO:
            raise ValueError("transaction costs must be non-negative")
        if not ZERO <= self.minimum_weight <= Decimal("1"):
            raise ValueError("minimum_weight must be between 0 and 1")
        if not self.price_currency:
            raise ValueError("price_currency is required")

    @property
    def market_value_eur(self) -> Decimal:
        return self.quantity * self.price_eur * self.price_multiplier


@dataclass(frozen=True)
class PortfolioSnapshot:
    valuation_date: date
    price_date: date
    nominal_capital_eur: Decimal
    cash_eur: Decimal
    operational_cash_minimum_eur: Decimal
    holdings: tuple[Holding, ...] = ()
    cpi_base: CpiObservation | None = None
    cpi_current: CpiObservation | None = None
    previous_hwm_real: Decimal = Decimal("614590.86")

    def __post_init__(self) -> None:
        if self.nominal_capital_eur < ZERO or self.cash_eur < ZERO:
            raise ValueError("portfolio values must be non-negative")
        if self.operational_cash_minimum_eur < ZERO:
            raise ValueError("operational_cash_minimum_eur must be non-negative")
        if self.previous_hwm_real < ZERO:
            raise ValueError("previous_hwm_real must be non-negative")
        if self.price_date > self.valuation_date:
            raise ValueError("price_date cannot be after valuation_date")
        isins = [holding.isin for holding in self.holdings]
        if len(isins) != len(set(isins)):
            raise ValueError("duplicate ISIN in portfolio")
        calculated = self.cash_eur + sum((h.market_value_eur for h in self.holdings), ZERO)
        if abs(calculated - self.nominal_capital_eur) > CENT:
            raise ValueError("portfolio total does not reconcile")


@dataclass(frozen=True)
class PolicyDecision:
    lifecycle: Lifecycle
    state: DistributionState
    real_capital: Decimal
    hwm_real: Decimal
    ratcheted_floor_real: Decimal
    target_gross_real: Decimal
    maximum_total_outflow_real: Decimal
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class FundingTrade:
    symbol: str
    isin: str
    units: Decimal
    gross_proceeds_real: Decimal
    tax_real: Decimal
    commission_real: Decimal
    spread_real: Decimal
    net_liquidity_real: Decimal
    retained_cash_real: Decimal = ZERO


@dataclass(frozen=True)
class FundingResult:
    delivered_net_real: Decimal
    cash_used_real: Decimal
    total_outflow_real: Decimal
    tax_real: Decimal = ZERO
    commission_real: Decimal = ZERO
    spread_real: Decimal = ZERO
    retained_cash_real: Decimal = ZERO
    realized_etf_losses_real: Decimal = ZERO
    trades: tuple[FundingTrade, ...] = ()
    errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class RunResult:
    config_version: str
    policy: PolicyDecision
    funding: FundingResult
    input_hashes: dict[str, str] = field(default_factory=dict)
    errors: tuple[str, ...] = ()


def to_primitive(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value.quantize(CENT, rounding=ROUND_HALF_UP), "f")
    if isinstance(value, (date, StrEnum)):
        return str(value)
    if hasattr(value, "__dataclass_fields__"):
        return {name: to_primitive(getattr(value, name)) for name in value.__dataclass_fields__}
    if isinstance(value, tuple):
        return [to_primitive(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_primitive(item) for key, item in value.items()}
    return value
