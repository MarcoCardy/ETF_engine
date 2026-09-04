from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import shutil
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Callable, Iterable, Mapping

import numpy as np

from perpetual_engine.chronos_data import (
    ChronosConfig,
    MonthlyTable,
    REQUIRED_V1_COVARIATES,
    load_chronos_config,
    load_covariate_table,
    load_covariate_vintage,
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


@dataclass(frozen=True)
class IssuedForecastRow:
    forecast_id: str
    issued_at: datetime
    origin: date
    scenario: str
    target: str
    forecast_month: date
    horizon: int
    q10: float
    q50: float
    q90: float


@dataclass(frozen=True)
class ReconciledRow:
    forecast_id: str
    issued_at: datetime
    origin: date
    scenario: str
    target: str
    forecast_month: date
    horizon: int
    q10: float
    q50: float
    q90: float
    actual: float
    signed_error: float
    absolute_error: float
    squared_error: float
    interval_hit: bool


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
    *,
    tables: tuple[MonthlyTable, MonthlyTable] | None = None,
) -> tuple[ForecastRow, ...]:
    _require_v1(config)
    if tables is None:
        targets = load_target_table(config)
        covariates, _vintage_id = load_covariate_table(config)
    else:
        targets, covariates = tables
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


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _forecast_identity(
    model_revision: str,
    config_hash: str,
    target_hash: str,
    vintage_id: str,
    origin: date,
    basis_points: int,
    prediction_length: int,
) -> str:
    if not all(_is_sha256(value) for value in (config_hash, target_hash, vintage_id)):
        raise ValueError("forecast identity hashes are invalid")
    identity = {
        "model_revision": model_revision,
        "config_hash": config_hash,
        "target_hash": target_hash,
        "vintage_id": vintage_id,
        "origin": origin.isoformat(),
        "scenarios": {
            name: values.tolist()
            for name, values in ecb_scenarios(0.0, basis_points, prediction_length).items()
        },
    }
    return hashlib.sha256(canonical_json(identity)).hexdigest()


def forecast_id(config: ChronosConfig, target_hash: str, vintage_id: str, origin: date) -> str:
    _require_v1(config)
    return _forecast_identity(
        config.model_revision,
        config.config_hash,
        target_hash,
        vintage_id,
        origin,
        config.scenario_basis_points,
        config.prediction_length,
    )


def _safe_project_path(path: Path, config: ChronosConfig, label: str) -> Path:
    resolved = Path(path).resolve()
    project_root = config.project_root.resolve()
    if resolved == project_root or not resolved.is_relative_to(project_root):
        raise ValueError(f"{label} escapes project root")
    return resolved


def _scenario_sensitivity_bytes(rows: Iterable[ForecastRow | IssuedForecastRow]) -> bytes:
    rows = tuple(rows)
    by_key = {(row.scenario, row.target, row.horizon): row for row in rows}
    if len(by_key) != len(rows):
        raise ValueError("duplicate forecast rows")
    sensitivity = []
    for target in _TARGETS:
        for scenario in SCENARIO_NAMES[1:]:
            for horizon in range(1, 13):
                try:
                    row = by_key[(scenario, target, horizon)]
                    flat = by_key[("ECB_FLAT", target, horizon)]
                except KeyError as error:
                    raise ValueError("forecast rows are incomplete") from error
                if row.forecast_month != flat.forecast_month:
                    raise ValueError("scenario forecast months do not align")
                width = row.q90 - row.q10
                flat_width = flat.q90 - flat.q10
                sensitivity.append({
                    "target": target,
                    "forecast_month": row.forecast_month.isoformat(),
                    "horizon": horizon,
                    "scenario": scenario,
                    "q50": row.q50,
                    "flat_q50": flat.q50,
                    "q50_delta": row.q50 - flat.q50,
                    "interval_width": width,
                    "flat_interval_width": flat_width,
                    "interval_width_delta": width - flat_width,
                })
    if len(rows) != 144:
        raise ValueError("forecast publication requires exactly 144 rows")
    return _csv_bytes(sensitivity, (
        "target", "forecast_month", "horizon", "scenario", "q50", "flat_q50", "q50_delta",
        "interval_width", "flat_interval_width", "interval_width_delta",
    ))


