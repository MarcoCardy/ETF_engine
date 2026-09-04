import sys

import numpy as np
import pandas as pd
import yfinance as yf
from pypfopt import EfficientFrontier, expected_returns, risk_models


def floor_ratchet(hwm_real, initial_hwm=614_590.86, hard_floor=600_000):
    steps = max(0, int((hwm_real - initial_hwm) // 100_000))
    return hard_floor + 50_000 * steps


def rule_e(capital_real, hwm_real, active=True):
    floor = floor_ratchet(hwm_real)
    if not active:
        return "GROWTH", 0.0, floor
    if capital_real < 600_000:
        return "HARD_STOP", 0.0, floor
    if capital_real < floor:
        # The intended protected payout must still be capped by the hard floor.
        return "PROTECTED", min(1_800.0, capital_real - 600_000), floor
    monthly = min((0.03 / 12) * capital_real, capital_real - floor)
    return "NORMAL", monthly, floor


prices = pd.DataFrame(
    {
        "A": [100, 101, 103, 102, 105, 107],
        "B": [100, 99, 100, 101, 101, 102],
        "C": [100, 102, 101, 104, 106, 108],
    },
    index=pd.date_range("2026-01-31", periods=6, freq="ME"),
)
mu = expected_returns.mean_historical_return(prices, frequency=12)
cov = risk_models.sample_cov(prices, frequency=12)
weights = EfficientFrontier(mu, cov).min_volatility()

cases = [
    (614_590.86, 614_590.86, False),
    (614_590.86, 614_590.86, True),
    (640_000, 720_000, True),
    (600_500, 720_000, True),
    (599_999, 720_000, True),
]

print(f"Python {sys.version.split()[0]}")
print(f"yfinance {yf.__version__}")
import pypfopt
print(f"PyPortfolioOpt {pypfopt.__version__}")
print(f"Optimization weight sum {sum(weights.values()):.12f}")
for capital, hwm, active in cases:
    state, payout, floor = rule_e(capital, hwm, active)
    print(f"capital={capital:.2f} hwm={hwm:.2f} active={active} state={state} monthly={payout:.2f} floor={floor:.2f}")
