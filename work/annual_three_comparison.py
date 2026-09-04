from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "outputs" / "four_sleeve_v1" / "monthly_returns.csv"
OUTPUT_DIR = ROOT / "outputs" / "three_sleeve_efficiency"


def annual_metrics(frame: pd.DataFrame) -> pd.DataFrame:
    grouped = frame.groupby(frame.index.year)
    result = pd.DataFrame(index=sorted(frame.index.year.unique()))
    result.index.name = "year"
    result["months"] = grouped.size()
    for column in frame:
        result[f"{column}_return"] = grouped[column].apply(lambda x: (1 + x).prod() - 1)
        result[f"{column}_volatility"] = grouped[column].std(ddof=1) * math.sqrt(12)
    return result


def standalone_after_costs(
    series: pd.Series,
    initial_capital: float = 800_000,
    commission: float = 19,
    spread_bps: float = 10,
) -> pd.Series:
    result = series.astype(float).copy()
    cost = commission + initial_capital * spread_bps / 10_000
    result.iloc[0] = (initial_capital - cost) * (1 + result.iloc[0]) / initial_capital - 1
    return result


def main() -> None:
    monthly = pd.read_csv(INPUT, parse_dates=["month"]).set_index("month")
    comparison = pd.DataFrame(
        {
            "SWDA_PROXY": standalone_after_costs(monthly["WORLD"]),
            "PORTAFOGLIO_3": monthly["THREE_EQUITY_FIXED"].astype(float),
            "IWMO_PROXY": standalone_after_costs(monthly["MOMENTUM"]),
            "IWQU_PROXY": standalone_after_costs(monthly["QUALITY"]),
        }
    )
    np.testing.assert_allclose(comparison["SWDA_PROXY"], monthly["WORLD_100"].astype(float), atol=1e-12)

    annual = annual_metrics(comparison).loc[2007:2026]
    assert annual.index.tolist() == list(range(2007, 2027))
    assert annual.loc[2007:2025, "months"].eq(12).all()
    assert annual.loc[2026, "months"] == 5

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    annual.to_csv(OUTPUT_DIR / "annual_comparison.csv")
    metadata = {
        "period": "2006-06-30/2026-05-31",
        "annual_table": "2007-2025 complete years; 2026 January-May",
        "return_definition": "product of monthly (1+r) minus 1",
        "volatility_definition": "monthly sample standard deviation times sqrt(12)",
        "portfolio_weights": {"WORLD": 0.330470588235, "MOMENTUM": 0.266352941176, "QUALITY": 0.403176470589},
        "history_note": "20-year historical proxies in EUR, not live ETF share-class returns",
    }
    (OUTPUT_DIR / "annual_comparison_metadata.json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
