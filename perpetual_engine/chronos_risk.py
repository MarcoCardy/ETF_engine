from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable, Mapping, Sequence

import numpy as np


TRADING_DAYS = 252


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


def load_risk_predictor(config: RiskConfig) -> Callable:
    from chronos import Chronos2Pipeline

    pipeline = Chronos2Pipeline.from_pretrained(
        config.model_id,
        revision=config.model_revision,
        device_map=config.device,
        local_files_only=True,
    )

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
