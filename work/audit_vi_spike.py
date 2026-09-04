"""Throwaway audit of the VI snapshot recovered from 'Allegato del motore'."""

import json

import numpy as np
import pandas as pd
import yfinance as yf

yf.set_tz_cache_location("work/yfinance-cache")


VALUES = {
    "SWDA": 143_919.36,
    "IWRD": 76_833.10,
    "IWVL": 41_913.60,
    "CINA": 5_173.00,
    "AIAI": 17_982.50,
    "LWLD": 35_299.18,
    "EQQQ": 70_707.00,
    "EST": 23_730.00,
    "SXLK": 62_242.70,
    "RBOT": 9_600.00,
    "BTP": 113_570.10,
    "IHYG": 14_793.84,
    "CASH": 1_950.90,
}

TICKERS = {
    "SWDA": "SWDA.MI",
    "IWRD": "IWRD.MI",
    "IWVL": "IWVL.MI",
    "CINA": "CINA.MI",
    "AIAI": "AIAI.MI",
    "EQQQ": "EQQQ.MI",
    "EST": "EST.MI",
    "SXLK": "SXLK.MI",
    "RBOT": "RBOT.MI",
    "BTP": "IBGS.MI",  # proxy: EUR government 1-3y, not the two exact BTPs
    "IHYG": "IHYG.MI",
}


def max_drawdown(r: pd.Series) -> float:
    wealth = (1 + r).cumprod()
    return float((wealth / wealth.cummax() - 1).min())


raw = yf.download(
    list(TICKERS.values()),
    period="10y",
    auto_adjust=True,
    progress=False,
    group_by="column",
    threads=True,
)
prices = raw["Close"].rename(columns={v: k for k, v in TICKERS.items()})
returns = prices.pct_change(fill_method=None)

# LWLD launched only in 2025. For historical risk (not performance), use a
# transparent 2x daily SWDA proxy; funding/TER barely affect beta/volatility.
returns["LWLD"] = 2 * returns["SWDA"]
returns["CASH"] = 0.0

assets = list(VALUES)
common = returns[assets].dropna()
weights = pd.Series(VALUES, dtype=float) / sum(VALUES.values())
portfolio = common.mul(weights).sum(axis=1)
benchmark = common["SWDA"]
cov = common.cov() * 252
variance = float(weights @ cov @ weights)
risk_contrib = weights * (cov @ weights) / variance

metrics = {
    "snapshot_total": round(sum(VALUES.values()), 2),
    "data_start": str(common.index.min().date()),
    "data_end": str(common.index.max().date()),
    "observations": len(common),
    "gross_notional": round(1 + weights["LWLD"], 4),
    "equity_accounting": round(weights[["SWDA", "IWRD", "IWVL", "CINA", "AIAI", "LWLD", "EQQQ", "EST", "SXLK", "RBOT"]].sum(), 4),
    "equity_economic": round(weights[["SWDA", "IWRD", "IWVL", "CINA", "AIAI", "EQQQ", "EST", "SXLK", "RBOT"]].sum() + 2 * weights["LWLD"], 4),
    "annualized_volatility": round(float(portfolio.std() * np.sqrt(252)), 4),
    "beta_vs_swda": round(float(portfolio.cov(benchmark) / benchmark.var()), 4),
    "correlation_vs_swda": round(float(portfolio.corr(benchmark)), 4),
    "max_drawdown": round(max_drawdown(portfolio), 4),
    "historical_var_95_daily": round(float(portfolio.quantile(0.05)), 4),
    "2020_return": round(float((1 + portfolio.loc["2020"]).prod() - 1), 4),
    "2022_return": round(float((1 + portfolio.loc["2022"]).prod() - 1), 4),
    "pair_correlations": {
        "SWDA_IWRD": round(float(common["SWDA"].corr(common["IWRD"])), 4),
        "EQQQ_SXLK": round(float(common["EQQQ"].corr(common["SXLK"])), 4),
        "AIAI_RBOT": round(float(common["AIAI"].corr(common["RBOT"])), 4),
        "SWDA_IWVL": round(float(common["SWDA"].corr(common["IWVL"])), 4),
    },
    "risk_contribution": {k: round(float(v), 4) for k, v in risk_contrib.sort_values(ascending=False).items()},
    "weights": {k: round(float(v), 4) for k, v in weights.items()},
    "missing_counts": {k: int(v) for k, v in prices.isna().sum().items()},
}

assert abs(weights.sum() - 1) < 1e-12
assert abs(metrics["gross_notional"] - (1 + weights["LWLD"])) < 1e-4
print(json.dumps(metrics, indent=2))
