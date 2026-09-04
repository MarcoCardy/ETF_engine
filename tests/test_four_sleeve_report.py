from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest import TestCase

from perpetual_engine.four_sleeve_report import eur_sleeve_returns


class FourSleeveReportTests(TestCase):
    def test_excess_trend_gets_rf_once_and_all_usd_returns_are_converted_to_eur(self):
        may, june = date(2026, 5, 31), date(2026, 6, 30)
        total = {june: Decimal("0.10")}
        result = eur_sleeve_returns(
            {"WORLD": total, "MOMENTUM": total, "QUALITY": total},
            {june: Decimal("0.08")},
            {june: Decimal("0.02")},
            {may: Decimal("0.8"), june: Decimal("1.0")},
            june,
            june,
        )
        self.assertEqual(result["WORLD"][june], Decimal("0.375"))
        self.assertEqual(result["TREND"][june], Decimal("0.375"))

    def test_missing_final_month_fails_instead_of_truncating(self):
        may, june = date(2026, 5, 31), date(2026, 6, 30)
        with self.assertRaisesRegex(ValueError, "complete explicit calendar"):
            eur_sleeve_returns(
                {"WORLD": {}, "MOMENTUM": {}, "QUALITY": {}},
                {},
                {},
                {may: Decimal("0.8"), june: Decimal("1.0")},
                june,
                june,
            )
