from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, getcontext, localcontext
from pathlib import Path
from unittest import TestCase

from perpetual_engine.allocation import SignalSnapshot
from perpetual_engine.backtest import (
    DAMODARAN_CRISIS_RECOVERY,
    DAMODARAN_ONLY,
    FIXED_60_20_20,
    PHASE2_COMPLETE,
    STRATEGIES,
    WORLD_BENCHMARK,
    BacktestConfig,
    normalized_backtest_json,
    run_backtest,
    summary_metrics,
)
from perpetual_engine.leveraged_proxy import FundingRate, LeveragedReturn, leveraged_monthly_return


UTC = timezone.utc
ZERO = Decimal("0")
ONE = Decimal("1")
with localcontext() as _decimal_context:
    _decimal_context.prec = 50
    WORLD_FEE_MONTH = Decimal("0.998") ** (ONE / Decimal("12")) - ONE
    LEVERAGED_FEE_MONTH = Decimal("0.994") ** (ONE / Decimal("12")) - ONE


def month_end(year: int, month: int) -> date:
    return date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)


def previous_final_instant(month: date) -> datetime:
    prior = month.replace(day=1) - timedelta(days=1)
    return datetime(prior.year, prior.month, prior.day, 23, 59, 59, tzinfo=UTC)


def funding_rates_for(month: date) -> tuple[FundingRate, ...]:
    start = month.replace(day=1)
    return tuple(
        FundingRate(start + timedelta(days=index), "DFF", ZERO)
        for index in range((month - start).days + 1)
    )


def signal(month: date, erp: str = "0.04", **changes: object) -> SignalSnapshot:
    fields: dict[str, object] = {
        "decision_at": previous_final_instant(month),
        "erp": Decimal(erp),
        "erp_percentile": Decimal("0.50"),
        "erp_history_count": 36,
        "tips": None,
        "tips_percentile": None,
        "tips_history_count": 0,
        "drawdown": None,
        "momentum_1m": None,
        "momentum_3m": None,
        "volatility_12m": None,
        "market_history_count": 12,
    }
    fields.update(changes)
    return SignalSnapshot(**fields)  # type: ignore[arg-type]


def leveraged(
    underlying: str = "0",
    *,
    funding: str = "0",
    residual: str = "0",
    fee: Decimal = LEVERAGED_FEE_MONTH,
    fx: str = "0",
    wiped_out: bool = False,
) -> LeveragedReturn:
    underlying_return = Decimal(underlying)
    funding_return = Decimal(funding)
    if wiped_out:
        return LeveragedReturn(
            underlying=underlying_return,
            funding=funding_return,
            residual_drag=ZERO,
            fx=ZERO,
            etf_fee=ZERO,
            total_return=-ONE,
            return_usd=-ONE,
            flags=("LEVERAGED_SLEEVE_WIPEOUT",),
            wiped_out=True,
        )
    with localcontext() as context:
        context.prec = 50
        residual_return = Decimal(residual)
        fx_return = Decimal(fx)
        gross = underlying_return + funding_return
        return_usd = (ONE + gross) * (ONE + residual_return) - ONE
        before_fee = return_usd + fx_return
        total_return = (ONE + before_fee) * (ONE + fee) - ONE
    return LeveragedReturn(
        underlying=underlying_return,
        funding=funding_return,
        residual_drag=residual_return,
        fx=fx_return,
        etf_fee=fee,
        total_return=total_return,
        return_usd=return_usd,
    )


def config(
    *,
    capital: str = "1000",
    commission: str = "0",
    spread_bps: str = "0",
) -> BacktestConfig:
    return BacktestConfig(Decimal(capital), Decimal(commission), Decimal(spread_bps))


def inputs(
    months: tuple[date, ...],
    *,
    raw_world: tuple[Decimal, ...] | None = None,
    defensive: tuple[Decimal, ...] | None = None,
    levered: tuple[LeveragedReturn, ...] | None = None,
    signals: tuple[SignalSnapshot, ...] | None = None,
) -> dict[str, dict[date, object]]:
    return {
        "signals": dict(zip(months, signals or tuple(signal(month) for month in months))),
        "world_returns": dict(zip(months, raw_world or (ZERO,) * len(months))),
        "defensive_returns": dict(zip(months, defensive or (ZERO,) * len(months))),
        "leveraged_returns": dict(zip(months, levered or tuple(leveraged() for _ in months))),
    }


