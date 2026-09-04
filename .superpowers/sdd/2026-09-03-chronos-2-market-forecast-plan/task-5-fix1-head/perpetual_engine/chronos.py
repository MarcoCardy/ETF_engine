from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Callable, Mapping

import numpy as np

from perpetual_engine.chronos_data import (
    ChronosConfig,
    MonthlyTable,
    REQUIRED_V1_COVARIATES,
    load_chronos_config,
    load_covariate_table,
    load_target_snapshot,
    load_target_table,
)
from perpetual_engine.io import canonical_json, sha256_file


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


def pinball_loss(actual: np.ndarray, forecast: np.ndarray, quantile: float) -> np.ndarray:
    actual_values = np.asarray(actual, dtype=float)
    forecast_values = np.asarray(forecast, dtype=float)
    if not 0.0 < quantile < 1.0 or actual_values.shape != forecast_values.shape:
        raise ValueError("pinball loss requires matching arrays and a quantile inside (0, 1)")
    if not np.isfinite(actual_values).all() or not np.isfinite(forecast_values).all():
        raise ValueError("pinball loss requires finite arrays")
    error = actual_values - forecast_values
    return np.maximum(quantile * error, (quantile - 1.0) * error)


def moving_block_interval(origin_differences: np.ndarray, config: ChronosConfig) -> tuple[float, float]:
    values = np.asarray(origin_differences, dtype=float)
    block = config.bootstrap_block_months
    if values.ndim != 1 or len(values) < block or not np.isfinite(values).all():
        raise ValueError("bootstrap requires finite per-origin differences and one full block")
    rng = np.random.default_rng(config.bootstrap_seed)
    starts = np.arange(len(values) - block + 1)
    blocks_needed = math.ceil(len(values) / block)
    means = np.empty(config.bootstrap_resamples)
    for index in range(config.bootstrap_resamples):
        chosen = rng.choice(starts, size=blocks_needed, replace=True)
        sample = np.concatenate([values[start:start + block] for start in chosen])[:len(values)]
        means[index] = sample.mean()
    tail = (1.0 - config.bootstrap_confidence) / 2.0
    return tuple(float(item) for item in np.quantile(means, (tail, 1.0 - tail)))


