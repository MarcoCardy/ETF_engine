from __future__ import annotations

import json
import math
import os
import hashlib
import csv
import io
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Callable, Mapping, Sequence

import numpy as np

from perpetual_engine.point_in_time import ObservationRow, asof_select
from perpetual_engine.io import canonical_json


TRADING_DAYS = 252

FEATURE_CLASSIFICATION = {
    "VIX": "HIGH_FREQUENCY_OBSERVED",
    "US_TREASURY_10Y_NOMINAL": "HIGH_FREQUENCY_OBSERVED",
    "US_TREASURY_10Y_REAL": "HIGH_FREQUENCY_OBSERVED",
    "US_10Y_BREAKEVEN": "HIGH_FREQUENCY_OBSERVED",
    "EURUSD": "HIGH_FREQUENCY_OBSERVED",
    "VALUE_WORLD_RELATIVE": "HIGH_FREQUENCY_OBSERVED",
    "MOMENTUM_WORLD_RELATIVE": "HIGH_FREQUENCY_OBSERVED",
    "QUALITY_WORLD_RELATIVE": "HIGH_FREQUENCY_OBSERVED",
    "SMALL_VALUE_WORLD_RELATIVE": "HIGH_FREQUENCY_OBSERVED",
    "MSCI_WORLD_FORWARD_EARNINGS_YIELD": "SLOW_MOVING_MACRO",
    "DAMODARAN_ERP": "SLOW_MOVING_MACRO",
    "US_CPI": "SLOW_MOVING_MACRO",
}
_RATES = ("US_TREASURY_10Y_NOMINAL", "US_TREASURY_10Y_REAL", "US_10Y_BREAKEVEN")
_VALUATION = ("MSCI_WORLD_FORWARD_EARNINGS_YIELD", "DAMODARAN_ERP")
_FACTORS = ("VALUE_WORLD_RELATIVE", "MOMENTUM_WORLD_RELATIVE", "QUALITY_WORLD_RELATIVE", "SMALL_VALUE_WORLD_RELATIVE")
ABLATION_GROUPS = {
    "A_MARKET_ONLY": (),
    "B_MARKET_VOLATILITY": ("VIX",),
    "C_RATES": ("VIX", *_RATES),
    "D_VALUATION": ("VIX", *_RATES, *_VALUATION),
    "E_FACTORS": ("VIX", *_RATES, *_VALUATION, *_FACTORS),
    "F_FX": ("VIX", *_RATES, *_VALUATION, *_FACTORS, "EURUSD"),
    "G_FULL": ("VIX", *_RATES, *_VALUATION, *_FACTORS, "EURUSD", "US_CPI"),
}


@dataclass(frozen=True)
class RiskConfig:
    path: Path
    project_root: Path
    mode: str
    model_id: str
    model_revision: str
    device: str
    context_length: int
    horizons: tuple[int, ...]
    quantiles: tuple[float, ...]
    volatility_windows: tuple[int, int]
    ewma_lambda: float
    sigma_forecast_weights: tuple[float, float]
    sigma_risk_weights: tuple[float, float, float]
    chronos_volatility_modes: tuple[str, ...]
    beta_volatility_target: float
    beta_cap: float
    risk_regime_thresholds: tuple[float, float, float]
    price_config: Path
    portfolio_snapshot: Path
    output_root: Path


def load_risk_config(path: Path) -> RiskConfig:
    path = path.resolve()
    project_root = path.parent.parent.resolve()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("Chronos risk configuration is malformed") from error
    expected = {
        "schema_version", "mode", "model_id", "model_revision", "device", "context_length",
        "horizons", "quantiles", "volatility_windows", "ewma_lambda", "sigma_forecast_weights",
        "sigma_risk_weights", "chronos_volatility_modes", "beta_volatility_target", "beta_cap",
        "risk_regime_thresholds", "price_config", "portfolio_snapshot", "output_root",
    }
    if not isinstance(data, dict) or set(data) != expected or data["schema_version"] != "CHRONOS_RISK_CONFIG_V1":
        raise ValueError("Chronos risk configuration schema is invalid")
    if data["mode"] != "SHADOW" or data["model_id"] != "amazon/chronos-2" or data["device"] != "cpu":
        raise ValueError("Chronos risk must use the supported shadow model")
    horizons = tuple(data["horizons"])
    quantiles = tuple(float(value) for value in data["quantiles"])
    windows = tuple(data["volatility_windows"])
    forecast_weights = tuple(float(value) for value in data["sigma_forecast_weights"])
    risk_weights = tuple(float(value) for value in data["sigma_risk_weights"])
    regimes = tuple(float(value) for value in data["risk_regime_thresholds"])
    if horizons != (5, 10, 20) or quantiles != (0.1, 0.25, 0.5, 0.75, 0.9) or windows != (20, 60):
        raise ValueError("Chronos risk horizons, quantiles or windows are invalid")
    _weighted_volatility((1.0, 1.0), forecast_weights, "sigma forecast")
    _weighted_volatility((1.0, 1.0, 1.0), risk_weights, "sigma risk")
    if data["chronos_volatility_modes"] != ["Q50", "Q90"] or not 0 < float(data["ewma_lambda"]) < 1:
        raise ValueError("Chronos risk volatility settings are invalid")
    if sorted(regimes) != list(regimes) or len(regimes) != 3 or any(value <= 0 for value in regimes):
        raise ValueError("Chronos risk regime thresholds are invalid")
    resolved = []
    for name in ("price_config", "portfolio_snapshot", "output_root"):
        raw = data[name]
        candidate = (project_root / raw).resolve() if isinstance(raw, str) and not Path(raw).is_absolute() else Path()
        if not isinstance(raw, str) or not candidate.is_relative_to(project_root):
            raise ValueError(f"Chronos risk {name} path is invalid")
        resolved.append(candidate)
    return RiskConfig(
        path, project_root, data["mode"], data["model_id"], data["model_revision"], data["device"],
        int(data["context_length"]), horizons, quantiles, windows, float(data["ewma_lambda"]),
        forecast_weights, risk_weights, tuple(data["chronos_volatility_modes"]),
        float(data["beta_volatility_target"]), float(data["beta_cap"]), regimes, *resolved,
    )


