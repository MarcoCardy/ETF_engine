from unittest import TestCase

from perpetual_engine.funding import fund_withdrawal
from perpetual_engine.models import TaxCategory, money
from tests.helpers import decision, holding, snapshot, taxable_snapshot


class FundingTests(TestCase):
    def test_cash_above_minimum_is_used_first(self):
        result = fund_withdrawal(
            snapshot(cash="1000", cash_minimum="500"),
            decision(target="300", cap="300"),
        )
        self.assertEqual(result.delivered_net_real, money("300"))
        self.assertEqual(result.cash_used_real, money("300"))
        self.assertEqual(result.trades, ())

    def test_total_outflow_never_exceeds_floor_buffer(self):
        result = fund_withdrawal(taxable_snapshot(), decision(target="1800", cap="500"))
        self.assertLessEqual(result.total_outflow_real, money("500"))
        self.assertLess(result.delivered_net_real, money("500"))

    def test_tax_reduces_net_without_gross_up(self):
        result = fund_withdrawal(taxable_snapshot(), decision(target="1800", cap="5000"))
        self.assertEqual(result.total_outflow_real, money("1800"))
        self.assertLess(result.delivered_net_real, money("1800"))

    def test_missing_pmc_makes_asset_ineligible(self):
        result = fund_withdrawal(
            taxable_snapshot(weighted_average_cost_eur=None),
            decision(target="1000", cap="5000"),
        )
        self.assertEqual(result.delivered_net_real, money("0"))
        self.assertIn("weighted_average_cost", result.errors[0])

    def test_government_security_precedes_etf(self):
        government = holding(
            symbol="BTP",
            isin="IT0000000001",
            tax_category=TaxCategory.GOVERNMENT,
            tax_rate=money("0.125"),
            funding_priority=2,
            commission_eur=money("0"),
            spread_bps=money("0"),
        )
        etf = holding(symbol="ETF", isin="IE0000000002", funding_priority=3)
        result = fund_withdrawal(
            snapshot(cash="0", cash_minimum="0", holdings=(etf, government)),
            decision(target="50", cap="500"),
        )
        self.assertEqual(result.trades[0].symbol, "BTP")
        self.assertEqual(len(result.trades), 1)

    def test_lower_marginal_tax_cost_precedes_higher_cost(self):
        low = holding(
            symbol="LOW",
            isin="IE0000000003",
            weighted_average_cost_eur=money("99"),
            commission_eur=money("0"),
            spread_bps=money("0"),
        )
        high = holding(
            symbol="HIGH",
            isin="IE0000000004",
            weighted_average_cost_eur=money("50"),
            commission_eur=money("0"),
            spread_bps=money("0"),
        )
        result = fund_withdrawal(
            snapshot(cash="0", cash_minimum="0", holdings=(high, low)),
            decision(target="50", cap="500"),
        )
        self.assertEqual(result.trades[0].symbol, "LOW")

    def test_etf_loss_does_not_offset_positive_etf_income(self):
        loss = holding(
            symbol="LOSS",
            isin="IE0000000005",
            quantity=money("1"),
            price_eur=money("100"),
            weighted_average_cost_eur=money("110"),
            funding_priority=2,
            commission_eur=money("0"),
            spread_bps=money("0"),
        )
        gain = holding(
            symbol="GAIN",
            isin="IE0000000006",
            price_eur=money("100"),
            weighted_average_cost_eur=money("90"),
            funding_priority=3,
            commission_eur=money("0"),
            spread_bps=money("0"),
        )
        result = fund_withdrawal(
            snapshot(cash="0", cash_minimum="0", holdings=(gain, loss)),
            decision(target="150", cap="500"),
        )
        self.assertEqual(result.realized_etf_losses_real, money("10"))
        self.assertEqual(result.tax_real, money("2.60"))

    def test_minimum_weight_blocks_sale(self):
        protected = holding(
            symbol="CORE",
            isin="IE0000000007",
            quantity=money("50"),
            minimum_weight=money("0.50"),
            commission_eur=money("0"),
            spread_bps=money("0"),
        )
        result = fund_withdrawal(
            snapshot(cash="5000", cash_minimum="5000", holdings=(protected,)),
            decision(target="50", cap="500"),
        )
        self.assertEqual(result.delivered_net_real, money("0"))

    def test_outflow_reconciles_to_net_tax_and_costs(self):
        result = fund_withdrawal(taxable_snapshot(), decision(target="1800", cap="5000"))
        self.assertEqual(
            result.total_outflow_real,
            result.delivered_net_real + result.tax_real + result.commission_real + result.spread_real,
        )
