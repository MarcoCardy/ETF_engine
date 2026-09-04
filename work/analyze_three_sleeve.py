import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize


ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "outputs" / "four_sleeve_v1" / "monthly_returns.csv"
OUTPUT = ROOT / "outputs" / "three_sleeve_efficiency" / "analysis.json"
ASSETS = ["WORLD", "MOMENTUM", "QUALITY"]


def solve(objective, bounds=None):
    n = len(ASSETS)
    result = minimize(
        objective,
        np.full(n, 1 / n),
        method="SLSQP",
        bounds=bounds or [(0.0, 1.0)] * n,
        constraints={"type": "eq", "fun": lambda w: w.sum() - 1.0},
        options={"ftol": 1e-14, "maxiter": 2000},
    )
    if not result.success:
        raise RuntimeError(result.message)
    weights = np.where(result.x < 1e-10, 0.0, result.x)
    return weights / weights.sum()


def simulate(returns, weights):
    holdings = np.zeros(len(weights))
    nav0 = 800_000.0
    navs, monthly = [], []
    commissions = spreads = 0.0
    for i, (month, row) in enumerate(returns.iterrows()):
        pretrade_nav = nav0 if i == 0 else holdings.sum()
        if i == 0 or month.month == 1:
            targets = pretrade_nav * weights
            trades = targets - holdings
            active = np.abs(trades) > 1e-8
            commission = 19.0 * active.sum()
            spread = np.abs(trades) * 0.001
            holdings = targets - active * 19.0 - spread
            commissions += commission
            spreads += spread.sum()
        holdings *= 1.0 + row.to_numpy(float)
        nav = holdings.sum()
        monthly.append(nav / pretrade_nav - 1.0)
        navs.append(nav)
    monthly = np.asarray(monthly)
    navs = np.asarray(navs)
    peaks = np.maximum.accumulate(np.r_[nav0, navs])
    drawdowns = np.r_[nav0, navs] / peaks - 1.0
    return {
        "cagr": float((navs[-1] / nav0) ** (12 / len(monthly)) - 1),
        "annualized_volatility": float(monthly.std(ddof=1) * np.sqrt(12)),
        "max_drawdown": float(drawdowns.min()),
        "ending_nav_eur": float(navs[-1]),
        "commissions_eur": commissions,
        "spread_eur": float(spreads),
    }


def optimized_weights(frame):
    mu = frame.mean().to_numpy() * 12
    cov = frame.cov().to_numpy() * 12
    gmv = solve(lambda w: w @ cov @ w)
    max_sharpe = solve(lambda w: -(w @ mu) / np.sqrt(w @ cov @ w))

    def erc_objective(w):
        contributions = w * (cov @ w)
        return np.square(contributions / contributions.sum() - 1 / len(w)).sum()

    return {
        "MINIMUM_VARIANCE": gmv,
        "MAXIMUM_SHARPE_ZERO_RATE": max_sharpe,
        "EQUAL_RISK_CONTRIBUTION": solve(erc_objective),
    }, mu, cov


def main():
    frame = pd.read_csv(INPUT, parse_dates=["month"]).set_index("month")[ASSETS].astype(float)
    optimized, mu, cov = optimized_weights(frame)
    equal = np.full(len(ASSETS), 1 / len(ASSETS))

    solutions = {}
    for name, weights in {
        **optimized,
        "EQUAL_WEIGHT": equal,
    }.items():
        contributions = weights * (cov @ weights)
        solutions[name] = {
            "weights": dict(zip(ASSETS, map(float, weights))),
            "ex_ante_arithmetic_return": float(weights @ mu),
            "ex_ante_volatility": float(np.sqrt(weights @ cov @ weights)),
            "risk_contributions": dict(zip(ASSETS, map(float, contributions / contributions.sum()))),
            "backtest": simulate(frame, weights),
        }

    train, test = frame.iloc[:120], frame.iloc[120:]
    trained, _, _ = optimized_weights(train)
    payload = {
        "window": {"start": str(frame.index.min().date()), "end": str(frame.index.max().date()), "months": len(frame)},
        "currency": "EUR_UNHEDGED",
        "constraints": "LONG_ONLY_SUM_TO_ONE",
        "expected_return_method": "ANNUALIZED_MONTHLY_ARITHMETIC_MEAN",
        "asset_correlation": pd.DataFrame(frame.corr(), index=ASSETS, columns=ASSETS).to_dict(),
        "asset_backtests": {asset: simulate(frame, np.eye(len(ASSETS))[i]) for i, asset in enumerate(ASSETS)},
        "solutions": solutions,
        "chronological_split": {
            "train": {"start": str(train.index.min().date()), "end": str(train.index.max().date())},
            "test": {"start": str(test.index.min().date()), "end": str(test.index.max().date())},
            "trained_weights_and_test_results": {
                name: {"weights": dict(zip(ASSETS, map(float, weights))), "test_backtest": simulate(test, weights)}
                for name, weights in trained.items()
            },
        },
    }
    assert len(frame) == 240 and all(abs(sum(x["weights"].values()) - 1) < 1e-9 for x in solutions.values())
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