def run(months: tuple[date, ...], **changes: object):
    series = inputs(months, **{key: value for key, value in changes.items() if key in {"raw_world", "defensive", "levered", "signals"}})
    return run_backtest(
        **series,
        config=changes.get("config", config()),
    )


class BacktestAccountingTests(TestCase):
    def test_pretrade_targets_own_costs_and_returns_follow_the_frozen_order(self):
        month = month_end(1979, 1)
        result = run(
            (month,),
            raw_world=(Decimal("0.10"),),
            config=config(capital="1000", commission="10", spread_bps="100"),
        )
        row = result.rows_for(WORLD_BENCHMARK)[0]
        with localcontext() as context:
            context.prec = 50
            investable_return = (ONE + Decimal("0.10")) * (ONE + WORLD_FEE_MONTH) - ONE
            expected_ending_core = Decimal("980") * (ONE + investable_return)

        self.assertEqual(row.pretrade_nav, Decimal("1000"))
        self.assertEqual(row.target_core, Decimal("1000"))
        self.assertEqual(row.core_trade, Decimal("1000"))
        self.assertEqual(row.core_commission, Decimal("10"))
        self.assertEqual(row.core_spread, Decimal("10"))
        self.assertEqual(row.post_cost_core, Decimal("980"))
        self.assertEqual(row.ending_core, expected_ending_core)
        self.assertEqual(row.ending_nav, row.ending_core)

    def test_inception_order_counts_are_one_three_and_three(self):
        month = month_end(1979, 1)
        result = run((month,), config=config(capital="800000", commission="19", spread_bps="10"))
        by_strategy = {row.strategy: row for row in result.rows}

        self.assertEqual(by_strategy[WORLD_BENCHMARK].order_count, 1)
        self.assertEqual(by_strategy[FIXED_60_20_20].order_count, 3)
        self.assertEqual(by_strategy[DAMODARAN_ONLY].order_count, 3)
        self.assertEqual(by_strategy[DAMODARAN_CRISIS_RECOVERY].order_count, 3)
        self.assertEqual(by_strategy[PHASE2_COMPLETE].order_count, 3)
        self.assertEqual(by_strategy[WORLD_BENCHMARK].commission_total, Decimal("19"))
        self.assertEqual(by_strategy[FIXED_60_20_20].commission_total, Decimal("57"))

    def test_each_sleeve_bears_its_own_cost_and_a_negative_sleeve_fails(self):
        month = month_end(1979, 1)
        result = run((month,), config=config(capital="1000", commission="10", spread_bps="100"))
        fixed = result.rows_for(FIXED_60_20_20)[0]

        self.assertEqual(fixed.post_cost_core, Decimal("584"))
        self.assertEqual(fixed.post_cost_overlay, Decimal("188"))
        self.assertEqual(fixed.post_cost_defensive, Decimal("188"))
        with self.assertRaisesRegex(ValueError, "COST_EXCEEDS_SLEEVE_VALUE"):
            run((month,), config=config(capital="100", commission="21", spread_bps="0"))

    def test_tactical_hold_preserves_drift_and_a_change_trades_no_core(self):
        months = (month_end(1979, 1), month_end(1979, 2), month_end(1979, 3))
        result = run(
            months,
            raw_world=(Decimal("0.10"), ZERO, ZERO),
            defensive=(ZERO, ZERO, ZERO),
            levered=(leveraged("-0.10"), leveraged(), leveraged()),
            signals=(signal(months[0]), signal(months[1]), signal(months[2], "0.02")),
            config=config(),
        )
        rows = result.rows_for(DAMODARAN_ONLY)

        self.assertNotEqual(rows[1].pretrade_core_weight, Decimal("0.60"))
        self.assertEqual(rows[1].trigger, "HOLD")
        self.assertEqual(rows[1].order_count, 0)
        self.assertEqual((rows[1].core_trade, rows[1].overlay_trade, rows[1].defensive_trade), (ZERO, ZERO, ZERO))
        self.assertEqual(rows[1].target_core, rows[1].pretrade_core)
        self.assertEqual(rows[1].target_overlay, rows[1].pretrade_overlay)
        self.assertEqual(rows[1].target_defensive, rows[1].pretrade_defensive)

        self.assertEqual(rows[2].trigger, "TRADE")
        self.assertEqual(rows[2].core_trade, ZERO)
        self.assertNotEqual(rows[2].overlay_trade, ZERO)
        self.assertNotEqual(rows[2].defensive_trade, ZERO)

    def test_fixed_rebalances_only_in_january(self):
        months = tuple(month_end(1979 + index // 12, index % 12 + 1) for index in range(13))
        result = run(
            months,
            raw_world=(Decimal("0.10"),) + (ZERO,) * 12,
            defensive=(ZERO,) * 13,
            levered=(leveraged("-0.10"),) + tuple(leveraged() for _ in range(12)),
            config=config(),
        )
        rows = result.rows_for(FIXED_60_20_20)

        self.assertEqual(rows[0].trigger, "INCEPTION")
        self.assertEqual(rows[1].trigger, "HOLD")
        self.assertEqual(rows[1].order_count, 0)
        self.assertTrue(all(row.trigger == "HOLD" for row in rows[1:-1]))
        self.assertEqual(rows[-1].trigger, "JANUARY_REBALANCE")
        self.assertGreater(rows[-1].order_count, 0)
        self.assertEqual(
            (rows[-1].target_core_weight, rows[-1].target_overlay_weight, rows[-1].target_defensive_weight),
            (Decimal("0.60"), Decimal("0.20"), Decimal("0.20")),
        )

        drifted = rows[1]
        with localcontext() as context:
            context.prec = 50
            expected_actual_beta = drifted.target_core_weight + Decimal("2") * drifted.target_overlay_weight
        self.assertEqual(drifted.actual_beta, expected_actual_beta)
        self.assertNotEqual(drifted.actual_beta, ONE)

    def test_wipeout_lock_blocks_fixed_and_tactical_overlay_without_refinancing_costs(self):
        months = tuple(month_end(1979 + index // 12, index % 12 + 1) for index in range(13))
        leveraged_rows: list[LeveragedReturn] = []
        wiped_out = False
        for index, month in enumerate(months):
            item = leveraged_monthly_return(
                Decimal("-0.60") if index == 0 else Decimal("0.50"),
                funding_rates_for(month),
                month.replace(day=1),
                investable=True,
                wiped_out=wiped_out,
            )
            leveraged_rows.append(item)
            wiped_out = item.wiped_out
        crisis = signal(
            months[0],
            drawdown=Decimal("-0.05"),
            momentum_1m=Decimal("0.01"),
            momentum_3m=Decimal("-0.01"),
            volatility_12m=Decimal("0.25"),
        )
        recovery = signal(
            months[1],
            drawdown=Decimal("-0.01"),
            momentum_1m=Decimal("0.01"),
            momentum_3m=Decimal("0.02"),
            volatility_12m=Decimal("0.30"),
        )
        signals = (crisis, recovery, *(signal(month) for month in months[2:]))
        result = run(
            months,
            levered=tuple(leveraged_rows),
            signals=signals,
            config=config(commission="19", spread_bps="10"),
        )

        for strategy, requested_beta in ((DAMODARAN_CRISIS_RECOVERY, Decimal("0.90")), (FIXED_60_20_20, ONE)):
            with self.subTest(strategy=strategy):
                first, locked = result.rows_for(strategy)[:2]
                self.assertGreater(first.target_overlay, ZERO)
                self.assertEqual(first.ending_overlay, ZERO)
                self.assertEqual(locked.pretrade_overlay, ZERO)
                self.assertEqual(locked.target_overlay, ZERO)
                self.assertEqual(locked.overlay_trade, ZERO)
                self.assertEqual(locked.overlay_commission, ZERO)
                self.assertEqual(locked.overlay_spread, ZERO)
                self.assertEqual(locked.ending_overlay, ZERO)
                self.assertIn("LEVERAGED_WIPEOUT_LOCK", locked.allocation_flags)
                self.assertEqual(locked.accepted_final_beta, requested_beta)
                self.assertEqual(locked.actual_beta, locked.target_core_weight)

        annual = result.rows_for(FIXED_60_20_20)[-1]
        self.assertEqual(annual.trigger, "JANUARY_REBALANCE")
        self.assertEqual(annual.target_core_weight, Decimal("0.60"))
        self.assertEqual(annual.target_overlay_weight, ZERO)
        self.assertEqual(annual.target_defensive_weight, Decimal("0.40"))
        self.assertNotEqual(annual.core_trade, ZERO)
        self.assertEqual(annual.overlay_trade, ZERO)
        self.assertEqual(annual.actual_beta, Decimal("0.60"))


class BacktestStrategyTests(TestCase):
    def test_exactly_five_strategies_isolate_crisis_and_tips_features(self):
        months = (month_end(1979, 1), month_end(1979, 2))
        crisis = signal(
            months[0],
            drawdown=Decimal("-0.05"),
            momentum_1m=Decimal("0.01"),
            momentum_3m=Decimal("-0.01"),
            volatility_12m=Decimal("0.25"),
        )
        tips_only = signal(
            months[1],
            erp_percentile=Decimal("0.50"),
            tips=Decimal("0.03"),
            tips_percentile=Decimal("0.96"),
            tips_history_count=36,
        )
        result = run(months, signals=(crisis, tips_only), config=config())

        self.assertEqual(STRATEGIES, (
            WORLD_BENCHMARK,
            FIXED_60_20_20,
            DAMODARAN_ONLY,
            DAMODARAN_CRISIS_RECOVERY,
            PHASE2_COMPLETE,
        ))
        self.assertEqual(result.strategies, STRATEGIES)
        self.assertEqual({row.strategy for row in result.rows}, set(STRATEGIES))

        self.assertEqual(result.rows_for(DAMODARAN_ONLY)[0].accepted_final_beta, Decimal("1.00"))
        self.assertEqual(result.rows_for(DAMODARAN_CRISIS_RECOVERY)[0].accepted_final_beta, Decimal("0.80"))
        self.assertEqual(result.rows_for(PHASE2_COMPLETE)[0].accepted_final_beta, Decimal("0.80"))
        self.assertEqual(result.rows_for(DAMODARAN_ONLY)[1].accepted_final_beta, Decimal("1.00"))
        self.assertEqual(result.rows_for(DAMODARAN_CRISIS_RECOVERY)[1].accepted_final_beta, Decimal("1.00"))
        self.assertEqual(result.rows_for(PHASE2_COMPLETE)[1].accepted_final_beta, Decimal("0.90"))

    def test_world_fee_is_applied_once_and_leveraged_fee_is_not_reapplied(self):
        month = month_end(1979, 1)
        levered = leveraged()
        result = run((month,), levered=(levered,), config=config())
        benchmark = result.rows_for(WORLD_BENCHMARK)[0]
        fixed = result.rows_for(FIXED_60_20_20)[0]
        with localcontext() as context:
            context.prec = 50
            expected_benchmark_ending = Decimal("1000") * (ONE + WORLD_FEE_MONTH)
            expected_overlay_ending = Decimal("200") * (ONE + LEVERAGED_FEE_MONTH)

        self.assertEqual(benchmark.world_investable_return, WORLD_FEE_MONTH)
        self.assertEqual(benchmark.ending_core, expected_benchmark_ending)
        self.assertEqual(fixed.leveraged_return, LEVERAGED_FEE_MONTH)
        self.assertEqual(fixed.ending_overlay, expected_overlay_ending)
        self.assertEqual(fixed.leveraged_fee_rate, LEVERAGED_FEE_MONTH)

    def test_non_wiped_leveraged_input_requires_the_exact_task_six_fee_layer(self):
        month = month_end(1979, 1)
        for bad_fee in (ZERO, Decimal("-0.005")):
            with self.subTest(bad_fee=bad_fee), self.assertRaisesRegex(ValueError, "0.60% fee"):
                run((month,), levered=(leveraged("0", fee=bad_fee),), config=config())

    def test_leveraged_input_reconciles_every_task_six_return_layer(self):
        month = month_end(1979, 1)
        valid = leveraged("0.11", funding="-0.01", residual="-0.02", fx="-0.03")
        mismatches = (
            replace(valid, underlying=valid.underlying + Decimal("0.001")),
            replace(valid, return_usd=valid.return_usd + Decimal("0.001")),
            replace(valid, fx=valid.fx + Decimal("0.001")),
            replace(valid, total_return=valid.total_return + Decimal("0.001")),
        )
        run((month,), levered=(valid,), config=config())
        for item in mismatches:
            with self.subTest(item=item), self.assertRaisesRegex(ValueError, "reconcile"):
                run((month,), levered=(item,), config=config())

    def test_wipeout_requires_clamped_returns_and_zero_downstream_components(self):
        month = month_end(1979, 1)
        valid = leveraged("-1.20", wiped_out=True)
        mismatches = (
            replace(valid, underlying=ZERO),
            replace(valid, return_usd=Decimal("-0.99")),
            replace(valid, residual_drag=Decimal("-0.01")),
            replace(valid, fx=Decimal("0.01")),
            replace(valid, etf_fee=LEVERAGED_FEE_MONTH),
        )
        run((month,), levered=(valid,), config=config())
        for item in mismatches:
            with self.subTest(item=item), self.assertRaisesRegex(ValueError, "wipeout"):
                run((month,), levered=(item,), config=config())

    def test_gross_at_or_below_minus_one_cannot_be_rescued_by_downstream_fx(self):
        month = month_end(1979, 1)
        rescued = leveraged("-1.20", fx="0.30")

        with self.assertRaisesRegex(ValueError, "gross"):
            run((month,), levered=(rescued,), config=config())

    def test_allocation_flags_survive_core_drift_floor_and_no_borrowing_cap(self):
        months = tuple(month_end(1979, index) for index in range(1, 5))
        crisis = signal(
            months[0],
            drawdown=Decimal("-0.05"),
            momentum_1m=Decimal("0.01"),
            momentum_3m=Decimal("-0.01"),
            volatility_12m=Decimal("0.25"),
        )
        recoveries = tuple(
            signal(
                month,
                drawdown=Decimal("-0.01"),
                momentum_1m=Decimal("0.01"),
                momentum_3m=Decimal("0.02"),
                volatility_12m=Decimal("0.30"),
            )
            for month in months[1:]
        )
        result = run(
            months,
            signals=(crisis, *recoveries),
            raw_world=(Decimal("1"), ZERO, ZERO, ZERO),
            defensive=(Decimal("-0.90"), ZERO, ZERO, ZERO),
            levered=(leveraged("-0.90"), leveraged(), leveraged(), leveraged()),
            config=config(),
        )
        rows = result.rows_for(PHASE2_COMPLETE)

        self.assertIn("CORE_FLOOR_BINDING", rows[1].allocation_flags)
        self.assertIn("NO_BORROWING_CAP", rows[3].allocation_flags)
        with self.assertRaises((AttributeError, TypeError)):
            rows[1].allocation_flags += ("MUTATED",)  # type: ignore[misc]


class BacktestMetricsAndValidationTests(TestCase):
    def test_real_task_six_wipeout_persists_until_an_explicit_capital_reset(self):
        january, february, march = tuple(month_end(1979, index) for index in range(1, 4))
        first = leveraged_monthly_return(
            Decimal("-0.60"),
            funding_rates_for(january),
            january.replace(day=1),
            investable=True,
        )
        second = leveraged_monthly_return(
            Decimal("0.50"),
            funding_rates_for(february),
            february.replace(day=1),
            investable=True,
            wiped_out=first.wiped_out,
        )
        third = leveraged_monthly_return(
            Decimal("0.10"),
            funding_rates_for(march),
            march.replace(day=1),
            investable=True,
            wiped_out=second.wiped_out,
            reset=True,
        )
        months = (january, february, march)
        signals = (signal(january, "0.04"), signal(february, "0.02"), signal(march, "0.04"))

        result = run(months, levered=(first, second, third), signals=signals, config=config())
        rows = result.rows_for(DAMODARAN_ONLY)

        self.assertTrue(first.wiped_out)
        self.assertTrue(second.wiped_out)
        self.assertFalse(third.wiped_out)
        self.assertIn("CAPITAL_RESET", third.flags)
        self.assertEqual(rows[0].ending_overlay, ZERO)
        self.assertEqual(rows[1].ending_overlay, ZERO)
        self.assertIn("LEVERAGED_WIPEOUT_LOCK", rows[1].allocation_flags)
        self.assertNotIn("LEVERAGED_WIPEOUT_LOCK", rows[2].allocation_flags)
        self.assertEqual(rows[0].leveraged_flags, first.flags)
        self.assertEqual(rows[1].leveraged_flags, second.flags)
        self.assertEqual(rows[2].leveraged_flags, third.flags)
        self.assertGreater(rows[2].target_overlay, ZERO)
        self.assertGreater(rows[2].ending_overlay, ZERO)

        with self.assertRaisesRegex(ValueError, "CAPITAL_RESET"):
            run(
                months,
                levered=(first, second, replace(third, flags=())),
                signals=signals,
                config=config(),
            )

    def test_leveraged_flags_make_failed_reset_distinct_from_plain_persistence(self):
        january, february = month_end(1979, 1), month_end(1979, 2)
        first = leveraged_monthly_return(
            Decimal("-0.60"),
            funding_rates_for(january),
            january.replace(day=1),
            investable=True,
        )
        persistent = leveraged_monthly_return(
            Decimal("-0.60"),
            funding_rates_for(february),
            february.replace(day=1),
            investable=True,
            wiped_out=first.wiped_out,
        )
        failed_reset = leveraged_monthly_return(
            Decimal("-0.60"),
            funding_rates_for(february),
            february.replace(day=1),
            investable=True,
            wiped_out=first.wiped_out,
            reset=True,
        )
        months = (january, february)
        plain = run(months, levered=(first, persistent), config=config())
        reset = run(months, levered=(first, failed_reset), config=config())
        repeated_reset = run(months, levered=(first, failed_reset), config=config())
        plain_row = plain.rows_for(DAMODARAN_ONLY)[1]
        reset_row = reset.rows_for(DAMODARAN_ONLY)[1]

        self.assertEqual(plain_row.leveraged_flags, ("LEVERAGED_SLEEVE_WIPEOUT",))
        self.assertEqual(reset_row.leveraged_flags, ("CAPITAL_RESET", "LEVERAGED_SLEEVE_WIPEOUT"))
        self.assertIn("LEVERAGED_WIPEOUT_LOCK", plain_row.allocation_flags)
        self.assertNotIn("LEVERAGED_WIPEOUT_LOCK", reset_row.allocation_flags)
        with self.assertRaises((AttributeError, TypeError)):
            reset_row.leveraged_flags += ("MUTATED",)  # type: ignore[misc]

        plain_bytes = normalized_backtest_json(plain)
        reset_bytes = normalized_backtest_json(reset)
        self.assertNotEqual(plain_bytes, reset_bytes)
        self.assertEqual(reset_bytes, normalized_backtest_json(repeated_reset))
        reset_payload = json.loads(reset_bytes)
        serialized = next(
            row for row in reset_payload["rows"]
            if row["strategy"] == DAMODARAN_ONLY and row["month"] == "1979-02-28"
        )
        self.assertEqual(serialized["leveraged_flags"], ["CAPITAL_RESET", "LEVERAGED_SLEEVE_WIPEOUT"])

    def test_metrics_use_sample_statistics_and_preserve_cost_decomposition(self):
        months = (month_end(1979, 1), month_end(1979, 2), month_end(1979, 3))
        desired = (Decimal("0.10"), Decimal("-0.05"), Decimal("0.02"))
        raw = tuple((ONE + value) / (ONE + WORLD_FEE_MONTH) - ONE for value in desired)
        result = run(months, raw_world=raw, defensive=(ZERO, ZERO, ZERO), config=config())
        metrics = summary_metrics(result.rows_for(WORLD_BENCHMARK), {month: ZERO for month in months})

        self.assertAlmostEqual(metrics.cagr, 0.29082048471469535)
        self.assertAlmostEqual(metrics.annualized_volatility, 0.26)
        self.assertAlmostEqual(metrics.sharpe, 1.0769230769230769)
        self.assertAlmostEqual(metrics.max_drawdown, -0.05)
        self.assertEqual(metrics.turnover, Decimal("1"))
        self.assertEqual(metrics.costs.commissions_eur, ZERO)
        self.assertEqual(metrics.costs.spread_eur, ZERO)
        self.assertGreater(metrics.costs.world_fee_eur, ZERO)

        flat = run(months, defensive=(ZERO, ZERO, ZERO), config=config())
        flat_metrics = summary_metrics(flat.rows_for(WORLD_BENCHMARK), {month: ZERO for month in months})
        self.assertIsNone(flat_metrics.sharpe)

    def test_multiplicative_leveraged_costs_reconcile_to_ending_wealth(self):
        month = month_end(1979, 1)
        item = leveraged("0.11", funding="-0.01", residual="-0.02")
        result = run((month,), levered=(item,), config=config())
        row = result.rows_for(FIXED_60_20_20)[0]
        metrics = result.summary_for(FIXED_60_20_20)
        with localcontext() as context:
            context.prec = 50
            expected_leveraged_fee = -Decimal("215.600") * LEVERAGED_FEE_MONTH
            expected_ending_overlay = (
                row.post_cost_overlay
                + row.leveraged_underlying_effect_eur
                - row.funding_eur
                - row.residual_drag_eur
                + row.leveraged_fx_effect_eur
                - row.leveraged_fee_eur
            )
            expected_total_effect = row.ending_overlay - row.post_cost_overlay

        self.assertEqual(metrics.costs.funding_eur, Decimal("2"))
        self.assertEqual(metrics.costs.residual_drag_eur, Decimal("4.400"))
        self.assertEqual(metrics.costs.leveraged_fee_eur, expected_leveraged_fee)
        self.assertEqual(row.leveraged_underlying_effect_eur, Decimal("22"))
        self.assertEqual(row.leveraged_fx_effect_eur, ZERO)
        self.assertEqual(row.ending_overlay, expected_ending_overlay)
        self.assertEqual(row.leveraged_total_effect_eur, expected_total_effect)
        self.assertGreater(metrics.costs.world_fee_eur, ZERO)

    def test_wipeout_preserves_underlying_and_funding_then_adds_positive_clamp_correction(self):
        month = month_end(1979, 1)
        item = leveraged("-1", funding="-0.2", wiped_out=True)
        result = run((month,), levered=(item,), config=config())
        row = result.rows_for(FIXED_60_20_20)[0]

        self.assertEqual(row.post_cost_overlay, Decimal("200"))
        self.assertEqual(row.leveraged_underlying_effect_eur, Decimal("-200"))
        self.assertEqual(row.funding_eur, Decimal("40"))
        self.assertEqual(row.residual_drag_eur, ZERO)
        self.assertEqual(row.leveraged_fx_effect_eur, ZERO)
        self.assertEqual(row.leveraged_fee_eur, ZERO)
        self.assertEqual(row.leveraged_wipeout_effect_eur, Decimal("40"))
        self.assertEqual(row.ending_overlay, ZERO)
        self.assertEqual(
            row.post_cost_overlay
            + row.leveraged_underlying_effect_eur
            - row.funding_eur
            + row.leveraged_wipeout_effect_eur,
            ZERO,
        )
        self.assertEqual(result.summary_for(FIXED_60_20_20).costs.funding_eur, Decimal("40"))

    def test_calendar_return_bounds_weights_and_decision_timing_fail_closed(self):
        january, february, march = month_end(1979, 1), month_end(1979, 2), month_end(1979, 3)
        with self.assertRaisesRegex(ValueError, "contiguous"):
            run((january, march))
        series = inputs((january, february))
        series["defensive_returns"].pop(february)
        with self.assertRaisesRegex(ValueError, "same months"):
            run_backtest(**series, config=config())
        with self.assertRaisesRegex(ValueError, "greater than -100%"):
            run((january,), raw_world=(Decimal("-1"),))
        with self.assertRaisesRegex(ValueError, "greater than -100%"):
            run((january,), defensive=(Decimal("-1"),))
        with self.assertRaisesRegex(ValueError, "wiped-out"):
            run((january,), levered=(leveraged("-1"),))
        with self.assertRaisesRegex(ValueError, "wiped-out"):
            run((january,), levered=(replace(leveraged("-1", wiped_out=True), total_return=ZERO),))
        with self.assertRaisesRegex(ValueError, "cannot recover"):
            run(
                (january, february),
                levered=(leveraged("-1", wiped_out=True), leveraged("0.10")),
            )
        wrong_time = replace(signal(january), decision_at=datetime(1979, 1, 1, tzinfo=UTC))
        with self.assertRaisesRegex(ValueError, "decision_at"):
            run((january,), signals=(wrong_time,))

        valid = run((january,), config=config())
        for row in valid.rows:
            self.assertGreaterEqual(row.target_core_weight, ZERO)
            self.assertGreaterEqual(row.target_overlay_weight, ZERO)
            self.assertGreaterEqual(row.target_defensive_weight, ZERO)
            self.assertLessEqual(abs(row.target_core_weight + row.target_overlay_weight + row.target_defensive_weight - ONE), Decimal("1e-12"))

    def test_primary_history_starts_exactly_in_january_1979(self):
        with self.assertRaisesRegex(ValueError, "January 1979"):
            run((month_end(1979, 2),), config=config())

    def test_month_outcome_changes_ending_nav_not_its_own_targets(self):
        month = month_end(1979, 1)
        up = run((month,), raw_world=(Decimal("0.50"),), config=config())
        down = run((month,), raw_world=(Decimal("-0.50"),), config=config())
        up_row = up.rows_for(PHASE2_COMPLETE)[0]
        down_row = down.rows_for(PHASE2_COMPLETE)[0]

        self.assertEqual(
            (up_row.target_core, up_row.target_overlay, up_row.target_defensive),
            (down_row.target_core, down_row.target_overlay, down_row.target_defensive),
        )
        self.assertNotEqual(up_row.ending_nav, down_row.ending_nav)

    def test_normalized_json_is_byte_stable_and_config_is_frozen(self):
        months = (month_end(1979, 1), month_end(1979, 2))
        first = run(months, config=config(capital="800000", commission="19", spread_bps="10"))
        second = run(months, config=config(capital="800000", commission="19", spread_bps="10"))
        first_bytes = normalized_backtest_json(first)
        second_bytes = normalized_backtest_json(second)

        self.assertEqual(first_bytes, second_bytes)
        self.assertNotIn(b"NaN", first_bytes)
        payload = json.loads(first_bytes)
        self.assertEqual(payload["strategies"], list(STRATEGIES))
        self.assertEqual(payload["metadata"]["capital_unit"], "NOMINAL_MODEL_EUR")
        self.assertEqual(payload["metadata"]["tax_treatment"], "PRE_TAX")

        frozen = json.loads(Path("config/backtest_v1.json").read_text(encoding="utf-8"))
        self.assertEqual(frozen["initial_capital_eur"], "800000")
        self.assertEqual(frozen["commission_per_executed_sleeve_order_eur"], "19")
        self.assertEqual(frozen["spread_slippage_bps"], "10")
        self.assertEqual(frozen["spread_sensitivity_bps"], ["5", "20"])
        self.assertEqual(frozen["world_annual_fee"], "0.002")
        self.assertEqual(frozen["leveraged_annual_fee"], "0.006")
        self.assertEqual(frozen["strategies"], list(STRATEGIES))

    def test_global_decimal_precision_cannot_change_rows_metrics_or_json(self):
        months = (month_end(1979, 1), month_end(1979, 2))
        series = inputs(months, raw_world=(Decimal("0.123456789"), Decimal("-0.0123456789")))
        frozen_config = config(capital="800000", commission="19", spread_bps="10")
        original_precision = getcontext().prec
        try:
            getcontext().prec = 6
            low_precision = run_backtest(**series, config=frozen_config)
            low_bytes = normalized_backtest_json(low_precision)
            getcontext().prec = 50
            high_precision = run_backtest(**series, config=frozen_config)
            high_bytes = normalized_backtest_json(high_precision)
        finally:
            getcontext().prec = original_precision

        self.assertEqual(low_precision, high_precision)
        self.assertEqual(low_bytes, high_bytes)

    def test_global_decimal_rounding_cannot_change_rows_metrics_or_json(self):
        months = (month_end(1979, 1), month_end(1979, 2))
        series = inputs(months, raw_world=(Decimal("0.123456789"), Decimal("-0.0123456789")))
        frozen_config = config(capital="800000", commission="19", spread_bps="10")
        original_rounding = getcontext().rounding
        try:
            getcontext().rounding = ROUND_DOWN
            rounded_down = run_backtest(**series, config=frozen_config)
            down_bytes = normalized_backtest_json(rounded_down)
            getcontext().rounding = ROUND_HALF_UP
            rounded_up = run_backtest(**series, config=frozen_config)
            up_bytes = normalized_backtest_json(rounded_up)
        finally:
            getcontext().rounding = original_rounding

        self.assertEqual(rounded_down, rounded_up)
        self.assertEqual(down_bytes, up_bytes)

    def test_metadata_identifies_cost_and_fee_scenarios(self):
        month = month_end(1979, 1)
        five = run((month,), config=config(capital="800000", commission="19", spread_bps="5"))
        twenty = run((month,), config=config(capital="800000", commission="19", spread_bps="20"))

        self.assertEqual(five.metadata.config_id, "BACKTEST_V1")
        self.assertEqual(five.metadata.initial_capital_eur, Decimal("800000"))
        self.assertEqual(five.metadata.commission_per_executed_sleeve_order_eur, Decimal("19"))
        self.assertEqual(five.metadata.spread_slippage_bps, Decimal("5"))
        self.assertEqual(five.metadata.spread_scenario_id, "SPREAD_5_BPS")
        self.assertEqual(five.metadata.world_fee_scenario_id, "WORLD_TER_0.20_PERCENT_ANNUAL")
        self.assertEqual(five.metadata.leveraged_fee_scenario_id, "LEVERAGED_TER_0.60_PERCENT_ANNUAL")
        self.assertNotEqual(five.metadata, twenty.metadata)
        self.assertNotEqual(normalized_backtest_json(five), normalized_backtest_json(twenty))

    def test_public_records_are_immutable(self):
        month = month_end(1979, 1)
        result = run((month,), config=config())
        with self.assertRaises((AttributeError, TypeError)):
            result.rows[0].ending_nav = ZERO  # type: ignore[misc]
        with self.assertRaises((AttributeError, TypeError)):
            result.summaries[0].cagr = math.nan  # type: ignore[misc]
