from dataclasses import replace
from decimal import Decimal
from unittest import TestCase

from perpetual_engine.models import Holding, PolicyConfig, TaxCategory, money


class ModelTests(TestCase):
    def test_money_uses_exact_decimal_text(self):
        self.assertEqual(money("614590.86"), Decimal("614590.86"))

    def test_money_rejects_non_finite_values(self):
        with self.assertRaisesRegex(ValueError, "finite"):
            money("NaN")

    def test_holding_rejects_negative_quantity(self):
        with self.assertRaisesRegex(ValueError, "quantity"):
            Holding(
                symbol="SWDA",
                isin="IE00B4L5Y983",
                role="CORE",
                quantity=money("-1"),
                price_eur=money("129.22"),
                weighted_average_cost_eur=None,
                price_multiplier=money("1"),
                sale_increment=money("1"),
                tax_category=TaxCategory.ETF,
                tax_rate=money("0.26"),
                funding_priority=3,
                spread_bps=money("10"),
                commission_eur=money("19"),
                minimum_weight=money("0"),
                sale_permitted=True,
            )

    def test_config_rejects_floor_above_activation_threshold(self):
        with self.assertRaisesRegex(ValueError, "hard_floor"):
            replace(PolicyConfig.default(), hard_floor=money("900000"))

    def test_config_exposes_protected_monthly_gross_target(self):
        self.assertEqual(PolicyConfig.default().protected_monthly_gross, money("1800"))
