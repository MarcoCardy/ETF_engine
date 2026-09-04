from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from perpetual_engine.tce import (
    EventRecord,
    TceSnapshot,
    TceState,
    intramonth_decision,
    monthly_decision,
    score_snapshot,
)


UTC = timezone.utc
HASH = "a" * 64


def at(day: int, hour: int = 18) -> datetime:
    return datetime(2026, 8, day, hour, tzinfo=UTC)


class TceConfigTests(TestCase):
    def test_deterministic_tce_is_a_separate_optional_supplement(self):
        config = json.loads((Path(__file__).parents[1] / "config" / "tce_v1.json").read_text(encoding="utf-8"))
        integration = config["portfolio_integration"]
        self.assertEqual(integration["mode"], "SEPARATE_OPTIONAL_SUPPLEMENT")
        self.assertEqual(integration["structural_portfolio_weight"], "1.00")
        self.assertEqual(integration["maximum_optional_tce_notional"], "0.10")
        self.assertFalse(integration["automatic_funding_from_structural"])


def month_at(month: int) -> datetime:
    day = {7: 31, 8: 31, 9: 30, 10: 31}[month]
    return datetime(2026, month, day, 18, tzinfo=UTC)


def snapshot(candidate: str = "A", when: datetime | None = None, score: str = "80", **changes: object) -> TceSnapshot:
    fields: dict[str, object] = {
        "candidate_id": candidate,
        "variant": "FIXED_UNIVERSE",
        "decision_at": when or at(1),
        "policy": Decimal(score),
        "capex_orders": Decimal(score),
        "earnings_revisions": Decimal(score),
        "relative_strength": Decimal(score),
        "valuation": Decimal(score),
        "crowding": Decimal(score),
        "macro": Decimal(score),
        "portfolio_fit": Decimal(score),
        "confidence": Decimal("80"),
        "evidence_hashes": ("b" * 64, "a" * 64),
        "config_hash": HASH,
    }
    fields.update(changes)
    return TceSnapshot(**fields)  # type: ignore[arg-type]


def event(candidate: str = "B", **changes: object) -> EventRecord:
    fields: dict[str, object] = {
        "candidate_id": candidate,
        "event_type": "GUIDANCE_OR_EARNINGS_REVISION",
        "published_at": at(20, 8),
        "available_at": at(20, 9),
        "recorded_at": at(20, 9),
        "source_url": "https://example.test/release",
        "source_hash": HASH,
        "mapper_version": "TCE_EVENT_V1",
        "availability_status": "LIVE_RECEIVED",
    }
    fields.update(changes)
    return EventRecord(**fields)  # type: ignore[arg-type]


class TceScoreTests(TestCase):
    def test_formula_uses_the_eight_frozen_weights(self):
        scored = score_snapshot(
            snapshot(
                policy=Decimal("80"),
                capex_orders=Decimal("70"),
                earnings_revisions=Decimal("60"),
                relative_strength=Decimal("50"),
                valuation=Decimal("40"),
                crowding=Decimal("30"),
                macro=Decimal("20"),
                portfolio_fit=Decimal("10"),
            )
        )
        self.assertEqual(scored.raw_tce, Decimal("52.50"))
        self.assertEqual(scored.operational_tce, Decimal("52.50"))

    def test_vetoes_cap_the_operational_score(self):
        narrative = score_snapshot(
            snapshot(earnings_revisions=Decimal("39"), relative_strength=Decimal("39"), score="100")
        )
        bubble = score_snapshot(snapshot(valuation=Decimal("29"), crowding=Decimal("29"), score="100"))
        self.assertEqual((narrative.operational_tce, narrative.vetoes), (Decimal("64"), ("NARRATIVE_VETO",)))
        self.assertEqual((bubble.operational_tce, bubble.vetoes), (Decimal("69"), ("BUBBLE_VETO",)))

    def test_eligibility_requires_score_confidence_and_no_veto(self):
        self.assertTrue(score_snapshot(snapshot(score="70", confidence=Decimal("60"))).eligible)
        self.assertFalse(score_snapshot(snapshot(score="69.99", confidence=Decimal("100"))).eligible)
        self.assertFalse(score_snapshot(snapshot(score="100", confidence=Decimal("59.99"))).eligible)

    def test_missing_score_fails_closed_instead_of_renormalizing(self):
        with self.assertRaisesRegex(ValueError, "DATA_INCOMPLETE.*valuation"):
            score_snapshot(snapshot(valuation=None))

    def test_hash_is_stable_when_evidence_arrives_in_another_order(self):
        first = score_snapshot(snapshot(evidence_hashes=("a" * 64, "b" * 64)))
        second = score_snapshot(snapshot(evidence_hashes=("b" * 64, "a" * 64)))
        self.assertEqual(first.decision_hash, second.decision_hash)


