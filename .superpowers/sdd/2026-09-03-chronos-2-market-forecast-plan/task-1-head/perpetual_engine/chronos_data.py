from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np

from perpetual_engine.io import canonical_json, sha256_file


_TARGETS = ("WORLD", "MOMENTUM", "QUALITY", "TREND")
_REQUIRED_SOURCES = ("ECB_DFR", "US_TREASURY_10Y", "BRENT_RETURN", "BIS_USD_CREDIT_YOY")
_REGISTERED_PARSERS = frozenset({"ecb_dfr", "fred_dgs10", "fred_brent", "bis_gli"})
_MODEL = ("amazon/chronos-2", "29ec3766d36d6f73f0696f85560a422f50e8498c", "cpu")


@dataclass(frozen=True)
class SourceSpec:
    source_id: str
    url: str
    parser: str
    role: str


@dataclass(frozen=True)
class ChronosConfig:
    path: Path
    project_root: Path
    model_id: str
    model_revision: str
    device: str
    target_csv: Path
    target_manifest: Path
    targets: tuple[str, ...]
    prediction_length: int
    quantiles: tuple[float, ...]
    scenario_basis_points: int
    data_root: Path
    sources: tuple[SourceSpec, ...]
    evaluation_origins: int
    reported_horizons: tuple[int, ...]
    bootstrap_block_months: int
    bootstrap_resamples: int
    bootstrap_seed: int
    bootstrap_confidence: float
    config_hash: str


@dataclass(frozen=True)
class MonthlyTable:
    months: tuple[date, ...]
    names: tuple[str, ...]
    values: np.ndarray


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _path(root: Path, value: Any, field: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty relative path")
    resolved = (root / value).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"{field} escapes project root")
    return resolved


def _month_end(value: date) -> date:
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1) - timedelta(days=1)


def _next_month_end(value: date) -> date:
    return _month_end(date(value.year + (value.month == 12), value.month % 12 + 1, 1))


def load_chronos_config(path: Path) -> ChronosConfig:
    path = path.resolve()
    project_root = path.parent.parent.resolve()
    if not path.is_relative_to(project_root):
        raise ValueError("configuration path escapes project root")
    try:
        data = _mapping(json.loads(path.read_text(encoding="utf-8")), "configuration")
        model = _mapping(data["model"], "model")
        target = _mapping(data["target"], "target")
        evaluation = _mapping(data["evaluation"], "evaluation")
        bootstrap = _mapping(data["bootstrap"], "bootstrap")
    except (KeyError, OSError, json.JSONDecodeError) as error:
        raise ValueError("Chronos configuration is malformed") from error
    if data.get("schema_version") != "CHRONOS_CONFIG_V1":
        raise ValueError("Chronos configuration schema is invalid")
    model_values = (model.get("id"), model.get("revision"), model.get("device"))
    if model_values != _MODEL:
        raise ValueError("Chronos model contract is invalid")
    targets = tuple(target.get("columns", ()))
    if targets != _TARGETS:
        raise ValueError("Chronos targets must be WORLD, MOMENTUM, QUALITY, TREND in order")
    quantiles = tuple(data.get("quantiles", ()))
    if data.get("prediction_length") != 12 or quantiles != (0.1, 0.5, 0.9):
        raise ValueError("Chronos prediction contract is invalid")
    raw_sources = data.get("sources")
    if not isinstance(raw_sources, list):
        raise ValueError("Chronos sources must be an array")
    sources: list[SourceSpec] = []
    for raw in raw_sources:
        source = _mapping(raw, "source")
        source_id, url, parser, role = source.get("id"), source.get("url"), source.get("parser"), source.get("role")
        if not all(isinstance(value, str) and value for value in (source_id, url, parser, role)) or parser not in _REGISTERED_PARSERS:
            raise ValueError("Chronos source is invalid or has an unregistered parser")
        sources.append(SourceSpec(source_id, url, parser, role))
    source_ids = tuple(source.source_id for source in sources)
    if len(source_ids) != len(set(source_ids)) or any(source_ids.count(source_id) != 1 for source_id in _REQUIRED_SOURCES):
        raise ValueError("Chronos requires each v1 source exactly once")
    try:
        reported_horizons = tuple(evaluation["reported_horizons"])
        numeric_values = (data["scenario_basis_points"], evaluation["origins"], bootstrap["block_months"], bootstrap["resamples"], bootstrap["seed"])
        confidence = bootstrap["confidence"]
    except KeyError as error:
        raise ValueError("Chronos configuration is incomplete") from error
    if numeric_values != (100, 36, 6, 2000, 42) or reported_horizons != (1, 3, 6, 12) or confidence != 0.95:
        raise ValueError("Chronos evaluation contract is invalid")
    return ChronosConfig(
        path, project_root, model_values[0], model_values[1], model_values[2],
        _path(project_root, target.get("csv"), "target.csv"), _path(project_root, target.get("manifest"), "target.manifest"),
        targets, 12, (0.1, 0.5, 0.9), 100, _path(project_root, data.get("data_root"), "data_root"), tuple(sources),
        36, reported_horizons, 6, 2000, 42, 0.95, hashlib.sha256(canonical_json(data)).hexdigest(),
    )


def load_target_table(config: ChronosConfig) -> MonthlyTable:
    try:
        manifest = _mapping(json.loads(config.target_manifest.read_text(encoding="utf-8")), "target manifest")
        expected_hash = _mapping(manifest["generated_sha256"], "target manifest generated_sha256")["monthly_returns.csv"]
    except (KeyError, OSError, json.JSONDecodeError) as error:
        raise ValueError("target manifest is malformed") from error
    if manifest.get("schema_version") != "FOUR_SLEEVE_REPORT_V1" or not isinstance(expected_hash, str) or expected_hash != sha256_file(config.target_csv):
        raise ValueError("target hash does not match manifest")
    try:
        with config.target_csv.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.reader(handle))
    except OSError as error:
        raise ValueError("target CSV is unreadable") from error
    required_header = ("month", *config.targets)
    if not rows or tuple(rows[0][:len(required_header)]) != required_header:
        raise ValueError("target CSV required columns are missing or reordered")
    months: list[date] = []
    values: list[list[float]] = []
    for row in rows[1:]:
        if len(row) < len(required_header):
            raise ValueError("target CSV row is incomplete")
        try:
            month = date.fromisoformat(row[0])
            row_values = [float(value) for value in row[1:len(required_header)]]
        except ValueError as error:
            raise ValueError("target CSV row is malformed") from error
        if month != _month_end(month):
            raise ValueError("target month must be month-end")
        if not all(math.isfinite(value) for value in row_values):
            raise ValueError("target values must be finite")
        if months and month != _next_month_end(months[-1]):
            raise ValueError("target months must be unique, ordered, and contiguous")
        months.append(month)
        values.append(row_values)
    if not months:
        raise ValueError("target CSV has no observations")
    array = np.asarray(values, dtype=float).T
    array.flags.writeable = False
    return MonthlyTable(tuple(months), config.targets, array)
