from __future__ import annotations

import json
import csv
import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from perpetual_engine.point_in_time import ObservationRow, load_observation_csv, validate_rows


@dataclass(frozen=True)
class EconomicSeries:
    series: str
    source: str
    store: str
    classification: str
    role: str = "PAST_ONLY"


@dataclass(frozen=True)
class EconomicInputs:
    covariates: Mapping[str, np.ndarray]
    catalog: tuple[dict[str, object], ...]
    erp: float | None
    warnings: tuple[str, ...]


SERIES = (
    EconomicSeries("VIX", "FRED_VIXCLS", "frozen", "HIGH_FREQUENCY_OBSERVED"),
    EconomicSeries("US_TREASURY_10Y_NOMINAL", "FRED_DGS10", "frozen", "HIGH_FREQUENCY_OBSERVED"),
    EconomicSeries("US_TREASURY_10Y_REAL", "FRED_DFII10", "frozen", "HIGH_FREQUENCY_OBSERVED"),
    EconomicSeries("US_10Y_BREAKEVEN", "FRED_T10YIE", "frozen", "HIGH_FREQUENCY_OBSERVED"),
    EconomicSeries("EURUSD", "FRED_DEXUSEU", "frozen", "HIGH_FREQUENCY_OBSERVED"),
    EconomicSeries("DAMODARAN_ERP", "DAMODARAN_ERP_MONTHLY", "frozen", "SLOW_MOVING_MACRO"),
    EconomicSeries("ECB_DFR", "ECB_DFR", "chronos", "SLOW_MOVING_MACRO", "KNOWN_FUTURE_SCENARIO"),
    EconomicSeries("BRENT_RETURN", "BRENT_RETURN", "chronos", "HIGH_FREQUENCY_OBSERVED"),
    EconomicSeries("BIS_USD_CREDIT_YOY", "BIS_USD_CREDIT_YOY", "chronos", "SLOW_MOVING_MACRO"),
    EconomicSeries("US_CPI", "US_CPI_YOY", "chronos", "SLOW_MOVING_MACRO"),
    EconomicSeries("MOMENTUM_WORLD_RELATIVE", "FOUR_SLEEVE_MONTHLY", "derived", "HIGH_FREQUENCY_OBSERVED"),
    EconomicSeries("QUALITY_WORLD_RELATIVE", "FOUR_SLEEVE_MONTHLY", "derived", "HIGH_FREQUENCY_OBSERVED"),
    EconomicSeries("TREND_WORLD_RELATIVE", "FOUR_SLEEVE_MONTHLY", "derived", "HIGH_FREQUENCY_OBSERVED"),
    EconomicSeries("VALUE_WORLD_RELATIVE", "UNAVAILABLE", "unavailable", "HIGH_FREQUENCY_OBSERVED"),
    EconomicSeries("SMALL_VALUE_WORLD_RELATIVE", "UNAVAILABLE", "unavailable", "HIGH_FREQUENCY_OBSERVED"),
    EconomicSeries("MSCI_WORLD_FORWARD_EARNINGS_YIELD", "UNAVAILABLE", "unavailable", "SLOW_MOVING_MACRO"),
)