def evaluation_variants(covariates: tuple[str, ...]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if not covariates or len(set(covariates)) != len(covariates):
        raise ValueError("evaluation covariates must be non-empty and unique")
    return (
        (("TARGET_ONLY", ()),)
        + tuple((f"TARGET_PLUS_{name}", (name,)) for name in covariates)
        + (("FULL", covariates),)
        + tuple((f"FULL_MINUS_{name}", tuple(item for item in covariates if item != name)) for name in covariates)
    )


def _evaluation_history(
    targets: MonthlyTable,
    covariates: MonthlyTable,
    origins_count: int,
) -> tuple[tuple[int, ...], np.ndarray, np.ndarray]:
    if targets.names != _TARGETS or covariates.names != REQUIRED_V1_COVARIATES:
        raise ValueError("evaluation table columns are invalid or reordered")
    if targets.values.shape != (4, len(targets.months)) or covariates.values.shape != (5, len(covariates.months)):
        raise ValueError("evaluation table shape is invalid")
    if not np.isfinite(targets.values).all() or not np.isfinite(covariates.values).all():
        raise ValueError("evaluation tables must be finite")
    if len(targets.months) < origins_count + 23:
        raise ValueError("evaluation requires 36 origins, 12 realized months, and trailing volatility history")
    target_index = {month: index for index, month in enumerate(targets.months)}
    covariate_index = {month: index for index, month in enumerate(covariates.months)}
    if len(target_index) != len(targets.months) or len(covariate_index) != len(covariates.months):
        raise ValueError("evaluation months must be unique")
    origins = tuple(range(len(targets.months) - 12 - origins_count, len(targets.months) - 12))
    needed_months = targets.months[:origins[-1] + 13]
    if any(month not in covariate_index for month in needed_months):
        raise ValueError("every evaluation history and oracle month needs a covariate observation")
    start = covariate_index[targets.months[0]]
    expected_covariate_indices = tuple(range(start, start + len(needed_months)))
    if tuple(covariate_index[month] for month in needed_months) != expected_covariate_indices:
        raise ValueError("target and covariate evaluation histories must be contiguous and aligned")
    covariate_values = covariates.values[:, list(expected_covariate_indices)]
    return origins, np.asarray(targets.values, dtype=float), np.asarray(covariate_values, dtype=float)


def _predict_evaluation_batch(predict: Callable, items: list[dict[str, object]], config: ChronosConfig) -> list[np.ndarray]:
    raw = predict(items, prediction_length=12, quantile_levels=list(config.quantiles))
    results = raw[0] if isinstance(raw, tuple) and len(raw) == 2 else raw
    if not isinstance(results, (list, tuple)) or len(results) != len(items):
        raise ValueError("predictor must return one result per evaluation origin")
    arrays = [np.asarray(result, dtype=float) for result in results]
    for array in arrays:
        if array.shape != (4, 12, 3) or not np.isfinite(array).all():
            raise ValueError("Chronos evaluation result must be a finite (4, 12, 3) array")
        if np.any(array[:, :, 0] > array[:, :, 1]) or np.any(array[:, :, 1] > array[:, :, 2]):
            raise ValueError("Chronos evaluation quantiles are crossed")
    return arrays


def _csv_bytes(rows: list[dict[str, object]], columns: tuple[str, ...]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def _loss(rows: list[dict[str, object]]) -> float:
    actual = np.asarray([row["actual"] for row in rows], dtype=float)
    return float(np.mean([
        pinball_loss(actual, np.asarray([row[name] for row in rows], dtype=float), quantile)
        for name, quantile in (("q10", 0.1), ("q50", 0.5), ("q90", 0.9))
    ]))


def _horizon_rows(rows: list[dict[str, object]], horizon: int | str) -> list[dict[str, object]]:
    return rows if horizon == "ALL" else [row for row in rows if row["horizon"] == horizon]


def _classify(low: float, high: float) -> str:
    if low > 0.0:
        return "USEFUL"
    if high < 0.0:
        return "HARMFUL"
    return "INCONCLUSIVE"


def _publish_evaluation(output: Path, files: Mapping[str, bytes]) -> Path:
    if output.exists() and not output.is_dir():
        raise ValueError("evaluation output collision")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ValueError("evaluation output ancestor collision") from error
    stage = Path(tempfile.mkdtemp(prefix=f".{output.name}-", dir=output.parent))
    try:
        for name, payload in files.items():
            (stage / name).write_bytes(payload)
        try:
            expected = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))["generated_sha256"]
        except (KeyError, OSError, json.JSONDecodeError, TypeError) as error:
            raise ValueError("evaluation manifest hashes are invalid") from error
        generated = {path.name: sha256_file(path) for path in stage.iterdir() if path.is_file() and path.name != "manifest.json"}
        if expected != generated:
            raise ValueError("evaluation staged output hash mismatch")
        if output.exists():
            current = {path.name: path.read_bytes() for path in output.iterdir() if path.is_file()}
            staged = {path.name: path.read_bytes() for path in stage.iterdir() if path.is_file()}
            if current != staged or len(tuple(output.iterdir())) != len(current):
                raise ValueError("evaluation output collision")
            shutil.rmtree(stage)
            return output
        stage.replace(output)
        return output
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def evaluate_chronos(config_path: Path, output: Path, predictor: Callable | None = None) -> Path:
    config = load_chronos_config(config_path)
    _require_v1(config)
    output = Path(output).resolve()
    project_root = config.project_root.resolve()
    if not output.is_relative_to(project_root) or output == project_root:
        raise ValueError("evaluation output escapes project root")
    targets, target_hash, target_manifest_hash = load_target_snapshot(config)
    covariates, vintage_id = load_covariate_table(config)
    origins, target_values, covariate_values = _evaluation_history(
        targets, covariates, config.evaluation_origins
    )
    predict = predictor or load_chronos_predictor(config)
    variants = evaluation_variants(REQUIRED_V1_COVARIATES)
    predictions: list[dict[str, object]] = []
    predictions_by_variant: dict[str, list[dict[str, object]]] = {}

    for variant, enabled in (*variants, ("ORACLE_FUTURE_RATE_UPPER_BOUND", REQUIRED_V1_COVARIATES)):
        items = []
        for origin_index in origins:
            item: dict[str, object] = {
                "target": np.asarray(target_values[:, :origin_index + 1], dtype=np.float32),
            }
            if enabled:
                item["past_covariates"] = {
                    name: np.asarray(covariate_values[index, :origin_index + 1], dtype=np.float32)
                    for index, name in enumerate(REQUIRED_V1_COVARIATES)
                    if name in enabled
                }
            if variant == "ORACLE_FUTURE_RATE_UPPER_BOUND":
                item["future_covariates"] = {
                    "ECB_DFR": np.asarray(covariate_values[0, origin_index + 1:origin_index + 13], dtype=np.float32)
                }
            items.append(item)
        arrays = _predict_evaluation_batch(predict, items, config)
        variant_rows = []
        for origin_index, array in zip(origins, arrays):
            origin = targets.months[origin_index]
            for target_index, target in enumerate(_TARGETS):
                for horizon in range(1, 13):
                    actual = float(target_values[target_index, origin_index + horizon])
                    row = {
                        "variant": variant,
                        "origin": origin.isoformat(),
                        "target": target,
                        "forecast_month": targets.months[origin_index + horizon].isoformat(),
                        "horizon": horizon,
                        "actual": actual,
                        "q10": float(array[target_index, horizon - 1, 0]),
                        "q50": float(array[target_index, horizon - 1, 1]),
                        "q90": float(array[target_index, horizon - 1, 2]),
                    }
                    predictions.append(row)
                    variant_rows.append(row)
        predictions_by_variant[variant] = variant_rows
        del items, arrays

    baseline_rows = []
    for origin_index in origins:
        origin = targets.months[origin_index]
        for target_index, target in enumerate(_TARGETS):
            for horizon in range(1, 13):
                row = {
                    "variant": "ZERO_RETURN_BASELINE",
                    "origin": origin.isoformat(),
                    "target": target,
                    "forecast_month": targets.months[origin_index + horizon].isoformat(),
                    "horizon": horizon,
                    "actual": float(target_values[target_index, origin_index + horizon]),
                    "q10": 0.0,
                    "q50": 0.0,
                    "q90": 0.0,
                }
                predictions.append(row)
                baseline_rows.append(row)
    predictions_by_variant["ZERO_RETURN_BASELINE"] = baseline_rows

    scopes: tuple[int | str, ...] = (*config.reported_horizons, "ALL")
    metrics = []
    for variant in (*[name for name, _enabled in variants], "ZERO_RETURN_BASELINE", "ORACLE_FUTURE_RATE_UPPER_BOUND"):
        for target in _TARGETS:
            target_rows = [row for row in predictions_by_variant[variant] if row["target"] == target]
            for horizon in scopes:
                selected = _horizon_rows(target_rows, horizon)
                actual = np.asarray([row["actual"] for row in selected], dtype=float)
                q50 = np.asarray([row["q50"] for row in selected], dtype=float)
                metrics.append({
                    "variant": variant,
                    "target": target,
                    "horizon": horizon,
                    "mae_q50": float(np.mean(np.abs(actual - q50))),
                    "mean_pinball_loss": _loss(selected),
                    "interval_80_coverage": float(np.mean([
                        row["q10"] <= row["actual"] <= row["q90"] for row in selected
                    ])),
                })

    contributions = []
    for covariate in REQUIRED_V1_COVARIATES:
        comparisons = (
            ("STANDALONE", "TARGET_ONLY", f"TARGET_PLUS_{covariate}"),
            ("CONDITIONAL", f"FULL_MINUS_{covariate}", "FULL"),
        )
        for comparison, without_variant, with_variant in comparisons:
            for horizon in scopes:
                without_rows = _horizon_rows(predictions_by_variant[without_variant], horizon)
                with_rows = _horizon_rows(predictions_by_variant[with_variant], horizon)
                origin_differences = []
                for origin in (targets.months[index].isoformat() for index in origins):
                    origin_differences.append(
                        _loss([row for row in without_rows if row["origin"] == origin])
                        - _loss([row for row in with_rows if row["origin"] == origin])
                    )
                low, high = moving_block_interval(np.asarray(origin_differences), config)
                loss_without, loss_with = _loss(without_rows), _loss(with_rows)
                contributions.append({
                    "covariate": covariate,
                    "comparison": comparison,
                    "horizon": horizon,
                    "loss_without": loss_without,
                    "loss_with": loss_with,
                    "improvement": loss_without - loss_with,
                    "ci_low": low,
                    "ci_high": high,
                    "classification": _classify(low, high) if comparison == "CONDITIONAL" else "NOT_CLASSIFIED",
                })

    volatilities = np.asarray([
        [float(np.std(target_values[target, origin - 11:origin + 1], ddof=1) * math.sqrt(12)) for target in range(4)]
        for origin in origins
    ])
    thresholds = {
        target: {
            "low_middle": float(np.quantile(volatilities[:, target_index], 1 / 3, method="linear")),
            "middle_high": float(np.quantile(volatilities[:, target_index], 2 / 3, method="linear")),
        }
        for target_index, target in enumerate(_TARGETS)
    }
    volatility_rows = []
    origin_positions = {targets.months[origin].isoformat(): index for index, origin in enumerate(origins)}
    for row in predictions_by_variant["FULL"]:
        origin_position = origin_positions[str(row["origin"])]
        target_index = _TARGETS.index(str(row["target"]))
        volatility = float(volatilities[origin_position, target_index])
        bounds = thresholds[str(row["target"])]
        tercile = "LOW" if volatility <= bounds["low_middle"] else "MIDDLE" if volatility <= bounds["middle_high"] else "HIGH"
        signed_error = float(row["actual"] - row["q50"])
        volatility_rows.append({
            "origin": row["origin"],
            "target": row["target"],
            "horizon": row["horizon"],
            "trailing_volatility_12m": volatility,
            "volatility_tercile": tercile,
            "q10": row["q10"],
            "q50": row["q50"],
            "q90": row["q90"],
            "interval_width": float(row["q90"] - row["q10"]),
            "actual": row["actual"],
            "signed_error": signed_error,
            "absolute_error": abs(signed_error),
            "interval_hit": "true" if row["q10"] <= row["actual"] <= row["q90"] else "false",
        })

    csv_files = {
        "predictions.csv": _csv_bytes(predictions, ("variant", "origin", "target", "forecast_month", "horizon", "actual", "q10", "q50", "q90")),
        "metrics.csv": _csv_bytes(metrics, ("variant", "target", "horizon", "mae_q50", "mean_pinball_loss", "interval_80_coverage")),
        "covariate_contribution.csv": _csv_bytes(contributions, ("covariate", "comparison", "horizon", "loss_without", "loss_with", "improvement", "ci_low", "ci_high", "classification")),
        "volatility_diagnostics.csv": _csv_bytes(volatility_rows, ("origin", "target", "horizon", "trailing_volatility_12m", "volatility_tercile", "q10", "q50", "q90", "interval_width", "actual", "signed_error", "absolute_error", "interval_hit")),
    }
    try:
        package_version = version("chronos-forecasting")
    except PackageNotFoundError:
        package_version = "unavailable"
    generated_hashes = {name: hashlib.sha256(payload).hexdigest() for name, payload in csv_files.items()}
    manifest = {
        "schema_version": "CHRONOS_EVALUATION_V1",
        "model": {"id": config.model_id, "revision": config.model_revision, "package": "chronos-forecasting", "package_version": package_version, "device": config.device},
        "config_sha256": config.config_hash,
        "target_csv_sha256": target_hash,
        "target_manifest_sha256": target_manifest_hash,
        "vintage_id": vintage_id,
        "evaluation_origins": {"count": 36, "first": targets.months[origins[0]].isoformat(), "last": targets.months[origins[-1]].isoformat()},
        "bootstrap": {"block_months": 6, "resamples": 2000, "seed": 42, "confidence": 0.95},
        "volatility_thresholds": thresholds,
        "labels": {"evaluation": "RETROSPECTIVE_WALK_FORWARD_RESEARCH", "oracle": "ORACLE_FUTURE_RATE_UPPER_BOUND"},
        "generated_sha256": generated_hashes,
    }
    if sha256_file(config.target_csv) != target_hash or sha256_file(config.target_manifest) != target_manifest_hash:
        raise ValueError("target inputs changed during evaluation")
    return _publish_evaluation(output, {**csv_files, "manifest.json": canonical_json(manifest)})
