import json
from pathlib import Path

import numpy as np
import pandas as pd

from analyze_three_sleeve import simulate


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "outputs" / "four_sleeve_v1" / "monthly_returns.csv"
OUTPUT = ROOT / "outputs" / "four_sleeve_v1" / "trend_contribution.json"
ASSETS = ["WORLD", "MOMENTUM", "QUALITY", "TREND"]
EQUITY_MIX = np.array([0.2809, 0.2264, 0.3427]) / 0.85


def main():
    frame = pd.read_csv(INPUT, parse_dates=["month"]).set_index("month")[ASSETS].astype(float)
    equity_return = frame.iloc[:, :3].to_numpy() @ EQUITY_MIX
    scenarios = {}
    for trend_weight in np.arange(0.0, 0.31, 0.05):
        weights = np.r_[EQUITY_MIX * (1 - trend_weight), trend_weight]
        scenarios[f"TREND_{int(round(trend_weight * 100)):02d}"] = {
            "weights": dict(zip(ASSETS, map(float, weights))),
            "full_20y": simulate(frame, weights),
            "recent_10y": simulate(frame.iloc[-120:], weights),
        }

    trend = frame["TREND"]
    annual = (1 + trend).groupby(frame.index.year).prod() - 1
    payload = {
        "window": {"start": str(frame.index.min().date()), "end": str(frame.index.max().date()), "months": len(frame)},
        "equity_mix_held_constant": dict(zip(ASSETS[:3], map(float, EQUITY_MIX))),
        "trend_standalone": simulate(frame[["TREND"]], np.array([1.0])),
        "trend_correlation": {
            **{asset: float(trend.corr(frame[asset])) for asset in ASSETS[:3]},
            "NORMALIZED_EQUITY_MIX": float(np.corrcoef(trend, equity_return)[0, 1]),
        },
        "trend_calendar_returns": {str(year): float(value) for year, value in annual.items()},
        "scenarios": scenarios,
    }
    reference = scenarios["TREND_15"]["full_20y"]
    assert len(frame) == 240
    assert abs(reference["cagr"] - 0.104675814478697) < 1e-12
    assert abs(reference["annualized_volatility"] - 0.11252286658326673) < 1e-12
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