def _atomic_snapshot(
    destination: Path,
    staging_root: Path,
    files: Mapping[str, bytes],
    label: str,
    *,
    precommit: Callable[[], None] | None = None,
) -> Path:
    destination_parent = destination.parent.resolve()
    staging_root = staging_root.resolve()
    destination_parent_existed = destination_parent.exists()
    if destination.exists() and (
        not destination.is_dir() or destination.resolve().parent != destination_parent
    ):
        raise ValueError(f"{label} collision")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        staging_root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ValueError(f"{label} ancestor collision") from error
    stage = Path(tempfile.mkdtemp(prefix=f"{label}-", dir=staging_root)).resolve()
    if stage.parent != staging_root:
        raise ValueError(f"{label} staging path escapes staging root")
    try:
        for name, payload in files.items():
            (stage / name).write_bytes(payload)
        try:
            manifest = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
            expected = manifest["generated_sha256"]
        except (KeyError, OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
            raise ValueError(f"{label} manifest hashes are invalid") from error
        generated = {
            path.name: sha256_file(path)
            for path in stage.iterdir()
            if path.is_file() and path.name != "manifest.json"
        }
        if expected != generated or set(files) != {*generated, "manifest.json"}:
            raise ValueError(f"{label} staged output hash mismatch")
        if destination.exists():
            current = {path.name: path.read_bytes() for path in destination.iterdir() if path.is_file()}
            staged = {path.name: path.read_bytes() for path in stage.iterdir() if path.is_file()}
            if current != staged or len(tuple(destination.iterdir())) != len(current):
                raise ValueError(f"{label} collision")
            if precommit is not None:
                precommit()
            shutil.rmtree(stage)
            return destination
        if precommit is not None:
            precommit()
        stage.replace(destination)
        return destination
    except Exception:
        if stage.exists() and stage.is_relative_to(staging_root.resolve()):
            shutil.rmtree(stage)
        if not destination_parent_existed and destination_parent.is_dir() and not any(destination_parent.iterdir()):
            destination_parent.rmdir()
        raise


def _parse_utc(value: object, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError as error:
        raise ValueError(f"{label} is invalid") from error
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{label} must be timezone-aware UTC")
    return parsed


def _chronos_package_version() -> str:
    try:
        return version("chronos-forecasting")
    except PackageNotFoundError:
        return "unavailable"


def _forecast_manifest(
    config: ChronosConfig,
    identifier: str,
    issued_at: datetime,
    origin: date,
    scenarios: Mapping[str, np.ndarray],
    target_hash: str,
    target_manifest_hash: str,
    vintage_id: str,
    generated_hashes: Mapping[str, str],
) -> dict[str, object]:
    return {
        "schema_version": "CHRONOS_FORECAST_V1",
        "forecast_id": identifier,
        "issued_at": issued_at.isoformat(),
        "origin": origin.isoformat(),
        "model": {
            "id": config.model_id,
            "revision": config.model_revision,
            "package": "chronos-forecasting",
            "package_version": _chronos_package_version(),
            "device": config.device,
        },
        "scenarios": {name: values.tolist() for name, values in scenarios.items()},
        "labels": {
            "forecast": "PROSPECTIVE_SCENARIO_FORECAST",
            "target_history": "RETROSPECTIVE_INPUT_ONLY",
        },
        "config_sha256": config.config_hash,
        "target_csv_sha256": target_hash,
        "target_manifest_sha256": target_manifest_hash,
        "vintage_id": vintage_id,
        "generated_sha256": dict(generated_hashes),
    }


def _read_forecast_archive(
    path: Path,
    forecast_root: Path,
    config: ChronosConfig,
) -> tuple[tuple[IssuedForecastRow, ...], str]:
    archive_id = path.name
    forecast_root = forecast_root.resolve()
    resolved = path.resolve()
    if (
        not path.is_dir()
        or not _is_sha256(archive_id)
        or resolved.parent != forecast_root
        or resolved.name != archive_id
    ):
        if _is_sha256(archive_id):
            raise ValueError("forecast archive path escapes forecast root")
        raise ValueError("unexpected forecast archive child")
    expected_files = {"forecast.csv", "scenario_sensitivity.csv", "manifest.json"}
    children = {item.name: item for item in resolved.iterdir()}
    if set(children) != expected_files:
        raise ValueError("forecast archive has unexpected files")
    resolved_files = {}
    for name, child in children.items():
        resolved_child = child.resolve()
        if not child.is_file() or resolved_child.parent != resolved:
            raise ValueError("forecast archive file escapes archive root")
        resolved_files[name] = resolved_child
    try:
        file_bytes = {name: resolved_files[name].read_bytes() for name in expected_files}
        manifest_bytes = file_bytes["manifest.json"]
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("forecast manifest is malformed") from error
    required = {
        "schema_version", "forecast_id", "issued_at", "origin", "model", "scenarios", "labels",
        "config_sha256", "target_csv_sha256", "target_manifest_sha256", "vintage_id", "generated_sha256",
    }
    if not isinstance(manifest, dict) or set(manifest) != required or manifest.get("schema_version") != "CHRONOS_FORECAST_V1":
        raise ValueError("forecast manifest schema is invalid")
    if manifest.get("forecast_id") != archive_id:
        raise ValueError("forecast identity/path mismatch")
    generated = manifest.get("generated_sha256")
    if not isinstance(generated, dict) or set(generated) != {"forecast.csv", "scenario_sensitivity.csv"}:
        raise ValueError("forecast generated hashes are invalid")
    for name, digest in generated.items():
        if not _is_sha256(digest) or hashlib.sha256(file_bytes[name]).hexdigest() != digest:
            raise ValueError("forecast generated hash mismatch")
    model = manifest.get("model")
    labels = manifest.get("labels")
    scenarios = manifest.get("scenarios")
    if (
        not isinstance(model, dict)
        or model != {
            "id": config.model_id,
            "revision": config.model_revision,
            "package": "chronos-forecasting",
            "package_version": _chronos_package_version(),
            "device": config.device,
        }
        or labels != {"forecast": "PROSPECTIVE_SCENARIO_FORECAST", "target_history": "RETROSPECTIVE_INPUT_ONLY"}
        or not isinstance(scenarios, dict)
        or set(scenarios) != set(SCENARIO_NAMES)
    ):
        raise ValueError("forecast manifest metadata is invalid")
    try:
        origin = date.fromisoformat(manifest["origin"])
        issued_at = _parse_utc(manifest["issued_at"], "forecast issued_at")
        scenario_arrays = {name: np.asarray(scenarios[name], dtype=float) for name in SCENARIO_NAMES}
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("forecast manifest values are invalid") from error
    if any(values.shape != (12,) or not np.isfinite(values).all() for values in scenario_arrays.values()):
        raise ValueError("forecast scenario definitions are invalid")
    vintage = load_covariate_vintage(config, manifest["vintage_id"])
    try:
        origin_index = vintage.months.index(origin)
    except ValueError as error:
        raise ValueError("forecast origin is missing from bound vintage") from error
    vintage_rate = float(vintage.values[REQUIRED_V1_COVARIATES.index("ECB_DFR"), origin_index])
    expected_scenarios = ecb_scenarios(vintage_rate, config.scenario_basis_points, config.prediction_length)
    if any(not np.array_equal(scenario_arrays[name], expected_scenarios[name]) for name in SCENARIO_NAMES):
        raise ValueError("forecast scenario definitions are invalid")
    try:
        expected_id = _forecast_identity(
            model["revision"], manifest["config_sha256"], manifest["target_csv_sha256"],
            manifest["vintage_id"], origin, config.scenario_basis_points, config.prediction_length,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("forecast identity is invalid") from error
    if expected_id != archive_id or not _is_sha256(manifest.get("target_manifest_sha256")):
        raise ValueError("forecast identity/path mismatch")

    try:
        text = file_bytes["forecast.csv"].decode("utf-8")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        fields = ("scenario", "target", "forecast_month", "horizon", "q10", "q50", "q90")
        if tuple(reader.fieldnames or ()) != fields:
            raise ValueError("forecast CSV header is invalid")
        issued_rows = []
        keys = set()
        for raw in reader:
            if None in raw or set(raw) != set(fields):
                raise ValueError("forecast CSV row has extra or missing fields")
            scenario, target = raw["scenario"], raw["target"]
            forecast_month = date.fromisoformat(raw["forecast_month"])
            horizon = int(raw["horizon"])
            quantiles = tuple(float(raw[name]) for name in ("q10", "q50", "q90"))
            key = (scenario, target, horizon)
            if (
                scenario not in SCENARIO_NAMES
                or target not in _TARGETS
                or horizon not in range(1, 13)
                or forecast_month != _month_end_after(origin, horizon)
                or not all(math.isfinite(value) for value in quantiles)
                or not quantiles[0] <= quantiles[1] <= quantiles[2]
                or key in keys
            ):
                raise ValueError("forecast CSV row is invalid or duplicate")
            keys.add(key)
            issued_rows.append(IssuedForecastRow(
                archive_id, issued_at, origin, scenario, target, forecast_month, horizon, *quantiles
            ))
    except (KeyError, OSError, UnicodeDecodeError, TypeError, ValueError) as error:
        if isinstance(error, ValueError) and str(error).startswith("forecast CSV"):
            raise
        raise ValueError("forecast CSV is malformed") from error
    if len(issued_rows) != 144:
        raise ValueError("forecast CSV must contain exactly 144 unique rows")
    if file_bytes["scenario_sensitivity.csv"] != _scenario_sensitivity_bytes(issued_rows):
        raise ValueError("forecast scenario sensitivity does not match forecast rows")
    return tuple(issued_rows), hashlib.sha256(manifest_bytes).hexdigest()


def _scan_forecasts(forecast_root: Path, config: ChronosConfig) -> tuple[tuple[IssuedForecastRow, ...], tuple[str, ...], tuple[str, ...]]:
    if not forecast_root.exists():
        return (), (), ()
    forecast_root = forecast_root.resolve()
    if not forecast_root.is_dir() or not forecast_root.is_relative_to(config.project_root.resolve()):
        raise ValueError("forecast root collision")
    rows: list[IssuedForecastRow] = []
    manifest_hashes = []
    forecast_ids = []
    for child in sorted(forecast_root.iterdir(), key=lambda item: item.name):
        archive_rows, manifest_hash = _read_forecast_archive(child, forecast_root, config)
        rows.extend(archive_rows)
        manifest_hashes.append(manifest_hash)
        forecast_ids.append(child.name)
    return tuple(rows), tuple(manifest_hashes), tuple(forecast_ids)


def _read_monitoring_manifest(path: Path, monitoring_root: Path) -> dict[str, object]:
    snapshot_id = path.name
    resolved = path.resolve()
    monitoring_root = monitoring_root.resolve()
    if not _is_sha256(snapshot_id) or not path.is_dir() or resolved.parent != monitoring_root:
        raise ValueError("monitoring anchor path escapes monitoring root")
    expected_files = {"forecast_vs_actual.csv", "pending_forecasts.csv", "live_metrics.csv", "manifest.json"}
    children = {child.name: child for child in resolved.iterdir()}
    if set(children) != expected_files:
        raise ValueError("monitoring anchor has unexpected files")
    resolved_files = {}
    for name, child in children.items():
        resolved_child = child.resolve()
        if not child.is_file() or resolved_child.parent != resolved:
            raise ValueError("monitoring anchor file escapes snapshot root")
        resolved_files[name] = resolved_child
    try:
        payloads = {name: resolved_files[name].read_bytes() for name in expected_files}
        manifest = json.loads(payloads["manifest.json"].decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("monitoring anchor manifest is malformed") from error
    required = {
        "schema_version", "monitoring_id", "label", "target_csv_sha256", "target_manifest_sha256",
        "included_forecast_manifest_sha256", "forecast_ids", "generated_sha256",
    }
    if (
        not isinstance(manifest, dict)
        or set(manifest) != required
        or manifest.get("schema_version") != "CHRONOS_MONITORING_V1"
        or manifest.get("monitoring_id") != snapshot_id
        or manifest.get("label") != "PROSPECTIVE_TRACK_RECORD"
    ):
        raise ValueError("monitoring anchor manifest is invalid")
    included = manifest.get("included_forecast_manifest_sha256")
    forecast_ids = manifest.get("forecast_ids")
    generated = manifest.get("generated_sha256")
    if (
        not isinstance(included, list)
        or included != sorted(included)
        or not all(_is_sha256(item) for item in included)
        or not isinstance(forecast_ids, list)
        or len(forecast_ids) != len(set(forecast_ids))
        or not all(_is_sha256(item) for item in forecast_ids)
        or not isinstance(generated, dict)
        or set(generated) != expected_files - {"manifest.json"}
    ):
        raise ValueError("monitoring anchor contents are invalid")
    for name, digest in generated.items():
        if not _is_sha256(digest) or hashlib.sha256(payloads[name]).hexdigest() != digest:
            raise ValueError("monitoring anchor generated hash mismatch")
    target_hash = manifest.get("target_csv_sha256")
    target_manifest_hash = manifest.get("target_manifest_sha256")
    if not _is_sha256(target_hash) or not _is_sha256(target_manifest_hash):
        raise ValueError("monitoring anchor target hashes are invalid")
    identity = {
        "target_data_hash": target_hash,
        "target_manifest_hash": target_manifest_hash,
        "forecast_manifest_hashes": included,
    }
    if hashlib.sha256(canonical_json(identity)).hexdigest() != snapshot_id:
        raise ValueError("monitoring anchor identity mismatch")
    return manifest


def _require_forecast_anchor(
    monitoring_root: Path,
    forecast_identifier: str,
    forecast_manifest_hash: str,
) -> None:
    if not monitoring_root.exists() or not monitoring_root.is_dir():
        raise ValueError("existing forecast issued_at has no monitoring anchor")
    monitoring_root = monitoring_root.resolve()
    anchored = False
    for child in sorted(monitoring_root.iterdir(), key=lambda item: item.name):
        if child.name == ".staging":
            if not child.is_dir() or child.resolve().parent != monitoring_root:
                raise ValueError("monitoring staging path escapes monitoring root")
            continue
        manifest = _read_monitoring_manifest(child, monitoring_root)
        if (
            forecast_identifier in manifest["forecast_ids"]
            and forecast_manifest_hash in manifest["included_forecast_manifest_sha256"]
        ):
            anchored = True
    if not anchored:
        raise ValueError("existing forecast issued_at is not anchored by prior monitoring")


def publish_forecast(
    config_path: Path,
    output_root: Path,
    *,
    predictor: Callable | None = None,
    issued_at: datetime | None = None,
) -> str:
    config = load_chronos_config(config_path)
    _require_v1(config)
    output_root = _safe_project_path(output_root, config, "forecast output")
    issued_at = _parse_utc(issued_at or datetime.now(timezone.utc), "issued_at")
    requested_forecast_root = output_root / "forecasts"
    forecast_root = requested_forecast_root.resolve()
    if forecast_root.parent != output_root:
        raise ValueError("forecast root escapes requested output root")
    archived_rows, archived_hashes, archived_ids = _scan_forecasts(forecast_root, config)
    archive_hash_by_id = dict(zip(archived_ids, archived_hashes))
    for archived_id, archived_hash in zip(archived_ids, archived_hashes):
        _require_forecast_anchor(output_root / "monitoring", archived_id, archived_hash)

    targets, target_hash, target_manifest_hash = load_target_snapshot(config)
    covariates, vintage_id = load_covariate_table(config)
    origin, _target_values, covariate_values = _aligned_values(targets, covariates)
    identifier = forecast_id(config, target_hash, vintage_id, origin)
    rows = run_scenario_forecasts(config, predictor, tables=(targets, covariates))
    if len(rows) != 144 or any(row.origin != origin for row in rows):
        raise ValueError("forecast rows do not match the bound origin")

    scenarios = ecb_scenarios(float(covariate_values[0, -1]), config.scenario_basis_points, config.prediction_length)
    forecast_payload = _csv_bytes([
        {
            "scenario": row.scenario,
            "target": row.target,
            "forecast_month": row.forecast_month.isoformat(),
            "horizon": row.horizon,
            "q10": row.q10,
            "q50": row.q50,
            "q90": row.q90,
        }
        for row in rows
    ], ("scenario", "target", "forecast_month", "horizon", "q10", "q50", "q90"))
    sensitivity_payload = _scenario_sensitivity_bytes(rows)
    csv_files = {"forecast.csv": forecast_payload, "scenario_sensitivity.csv": sensitivity_payload}
    generated_hashes = {name: hashlib.sha256(payload).hexdigest() for name, payload in csv_files.items()}
    destination = forecast_root / identifier
    first_issued_at = issued_at
    if destination.exists():
        existing_rows = tuple(row for row in archived_rows if row.forecast_id == identifier)
        if len(existing_rows) != 144 or identifier not in archive_hash_by_id:
            raise ValueError("existing forecast archive is ambiguous")
        first_issued_at = existing_rows[0].issued_at
    manifest = _forecast_manifest(
        config, identifier, first_issued_at, origin, scenarios, target_hash, target_manifest_hash,
        vintage_id, generated_hashes,
    )
    staging_root = (output_root / ".staging").resolve()
    if not staging_root.is_relative_to(output_root):
        raise ValueError("forecast staging path escapes output root")
    files = {**csv_files, "manifest.json": canonical_json(manifest)}

    def inputs_unchanged() -> None:
        try:
            unchanged = (
                sha256_file(config.target_csv) == target_hash
                and sha256_file(config.target_manifest) == target_manifest_hash
                and load_covariate_table(config)[1] == vintage_id
            )
        except OSError as error:
            raise ValueError("target inputs changed during forecast") from error
        if not unchanged:
            raise ValueError("target or covariate inputs changed during forecast")

    was_existing = destination.exists()
    _atomic_snapshot(destination, staging_root, files, "forecast", precommit=inputs_unchanged)
    try:
        reconcile_forecasts(
            config_path,
            forecast_root,
            output_root / "monitoring",
            _new_forecast_id=identifier if not was_existing else None,
        )
    except Exception:
        if not was_existing and destination.is_dir():
            current = {path.name: path.read_bytes() for path in destination.iterdir() if path.is_file()}
            if current == files and len(tuple(destination.iterdir())) == len(files):
                shutil.rmtree(destination)
        raise
    return identifier


def reconcile_rows(
    forecasts: Iterable[IssuedForecastRow],
    actuals: Mapping[date, Mapping[str, float]],
) -> tuple[ReconciledRow, ...]:
    reconciled = []
    seen = set()
    for row in forecasts:
        key = (row.forecast_id, row.scenario, row.target, row.forecast_month, row.horizon)
        if key in seen:
            raise ValueError("duplicate forecast row")
        seen.add(key)
        if (
            not _is_sha256(row.forecast_id)
            or row.scenario not in SCENARIO_NAMES
            or row.target not in _TARGETS
            or row.horizon not in range(1, 13)
            or row.forecast_month != _month_end_after(row.origin, row.horizon)
            or row.issued_at.tzinfo is None
            or row.issued_at.utcoffset() != timedelta(0)
            or not all(math.isfinite(value) for value in (row.q10, row.q50, row.q90))
            or not row.q10 <= row.q50 <= row.q90
        ):
            raise ValueError("issued forecast row is invalid")
        month_actuals = actuals.get(row.forecast_month)
        if month_actuals is None or row.target not in month_actuals:
            continue
        actual = float(month_actuals[row.target])
        if not math.isfinite(actual):
            raise ValueError("actual return must be finite")
        signed_error = actual - row.q50
        reconciled.append(ReconciledRow(
            *row.__dict__.values(), actual, signed_error, abs(signed_error), signed_error * signed_error,
            row.q10 <= actual <= row.q90,
        ))
    return tuple(sorted(
        reconciled,
        key=lambda row: (row.forecast_id, row.scenario, row.target, row.horizon, row.forecast_month),
    ))


def _metric_rows(rows: tuple[ReconciledRow, ...]) -> list[dict[str, object]]:
    if not rows:
        return []
    groups: dict[tuple[str, str, int | str], list[ReconciledRow]] = {}
    for row in rows:
        groups.setdefault((row.scenario, row.target, row.horizon), []).append(row)
        groups.setdefault((row.scenario, row.target, "ALL"), []).append(row)
    groups[("ALL", "ALL", "ALL")] = list(rows)
    metrics = []
    for (scenario, target, horizon), selected in sorted(
        groups.items(), key=lambda item: (item[0][0], item[0][1], str(item[0][2]))
    ):
        errors = np.asarray([row.signed_error for row in selected], dtype=float)
        metrics.append({
            "scenario": scenario,
            "target": target,
            "horizon": horizon,
            "count": len(selected),
            "bias": float(np.mean(errors)),
            "mae": float(np.mean(np.abs(errors))),
            "rmse": float(np.sqrt(np.mean(errors * errors))),
            "interval_80_coverage": float(np.mean([row.interval_hit for row in selected])),
        })
    return metrics


def reconcile_forecasts(
    config_path: Path,
    forecast_root: Path,
    output: Path,
    *,
    _new_forecast_id: str | None = None,
) -> str:
    config = load_chronos_config(config_path)
    _require_v1(config)
    forecast_root = _safe_project_path(forecast_root, config, "forecast root")
    output = _safe_project_path(output, config, "monitoring output")
    forecasts, manifest_hashes, forecast_ids = _scan_forecasts(forecast_root, config)
    anchor_root = forecast_root.parent / "monitoring"
    for archived_id, archived_hash in zip(forecast_ids, manifest_hashes):
        if archived_id != _new_forecast_id:
            _require_forecast_anchor(anchor_root, archived_id, archived_hash)
    manifest_hashes = tuple(sorted(manifest_hashes))
    targets, target_hash, target_manifest_hash = load_target_snapshot(config)
    if targets.names != _TARGETS or targets.values.shape != (4, len(targets.months)):
        raise ValueError("target snapshot is invalid")
    if len(set(targets.months)) != len(targets.months):
        raise ValueError("target snapshot has ambiguous months")
    actuals = {
        month: {target: float(targets.values[index, month_index]) for index, target in enumerate(_TARGETS)}
        for month_index, month in enumerate(targets.months)
    }
    realized = reconcile_rows(forecasts, actuals)
    realized_keys = {
        (row.forecast_id, row.scenario, row.target, row.forecast_month, row.horizon) for row in realized
    }
    pending = tuple(sorted(
        (
            row for row in forecasts
            if (row.forecast_id, row.scenario, row.target, row.forecast_month, row.horizon) not in realized_keys
        ),
        key=lambda row: (row.forecast_id, row.scenario, row.target, row.horizon, row.forecast_month),
    ))
    realized_payload = _csv_bytes([
        {
            **{name: getattr(row, name) for name in (
                "forecast_id", "scenario", "target", "horizon", "q10", "q50", "q90", "actual",
                "signed_error", "absolute_error", "squared_error",
            )},
            "issued_at": row.issued_at.isoformat(),
            "origin": row.origin.isoformat(),
            "forecast_month": row.forecast_month.isoformat(),
            "interval_hit": "true" if row.interval_hit else "false",
        }
        for row in realized
    ], (
        "forecast_id", "issued_at", "origin", "scenario", "target", "forecast_month", "horizon",
        "q10", "q50", "q90", "actual", "signed_error", "absolute_error", "squared_error", "interval_hit",
    ))
    pending_payload = _csv_bytes([
        {
            "forecast_id": row.forecast_id,
            "issued_at": row.issued_at.isoformat(),
            "origin": row.origin.isoformat(),
            "scenario": row.scenario,
            "target": row.target,
            "forecast_month": row.forecast_month.isoformat(),
            "horizon": row.horizon,
            "q10": row.q10,
            "q50": row.q50,
            "q90": row.q90,
        }
        for row in pending
    ], ("forecast_id", "issued_at", "origin", "scenario", "target", "forecast_month", "horizon", "q10", "q50", "q90"))
    metrics_payload = _csv_bytes(
        _metric_rows(realized),
        ("scenario", "target", "horizon", "count", "bias", "mae", "rmse", "interval_80_coverage"),
    )
    csv_files = {
        "forecast_vs_actual.csv": realized_payload,
        "pending_forecasts.csv": pending_payload,
        "live_metrics.csv": metrics_payload,
    }
    identity = {
        "target_data_hash": target_hash,
        "target_manifest_hash": target_manifest_hash,
        "forecast_manifest_hashes": list(manifest_hashes),
    }
    monitoring_id = hashlib.sha256(canonical_json(identity)).hexdigest()
    generated_hashes = {name: hashlib.sha256(payload).hexdigest() for name, payload in csv_files.items()}
    manifest = {
        "schema_version": "CHRONOS_MONITORING_V1",
        "monitoring_id": monitoring_id,
        "label": "PROSPECTIVE_TRACK_RECORD",
        "target_csv_sha256": target_hash,
        "target_manifest_sha256": target_manifest_hash,
        "included_forecast_manifest_sha256": list(manifest_hashes),
        "forecast_ids": list(forecast_ids),
        "generated_sha256": generated_hashes,
    }
    staging_root = (output / ".staging").resolve()
    if not staging_root.is_relative_to(output):
        raise ValueError("monitoring staging path escapes output root")

    def inputs_unchanged() -> None:
        try:
            unchanged = (
                sha256_file(config.target_csv) == target_hash
                and sha256_file(config.target_manifest) == target_manifest_hash
            )
        except OSError as error:
            raise ValueError("target inputs changed during reconciliation") from error
        if not unchanged:
            raise ValueError("target inputs changed during reconciliation")

    _atomic_snapshot(
        output / monitoring_id,
        staging_root,
        {**csv_files, "manifest.json": canonical_json(manifest)},
        "monitoring",
        precommit=inputs_unchanged,
    )
    return monitoring_id
