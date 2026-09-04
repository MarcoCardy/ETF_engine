from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from perpetual_engine.allocation import (
    HICP_ITALY_URL,
    ITALY_CPI_PROXY,
    SignalSnapshot,
    compute_signals,
    defensive_monthly_return,
    ir3tib_available_at,
    next_allocation,
    splice_italy_cpi,
    treasury_available_at,
)
from perpetual_engine.point_in_time import ObservationRow, asof_select


UTC = timezone.utc
WORLD = "PUBLIC_DEVELOPED_WORLD_TR_PROXY_EUR"


def moment(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def row(
    series_id: str,
    observed: date,
    value: str,
    *,
    available_at: datetime | None = None,
    source_hash: str = "a" * 64,
) -> ObservationRow:
    available = available_at or datetime(observed.year, observed.month, observed.day, 23, 59, 59, tzinfo=UTC)
    return ObservationRow(
        series_id,
        observed,
        observed,
        available,
        Decimal(value),
        "ratio",
        "https://example.test/frozen.csv",
        max(available, moment("2026-08-22T00:00:00+00:00")),
        source_hash,
    )


def month_end(year: int, month: int) -> date:
    first_next = date(year + (month == 12), month % 12 + 1, 1)
    return first_next - timedelta(days=1)


def signals(erp: str = "0.04", **changes: object) -> SignalSnapshot:
    fields: dict[str, object] = {
        "decision_at": moment("2024-08-31T23:59:59+00:00"),
        "erp": Decimal(erp),
        "erp_percentile": Decimal("0.50"),
        "erp_history_count": 12,
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


class AllocationTests(TestCase):
    def test_raw_erp_beta_boundaries_are_exact(self):
        below = next_allocation(None, signals("0.029999"))
        at_three = next_allocation(None, signals("0.03"))
        at_four = next_allocation(None, signals("0.04"))
        self.assertEqual(below.accepted_final_beta, Decimal("0.60"))
        self.assertEqual(at_three.accepted_final_beta, Decimal("0.95"))
        self.assertEqual(at_four.accepted_final_beta, Decimal("1.00"))

    def test_crisis_recovery_ramp_exit_and_crisis_precedence(self):
        crisis_signals = signals(
            "0.04",
            drawdown=Decimal("-0.05"),
            momentum_1m=Decimal("0.02"),
            momentum_3m=Decimal("-0.001"),
            volatility_12m=Decimal("0.240001"),
        )
        crisis = next_allocation(None, crisis_signals)
        self.assertEqual((crisis.state, crisis.state_proposal, crisis.accepted_final_beta), ("CRISIS", Decimal("0.80"), Decimal("0.80")))

        recovery_signals = signals(
            "0.04",
            drawdown=Decimal("-0.01"),
            momentum_1m=Decimal("0.01"),
            momentum_3m=Decimal("0.011"),
            volatility_12m=Decimal("0.31"),
        )
        recovery = next_allocation(crisis, recovery_signals)
        self.assertEqual((recovery.state, recovery.state_proposal), ("RECOVERY", Decimal("0.90")))

        conflict = next_allocation(recovery, replace(crisis_signals, momentum_1m=Decimal("0.01")))
        self.assertEqual((conflict.state, conflict.state_proposal, conflict.band_decision), ("CRISIS", Decimal("0.80"), "BYPASS_CRISIS"))

        normal = next_allocation(replace(recovery, accepted_final_beta=Decimal("0.80")), replace(recovery_signals, momentum_3m=Decimal("0.01")))
        self.assertEqual((normal.state, normal.accepted_final_beta), ("NORMAL", Decimal("1.00")))

    def test_tips_conflict_uses_lower_cap_and_requires_36_prior_observations(self):
        capped = next_allocation(None, signals("0.04", erp_percentile=Decimal("0.50"), tips_percentile=Decimal("0.96"), tips_history_count=36))
        unavailable = next_allocation(None, signals("0.04", tips_percentile=Decimal("0.99"), tips_history_count=35))
        self.assertEqual((capped.tips_cap, capped.accepted_final_beta), (Decimal("0.90"), Decimal("0.90")))
        self.assertIn("TIPS_CAP_REDUCTION", capped.flags)
        self.assertIsNone(unavailable.tips_cap)
        self.assertEqual(unavailable.accepted_final_beta, Decimal("1.00"))

    def test_normal_band_holds_strictly_below_boundary_and_equality_executes(self):
        prior = next_allocation(None, signals("0.02"))
        almost = replace(prior, accepted_final_beta=Decimal("0.800001"))
        held = next_allocation(almost, signals("0.03"))
        equality = next_allocation(replace(prior, accepted_final_beta=Decimal("0.80")), signals("0.03"))
        self.assertEqual((held.band_decision, held.accepted_final_beta, held.trade_decision), ("HOLD", Decimal("0.800001"), "HOLD"))
        self.assertEqual((equality.band_decision, equality.accepted_final_beta, equality.trade_decision), ("EXECUTE", Decimal("0.95"), "TRADE"))

    def test_band_is_disabled_at_inception_and_for_recovery_and_tips_reduction(self):
        inception = next_allocation(None, signals("0.04"))
        self.assertEqual(inception.band_decision, "BYPASS_INCEPTION")

        prior = next_allocation(None, signals("0.04"))
        crisis = next_allocation(
            prior,
            signals("0.04", drawdown=Decimal("-0.05"), momentum_1m=Decimal("0.01"), momentum_3m=Decimal("-0.01"), volatility_12m=Decimal("0.25")),
        )
        recovery = next_allocation(
            crisis,
            signals("0.04", drawdown=Decimal("-0.01"), momentum_1m=Decimal("0.01"), momentum_3m=Decimal("0.02"), volatility_12m=Decimal("0.30")),
        )
        self.assertEqual((recovery.state, recovery.band_decision), ("RECOVERY", "BYPASS_RECOVERY"))

        tips = next_allocation(prior, signals("0.04", tips_percentile=Decimal("0.96"), tips_history_count=36))
        self.assertEqual((tips.band_decision, tips.trade_decision), ("BYPASS_TIPS_CAP", "TRADE"))

    def test_expanding_percentiles_are_prior_only_midranks_with_history_counts(self):
        decision = moment("2024-04-30T23:59:59+00:00")
        erp_rows = (
            row("ERP", date(2024, 1, 31), "0.04"),
            row("ERP", date(2024, 2, 29), "0.05", source_hash="b" * 64),
            row("ERP", date(2024, 3, 31), "0.05", source_hash="c" * 64),
            row("ERP", date(2024, 4, 30), "0.05", source_hash="d" * 64),
        )
        result = compute_signals(decision_at=decision, erp_rows=erp_rows, treasury_rows=(), tips_rows=(), market_rows=())
        self.assertEqual(result.erp_history_count, 3)
        self.assertEqual(result.erp_percentile, Decimal(2) / Decimal(3))

    def test_treasury_fallback_excludes_month_end_until_next_business_day_and_ir3tib_credits_m_plus_2(self):
        self.assertEqual(treasury_available_at(date(2024, 5, 31)), moment("2024-06-03T23:59:59+00:00"))
        self.assertEqual(ir3tib_available_at(date(2024, 1, 31)), moment("2024-02-29T23:59:59+00:00"))

        result = compute_signals(
            decision_at=moment("2024-05-31T23:59:59+00:00"),
            erp_rows=(row("ERP", date(2024, 4, 30), "0.04"),),
            treasury_rows=(
                row("DGS10", date(2024, 5, 30), "0.04", available_at=moment("2024-05-31T23:59:59+00:00")),
                row("DGS10", date(2024, 5, 31), "0.05", available_at=treasury_available_at(date(2024, 5, 31)), source_hash="b" * 64),
            ),
            tips_rows=(),
            market_rows=(),
        )
        self.assertEqual(result.treasury_10y, Decimal("0.04"))

        rate = row("IR3TIB01ITM156N", date(2024, 1, 31), "0.036", available_at=ir3tib_available_at(date(2024, 1, 31)))
        with self.assertRaisesRegex(ValueError, "immediately following"):
            defensive_monthly_return((rate,), date(2024, 1, 1), moment("2024-01-31T23:59:59+00:00"), max_staleness_days=60)
        with self.assertRaisesRegex(ValueError, "available"):
            defensive_monthly_return((rate,), date(2024, 2, 1), moment("2024-01-31T23:59:59+00:00"), max_staleness_days=60)
        with self.assertRaisesRegex(ValueError, "immediately following"):
            defensive_monthly_return((rate,), date(2024, 3, 1), moment("2024-01-31T23:59:59+00:00"), max_staleness_days=60)
        credited = defensive_monthly_return((rate,), date(2024, 3, 1), moment("2024-02-29T23:59:59+00:00"), max_staleness_days=60)
        self.assertGreater(credited, Decimal("0"))

    def test_cpi_splice_uses_mandatory_november_link_and_rejects_missing_link(self):
        oecd = (
            row("ITACPALTT01IXNBM", date(2023, 10, 31), "99", source_hash="9" * 64),
            row("ITACPALTT01IXNBM", date(2023, 11, 30), "100", available_at=moment("2023-12-10T23:59:59+00:00")),
        )
        hicp = (
            row("HICP", date(2023, 11, 30), "200", source_hash="b" * 64),
            row("HICP", date(2023, 12, 31), "202", available_at=moment("2023-12-05T23:59:59+00:00"), source_hash="c" * 64),
        )
        result = splice_italy_cpi(oecd, hicp)
        by_month = {item.observation_date: item for item in result}
        self.assertEqual(HICP_ITALY_URL, "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_midx?geo=IT&coicop=CP00&unit=I15")
        self.assertEqual(by_month[date(2023, 11, 30)].value, Decimal("100"))
        self.assertEqual(by_month[date(2023, 12, 31)].value, Decimal("101"))
        self.assertEqual(by_month[date(2023, 12, 31)].series_id, ITALY_CPI_PROXY)
        self.assertEqual(by_month[date(2023, 12, 31)].available_at, moment("2023-12-10T23:59:59+00:00"))
        self.assertNotEqual(by_month[date(2023, 12, 31)].source_hash, "c" * 64)
        self.assertIsNone(asof_select((by_month[date(2023, 12, 31)],), moment("2023-12-05T23:59:59+00:00")))
        with self.assertRaisesRegex(ValueError, "November 2023"):
            splice_italy_cpi((), hicp)
        with self.assertRaisesRegex(ValueError, "gap"):
            splice_italy_cpi(oecd, hicp[:1] + (row("HICP", date(2024, 1, 31), "204", source_hash="d" * 64),))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            splice_italy_cpi(oecd, hicp + (row("HICP", date(2023, 12, 1), "201", source_hash="e" * 64),))

    def test_market_signals_use_only_prior_complete_month_and_report_missing_warmup(self):
        decision = moment("2024-07-31T23:59:59+00:00")
        market = tuple(
            row(WORLD, month_end(2023 + (7 + index) // 12, (7 + index) % 12 + 1), "0.01", source_hash=f"{index:064x}")
            for index in range(11)
        ) + (
            row(WORLD, date(2024, 7, 31), "-0.10", source_hash="e" * 64),
            row(WORLD, date(2024, 8, 31), "0.50", available_at=moment("2024-08-01T00:00:00+00:00"), source_hash="f" * 64),
        )
        complete = compute_signals(
            decision_at=decision,
            erp_rows=(row("ERP", date(2024, 7, 31), "0.04"),),
            treasury_rows=(),
            tips_rows=(),
            market_rows=market,
        )
        self.assertEqual(complete.momentum_1m, Decimal("-0.10"))
        self.assertIsNotNone(complete.volatility_12m)
        self.assertNotEqual(complete.drawdown, Decimal("0.50"))

        incomplete = compute_signals(
            decision_at=decision,
            erp_rows=(row("ERP", date(2024, 7, 31), "0.04"),),
            treasury_rows=(),
            tips_rows=(),
            market_rows=market[-3:],
        )
        self.assertIsNone(incomplete.volatility_12m)
        self.assertEqual(next_allocation(None, incomplete).state, "NORMAL")

    def test_core_floor_no_borrowing_weight_invariant_and_hold_has_zero_tactical_trades(self):
        floor = next_allocation(None, signals("0.02"), core_weight_pretrade=Decimal("0.70"))
        self.assertEqual((floor.actual_beta, floor.overlay_weight, floor.defensive_weight), (Decimal("0.70"), Decimal("0"), Decimal("0.30")))
        self.assertIn("CORE_FLOOR_BINDING", floor.flags)

        recovery_prior = replace(next_allocation(None, signals("0.04")), state="RECOVERY", accepted_final_beta=Decimal("1.10"))
        no_borrowing = next_allocation(
            recovery_prior,
            signals("0.04", drawdown=Decimal("-0.01"), momentum_1m=Decimal("0.01"), momentum_3m=Decimal("0.02"), volatility_12m=Decimal("0.30")),
            core_weight_pretrade=Decimal("1.00"),
            overlay_weight_pretrade=Decimal("0"),
            defensive_weight_pretrade=Decimal("0"),
        )
        self.assertEqual((no_borrowing.overlay_weight, no_borrowing.defensive_weight), (Decimal("0"), Decimal("0")))
        self.assertIn("NO_BORROWING_CAP", no_borrowing.flags)
        self.assertEqual(no_borrowing.core_weight + no_borrowing.overlay_weight + no_borrowing.defensive_weight, Decimal("1"))

        prior = next_allocation(None, signals("0.02"))
        drifted_prior = replace(prior, accepted_final_beta=Decimal("0.800001"))
        with self.assertRaisesRegex(ValueError, "full pretrade"):
            next_allocation(drifted_prior, signals("0.03"), core_weight_pretrade=Decimal("0.73"))
        held = next_allocation(
            drifted_prior,
            signals("0.03"),
            core_weight_pretrade=Decimal("0.73"),
            overlay_weight_pretrade=Decimal("0.05"),
            defensive_weight_pretrade=Decimal("0.22"),
        )
        self.assertEqual(
            (held.trade_decision, held.overlay_weight, held.defensive_weight, held.actual_beta, held.overlay_trade, held.defensive_trade),
            ("HOLD", Decimal("0.05"), Decimal("0.22"), Decimal("0.83"), Decimal("0"), Decimal("0")),
        )

    def test_trades_are_weight_deltas_from_validated_pretrade_weights(self):
        prior = next_allocation(None, signals("0.02"))
        traded = next_allocation(
            prior,
            signals("0.04"),
            core_weight_pretrade=Decimal("0.65"),
            overlay_weight_pretrade=Decimal("0.10"),
            defensive_weight_pretrade=Decimal("0.25"),
        )
        self.assertEqual((traded.overlay_weight, traded.defensive_weight), (Decimal("0.175"), Decimal("0.175")))
        self.assertEqual((traded.overlay_trade, traded.defensive_trade), (Decimal("0.075"), Decimal("-0.075")))
        with self.assertRaisesRegex(ValueError, "sum"):
            next_allocation(prior, signals("0.04"), core_weight_pretrade=Decimal("0.65"), overlay_weight_pretrade=Decimal("0.10"), defensive_weight_pretrade=Decimal("0.20"))

    def test_config_freezes_task_five_source_identifiers_without_funding_implementation(self):
        config = json.loads(Path("config/data_sources_v1.json").read_text(encoding="utf-8"))
        rates = config["rates"]
        self.assertEqual(rates["treasury_10y"]["series_id"], "DGS10")
        self.assertEqual(rates["tips_10y"]["series_id"], "DFII10")
        self.assertEqual(rates["defensive"]["series_id"], "IR3TIB01ITM156N")
        self.assertEqual(config["inflation"]["oecd_series_id"], "ITACPALTT01IXNBM")
        self.assertEqual(config["inflation"]["hicp_url"], HICP_ITALY_URL)