def _finite(values: Sequence[float], label: str) -> np.ndarray:
    result = np.asarray(values, dtype=float)
    if result.ndim != 1 or not len(result) or not np.isfinite(result).all():
        raise ValueError(f"{label} must be a non-empty finite one-dimensional series")
    return result


def daily_returns(prices: Sequence[float]) -> np.ndarray:
    values = _finite(prices, "prices")
    if len(values) < 2 or np.any(values <= 0):
        raise ValueError("prices require at least two positive observations")
    result = values[1:] / values[:-1] - 1.0
    result.setflags(write=False)
    return result


def future_realized_volatility(returns: Sequence[float], horizon: int) -> np.ndarray:
    values = _finite(returns, "returns")
    if type(horizon) is not int or horizon < 2 or len(values) < horizon:
        raise ValueError("realized-volatility horizon is invalid")
    result = np.asarray([
        np.std(values[start:start + horizon], ddof=1) * math.sqrt(TRADING_DAYS)
        for start in range(len(values) - horizon + 1)
    ])
    result.setflags(write=False)
    return result


def build_horizon_targets(returns: Sequence[float], horizon: int) -> np.ndarray:
    values = _finite(returns, "returns")
    volatility = future_realized_volatility(values, horizon)
    compounded = np.asarray([
        np.prod(1.0 + values[start:start + horizon]) - 1.0
        for start in range(len(values) - horizon + 1)
    ])
    if np.any(values <= -1.0):
        raise ValueError("daily return cannot be at or below -100%")
    result = np.vstack((compounded, volatility)).astype(np.float32)
    result.setflags(write=False)
    return result


@dataclass(frozen=True)
class MarketRiskForecast:
    horizon: int
    return_quantiles: tuple[float, ...]
    volatility_quantiles: tuple[float, ...]


@dataclass(frozen=True)
class WalkForwardForecast:
    forecast_cutoff: np.datetime64
    target_end: np.datetime64
    horizon: int
    actual_return: float
    actual_volatility: float
    return_quantiles: tuple[float, ...]
    volatility_quantiles: tuple[float, ...]
    sigma20: float
    sigma60: float
    sigma_forecast: float
    ewma: float
    naive_volatility: float
    zero_return: float
    historical_mean_return: float
    momentum_return: float
    external_scaling: str = "NONE"


def load_risk_predictor(config: RiskConfig) -> Callable:
    previous_offline = os.environ.get("HF_HUB_OFFLINE")
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        from chronos import Chronos2Pipeline

        pipeline = Chronos2Pipeline.from_pretrained(
            config.model_id,
            revision=config.model_revision,
            device_map=config.device,
            local_files_only=True,
        )
    finally:
        if previous_offline is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = previous_offline

    def predict(items, prediction_length, quantile_levels):
        result = pipeline.predict_quantiles(
            items,
            prediction_length=prediction_length,
            quantile_levels=list(quantile_levels),
            batch_size=100,
            context_length=config.context_length,
        )
        quantiles = result[0] if isinstance(result, tuple) else result
        return [tensor.detach().cpu().numpy() for tensor in quantiles]

    return predict


