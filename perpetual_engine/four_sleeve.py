from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from types import MappingProxyType
from typing import Any, Mapping

from perpetual_engine.io import canonical_json


ZERO = Decimal("0")
ONE = Decimal("1")
SLEEVES = ("WORLD", "MOMENTUM", "QUALITY", "TREND")
CANDIDATES: Mapping[str, tuple[Decimal, ...]] = MappingProxyType(
    {
        "BASELINE_60_15_15_10": (Decimal("0.60"), Decimal("0.15"), Decimal("0.15"), Decimal("0.10")),
        "THREE_EQUITY_FIXED": (
            Decimal("0.330470588235"),
            Decimal("0.266352941176"),
            Decimal("0.403176470589"),
            ZERO,
        ),
        "PRUDENT_TREND_10": (
            Decimal("0.297423529412"),
            Decimal("0.239717647059"),
            Decimal("0.362858823529"),
            Decimal("0.10"),
        ),
        "ERC_TREND_15": (Decimal("0.2809"), Decimal("0.2264"), Decimal("0.3427"), Decimal("0.1500")),
        "ERC_TREND_20": (Decimal("0.2653"), Decimal("0.2103"), Decimal("0.3244"), Decimal("0.2000")),
        "ERC_FREE": (Decimal("0.2199"), Decimal("0.1536"), Decimal("0.2528"), Decimal("0.3737")),
    }
)
WORLD_BENCHMARK = "WORLD_100"
PORTFOLIOS: Mapping[str, tuple[Decimal, ...]] = MappingProxyType(
    {WORLD_BENCHMARK: (ONE, ZERO, ZERO, ZERO), **CANDIDATES}
)


def _month_end(value: date) -> date:
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1) - timedelta(days=1)


def _next_month(value: date) -> date:
    return _month_end(date(value.year + (value.month == 12), value.month % 12 + 1, 1))


def _calendar(start: date, end: date) -> tuple[date, ...]:
    months: list[date] = []
    current = start
    while current <= end:
        months.append(current)
        current = _next_month(current)
    return tuple(months)


