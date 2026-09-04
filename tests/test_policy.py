from datetime import date
from decimal import Decimal
from unittest import TestCase

from perpetual_engine.models import DistributionState, Lifecycle, PolicyConfig, money
from perpetual_engine.policy import evaluate_policy, ratcheted_floor, real_capital, target_gross_real, update_hwm
from tests.helpers import cpi, snapshot


class PolicyTests(TestCase):
    def setUp(self):
        self.config = PolicyConfig.default()

    def test_real_capital_uses_cpi_ratio(self):
        self.assertEqual(real_capital(money("1020"), money("100"), money("102")), money("1000"))

    def test_hwm_never_decreases(self):
        self.assertEqual(update_hwm(money("700000"), money("650000")), money("700000"))
        self.assertEqual(update_hwm(money("700000"), money("710000")), money("710000"))

    def test_ratchet_changes_only_on_completed_steps(self):
        self.assertEqual(ratcheted_floor(money("714590.85"), self.config), money("600000"))
        self.assertEqual(ratcheted_floor(money("714590.86"), self.config), money("650000"))
        self.assertEqual(ratcheted_floor(money("814590.86"), self.config), money("700000"))

    def test_growth_always_requests_zero(self):
        decision = target_gross_real(money("900000"), money("700000"), Lifecycle.GROWTH, self.config)
        self.assertEqual(decision.state, DistributionState.GROWTH)
        self.assertEqual(decision.target_gross_real, Decimal("0"))

    def test_protected_target_is_gross_fiscal(self):
        result = target_gross_real(money("640000"), money("650000"), Lifecycle.DISTRIBUTION, self.config)
        self.assertEqual(result.target_gross_real, money("1800"))
        self.assertEqual(result.maximum_total_outflow_real, money("40000"))

    def test_hard_stop_requests_zero_gross_target(self):
        stopped = target_gross_real(money("599999"), money("650000"), Lifecycle.DISTRIBUTION, self.config)
        self.assertEqual(stopped.target_gross_real, money("0"))

    def test_normal_target_is_three_percent_divided_monthly(self):
        decision = target_gross_real(money("800000"), money("650000"), Lifecycle.DISTRIBUTION, self.config)
        self.assertEqual(decision.target_gross_real, money("2000"))

    def test_missing_cpi_fails_closed(self):
        decision = evaluate_policy(snapshot(cash="614590.86", cash_minimum="0"), self.config, activation_date=None)
        self.assertEqual(decision.state, DistributionState.DATA_INCOMPLETE)
        self.assertEqual(decision.target_gross_real, money("0"))
        self.assertIn("CPI", decision.errors[0])

    def test_activation_below_threshold_is_rejected(self):
        base_cpi = cpi("100")
        current_cpi = cpi("100")
        decision = evaluate_policy(
            snapshot(cash="700000", cash_minimum="0", cpi_base=base_cpi, cpi_current=current_cpi),
            self.config,
            activation_date=date(2026, 8, 19),
        )
        self.assertEqual(decision.state, DistributionState.DATA_INCOMPLETE)
        self.assertIn("activation threshold", decision.errors[0])