def forecast_market_risk(
    config: RiskConfig,
    returns: Sequence[float],
    *,
    covariates: Mapping[str, Sequence[float]] | None = None,
    predictor: Callable | None = None,
) -> tuple[MarketRiskForecast, ...]:
    values = _finite(returns, "returns")
    if np.any(values <= -1.0):
        raise ValueError("daily return cannot be at or below -100%")
    prepared_covariates = {}
    for name, series in (covariates or {}).items():
        if not isinstance(name, str) or not name:
            raise ValueError("covariate name is invalid")
        prepared = _finite(series, f"covariate {name}")
        if len(prepared) != len(values):
            raise ValueError(f"covariate {name} is not aligned with returns")
        prepared_covariates[name] = prepared
    predict = predictor or load_risk_predictor(config)
    forecasts = []
    for horizon in config.horizons:
        targets = build_horizon_targets(values, horizon)
        if targets.shape[1] < 2:
            raise ValueError(f"horizon {horizon} has insufficient target history")
        item: dict[str, object] = {"target": targets}
        if prepared_covariates:
            item["past_covariates"] = {
                name: np.asarray(series[horizon - 1:], dtype=np.float32)
                for name, series in prepared_covariates.items()
            }
        raw = predict([item], horizon, list(config.quantiles))
        if not isinstance(raw, (list, tuple)) or len(raw) != 1:
            raise ValueError("Chronos risk result count is invalid")
        result = np.asarray(raw[0], dtype=float)
        expected = (2, horizon, len(config.quantiles))
        if result.shape != expected or not np.isfinite(result).all():
            raise ValueError("Chronos risk result shape or values are invalid")
        final = result[:, -1, :]
        if np.any(np.diff(final, axis=1) < 0):
            raise ValueError("Chronos risk quantiles are not ordered")
        return_quantiles = tuple(float(value) for value in final[0])
        volatility_quantiles = tuple(max(0.0, float(value)) for value in final[1])
        forecasts.append(MarketRiskForecast(horizon, return_quantiles, volatility_quantiles))
    return tuple(forecasts)


def align_point_in_time(
    dates: Sequence[np.datetime64], rows: Sequence[ObservationRow], series_id: str
) -> np.ndarray:
    observed = np.asarray(dates, dtype="datetime64[D]")
    if observed.ndim != 1 or not len(observed) or not isinstance(series_id, str) or not series_id:
        raise ValueError("point-in-time alignment inputs are invalid")
    candidates = tuple(row for row in rows if row.series_id == series_id)
    if not candidates:
        raise ValueError(f"point-in-time series {series_id} is missing")
    result = []
    for value in observed:
        day = date.fromisoformat(str(value))
        cutoff = datetime.combine(day, time.max, tzinfo=timezone.utc)
        selected = asof_select(candidates, cutoff)
        result.append(float("nan") if selected is None else float(selected.value))
    output = np.asarray(result)
    output.setflags(write=False)
    return output


def walk_forward_evaluate(
    config: RiskConfig,
    dates: Sequence[np.datetime64],
    returns: Sequence[float],
    *,
    predictor: Callable | None = None,
    covariates: Mapping[str, Sequence[float]] | None = None,
    min_context: int = 252,
    origin_step: int = 5,
    max_origins: int | None = None,
) -> tuple[WalkForwardForecast, ...]:
    observed = np.asarray(dates, dtype="datetime64[D]")
    values = _finite(returns, "returns")
    if observed.ndim != 1 or len(observed) != len(values) or np.any(observed[1:] <= observed[:-1]):
        raise ValueError("walk-forward dates and returns are not aligned")
    if type(min_context) is not int or min_context < max(config.volatility_windows) or type(origin_step) is not int or origin_step < 1:
        raise ValueError("walk-forward context or step is invalid")
    last_origin = len(values) - max(config.horizons) - 1
    origins = list(range(min_context - 1, last_origin + 1, origin_step))
    if max_origins is not None:
        if type(max_origins) is not int or max_origins < 1:
            raise ValueError("walk-forward maximum origins is invalid")
        origins = origins[-max_origins:]
    prepared_covariates = {name: _finite(series, f"covariate {name}") for name, series in (covariates or {}).items()}
    if any(len(series) != len(values) for series in prepared_covariates.values()):
        raise ValueError("walk-forward covariates are not aligned")
    predict = predictor or load_risk_predictor(config)
    output = []
    for horizon in config.horizons:
        items = []
        for origin in origins:
            target = build_horizon_targets(values[:origin + 1], horizon)
            item: dict[str, object] = {"target": target}
            if prepared_covariates:
                item["past_covariates"] = {
                    name: np.asarray(series[horizon - 1:origin + 1], dtype=np.float32)
                    for name, series in prepared_covariates.items()
                }
            items.append(item)
        if not items:
            continue
        results = predict(items, horizon, list(config.quantiles))
        if not isinstance(results, (list, tuple)) or len(results) != len(items):
            raise ValueError("Chronos walk-forward result count is invalid")
        for origin, raw in zip(origins, results):
            result = np.asarray(raw, dtype=float)
            if result.shape != (2, horizon, len(config.quantiles)) or not np.isfinite(result).all():
                raise ValueError("Chronos walk-forward result shape or values are invalid")
            final = result[:, -1, :]
            if np.any(np.diff(final, axis=1) < 0):
                raise ValueError("Chronos walk-forward quantiles are not ordered")
            future = values[origin + 1:origin + horizon + 1]
            context = values[:origin + 1]
            sigma20 = historical_volatility(context, 20)
            sigma60 = historical_volatility(context, 60)
            output.append(WalkForwardForecast(
                observed[origin], observed[origin + horizon], horizon,
                float(np.prod(1.0 + future) - 1.0),
                float(np.std(future, ddof=1) * math.sqrt(TRADING_DAYS)),
                tuple(float(value) for value in final[0]),
                tuple(max(0.0, float(value)) for value in final[1]),
                sigma20, sigma60, sigma_forecast(sigma20, sigma60),
                ewma_volatility(context, config.ewma_lambda), historical_volatility(context, horizon),
                0.0, float((1.0 + np.mean(context[-min(252, len(context)):])) ** horizon - 1.0),
                float(np.prod(1.0 + context[-horizon:]) - 1.0),
            ))
    return tuple(sorted(output, key=lambda row: (row.forecast_cutoff, row.horizon)))


