from __future__ import annotations

import math
import statistics
from unittest import TestCase

import pandas as pd

from work.annual_three_comparison import annual_metrics


class AnnualThreeComparisonTests(TestCase):
    def test_annual_metrics_compound_returns_and_annualize_monthly_sample_volatility(self):
        frame = pd.DataFrame(
            {"TEST": [0.10, -0.05]},
            index=pd.to_datetime(["2024-01-31", "2024-02-29"]),
        )

        result = annual_metrics(frame)

        self.assertAlmostEqual(result.loc[2024, "TEST_return"], 0.045)
        self.assertAlmostEqual(
            result.loc[2024, "TEST_volatility"],
            statistics.stdev([0.10, -0.05]) * math.sqrt(12),
        )
        self.assertEqual(result.loc[2024, "months"], 2)