class TceMonthlyTests(TestCase):
    def test_fixed_universe_enters_the_monthly_leader_immediately(self):
        result = monthly_decision(TceState(), (snapshot("A", month_at(7)),))
        self.assertEqual((result.action, result.state.active_candidate_id), ("ENTER", "A"))

    def test_thematic_entry_needs_two_consecutive_monthly_confirmations(self):
        first = monthly_decision(TceState(), (snapshot("A", month_at(7), variant="THEMATIC"),))
        second = monthly_decision(first.state, (snapshot("A", month_at(8), variant="THEMATIC"),))
        self.assertEqual((first.action, first.state.active_candidate_id), ("WATCH", None))
        self.assertEqual((second.action, second.state.active_candidate_id), ("ENTER", "A"))

    def test_repeated_decision_in_the_same_month_cannot_fake_confirmation(self):
        first = monthly_decision(TceState(), (snapshot("A", at(1), variant="THEMATIC"),))
        with self.assertRaisesRegex(ValueError, "following calendar month"):
            monthly_decision(first.state, (snapshot("A", at(31), variant="THEMATIC"),))

    def test_rotation_waits_for_the_new_leaders_second_confirmation(self):
        first = monthly_decision(TceState(), (snapshot("A", month_at(7), variant="THEMATIC"),))
        active = monthly_decision(first.state, (snapshot("A", month_at(8), variant="THEMATIC"),))
        watch_b = monthly_decision(
            active.state,
            (snapshot("A", month_at(9), "80", variant="THEMATIC"), snapshot("B", month_at(9), "85", variant="THEMATIC")),
        )
        rotate_b = monthly_decision(
            watch_b.state,
            (snapshot("A", month_at(10), "80", variant="THEMATIC"), snapshot("B", month_at(10), "85", variant="THEMATIC")),
        )
        self.assertEqual((watch_b.action, watch_b.state.active_candidate_id), ("HOLD", "A"))
        self.assertEqual((rotate_b.action, rotate_b.state.active_candidate_id), ("ROTATE", "B"))

    def test_no_eligible_candidate_moves_the_sleeve_to_cash(self):
        prior = TceState(active_candidate_id="A", eligible_streaks=(("A", 2),), last_trade_at=at(1))
        result = monthly_decision(prior, (snapshot("A", at(31), "69"),))
        self.assertEqual((result.action, result.state.active_candidate_id), ("CASH", None))

    def test_thematic_exit_requires_two_consecutive_months_below_55(self):
        prior = TceState(active_candidate_id="A", eligible_streaks=(("A", 2),), last_trade_at=month_at(8))
        first = monthly_decision(prior, (snapshot("A", month_at(9), "54", variant="THEMATIC"),))
        second = monthly_decision(first.state, (snapshot("A", month_at(10), "54", variant="THEMATIC"),))
        self.assertEqual((first.action, first.state.active_candidate_id, first.state.exit_streak), ("HOLD", "A", 1))
        self.assertEqual((second.action, second.state.active_candidate_id, second.state.exit_streak), ("CASH", None, 0))

    def test_monthly_universe_cannot_mix_variants(self):
        with self.assertRaisesRegex(ValueError, "same variant"):
            monthly_decision(
                TceState(),
                (snapshot("A", month_at(7)), snapshot("B", month_at(7), variant="THEMATIC")),
            )

    def test_monthly_decision_hash_is_reproducible(self):
        inputs = (snapshot("B", at(1)), snapshot("A", at(1)))
        first = monthly_decision(TceState(), inputs)
        second = monthly_decision(TceState(), tuple(reversed(inputs)))
        self.assertEqual(first.decision_hash, second.decision_hash)


class TceIntramonthTests(TestCase):
    def setUp(self):
        self.state = TceState(
            active_candidate_id="A",
            eligible_streaks=(("A", 2), ("B", 2)),
            last_trade_at=at(1),
        )
        self.sessions = (date(2026, 8, 21), date(2026, 8, 24), date(2026, 8, 25))

    def test_event_type_must_be_whitelisted(self):
        with self.assertRaisesRegex(ValueError, "event_type"):
            event(event_type="SOCIAL_MEDIA_RUMOR")

    def test_future_event_cannot_enter_a_decision(self):
        with self.assertRaisesRegex(ValueError, "not available"):
            intramonth_decision(
                self.state,
                event(available_at=at(22), recorded_at=at(22)),
                (snapshot("A", at(21)), snapshot("B", at(21), "90")),
                decision_at=at(21),
                trading_sessions=self.sessions,
            )

    def test_rotation_requires_five_points_and_executes_after_one_full_session(self):
        no_hurdle = intramonth_decision(
            replace(self.state, last_trade_at=at(1) - timedelta(days=31)),
            event(),
            (snapshot("A", at(20, 10), "80"), snapshot("B", at(20, 10), "84.99")),
            decision_at=at(20, 10),
            trading_sessions=self.sessions,
        )
        rotate = intramonth_decision(
            replace(self.state, last_trade_at=at(1) - timedelta(days=31)),
            event(),
            (snapshot("A", at(20, 10), "80"), snapshot("B", at(20, 10), "85")),
            decision_at=at(20, 10),
            trading_sessions=self.sessions,
        )
        self.assertEqual((no_hurdle.action, no_hurdle.execution_date), ("HOLD", None))
        self.assertEqual((rotate.action, rotate.execution_date), ("ROTATE", date(2026, 8, 24)))

    def test_incumbent_below_65_exits_to_cash(self):
        result = intramonth_decision(
            replace(self.state, last_trade_at=at(1) - timedelta(days=31)),
            event("A", event_type="PROGRAM_CANCELLATION"),
            (snapshot("A", at(20, 10), "64"), snapshot("B", at(20, 10), "60")),
            decision_at=at(20, 10),
            trading_sessions=self.sessions,
        )
        self.assertEqual((result.action, result.state.active_candidate_id), ("CASH", None))

    def test_cooldown_blocks_an_otherwise_valid_rotation(self):
        result = intramonth_decision(
            self.state,
            event(),
            (snapshot("A", at(20, 10), "80"), snapshot("B", at(20, 10), "90")),
            decision_at=at(20, 10),
            trading_sessions=self.sessions,
        )
        self.assertEqual((result.action, result.execution_date), ("HOLD", None))

    def test_event_id_is_append_only_and_deterministic(self):
        first = event()
        second = event()
        changed = event(source_hash="b" * 64)
        self.assertEqual(first.event_id, second.event_id)
        self.assertNotEqual(first.event_id, changed.event_id)