def probabilistic_metrics(
    actual: Sequence[float], forecasts: Sequence[Sequence[float]], quantiles: Sequence[float], *, target: str
) -> dict[str, float]:
    observed = _finite(actual, "actual values")
    predicted = np.asarray(forecasts, dtype=float)
    levels = _finite(quantiles, "quantiles")
    if predicted.shape != (len(observed), len(levels)) or not np.isfinite(predicted).all():
        raise ValueError("probabilistic forecasts are invalid")
    if np.any(np.diff(predicted, axis=1) < 0) or np.any(levels[1:] <= levels[:-1]) or 0.5 not in levels:
        raise ValueError("forecast quantiles are invalid")
    median = predicted[:, list(levels).index(0.5)]
    errors = median - observed
    metrics = {
        "mae_q50": float(np.mean(np.abs(errors))),
        "rmse_q50": float(np.sqrt(np.mean(errors**2))),
        "bias_q50": float(np.mean(errors)),
        "pearson": _correlation(observed, median),
        "spearman": _correlation(_ranks(observed), _ranks(median)),
        "forecast_dispersion": float(np.mean(predicted[:, -1] - predicted[:, 0])),
    }
    losses = []
    for index, level in enumerate(levels):
        residual = observed - predicted[:, index]
        losses.extend(np.maximum(level * residual, (level - 1.0) * residual))
        metrics[f"coverage_q{int(round(level * 100)):02d}"] = float(np.mean(observed <= predicted[:, index]))
    metrics["mean_pinball_loss"] = float(np.mean(losses))
    if target == "RETURN":
        metrics["sign_accuracy"] = float(np.mean(np.sign(observed) == np.sign(median)))
        downside = observed < 0
        metrics["downside_mae_q50"] = float(np.mean(np.abs(errors[downside]))) if np.any(downside) else float("nan")
    elif target == "VOLATILITY":
        ratio = np.maximum(observed, 1e-12) ** 2 / np.maximum(median, 1e-12) ** 2
        metrics["qlike"] = float(np.mean(ratio - np.log(ratio) - 1.0))
    else:
        raise ValueError("metric target must be RETURN or VOLATILITY")
    return metrics


def _correlation(left: np.ndarray, right: np.ndarray) -> float:
    return float(np.corrcoef(left, right)[0, 1]) if len(left) > 1 and np.std(left) > 0 and np.std(right) > 0 else float("nan")


def _ranks(values: np.ndarray) -> np.ndarray:
    order = np.argsort(values, kind="mergesort")
    ranks = np.empty(len(values), dtype=float)
    ranks[order] = np.arange(len(values), dtype=float)
    return ranks


def portfolio_risk_utility(returns: Sequence[float], *, turnover: float = 0.0) -> dict[str, float]:
    values = _finite(returns, "portfolio returns")
    if np.any(values <= -1.0) or not math.isfinite(turnover) or turnover < 0:
        raise ValueError("portfolio returns or turnover are invalid")
    wealth = np.cumprod(1.0 + values)
    drawdowns = wealth / np.maximum.accumulate(np.r_[1.0, wealth])[-len(wealth):] - 1.0
    maximum_drawdown = float(np.min(drawdowns))
    annualized_return = float(wealth[-1] ** (TRADING_DAYS / len(values)) - 1.0)
    volatility = float(np.std(values, ddof=1) * math.sqrt(TRADING_DAYS)) if len(values) > 1 else 0.0
    downside = float(math.sqrt(np.mean(np.minimum(values, 0.0) ** 2)) * math.sqrt(TRADING_DAYS))
    mean = float(np.mean(values) * TRADING_DAYS)
    cutoff = float(np.quantile(values, 0.05))
    return {
        "annualized_return": annualized_return,
        "annualized_volatility": volatility,
        "downside_deviation": downside,
        "sharpe": mean / volatility if volatility else float("nan"),
        "sortino": mean / downside if downside else float("nan"),
        "calmar": annualized_return / abs(maximum_drawdown) if maximum_drawdown else float("nan"),
        "max_drawdown": maximum_drawdown,
        "cvar_05": float(np.mean(values[values <= cutoff])),
        "turnover": float(turnover),
    }


