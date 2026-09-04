from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Mapping

import numpy as np

from perpetual_engine.chronos_data import (
    ChronosConfig,
    MonthlyTable,
    REQUIRED_V1_COVARIATES,
    load_covariate_table,
    load_target_table,
)


SCENARIO_NAMES = ("ECB_FLAT", "ECB_DOWN_100BP", "ECB_UP_100BP")
_TARGETS = ("WORLD", "MOMENTUM", "QUALITY", "TREND")
_MODEL_ID = "amazon/chronos-2"
_MODEL_REVISION = "29ec3766d36d6f73f0696f85560a422f50e8498c"


@dataclass(frozen=True)
class ForecastRow:
    origin: date
    scenario: str
    target: str
    forecast_month: date
    horizon: int
    q10: float
    q50: float
    q90: float


def _month_end_after(origin: date, months: int) -> date:
    index = origin.year * 12 + origin.month - 1 + months
    first_after = date(index // 12, index % 12 + 1, 1)
    following = date(first_after.year + (first_after.month == 12), first_after.month % 12 + 1, 1)
    return following - timedelta(days=1)


def _require_v1(config: ChronosConfig) -> None:
    if (
        config.model_id != _MODEL_ID
        or config.model_revision != _MODEL_REVISION
        or config.device != "cpu"
        or config.targets != _TARGETS
        or config.prediction_length != 12
        or config.quantiles != (0.1, 0.5, 0.9)
        or config.scenario_basis_points != 100
        or tuple(source.source_id for source in config.sources) != REQUIRED_V1_COVARIATES
    ):
        raise ValueError("Chronos v1 forecast contract is invalid")


def ecb_scenarios(last_rate: float, basis_points: int, length: int) -> dict[str, np.ndarray]:
    if not math.isfinite(last_rate) or basis_points != 100 or length != 12:
        raise ValueError("Chronos v1 requires a finite rate, 100 basis points, and 12 months")
    steps = np.arange(1, length + 1, dtype=np.float32) / np.float32(length)
    rate = np.float32(last_rate)
    movement = np.float32(basis_points / 100.0)
    return {
        "ECB_FLAT": np.full(length, rate, dtype=np.float32),
        "ECB_DOWN_100BP": rate - movement * steps,
        "ECB_UP_100BP": rate + movement * steps,
    }


def _aligned_values(targets: MonthlyTable, covariates: MonthlyTable) -> tuple[date, np.ndarray, np.ndarray]:
    if targets.names != _TARGETS or covariates.names != REQUIRED_V1_COVARIATES:
        raise ValueError("forecast table columns are invalid or reordered")
    if targets.values.shape != (len(targets.names), len(targets.months)) or covariates.values.shape != (
        len(covariates.names), len(covariates.months)
    ):
        raise ValueError("forecast table shape is invalid")
    common = tuple(month for month in targets.months if month in set(covariates.months))
    if not common:
        raise ValueError("targets and covariates have no common history")
    target_index = {month: index for index, month in enumerate(targets.months)}
    covariate_index = {month: index for index, month in enumerate(covariates.months)}
    target_values = targets.values[:, [target_index[month] for month in common]]
    covariate_values = covariates.values[:, [covariate_index[month] for month in common]]
    if not np.isfinite(target_values).all() or not np.isfinite(covariate_values).all():
        raise ValueError("forecast history must be finite")
    return common[-1], target_values, covariate_values


def build_forecast_inputs(
    targets: MonthlyTable,
    covariates: MonthlyTable,
    scenarios: Mapping[str, np.ndarray],
) -> list[dict[str, object]]:
    if set(scenarios) != set(SCENARIO_NAMES):
        raise ValueError("ECB scenario names are invalid")
    _origin, target_values, covariate_values = _aligned_values(targets, covariates)
    items: list[dict[str, object]] = []
    for name in SCENARIO_NAMES:
        future = np.asarray(scenarios[name], dtype=np.float32)
        if future.shape != (12,) or not np.isfinite(future).all():
            raise ValueError("ECB future scenario must contain 12 finite values")
        items.append({
            "target": np.asarray(target_values, dtype=np.float32),
            "past_covariates": {
                covariate: np.asarray(covariate_values[index], dtype=np.float32)
                for index, covariate in enumerate(REQUIRED_V1_COVARIATES)
            },
            "future_covariates": {"ECB_DFR": future},
        })
    return items


def load_chronos_predictor(config: ChronosConfig) -> Callable:
    _require_v1(config)
    from chronos import Chronos2Pipeline

    pipeline = Chronos2Pipeline.from_pretrained(
        _MODEL_ID,
        revision=_MODEL_REVISION,
        device_map="cpu",
        local_files_only=True,
    )

    def predict(items, prediction_length, quantile_levels):
        result = pipeline.predict_quantiles(
            items,
            prediction_length=prediction_length,
            quantile_levels=list(quantile_levels),
            batch_size=len(items),
        )
        quantiles = result[0] if isinstance(result, tuple) else result
        return [tensor.detach().cpu().numpy() for tensor in quantiles]

    return predict


def run_scenario_forecasts(
    config: ChronosConfig,
    predictor: Callable | None = None,
) -> tuple[ForecastRow, ...]:
    _require_v1(config)
    targets = load_target_table(config)
    covariates, _vintage_id = load_covariate_table(config)
    origin, _target_values, covariate_values = _aligned_values(targets, covariates)
    last_rate = float(covariate_values[REQUIRED_V1_COVARIATES.index("ECB_DFR"), -1])
    items = build_forecast_inputs(
        targets,
        covariates,
        ecb_scenarios(last_rate, config.scenario_basis_points, config.prediction_length),
    )
    predict = predictor or load_chronos_predictor(config)
    raw_results = predict(
        items,
        prediction_length=config.prediction_length,
        quantile_levels=list(config.quantiles),
    )
    results = raw_results[0] if isinstance(raw_results, tuple) and len(raw_results) == 2 else raw_results
    if not isinstance(results, (list, tuple)) or len(results) != len(SCENARIO_NAMES):
        raise ValueError("predictor must return one result per ECB scenario")
    arrays = [np.asarray(result, dtype=float) for result in results]
    for array in arrays:
        if array.shape != (4, 12, 3) or not np.isfinite(array).all():
            raise ValueError("Chronos result must be a finite (4, 12, 3) array")
        if np.any(array[:, :, 0] > array[:, :, 1]) or np.any(array[:, :, 1] > array[:, :, 2]):
            raise ValueError("Chronos quantiles are crossed")
    return tuple(
        ForecastRow(
            origin=origin,
            scenario=scenario,
            target=target,
            forecast_month=_month_end_after(origin, horizon),
            horizon=horizon,
            q10=float(arrays[scenario_index][target_index, horizon - 1, 0]),
            q50=float(arrays[scenario_index][target_index, horizon - 1, 1]),
            q90=float(arrays[scenario_index][target_index, horizon - 1, 2]),
        )
        for scenario_index, scenario in enumerate(SCENARIO_NAMES)
        for target_index, target in enumerate(_TARGETS)
        for horizon in range(1, 13)
    )
