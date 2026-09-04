from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest import TestCase

from perpetual_engine.four_sleeve import (
    CANDIDATES,
    PORTFOLIOS,
    SLEEVES,
    WORLD_BENCHMARK,
    FourSleeveConfig,
    normalized_four_sleeve_json,
    run_four_sleeve,
)


UTC = timezone.utc
ZERO = Decimal("0")


def month_end(year: int, month: int) -> date:
    return date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)


def series(months: tuple[date, ...], **changes: tuple[Decimal, ...]):
    values = {sleeve: (ZERO,) * len(months) for sleeve in SLEEVES}
    values.update(changes)
    return {sleeve: dict(zip(months, returns)) for sleeve, returns in values.items()}


def config(start: date, end: date, **changes: object) -> FourSleeveConfig:
    fields: dict[str, object] = {
        "explicit_start": start,
        "explicit_end": end,
        "retrieved_at": datetime(end.year, end.month, end.day, tzinfo=UTC) + timedelta(days=30),
        "max_staleness_days": 45,
        "initial_capital_eur": Decimal("1000"),
        "commission_per_order_eur": ZERO,
        "spread_slippage_bps": ZERO,
    }
    fields.update(changes)
    return FourSleeveConfig(**fields)  # type: ignore[arg-type]


class FourSleeveTests(TestCase):
    def test_candidate_weights_are_frozen_and_sum_exactly_to_one(self):
        self.assertEqual(SLEEVES, ("WORLD", "MOMENTUM", "QUALITY", "TREND"))
        self.assertEqual(
            tuple(CANDIDATES),
            (
                "BASELINE_60_15_15_10",
                "THREE_EQUITY_FIXED",
                "PRUDENT_TREND_10",
                "ERC_TREND_15",
                "ERC_TREND_20",
                "ERC_FREE",
            ),
        )
        self.assertEqual(PORTFOLIOS[WORLD_BENCHMARK], (Decimal("1"), ZERO, ZERO, ZERO))
        self.assertEqual(CANDIDATES["BASELINE_60_15_15_10"], (Decimal("0.60"), Decimal("0.15"), Decimal("0.15"), Decimal("0.10")))
        for weights in CANDIDATES.values():
            self.assertEqual(sum(weights), Decimal("1"))
            self.assertTrue(all(weight >= ZERO for weight in weights))

    def test_prudent_candidate_keeps_the_frozen_equity_mix_and_caps_trend_at_ten_percent(self):
        month = month_end(2026, 1)
        result = run_four_sleeve(series((month,)), config(month, month))

        row = result.rows_for("PRUDENT_TREND_10")[0]
        self.assertEqual(
            row.target_values,
            (Decimal("297.423529412"), Decimal("239.717647059"), Decimal("362.858823529"), Decimal("100")),
        )

    def test_three_etf_candidate_preserves_the_equity_mix_without_buying_trend(self):
        month = month_end(2026, 1)
        result = run_four_sleeve(series((month,)), config(month, month))

        row = result.rows_for("THREE_EQUITY_FIXED")[0]
        self.assertEqual(
            row.target_values,
            (Decimal("330.470588235"), Decimal("266.352941176"), Decimal("403.176470589"), ZERO),
        )
        self.assertEqual(row.order_count, 3)

    def test_returns_and_inception_costs_are_accounted_without_optimization(self):
        months = (month_end(2025, 12), month_end(2026, 1))
        inputs = series(months, WORLD=(Decimal("0.10"), ZERO))
        result = run_four_sleeve(
            inputs,
            config(
                months[0],
                months[-1],
                commission_per_order_eur=Decimal("1"),
                spread_slippage_bps=Decimal("10"),
            ),
        )
        first = result.rows_for("BASELINE_60_15_15_10")[0]
        second = result.rows_for("BASELINE_60_15_15_10")[1]

        self.assertEqual(first.trigger, "INCEPTION")
        self.assertEqual(first.order_count, 4)
        self.assertEqual(first.commissions_eur, Decimal("4"))
        self.assertEqual(first.spread_eur, Decimal("1.00000"))
        self.assertEqual(first.ending_values[0], Decimal("658.24000"))
        self.assertEqual(second.trigger, "JANUARY_REBALANCE")
        self.assertGreater(second.turnover, ZERO)
        self.assertEqual(result.rows_for(WORLD_BENCHMARK)[1].trigger, "JANUARY_REVIEW_NO_TRADE")
        self.assertEqual(tuple(row.strategy for row in result.rows[: len(PORTFOLIOS)]), tuple(PORTFOLIOS))
        self.assertEqual(result.strategies[0], WORLD_BENCHMARK)

    def test_calendar_must_reach_both_explicit_limits_and_be_complete(self):
        january, february, march = (month_end(2026, month) for month in (1, 2, 3))
        with self.assertRaisesRegex(ValueError, "explicit final limit"):
            run_four_sleeve(series((january, february)), config(january, march))

        broken = series((january, february, march))
        broken["QUALITY"].pop(february)
        with self.assertRaisesRegex(ValueError, "same complete calendar"):
            run_four_sleeve(broken, config(january, march))

        with self.assertRaisesRegex(ValueError, "explicit start"):
            run_four_sleeve(series((february, march)), config(january, march))

    def test_stale_or_invalid_later_observation_fails_instead_of_truncating(self):
        january, february = month_end(2026, 1), month_end(2026, 2)
        with self.assertRaisesRegex(ValueError, "stale"):
            run_four_sleeve(
                series((january, february)),
                config(january, february, retrieved_at=datetime(2026, 5, 1, tzinfo=UTC)),
            )

        inputs = series((january, february))
        inputs["TREND"][february] = Decimal("NaN")
        with self.assertRaisesRegex(ValueError, "finite"):
            run_four_sleeve(inputs, config(january, february))

    def test_output_is_byte_stable(self):
        january, february = month_end(2026, 1), month_end(2026, 2)
        inputs = series((january, february), QUALITY=(Decimal("0.01"), Decimal("-0.02")))
        frozen = config(january, february)
        first = run_four_sleeve(inputs, frozen)
        second = run_four_sleeve(inputs, frozen)
        self.assertEqual(normalized_four_sleeve_json(first), normalized_four_sleeve_json(second))