def build_current_risk_report(
    config: RiskConfig,
    dates: Sequence[np.datetime64],
    returns: Sequence[float],
    *,
    dataset_version: str,
    predictor: Callable | None = None,
    covariates: Mapping[str, Sequence[float]] | None = None,
    issued_at: datetime | None = None,
) -> dict[str, object]:
    observed = np.asarray(dates, dtype="datetime64[D]")
    values = _finite(returns, "returns")
    if observed.ndim != 1 or len(observed) != len(values) or np.any(observed[1:] <= observed[:-1]):
        raise ValueError("report dates and returns are not aligned")
    if not isinstance(dataset_version, str) or not dataset_version:
        raise ValueError("dataset version is missing")
    issued = issued_at or datetime.now(timezone.utc)
    if issued.tzinfo is None or issued.utcoffset() != timezone.utc.utcoffset(issued):
        raise ValueError("forecast timestamp must be UTC")
    sigma20 = historical_volatility(values, 20)
    sigma60 = historical_volatility(values, 60)
    baseline = sigma_forecast(sigma20, sigma60)
    forecasts = forecast_market_risk(config, values, covariates=covariates, predictor=predictor)
    labels = tuple(f"Q{int(round(level * 100)):02d}" for level in config.quantiles)
    forecast_payload = {
        f"{item.horizon}d": {
            "return_quantiles": dict(zip(labels, item.return_quantiles)),
            "volatility_quantiles": dict(zip(labels, item.volatility_quantiles)),
            "forecast_dispersion": item.return_quantiles[-1] - item.return_quantiles[0],
        }
        for item in forecasts
    }
    primary = next(item for item in forecasts if item.horizon == 20)
    q50 = primary.volatility_quantiles[config.quantiles.index(0.5)]
    q90 = primary.volatility_quantiles[config.quantiles.index(0.9)]
    normal, caution, high = config.risk_regime_thresholds
    regime = "NORMAL" if q90 <= normal else "CAUTION" if q90 <= caution else "HIGH_RISK" if q90 <= high else "STRESS"
    try:
        package_version = version("chronos-forecasting")
    except PackageNotFoundError:
        package_version = "unavailable"
    return {
        "schema_version": "CHRONOS_RISK_REPORT_V1",
        "mode": config.mode,
        "forecast_timestamp": issued.isoformat(),
        "data_cutoff": str(observed[-1]),
        "dataset_version": dataset_version,
        "model": {
            "id": config.model_id, "revision": config.model_revision, "package_version": package_version,
            "device": config.device, "context_length": config.context_length,
        },
        "target": "MSCI_WORLD_OR_SWDA_RETURN_AND_FUTURE_REALIZED_VOLATILITY",
        "covariates": sorted((covariates or {}).keys()),
        "quantiles": list(config.quantiles),
        "baselines": {
            "sigma20": sigma20, "sigma60": sigma60, "sigma_forecast": baseline,
            "ewma_094": ewma_volatility(values, config.ewma_lambda), "naive_persistence": sigma20,
        },
        "forecasts": forecast_payload,
        "chronos_vol_median": q50,
        "chronos_vol_conservative": q90,
        "sigma_risk_q50": sigma_risk(sigma20, sigma60, q50, config.sigma_risk_weights),
        "sigma_risk_q90": sigma_risk(sigma20, sigma60, q90, config.sigma_risk_weights),
        "chronos_vs_baseline": "ABOVE" if q90 > baseline else "BELOW_OR_EQUAL",
        "risk_regime": regime,
        "portfolio_comparison": {"status": "UNAVAILABLE_MISSING_POINT_IN_TIME_ERP_TREND"},
        "drawdown_probability_status": "NOT_COMPUTED_NO_VALIDATED_COHERENT_PATHS",
        "external_scaling": "NONE",
    }


def summarize_walk_forward(
    rows: Sequence[WalkForwardForecast], quantiles: Sequence[float]
) -> dict[str, object]:
    if not rows:
        raise ValueError("walk-forward rows are missing")
    output = {}
    for horizon in sorted({row.horizon for row in rows}):
        selected = tuple(row for row in rows if row.horizon == horizon)
        actual_return = np.asarray([row.actual_return for row in selected])
        actual_volatility = np.asarray([row.actual_volatility for row in selected])
        output[f"{horizon}d"] = {
            "origin_count": len(selected),
            "chronos_return": probabilistic_metrics(
                actual_return, [row.return_quantiles for row in selected], quantiles, target="RETURN",
            ),
            "chronos_volatility": probabilistic_metrics(
                actual_volatility, [row.volatility_quantiles for row in selected], quantiles, target="VOLATILITY",
            ),
            "return_baselines": {
                "zero": _point_metrics(actual_return, np.asarray([row.zero_return for row in selected]), "RETURN"),
                "historical_mean": _point_metrics(actual_return, np.asarray([row.historical_mean_return for row in selected]), "RETURN"),
                "momentum_persistence": _point_metrics(actual_return, np.asarray([row.momentum_return for row in selected]), "RETURN"),
            },
            "volatility_baselines": {
                "sigma20": _point_metrics(actual_volatility, np.asarray([row.sigma20 for row in selected]), "VOLATILITY"),
                "sigma60": _point_metrics(actual_volatility, np.asarray([row.sigma60 for row in selected]), "VOLATILITY"),
                "sigma_forecast": _point_metrics(actual_volatility, np.asarray([row.sigma_forecast for row in selected]), "VOLATILITY"),
                "ewma_094": _point_metrics(actual_volatility, np.asarray([row.ewma for row in selected]), "VOLATILITY"),
                "naive_persistence": _point_metrics(actual_volatility, np.asarray([row.naive_volatility for row in selected]), "VOLATILITY"),
            },
        }
    return output