def _object(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _configured_sources(path: Path, key: str) -> dict[str, dict[str, object]]:
    payload = _object(path) or {}
    values = payload.get("sources", [])
    if not isinstance(values, list):
        return {}
    return {
        str(item[key]): item
        for item in values
        if isinstance(item, dict) and isinstance(item.get(key), str)
    }


def _manifest_sources(path: Path, key: str) -> tuple[dict[str, object], dict[str, dict[str, object]]]:
    manifest = _object(path) or {}
    values = manifest.get("sources", [])
    sources = {
        str(item[key]): item
        for item in values
        if isinstance(item, dict) and isinstance(item.get(key), str)
    } if isinstance(values, list) else {}
    return manifest, sources


def economic_series_catalog(project_root: Path) -> tuple[dict[str, object], ...]:
    root = Path(project_root).resolve()
    general = _configured_sources(root / "config" / "data_sources_v1.json", "source_id")
    chronos = _configured_sources(root / "config" / "chronos_v1.json", "id")
    frozen_manifest, frozen = _manifest_sources(root / "data" / "frozen" / "current_manifest.json", "source_id")
    chronos_pointer = _object(root / "data" / "chronos_v1" / "current_manifest.json") or {}
    chronos_id = chronos_pointer.get("vintage_id")
    chronos_manifest, chronos_saved = _manifest_sources(
        root / "data" / "chronos_v1" / "vintages" / str(chronos_id) / "manifest.json", "id"
    )
    factor_path = root / "outputs" / "four_sleeve_v1" / "monthly_returns.csv"
    frozen_rows = _frozen_rows(root)
    chronos_latest = None
    if isinstance(chronos_id, str):
        table_path = root / "data" / "chronos_v1" / "vintages" / chronos_id / "covariates.csv"
        try:
            with table_path.open(encoding="utf-8", newline="") as handle:
                chronos_latest = list(csv.DictReader(handle))[-1]["month"]
        except (IndexError, KeyError, OSError):
            pass
    factor_latest = None
    try:
        with factor_path.open(encoding="utf-8", newline="") as handle:
            factor_latest = list(csv.DictReader(handle))[-1]["month"]
    except (IndexError, KeyError, OSError):
        pass
    output: list[dict[str, object]] = []
    for item in SERIES:
        configured: dict[str, object] | None
        saved: dict[str, object] | None
        manifest: dict[str, object]
        if item.store == "frozen":
            configured, saved, manifest = general.get(item.source), frozen.get(item.source), frozen_manifest
        elif item.store == "chronos":
            configured, saved, manifest = chronos.get(item.source), chronos_saved.get(item.source), chronos_manifest
        elif item.store == "derived":
            configured, saved, manifest = ({"url": "local://four_sleeve"}, {} if factor_path.is_file() else None, {})
        else:
            configured = saved = None
            manifest = {}
        if item.store == "unavailable":
            status = "UNAVAILABLE_NO_POINT_IN_TIME_SOURCE"
        elif configured is None:
            status = "NOT_CONFIGURED"
        elif saved is None:
            status = "MISSING_VINTAGE"
        else:
            status = str(saved.get("vintage_status", "AVAILABLE"))
        latest_observation = None
        latest_available_at = None
        source_rows = frozen_rows.get(item.source)
        if source_rows:
            latest = max(source_rows, key=lambda row: (row.observation_date, row.available_at))
            latest_observation = latest.observation_date.isoformat()
            latest_available_at = latest.available_at.isoformat()
        elif item.store == "chronos":
            latest_observation = chronos_latest
        elif item.store == "derived":
            latest_observation = factor_latest
        output.append({
            "series": item.series,
            "source": item.source,
            "classification": item.classification,
            "role": item.role,
            "status": status,
            "url": None if configured is None else configured.get("url"),
            "vintage_id": manifest.get("vintage_id"),
            "retrieved_at": manifest.get("retrieved_at"),
            "latest_observation": latest_observation,
            "latest_available_at": latest_available_at,
            "limitation": (
                "CURRENT_OR_REVISED_HISTORY_NOT_TRUE_VINTAGE"
                if item.store in {"frozen", "chronos"} else
                "NO_LICENSED_POINT_IN_TIME_SOURCE" if item.store == "unavailable" else None
            ),
        })
    return tuple(output)


def align_rows_asof(
    dates: Sequence[np.datetime64], rows: Sequence[ObservationRow], series_id: str
) -> np.ndarray:
    observed = np.asarray(dates, dtype="datetime64[D]")
    if observed.ndim != 1 or not len(observed):
        raise ValueError("economic alignment dates are invalid")
    candidates = tuple(row for row in rows if row.series_id == series_id)
    if not candidates:
        raise ValueError(f"economic series {series_id} is missing")
    ordered = sorted(
        validate_rows(candidates),
        key=lambda row: (row.available_at, row.observation_date, row.retrieved_at, row.source_hash),
    )
    availability = [row.available_at for row in ordered]
    values = []
    for value in observed:
        day = date.fromisoformat(str(value))
        position = bisect_right(availability, datetime.combine(day, time.max, tzinfo=timezone.utc)) - 1
        values.append(float("nan") if position < 0 else float(ordered[position].value))
    result = np.asarray(values, dtype=float)
    result.setflags(write=False)
    return result


def _frozen_rows(root: Path) -> dict[str, tuple[ObservationRow, ...]]:
    data_root = root / "data" / "frozen"
    manifest = _object(data_root / "current_manifest.json")
    if manifest is None:
        return {}
    output: dict[str, tuple[ObservationRow, ...]] = {}
    for source in manifest.get("sources", []):
        if not isinstance(source, dict) or not isinstance(source.get("source_id"), str):
            continue
        relative = source.get("normalized_path")
        if not isinstance(relative, str) or Path(relative).is_absolute():
            continue
        path = (data_root / relative).resolve()
        if path.is_relative_to(data_root.resolve()) and path.is_file():
            output[source["source_id"]] = load_observation_csv(path)
    return output


def _monthly_asof(
    dates: np.ndarray, months: Sequence[date], values: Sequence[float]
) -> np.ndarray:
    month_days = np.asarray(months, dtype="datetime64[D]")
    data = np.asarray(values, dtype=float)
    positions = np.searchsorted(month_days, dates, side="right") - 1
    result = np.full(len(dates), np.nan)
    valid = positions >= 0
    result[valid] = data[positions[valid]]
    result.setflags(write=False)
    return result


def _chronos_covariates(root: Path, dates: np.ndarray) -> dict[str, np.ndarray]:
    if not (root / "data" / "chronos_v1" / "current_manifest.json").is_file():
        return {}
    from perpetual_engine.chronos_data import load_chronos_config, load_covariate_table

    try:
        config = load_chronos_config(root / "config" / "chronos_v1.json")
        table, _vintage = load_covariate_table(config)
    except (KeyError, OSError, TypeError, ValueError):
        return {}
    rename = {"US_TREASURY_10Y": "US_TREASURY_10Y_NOMINAL", "US_CPI_YOY": "US_CPI"}
    return {
        rename.get(name, name): _monthly_asof(dates, table.months, table.values[index])
        for index, name in enumerate(table.names)
    }


def _factor_covariates(root: Path, dates: np.ndarray) -> dict[str, np.ndarray]:
    path = root / "outputs" / "four_sleeve_v1" / "monthly_returns.csv"
    if not path.is_file():
        return {}
    months: list[date] = []
    values = {name: [] for name in ("MOMENTUM", "QUALITY", "TREND")}
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                world = float(row["WORLD"])
                months.append(date.fromisoformat(row["month"]))
                for name in values:
                    values[name].append(math.log1p(float(row[name])) - math.log1p(world))
    except (KeyError, OSError, TypeError, ValueError):
        return {}
    return {
        f"{name}_WORLD_RELATIVE": _monthly_asof(dates, months, series)
        for name, series in values.items()
    }


def load_risk_covariates(project_root: Path, dates: Sequence[np.datetime64]) -> EconomicInputs:
    root = Path(project_root).resolve()
    observed = np.asarray(dates, dtype="datetime64[D]")
    if observed.ndim != 1 or not len(observed) or np.any(observed[1:] <= observed[:-1]):
        raise ValueError("risk covariate dates are invalid")
    rows = _frozen_rows(root)
    definitions = {
        "FRED_VIXCLS": ("VIX", "VIXCLS"),
        "FRED_DGS10": ("US_TREASURY_10Y_NOMINAL", "DGS10"),
        "FRED_DFII10": ("US_TREASURY_10Y_REAL", "DFII10"),
        "FRED_T10YIE": ("US_10Y_BREAKEVEN", "T10YIE"),
        "FRED_DEXUSEU": ("EURUSD", "DEXUSEU"),
        "DAMODARAN_ERP_MONTHLY": ("DAMODARAN_ERP", "DAMODARAN_ERP_T12M"),
    }
    covariates: dict[str, np.ndarray] = {}
    warnings: list[str] = []
    for source, (name, series_id) in definitions.items():
        if source not in rows:
            warnings.append(f"{name}: MISSING_VINTAGE")
            continue
        try:
            covariates[name] = align_rows_asof(observed, rows[source], series_id)
        except ValueError:
            warnings.append(f"{name}: INVALID_OR_EMPTY")
    covariates.update({name: value for name, value in _chronos_covariates(root, observed).items() if name not in covariates})
    covariates.update(_factor_covariates(root, observed))
    erp_values = covariates.get("DAMODARAN_ERP")
    erp = None
    if erp_values is not None:
        finite = erp_values[np.isfinite(erp_values)]
        erp = float(finite[-1]) if len(finite) else None
    return EconomicInputs(covariates, economic_series_catalog(root), erp, tuple(warnings))


def refresh_economic_data(project_root: Path) -> dict[str, str]:
    from perpetual_engine.backtest_report import refresh_data
    from perpetual_engine.chronos_data import refresh_chronos_data

    root = Path(project_root).resolve()
    chronos = refresh_chronos_data(root / "config" / "chronos_v1.json")
    refresh_data(root / "config" / "economic_sources_v1.json")
    frozen = _object(root / "data" / "frozen" / "current_manifest.json") or {}
    return {"chronos_vintage": str(chronos), "frozen_vintage": str(frozen.get("vintage_id", ""))}