@dataclass(frozen=True)
class FourSleeveConfig:
    explicit_start: date
    explicit_end: date
    retrieved_at: datetime
    max_staleness_days: int = 45
    initial_capital_eur: Decimal = Decimal("800000")
    commission_per_order_eur: Decimal = Decimal("19")
    spread_slippage_bps: Decimal = Decimal("10")
    config_id: str = "FOUR_SLEEVE_V1"

    def __post_init__(self) -> None:
        if self.explicit_start != _month_end(self.explicit_start):
            raise ValueError("explicit start must be a month-end")
        if self.explicit_end != _month_end(self.explicit_end) or self.explicit_end < self.explicit_start:
            raise ValueError("explicit final limit must be a month-end on or after explicit start")
        if self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        if not isinstance(self.max_staleness_days, int) or isinstance(self.max_staleness_days, bool) or self.max_staleness_days < 0:
            raise ValueError("max_staleness_days must be a non-negative integer")
        for name in ("initial_capital_eur", "commission_per_order_eur", "spread_slippage_bps"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{name} must be a finite Decimal")
        if self.initial_capital_eur <= ZERO:
            raise ValueError("initial_capital_eur must be positive")
        if self.commission_per_order_eur < ZERO or self.spread_slippage_bps < ZERO:
            raise ValueError("costs must be non-negative")


@dataclass(frozen=True)
class FourSleeveRow:
    strategy: str
    month: date
    trigger: str
    pretrade_nav: Decimal
    pretrade_values: tuple[Decimal, ...]
    target_values: tuple[Decimal, ...]
    trades: tuple[Decimal, ...]
    order_count: int
    commissions_eur: Decimal
    spread_eur: Decimal
    turnover: Decimal
    sleeve_returns: tuple[Decimal, ...]
    ending_values: tuple[Decimal, ...]
    ending_nav: Decimal
    portfolio_return: Decimal


@dataclass(frozen=True)
class FourSleeveSummary:
    strategy: str
    observations: int
    ending_nav: Decimal
    cagr: float
    annualized_volatility: float | None
    return_volatility_ratio: float | None
    max_drawdown: float
    turnover: Decimal
    commissions_eur: Decimal
    spread_eur: Decimal


@dataclass(frozen=True)
class FourSleeveResult:
    strategies: tuple[str, ...]
    sleeves: tuple[str, ...]
    rows: tuple[FourSleeveRow, ...]
    summaries: tuple[FourSleeveSummary, ...]
    config: FourSleeveConfig

    def rows_for(self, strategy: str) -> tuple[FourSleeveRow, ...]:
        if strategy not in self.strategies:
            raise ValueError("unknown strategy")
        return tuple(row for row in self.rows if row.strategy == strategy)


def _validate_inputs(
    returns: Mapping[str, Mapping[date, Decimal]], config: FourSleeveConfig
) -> tuple[date, ...]:
    if tuple(returns) != SLEEVES:
        raise ValueError("returns must contain the four sleeves in frozen order")
    expected = _calendar(config.explicit_start, config.explicit_end)
    if not expected:
        raise ValueError("empty calendar")
    if (config.retrieved_at.astimezone(timezone.utc).date() - config.explicit_end).days > config.max_staleness_days:
        raise ValueError("explicit final limit is stale")
    calendars = tuple(tuple(sorted(series)) for series in returns.values())
    if any(calendar and calendar[0] != config.explicit_start for calendar in calendars):
        raise ValueError("series does not reach explicit start")
    if any(calendar and calendar[-1] != config.explicit_end for calendar in calendars):
        raise ValueError("series does not reach explicit final limit")
    if any(calendar != expected for calendar in calendars):
        raise ValueError("all sleeves must contain the same complete calendar")
    for sleeve, series in returns.items():
        for month in expected:
            value = series[month]
            if not isinstance(value, Decimal) or not value.is_finite():
                raise ValueError(f"{sleeve} return must be a finite Decimal")
            if value <= -ONE:
                raise ValueError(f"{sleeve} return must be greater than -100%")
    for strategy, weights in PORTFOLIOS.items():
        if len(weights) != len(SLEEVES) or any(weight < ZERO for weight in weights) or sum(weights) != ONE:
            raise ValueError(f"{strategy} weights must be non-negative and sum to one")
    return expected


def _summary(strategy: str, rows: tuple[FourSleeveRow, ...], initial_capital: Decimal) -> FourSleeveSummary:
    monthly = [float(row.portfolio_return) for row in rows]
    observations = len(rows)
    cagr = float(rows[-1].ending_nav / initial_capital) ** (12 / observations) - 1
    volatility = statistics.stdev(monthly) * math.sqrt(12) if observations > 1 else None
    mean = statistics.mean(monthly)
    return_volatility_ratio = mean / statistics.stdev(monthly) * math.sqrt(12) if observations > 1 and statistics.stdev(monthly) else None
    peak = float(initial_capital)
    max_drawdown = 0.0
    for row in rows:
        nav = float(row.ending_nav)
        peak = max(peak, nav)
        max_drawdown = min(max_drawdown, nav / peak - 1)
    return FourSleeveSummary(
        strategy,
        observations,
        rows[-1].ending_nav,
        cagr,
        volatility,
        return_volatility_ratio,
        max_drawdown,
        sum((row.turnover for row in rows), ZERO),
        sum((row.commissions_eur for row in rows), ZERO),
        sum((row.spread_eur for row in rows), ZERO),
    )


def run_four_sleeve(
    returns: Mapping[str, Mapping[date, Decimal]], config: FourSleeveConfig
) -> FourSleeveResult:
    months = _validate_inputs(returns, config)
    states = {strategy: (ZERO,) * len(SLEEVES) for strategy in PORTFOLIOS}
    rows: list[FourSleeveRow] = []
    with localcontext() as context:
        context.prec = 50
        context.rounding = ROUND_HALF_EVEN
        for index, month in enumerate(months):
            for strategy, weights in PORTFOLIOS.items():
                pretrade = states[strategy]
                pretrade_nav = config.initial_capital_eur if index == 0 else sum(pretrade, ZERO)
                rebalance = index == 0 or month.month == 1
                targets = tuple(pretrade_nav * weight for weight in weights) if rebalance else pretrade
                trades = tuple(target - current for target, current in zip(targets, pretrade))
                active = tuple(trade != ZERO for trade in trades)
                order_count = sum(active)
                trigger = "INCEPTION" if index == 0 else "JANUARY_REBALANCE" if order_count else "JANUARY_REVIEW_NO_TRADE" if rebalance else "HOLD"
                commissions = config.commission_per_order_eur * order_count
                spreads = tuple(abs(trade) * config.spread_slippage_bps / Decimal("10000") for trade in trades)
                posttrade = tuple(
                    target - (config.commission_per_order_eur if is_active else ZERO) - spread
                    for target, is_active, spread in zip(targets, active, spreads)
                )
                if any(value < ZERO for value in posttrade):
                    raise ValueError("COST_EXCEEDS_SLEEVE_VALUE")
                sleeve_returns = tuple(returns[sleeve][month] for sleeve in SLEEVES)
                ending = tuple(value * (ONE + outcome) for value, outcome in zip(posttrade, sleeve_returns))
                ending_nav = sum(ending, ZERO)
                portfolio_return = ending_nav / pretrade_nav - ONE
                turnover = sum((abs(trade) for trade in trades), ZERO) / pretrade_nav
                states[strategy] = ending
                rows.append(
                    FourSleeveRow(
                        strategy,
                        month,
                        trigger,
                        pretrade_nav,
                        pretrade,
                        targets,
                        trades,
                        order_count,
                        commissions,
                        sum(spreads, ZERO),
                        turnover,
                        sleeve_returns,
                        ending,
                        ending_nav,
                        portfolio_return,
                    )
                )
    frozen_rows = tuple(rows)
    summaries = tuple(
        _summary(strategy, tuple(row for row in frozen_rows if row.strategy == strategy), config.initial_capital_eur)
        for strategy in PORTFOLIOS
    )
    return FourSleeveResult(tuple(PORTFOLIOS), SLEEVES, frozen_rows, summaries, config)


def _primitive(value: Any) -> Any:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if is_dataclass(value):
        return {field.name: _primitive(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, tuple):
        return [_primitive(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _primitive(item) for key, item in value.items()}
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("normalized JSON does not permit non-finite values")
    return value


def normalized_four_sleeve_json(result: FourSleeveResult) -> bytes:
    if not isinstance(result, FourSleeveResult):
        raise ValueError("result must be a FourSleeveResult")
    return canonical_json(_primitive(result))