def _point_metrics(actual: np.ndarray, predicted: np.ndarray, target: str) -> dict[str, float]:
    errors = predicted - actual
    result = {
        "mae": float(np.mean(np.abs(errors))),
        "rmse": float(np.sqrt(np.mean(errors**2))),
        "bias": float(np.mean(errors)),
        "pearson": _correlation(actual, predicted),
        "spearman": _correlation(_ranks(actual), _ranks(predicted)),
    }
    if target == "RETURN":
        result["sign_accuracy"] = float(np.mean(np.sign(actual) == np.sign(predicted)))
    else:
        ratio = np.maximum(actual, 1e-12) ** 2 / np.maximum(predicted, 1e-12) ** 2
        result["qlike"] = float(np.mean(ratio - np.log(ratio) - 1.0))
    return result


def publish_current_risk_report(
    config_path: Path,
    output_root: Path,
    *,
    predictor: Callable | None = None,
    issued_at: datetime | None = None,
) -> Path:
    from perpetual_engine.portfolio_monitor import load_current_portfolio_prices

    config = load_risk_config(config_path)
    output_root = output_root.resolve()
    if not output_root.is_relative_to(config.project_root):
        raise ValueError("Chronos risk output escapes project root")
    portfolio, prices, manifest = load_current_portfolio_prices(
        config.price_config, project_root=config.project_root,
    )
    market = next((component for component in portfolio.components if component.component_id == "SWDA"), None)
    if market is None or market.ticker not in prices:
        raise ValueError("SWDA market history is missing")
    series = prices[market.ticker]
    ordered = sorted(series)
    returns = daily_returns([series[day] for day in ordered])
    report = build_current_risk_report(
        config,
        np.asarray(ordered[1:], dtype="datetime64[D]"),
        returns,
        dataset_version=str(manifest["vintage_id"]),
        predictor=predictor,
        issued_at=issued_at,
    )
    report["config_sha256"] = hashlib.sha256(config.path.read_bytes()).hexdigest()
    report["price_manifest_retrieved_at"] = manifest["retrieved_at"]
    payload = canonical_json(report)
    identifier = hashlib.sha256(payload).hexdigest()
    return _publish_archive(output_root / "forecasts" / identifier, {"report.json": payload})


def publish_walk_forward_evaluation(
    config_path: Path,
    output_root: Path,
    *,
    predictor: Callable | None = None,
    min_context: int = 252,
    origin_step: int = 5,
    max_origins: int = 36,
) -> Path:
    from perpetual_engine.portfolio_monitor import load_current_portfolio_prices

    config = load_risk_config(config_path)
    output_root = output_root.resolve()
    if not output_root.is_relative_to(config.project_root):
        raise ValueError("Chronos risk evaluation output escapes project root")
    portfolio, prices, source_manifest = load_current_portfolio_prices(config.price_config, project_root=config.project_root)
    market = next((component for component in portfolio.components if component.component_id == "SWDA"), None)
    if market is None or market.ticker not in prices:
        raise ValueError("SWDA market history is missing")
    series = prices[market.ticker]
    ordered = sorted(series)
    returns = daily_returns([series[day] for day in ordered])
    rows = walk_forward_evaluate(
        config, np.asarray(ordered[1:], dtype="datetime64[D]"), returns,
        predictor=predictor, min_context=min_context, origin_step=origin_step, max_origins=max_origins,
    )
    labels = tuple(f"q{int(round(level * 100)):02d}" for level in config.quantiles)
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow((
        "forecast_cutoff", "target_end", "horizon", "actual_return", "actual_volatility",
        *(f"return_{label}" for label in labels), *(f"volatility_{label}" for label in labels),
        "sigma20", "sigma60", "sigma_forecast", "ewma_094", "naive_volatility",
        "zero_return", "historical_mean_return", "momentum_persistence", "external_scaling",
    ))
    for row in rows:
        writer.writerow((
            str(row.forecast_cutoff), str(row.target_end), row.horizon,
            format(row.actual_return, ".17g"), format(row.actual_volatility, ".17g"),
            *(format(value, ".17g") for value in row.return_quantiles),
            *(format(value, ".17g") for value in row.volatility_quantiles),
            *(format(value, ".17g") for value in (
                row.sigma20, row.sigma60, row.sigma_forecast, row.ewma, row.naive_volatility,
                row.zero_return, row.historical_mean_return, row.momentum_return,
            )), row.external_scaling,
        ))
    predictions = stream.getvalue().encode("utf-8")
    metrics = canonical_json(_json_safe(summarize_walk_forward(rows, config.quantiles)))
    generated = {
        "predictions.csv": hashlib.sha256(predictions).hexdigest(),
        "metrics.json": hashlib.sha256(metrics).hexdigest(),
    }
    origin_dates = {str(row.forecast_cutoff) for row in rows}
    identity = {
        "schema_version": "CHRONOS_RISK_EVALUATION_V1", "mode": config.mode,
        "dataset_version": source_manifest["vintage_id"],
        "config_sha256": hashlib.sha256(config.path.read_bytes()).hexdigest(),
        "origin_count": len(origin_dates), "origin_step": origin_step, "min_context": min_context,
        "horizons": list(config.horizons), "quantiles": list(config.quantiles),
        "external_scaling": "NONE", "generated_sha256": generated,
    }
    manifest = canonical_json(identity)
    identifier = hashlib.sha256(manifest).hexdigest()
    return _publish_archive(
        output_root / identifier,
        {"predictions.csv": predictions, "metrics.json": metrics, "manifest.json": manifest},
    )


