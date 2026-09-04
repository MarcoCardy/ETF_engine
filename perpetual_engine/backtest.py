from __future__ import annotations

import math
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from typing import Any, Mapping, Sequence

from perpetual_engine.allocation import AllocationState, SignalSnapshot, next_allocation
from perpetual_engine.io import canonical_json
from perpetual_engine.leveraged_proxy import LeveragedReturn


ZERO = Decimal("0")
ONE = Decimal("1")
CENT = Decimal("0.01")
DECIMAL_PRECISION = 50
RECONCILIATION_TOLERANCE = Decimal("1e-24")


def _annual_fee_month(annual_fee: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        return (ONE - annual_fee) ** (ONE / Decimal("12")) - ONE


WORLD_FEE_MONTH = _annual_fee_month(Decimal("0.002"))
LEVERAGED_FEE_MONTH = _annual_fee_month(Decimal("0.006"))

WORLD_BENCHMARK = "WORLD_BENCHMARK"
FIXED_60_20_20 = "FIXED_60_20_20"
DAMODARAN_ONLY = "DAMODARAN_ONLY"
DAMODARAN_CRISIS_RECOVERY = "DAMODARAN_CRISIS_RECOVERY"
PHASE2_COMPLETE = "PHASE2_COMPLETE"
STRATEGIES = (
    WORLD_BENCHMARK,
    FIXED_60_20_20,
    DAMODARAN_ONLY,
    DAMODARAN_CRISIS_RECOVERY,
    PHASE2_COMPLETE,
)
_TACTICAL = STRATEGIES[2:]


def _finite(value: Decimal, field: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field} must be a finite Decimal")
    return value


@dataclass(frozen=True)
class BacktestConfig:
    initial_capital_eur: Decimal = Decimal("800000")
    commission_per_order_eur: Decimal = Decimal("19")
    spread_slippage_bps: Decimal = Decimal("10")
    config_id: str = "BACKTEST_V1"

    def __post_init__(self) -> None:
        for field in ("initial_capital_eur", "commission_per_order_eur", "spread_slippage_bps"):
            _finite(getattr(self, field), field)
        if self.initial_capital_eur <= ZERO:
            raise ValueError("initial_capital_eur must be positive")
        if self.commission_per_order_eur < ZERO or self.spread_slippage_bps < ZERO:
            raise ValueError("cost configuration must be non-negative")
        if not isinstance(self.config_id, str) or not self.config_id:
            raise ValueError("config_id is required")


@dataclass(frozen=True)
class CostDecomposition:
    commissions_eur: Decimal
    spread_eur: Decimal
    funding_eur: Decimal
    world_fee_eur: Decimal
    leveraged_fee_eur: Decimal
    residual_drag_eur: Decimal


@dataclass(frozen=True)
class SummaryMetrics:
    strategy: str
    observations: int
    cagr: float
    annualized_volatility: float | None
    sharpe: float | None
    max_drawdown: float
    turnover: Decimal
    costs: CostDecomposition


@dataclass(frozen=True)
class BacktestMetadata:
    config_id: str
    initial_capital_eur: Decimal
    commission_per_executed_sleeve_order_eur: Decimal
    spread_slippage_bps: Decimal
    spread_scenario_id: str
    world_fee_scenario_id: str
    leveraged_fee_scenario_id: str
    capital_unit: str = "NOMINAL_MODEL_EUR"
    tax_treatment: str = "PRE_TAX"
    objective: str = "ACCUMULATION_GROWTH"
    distributions_eur: Decimal = ZERO
    world_annual_fee: Decimal = Decimal("0.002")
    leveraged_annual_fee: Decimal = Decimal("0.006")


@dataclass(frozen=True)
class BacktestRow:
    strategy: str
    month: date
    decision_at: datetime
    state: str
    trigger: str
    accepted_final_beta: Decimal | None
    actual_beta: Decimal | None
    allocation_flags: tuple[str, ...]
    leveraged_flags: tuple[str, ...]
    pretrade_nav: Decimal
    pretrade_cash: Decimal
    pretrade_core: Decimal
    pretrade_overlay: Decimal
    pretrade_defensive: Decimal
    pretrade_core_weight: Decimal
    pretrade_overlay_weight: Decimal
    pretrade_defensive_weight: Decimal
    target_core: Decimal
    target_overlay: Decimal
    target_defensive: Decimal
    target_core_weight: Decimal
    target_overlay_weight: Decimal
    target_defensive_weight: Decimal
    core_trade: Decimal
    overlay_trade: Decimal
    defensive_trade: Decimal
    core_commission: Decimal
    overlay_commission: Decimal
    defensive_commission: Decimal
    core_spread: Decimal
    overlay_spread: Decimal
    defensive_spread: Decimal
    order_count: int
    post_cost_nav: Decimal
    post_cost_core: Decimal
    post_cost_overlay: Decimal
    post_cost_defensive: Decimal
    raw_world_return: Decimal
    world_fee_rate: Decimal
    world_investable_return: Decimal
    leveraged_return: Decimal
    leveraged_funding_rate: Decimal
    leveraged_fee_rate: Decimal
    leveraged_residual_drag_rate: Decimal
    defensive_return: Decimal
    ending_core: Decimal
    ending_overlay: Decimal
    ending_defensive: Decimal
    ending_nav: Decimal
    portfolio_return: Decimal
    core_turnover: Decimal
    overlay_turnover: Decimal
    defensive_turnover: Decimal
    world_fee_eur: Decimal
    funding_eur: Decimal
    leveraged_fee_eur: Decimal
    residual_drag_eur: Decimal
    leveraged_underlying_effect_eur: Decimal
    leveraged_fx_effect_eur: Decimal
    leveraged_wipeout_effect_eur: Decimal
    leveraged_total_effect_eur: Decimal

    @property
    def commission_total(self) -> Decimal:
        with localcontext() as context:
            context.prec = DECIMAL_PRECISION
            context.rounding = ROUND_HALF_EVEN
            return self.core_commission + self.overlay_commission + self.defensive_commission

    @property
    def spread_total(self) -> Decimal:
        with localcontext() as context:
            context.prec = DECIMAL_PRECISION
            context.rounding = ROUND_HALF_EVEN
            return self.core_spread + self.overlay_spread + self.defensive_spread


@dataclass(frozen=True)
class BacktestResult:
    strategies: tuple[str, ...]
    rows: tuple[BacktestRow, ...]
    summaries: tuple[SummaryMetrics, ...]
    metadata: BacktestMetadata

    def rows_for(self, strategy: str) -> tuple[BacktestRow, ...]:
        if strategy not in STRATEGIES:
            raise ValueError("unknown strategy")
        return tuple(row for row in self.rows if row.strategy == strategy)

    def summary_for(self, strategy: str) -> SummaryMetrics:
        if strategy not in STRATEGIES:
            raise ValueError("unknown strategy")
        return next(summary for summary in self.summaries if summary.strategy == strategy)


@dataclass
class _StrategyState:
    core: Decimal = ZERO
    overlay: Decimal = ZERO
    defensive: Decimal = ZERO
    allocation: AllocationState | None = None


def _month_end(value: date) -> date:
    following = date(value.year + (value.month == 12), value.month % 12 + 1, 1)
    return following - timedelta(days=1)


def _next_month(value: date) -> date:
    following = date(value.year + (value.month == 12), value.month % 12 + 1, 1)
    return _month_end(following)


def _expected_decision(month: date) -> datetime:
    prior = month.replace(day=1) - timedelta(days=1)
    return datetime(prior.year, prior.month, prior.day, 23, 59, 59, tzinfo=timezone.utc)


def _reconciles(left: Decimal, right: Decimal) -> bool:
    scale = max(ONE, abs(left), abs(right))
    return abs(left - right) <= RECONCILIATION_TOLERANCE * scale


def _validate_inputs(
    signals: Mapping[date, SignalSnapshot],
    world_returns: Mapping[date, Decimal],
    defensive_returns: Mapping[date, Decimal],
    leveraged_returns: Mapping[date, LeveragedReturn],
) -> tuple[date, ...]:
    months = tuple(sorted(signals))
    if not months or any(not isinstance(month, date) or isinstance(month, datetime) or month != _month_end(month) for month in months):
        raise ValueError("calendar keys must be unique month-ends")
    if any(current != _next_month(previous) for previous, current in zip(months, months[1:])):
        raise ValueError("input calendar must be contiguous")
    if months[0] != date(1979, 1, 31):
        raise ValueError("primary backtest history must start in January 1979")
    if any(set(series) != set(months) for series in (world_returns, defensive_returns, leveraged_returns)):
        raise ValueError("required inputs must contain the same months")
    wiped_out = False
    for month in months:
        snapshot = signals[month]
        if not isinstance(snapshot, SignalSnapshot) or snapshot.decision_at != _expected_decision(month):
            raise ValueError("SignalSnapshot.decision_at must be the prior month final instant")
        for name, value in (("raw World return", world_returns[month]), ("defensive return", defensive_returns[month])):
            _finite(value, name)
            if value <= -ONE:
                raise ValueError(f"{name} must be greater than -100%")
        item = leveraged_returns[month]
        if not isinstance(item, LeveragedReturn):
            raise ValueError("leveraged returns must be LeveragedReturn records")
        for name in ("underlying", "funding", "residual_drag", "fx", "etf_fee", "return_usd", "total_return"):
            _finite(getattr(item, name), f"leveraged {name}")
        gross = item.underlying + item.funding
        reset = "CAPITAL_RESET" in item.flags
        if gross <= -ONE and not item.wiped_out:
            raise ValueError("gross at or below -100% requires a coherent wiped-out record (wipeout)")
        if gross > -ONE:
            if reset and item.wiped_out:
                raise ValueError("CAPITAL_RESET must reapply the gross wipeout rule")
            if wiped_out and not reset and not item.wiped_out:
                raise ValueError("a wiped-out leveraged sleeve cannot recover without an explicit CAPITAL_RESET")
            if not wiped_out and item.wiped_out:
                raise ValueError("gross above -100% forbids a first wiped-out record (wipeout)")
        if item.wiped_out and (
            item.total_return != -ONE
            or item.return_usd != -ONE
            or any(value != ZERO for value in (item.residual_drag, item.fx, item.etf_fee))
            or "LEVERAGED_SLEEVE_WIPEOUT" not in item.flags
        ):
            raise ValueError("wiped-out record has incoherent downstream components (wipeout)")
        if not item.wiped_out and not _reconciles(item.etf_fee, LEVERAGED_FEE_MONTH):
            raise ValueError("leveraged investable input must contain the exact monthly 0.60% fee layer")
        if not item.wiped_out:
            expected_return_usd = (ONE + gross) * (ONE + item.residual_drag) - ONE
            before_fee = item.return_usd + item.fx
            expected_total = (ONE + before_fee) * (ONE + item.etf_fee) - ONE
            if not _reconciles(item.return_usd, expected_return_usd) or not _reconciles(item.total_return, expected_total):
                raise ValueError("leveraged return layers do not reconcile")
        wiped_out = item.wiped_out
    return months


def _strategy_signal(strategy: str, snapshot: SignalSnapshot) -> SignalSnapshot:
    if strategy == DAMODARAN_ONLY:
        return replace(
            snapshot,
            tips=None,
            tips_percentile=None,
            tips_history_count=0,
            drawdown=None,
            momentum_1m=None,
            momentum_3m=None,
            volatility_12m=None,
            market_history_count=0,
        )
    if strategy == DAMODARAN_CRISIS_RECOVERY:
        return replace(snapshot, tips=None, tips_percentile=None, tips_history_count=0)
    return snapshot


def _weights(state: _StrategyState, nav: Decimal, *, inception: bool) -> tuple[Decimal, Decimal, Decimal]:
    if inception:
        return ZERO, ZERO, ZERO
    return state.core / nav, state.overlay / nav, state.defensive / nav


def _targets(
    strategy: str,
    month: date,
    snapshot: SignalSnapshot,
    state: _StrategyState,
    nav: Decimal,
    inception: bool,
) -> tuple[tuple[Decimal, Decimal, Decimal], str, str, Decimal | None, Decimal | None, AllocationState | None]:
    if strategy == WORLD_BENCHMARK:
        targets = (nav, ZERO, ZERO) if inception else (state.core, state.overlay, state.defensive)
        return targets, "BUY_AND_HOLD", "INCEPTION" if inception else "HOLD", None, None, None
    if strategy == FIXED_60_20_20:
        rebalance = inception or month.month == 1
        targets = (
            (nav * Decimal("0.60"), nav * Decimal("0.20"), nav * Decimal("0.20"))
            if rebalance
            else (state.core, state.overlay, state.defensive)
        )
        trigger = "INCEPTION" if inception else "JANUARY_REBALANCE" if rebalance else "HOLD"
        actual_beta = targets[0] / nav + Decimal("2") * targets[1] / nav
        return targets, "FIXED", trigger, Decimal("1.00"), actual_beta, None

    current_weights = _weights(state, nav, inception=inception)
    allocation = next_allocation(
        state.allocation,
        _strategy_signal(strategy, snapshot),
        core_weight_pretrade=Decimal("0.60") if inception else current_weights[0],
        overlay_weight_pretrade=None if inception else current_weights[1],
        defensive_weight_pretrade=None if inception else current_weights[2],
    )
    if inception:
        targets = (
            nav * allocation.core_weight,
            nav * (allocation.overlay_weight or ZERO),
            nav * (allocation.defensive_weight or ZERO),
        )
        trigger = "INCEPTION"
    elif allocation.trade_decision == "HOLD":
        targets = (state.core, state.overlay, state.defensive)
        trigger = "HOLD"
    else:
        targets = (
            state.core,
            nav * (allocation.overlay_weight or ZERO),
            nav * (allocation.defensive_weight or ZERO),
        )
        trigger = "TRADE"
    return targets, allocation.state, trigger, allocation.accepted_final_beta, allocation.actual_beta, allocation


def _trade_cost(trade: Decimal, config: BacktestConfig) -> tuple[Decimal, Decimal, bool]:
    order = abs(trade) >= CENT
    if not order:
        return ZERO, ZERO, False
    return config.commission_per_order_eur, abs(trade) * config.spread_slippage_bps / Decimal("10000"), True


def _row(
    strategy: str,
    month: date,
    snapshot: SignalSnapshot,
    state: _StrategyState,
    raw_world: Decimal,
    defensive_return: Decimal,
    leveraged_return: LeveragedReturn,
    config: BacktestConfig,
    *,
    inception: bool,
    leveraged_wipeout_locked: bool,
) -> BacktestRow:
    pretrade_cash = config.initial_capital_eur if inception else ZERO
    pretrade_nav = pretrade_cash if inception else state.core + state.overlay + state.defensive
    if pretrade_nav <= ZERO:
        raise ValueError("pretrade NAV must be positive")
    pretrade_weights = _weights(state, pretrade_nav, inception=inception)
    targets, allocation_state, trigger, accepted_beta, actual_beta, allocation = _targets(
        strategy, month, snapshot, state, pretrade_nav, inception
    )
    if leveraged_wipeout_locked and strategy != WORLD_BENCHMARK:
        targets = (targets[0], ZERO, pretrade_nav - targets[0])
        actual_beta = targets[0] / pretrade_nav
    if any(value < ZERO for value in targets) or abs(sum(targets, ZERO) - pretrade_nav) > Decimal("1e-12"):
        raise ValueError("target weights violate no-borrowing or sum-to-one invariant")
    target_weights = tuple(value / pretrade_nav for value in targets)
    if abs(sum(target_weights, ZERO) - ONE) > Decimal("1e-12"):
        raise ValueError("target weights must sum to one within 1e-12")
    trades = tuple(target - pretrade for target, pretrade in zip(targets, (state.core, state.overlay, state.defensive)))
    costs = tuple(_trade_cost(trade, config) for trade in trades)
    post_cost = tuple(target - commission - spread for target, (commission, spread, _) in zip(targets, costs))
    if any(value < ZERO for value in post_cost):
        raise ValueError("COST_EXCEEDS_SLEEVE_VALUE")

    world_investable = (ONE + raw_world) * (ONE + WORLD_FEE_MONTH) - ONE
    ending = (
        post_cost[0] * (ONE + world_investable),
        post_cost[1] * (ONE + leveraged_return.total_return),
        post_cost[2] * (ONE + defensive_return),
    )
    ending_nav = sum(ending, ZERO)
    post_cost_nav = sum(post_cost, ZERO)
    trades_turnover = tuple(abs(trade) / pretrade_nav for trade in trades)
    gross_leveraged = leveraged_return.underlying + leveraged_return.funding
    before_leveraged_fee = leveraged_return.return_usd + leveraged_return.fx
    if leveraged_return.wiped_out:
        funding_eur = -post_cost[1] * leveraged_return.funding
        residual_drag_eur = leveraged_fee_eur = ZERO
        leveraged_underlying_effect_eur = post_cost[1] * leveraged_return.underlying
        leveraged_fx_effect_eur = ZERO
        leveraged_wipeout_effect_eur = -post_cost[1] * (ONE + gross_leveraged)
    else:
        funding_eur = -post_cost[1] * leveraged_return.funding
        residual_drag_eur = -post_cost[1] * (ONE + gross_leveraged) * leveraged_return.residual_drag
        leveraged_fee_eur = -post_cost[1] * (ONE + before_leveraged_fee) * leveraged_return.etf_fee
        leveraged_underlying_effect_eur = post_cost[1] * leveraged_return.underlying
        leveraged_fx_effect_eur = post_cost[1] * leveraged_return.fx
        leveraged_wipeout_effect_eur = ZERO
    leveraged_total_effect_eur = ending[1] - post_cost[1]
    reconciled_overlay = (
        post_cost[1]
        + leveraged_underlying_effect_eur
        - funding_eur
        - residual_drag_eur
        + leveraged_fx_effect_eur
        - leveraged_fee_eur
        + leveraged_wipeout_effect_eur
    )
    if not _reconciles(reconciled_overlay, ending[1]):
        raise AssertionError("leveraged EUR wealth effects do not reconcile")
    state.core, state.overlay, state.defensive, state.allocation = *ending, allocation

    return BacktestRow(
        strategy=strategy,
        month=month,
        decision_at=snapshot.decision_at,
        state=allocation_state,
        trigger=trigger,
        accepted_final_beta=accepted_beta,
        actual_beta=actual_beta,
        allocation_flags=(
            (*(allocation.flags if allocation is not None else ()), "LEVERAGED_WIPEOUT_LOCK")
            if leveraged_wipeout_locked and strategy != WORLD_BENCHMARK
            else allocation.flags if allocation is not None else ()
        ),
        leveraged_flags=tuple(leveraged_return.flags),
        pretrade_nav=pretrade_nav,
        pretrade_cash=pretrade_cash,
        pretrade_core=ZERO if inception else targets[0] - trades[0],
        pretrade_overlay=ZERO if inception else targets[1] - trades[1],
        pretrade_defensive=ZERO if inception else targets[2] - trades[2],
        pretrade_core_weight=pretrade_weights[0],
        pretrade_overlay_weight=pretrade_weights[1],
        pretrade_defensive_weight=pretrade_weights[2],
        target_core=targets[0],
        target_overlay=targets[1],
        target_defensive=targets[2],
        target_core_weight=target_weights[0],
        target_overlay_weight=target_weights[1],
        target_defensive_weight=target_weights[2],
        core_trade=trades[0],
        overlay_trade=trades[1],
        defensive_trade=trades[2],
        core_commission=costs[0][0],
        overlay_commission=costs[1][0],
        defensive_commission=costs[2][0],
        core_spread=costs[0][1],
        overlay_spread=costs[1][1],
        defensive_spread=costs[2][1],
        order_count=sum(cost[2] for cost in costs),
        post_cost_nav=post_cost_nav,
        post_cost_core=post_cost[0],
        post_cost_overlay=post_cost[1],
        post_cost_defensive=post_cost[2],
        raw_world_return=raw_world,
        world_fee_rate=WORLD_FEE_MONTH,
        world_investable_return=world_investable,
        leveraged_return=leveraged_return.total_return,
        leveraged_funding_rate=leveraged_return.funding,
        leveraged_fee_rate=leveraged_return.etf_fee,
        leveraged_residual_drag_rate=leveraged_return.residual_drag,
        defensive_return=defensive_return,
        ending_core=ending[0],
        ending_overlay=ending[1],
        ending_defensive=ending[2],
        ending_nav=ending_nav,
        portfolio_return=ending_nav / pretrade_nav - ONE,
        core_turnover=trades_turnover[0],
        overlay_turnover=trades_turnover[1],
        defensive_turnover=trades_turnover[2],
        world_fee_eur=-post_cost[0] * (ONE + raw_world) * WORLD_FEE_MONTH,
        funding_eur=funding_eur,
        leveraged_fee_eur=leveraged_fee_eur,
        residual_drag_eur=residual_drag_eur,
        leveraged_underlying_effect_eur=leveraged_underlying_effect_eur,
        leveraged_fx_effect_eur=leveraged_fx_effect_eur,
        leveraged_wipeout_effect_eur=leveraged_wipeout_effect_eur,
        leveraged_total_effect_eur=leveraged_total_effect_eur,
    )


def _sample_std(values: Sequence[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / (len(values) - 1))


def _summary_metrics(rows: Sequence[BacktestRow], risk_free_returns: Mapping[date, Decimal]) -> SummaryMetrics:
    rows = tuple(rows)
    if not rows or len({row.strategy for row in rows}) != 1:
        raise ValueError("summary_metrics requires one non-empty strategy result")
    months = tuple(row.month for row in rows)
    if any(current != _next_month(previous) for previous, current in zip(months, months[1:])):
        raise ValueError("summary result must be uninterrupted")
    if set(risk_free_returns) != set(months):
        raise ValueError("risk-free returns must contain the same months")
    monthly = [float(row.portfolio_return) for row in rows]
    wealth = math.prod(1.0 + value for value in monthly)
    cagr = wealth ** (12.0 / len(monthly)) - 1.0
    monthly_std = _sample_std(monthly)
    annualized_volatility = None if monthly_std is None else monthly_std * math.sqrt(12.0)
    excess = [value - float(_finite(risk_free_returns[month], "risk-free return")) for month, value in zip(months, monthly)]
    excess_std = _sample_std(excess)
    sharpe = None if excess_std in (None, 0.0) else sum(excess) / len(excess) / excess_std * math.sqrt(12.0)
    level = peak = 1.0
    max_drawdown = 0.0
    for value in monthly:
        level *= 1.0 + value
        peak = max(peak, level)
        max_drawdown = min(max_drawdown, level / peak - 1.0)
    costs = CostDecomposition(
        commissions_eur=sum((row.commission_total for row in rows), ZERO),
        spread_eur=sum((row.spread_total for row in rows), ZERO),
        funding_eur=sum((row.funding_eur for row in rows), ZERO),
        world_fee_eur=sum((row.world_fee_eur for row in rows), ZERO),
        leveraged_fee_eur=sum((row.leveraged_fee_eur for row in rows), ZERO),
        residual_drag_eur=sum((row.residual_drag_eur for row in rows), ZERO),
    )
    return SummaryMetrics(
        rows[0].strategy,
        len(rows),
        cagr,
        annualized_volatility,
        sharpe,
        max_drawdown,
        sum((row.core_turnover + row.overlay_turnover + row.defensive_turnover for row in rows), ZERO),
        costs,
    )


def summary_metrics(rows: Sequence[BacktestRow], risk_free_returns: Mapping[date, Decimal]) -> SummaryMetrics:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        return _summary_metrics(rows, risk_free_returns)


def _run_backtest(
    *,
    signals: Mapping[date, SignalSnapshot],
    world_returns: Mapping[date, Decimal],
    defensive_returns: Mapping[date, Decimal],
    leveraged_returns: Mapping[date, LeveragedReturn],
    config: BacktestConfig = BacktestConfig(),
) -> BacktestResult:
    if not isinstance(config, BacktestConfig):
        raise ValueError("config must be a BacktestConfig")
    months = _validate_inputs(signals, world_returns, defensive_returns, leveraged_returns)
    rows: list[BacktestRow] = []
    for strategy in STRATEGIES:
        state = _StrategyState()
        leveraged_wipeout_locked = False
        for index, month in enumerate(months):
            item = leveraged_returns[month]
            if "CAPITAL_RESET" in item.flags:
                leveraged_wipeout_locked = False
            rows.append(
                _row(
                    strategy,
                    month,
                    signals[month],
                    state,
                    world_returns[month],
                    defensive_returns[month],
                    item,
                    config,
                    inception=index == 0,
                    leveraged_wipeout_locked=leveraged_wipeout_locked,
                )
            )
            leveraged_wipeout_locked = item.wiped_out
    rows_tuple = tuple(rows)
    summaries = tuple(
        summary_metrics(tuple(row for row in rows_tuple if row.strategy == strategy), defensive_returns)
        for strategy in STRATEGIES
    )
    spread_label = format(config.spread_slippage_bps.normalize(), "f")
    metadata = BacktestMetadata(
        config_id=config.config_id,
        initial_capital_eur=config.initial_capital_eur,
        commission_per_executed_sleeve_order_eur=config.commission_per_order_eur,
        spread_slippage_bps=config.spread_slippage_bps,
        spread_scenario_id=f"SPREAD_{spread_label}_BPS",
        world_fee_scenario_id="WORLD_TER_0.20_PERCENT_ANNUAL",
        leveraged_fee_scenario_id="LEVERAGED_TER_0.60_PERCENT_ANNUAL",
    )
    return BacktestResult(STRATEGIES, rows_tuple, summaries, metadata)


def run_backtest(
    *,
    signals: Mapping[date, SignalSnapshot],
    world_returns: Mapping[date, Decimal],
    defensive_returns: Mapping[date, Decimal],
    leveraged_returns: Mapping[date, LeveragedReturn],
    config: BacktestConfig = BacktestConfig(),
) -> BacktestResult:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        return _run_backtest(
            signals=signals,
            world_returns=world_returns,
            defensive_returns=defensive_returns,
            leveraged_returns=leveraged_returns,
            config=config,
        )


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


def normalized_backtest_json(result: BacktestResult) -> bytes:
    with localcontext() as context:
        context.prec = DECIMAL_PRECISION
        context.rounding = ROUND_HALF_EVEN
        if not isinstance(result, BacktestResult):
            raise ValueError("result must be a BacktestResult")
        return canonical_json(_primitive(result))