def _json_safe(value):
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _publish_archive(destination: Path, files: Mapping[str, bytes]) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if not destination.is_dir():
            raise ValueError("Chronos risk archive collision")
        current = {path.name: path.read_bytes() for path in destination.iterdir() if path.is_file()}
        if current != dict(files) or len(tuple(destination.iterdir())) != len(files):
            raise ValueError("Chronos risk archive collision")
        return destination
    stage = Path(tempfile.mkdtemp(prefix=".chronos-risk-", dir=destination.parent))
    try:
        for name, payload in files.items():
            (stage / name).write_bytes(payload)
        stage.replace(destination)
        return destination
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def historical_volatility(returns: Sequence[float], window: int) -> float:
    values = _finite(returns, "returns")
    if type(window) is not int or window < 2 or len(values) < window:
        raise ValueError("historical-volatility window is invalid")
    return float(np.std(values[-window:], ddof=1) * math.sqrt(TRADING_DAYS))


def ewma_volatility(returns: Sequence[float], decay: float = 0.94) -> float:
    values = _finite(returns, "returns")
    if not 0.0 < decay < 1.0:
        raise ValueError("EWMA decay must be between zero and one")
    variance = float(values[0] ** 2)
    for value in values[1:]:
        variance = decay * variance + (1.0 - decay) * float(value**2)
    return math.sqrt(variance * TRADING_DAYS)


def sigma_forecast(sigma20: float, sigma60: float) -> float:
    return _weighted_volatility((sigma20, sigma60), (0.35, 0.65), "sigma forecast")


def sigma_risk(sigma20: float, sigma60: float, chronos_vol: float, weights: Sequence[float]) -> float:
    return _weighted_volatility((sigma20, sigma60, chronos_vol), weights, "sigma risk")


def _weighted_volatility(values: Sequence[float], weights: Sequence[float], label: str) -> float:
    inputs = _finite(values, label)
    coefficients = _finite(weights, f"{label} weights")
    if len(inputs) != len(coefficients) or np.any(inputs < 0) or np.any(coefficients < 0):
        raise ValueError(f"{label} values and weights are invalid")
    if not math.isclose(float(coefficients.sum()), 1.0, abs_tol=1e-12):
        raise ValueError(f"{label} weights must sum to one")
    return float(inputs @ coefficients)


def beta_desired(erp: float) -> float:
    if not math.isfinite(erp):
        raise ValueError("ERP must be finite")
    if erp < 0.03:
        return 0.75
    if erp < 0.04:
        return 0.90
    if erp < 0.05:
        return 1.00
    if erp < 0.06:
        return 1.15
    return 1.30


def beta_trend(fast_positive: bool, slow_positive: bool) -> float:
    if type(fast_positive) is not bool or type(slow_positive) is not bool:
        raise ValueError("trend indicators must be boolean")
    return 1.60 if fast_positive and slow_positive else 0.90 if not fast_positive and not slow_positive else 1.20


@dataclass(frozen=True)
class BetaComparison:
    beta_desired: float
    sigma_kelly: float
    half_kelly: float
    beta_trend: float
    beta_vol_production: float
    beta_vol_chronos: float
    beta_ceiling_production: float
    beta_ceiling_chronos: float
    beta_operational_production: float
    beta_operational_chronos: float
    chronos_impact: str


def beta_comparison(
    *,
    erp: float,
    sigma_forecast_value: float,
    structural_volatility: float,
    chronos_vol_q90: float,
    trend_fast_positive: bool,
    trend_slow_positive: bool,
) -> BetaComparison:
    values = _finite((erp, sigma_forecast_value, structural_volatility, chronos_vol_q90), "beta inputs")
    if erp < 0 or np.any(values[1:] <= 0):
        raise ValueError("beta inputs are outside their valid range")
    desired = beta_desired(erp)
    trend = beta_trend(trend_fast_positive, trend_slow_positive)
    sigma_kelly = max(sigma_forecast_value, structural_volatility)
    half_kelly = erp / sigma_kelly**2 / 2.0
    beta_vol_production = min(1.60, 0.18 / sigma_forecast_value)
    beta_vol_chronos = min(1.60, 0.18 / max(sigma_forecast_value, chronos_vol_q90))
    ceiling_production = min(half_kelly, beta_vol_production, trend)
    ceiling_chronos = min(half_kelly, beta_vol_chronos, trend)
    operational_production = min(desired, ceiling_production)
    operational_chronos = min(desired, ceiling_chronos)
    if operational_chronos < operational_production - 1e-12:
        impact = "REDUCE_RISK"
    elif chronos_vol_q90 > sigma_forecast_value:
        impact = "CAUTION"
    else:
        impact = "NONE"
    return BetaComparison(
        desired, sigma_kelly, half_kelly, trend, beta_vol_production, beta_vol_chronos,
        ceiling_production, ceiling_chronos, operational_production, operational_chronos, impact,
    )


@dataclass(frozen=True)
class WalkForwardStep:
    data_cutoff: np.datetime64
    horizon: int
    context: np.ndarray
    actual: float


def prepare_walk_forward_step(
    dates: Sequence[np.datetime64], returns: Sequence[float], *, cutoff_index: int, horizon: int
) -> WalkForwardStep:
    observed = np.asarray(dates, dtype="datetime64[D]")
    values = _finite(returns, "returns")
    if observed.ndim != 1 or len(observed) != len(values) or len(np.unique(observed)) != len(observed):
        raise ValueError("walk-forward dates are invalid")
    if len(observed) > 1 and np.any(observed[1:] <= observed[:-1]):
        raise ValueError("walk-forward dates must be strictly increasing")
    if type(cutoff_index) is not int or type(horizon) is not int or horizon < 2:
        raise ValueError("walk-forward cutoff or horizon is invalid")
    if cutoff_index < 1 or cutoff_index + horizon >= len(values):
        raise ValueError("walk-forward step lacks context or future observations")
    context = values[:cutoff_index + 1].copy()
    context.setflags(write=False)
    future = values[cutoff_index + 1:cutoff_index + horizon + 1]
    actual = float(np.std(future, ddof=1) * math.sqrt(TRADING_DAYS))
    return WalkForwardStep(observed[cutoff_index], horizon, context, actual)


@dataclass(frozen=True)
class PortfolioSnapshot:
    snapshot_date: date
    total_eur: float
    cash_eur: float
    securities_eur: float
    nominal_equity_eur: float
    economic_equity_eur: float

    @property
    def nominal_equity_weight(self) -> float:
        return self.nominal_equity_eur / self.total_eur

    @property
    def economic_equity_weight(self) -> float:
        return self.economic_equity_eur / self.total_eur


def load_portfolio_snapshot(path: Path) -> PortfolioSnapshot:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("portfolio snapshot is malformed") from error
    expected = {"schema_version", "snapshot_date", "total_eur", "cash_eur", "securities_eur", "positions"}
    if not isinstance(data, dict) or set(data) != expected or data["schema_version"] != "PORTFOLIO_SNAPSHOT_V1":
        raise ValueError("portfolio snapshot schema is invalid")
    try:
        snapshot_date = date.fromisoformat(data["snapshot_date"])
        total, cash, securities = (float(data[name]) for name in ("total_eur", "cash_eur", "securities_eur"))
    except (TypeError, ValueError) as error:
        raise ValueError("portfolio snapshot totals are invalid") from error
    positions = data["positions"]
    if not isinstance(positions, list) or not positions:
        raise ValueError("portfolio snapshot positions are invalid")
    seen: set[str] = set()
    position_total = nominal_equity = economic_equity = 0.0
    for item in positions:
        if not isinstance(item, dict) or set(item) != {"id", "value_eur", "asset_class", "exposure_multiplier"}:
            raise ValueError("portfolio snapshot position schema is invalid")
        identifier, asset_class = item["id"], item["asset_class"]
        try:
            value, multiplier = float(item["value_eur"]), float(item["exposure_multiplier"])
        except (TypeError, ValueError) as error:
            raise ValueError("portfolio snapshot position value is invalid") from error
        if not isinstance(identifier, str) or not identifier or identifier in seen or asset_class not in {"EQUITY", "BOND", "CREDIT"}:
            raise ValueError("portfolio snapshot position identity is invalid")
        if not math.isfinite(value) or not math.isfinite(multiplier) or value < 0 or multiplier < 0:
            raise ValueError("portfolio snapshot position value is invalid")
        seen.add(identifier)
        position_total += value
        if asset_class == "EQUITY":
            nominal_equity += value
            economic_equity += value * multiplier
    if not all(math.isfinite(value) and value >= 0 for value in (total, cash, securities)):
        raise ValueError("portfolio snapshot totals are invalid")
    if not math.isclose(position_total, securities, abs_tol=0.01) or not math.isclose(cash + securities, total, abs_tol=0.01):
        raise ValueError("portfolio snapshot total does not reconcile")
    return PortfolioSnapshot(snapshot_date, total, cash, securities, nominal_equity, economic_equity)
