from __future__ import annotations

import csv
import ctypes
import hashlib
import io
import json
import math
import tempfile
from contextlib import ExitStack, contextmanager
from ctypes import wintypes
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np

from perpetual_engine.dashboard_service import DashboardPaths, DashboardState, _atomic_write, exchange_for_ticker
from perpetual_engine.chronos import (
    ForecastRow,
    SCENARIO_NAMES,
    _atomic_snapshot,
    _chronos_package_version,
    _csv_bytes,
    _is_sha256,
    _month_end_after,
    _parse_utc,
    _publication_lock,
    _require_v1,
    ecb_scenarios,
    evaluation_variants,
    load_chronos_predictor,
    moving_block_interval,
    moving_block_p_value,
    pinball_loss,
)
from perpetual_engine.chronos_data import (
    ChronosConfig,
    MonthlyTable,
    REQUIRED_V1_COVARIATES,
    load_chronos_config,
    load_covariate_table,
    load_covariate_vintage,
)
from perpetual_engine.io import canonical_json, remove_tree
from perpetual_engine.portfolio_monitor import ComponentSpec, load_current_portfolio_prices, load_portfolio_config


_SCHEMA = "DIRECT_CHRONOS_PORTFOLIO_V1"


@dataclass(frozen=True)
class DirectComponent:
    component_id: str
    name: str
    ticker: str
    isin: str
    exchange: str
    currency: str
    weight: float


@dataclass(frozen=True)
class DirectPortfolio:
    label: str
    components: tuple[DirectComponent, ...]


@dataclass(frozen=True)
class EtfTargetSeries:
    component: DirectComponent
    months: tuple[date, ...]
    returns: np.ndarray
    history_status: str
    series_sha256: str


@dataclass(frozen=True)
class DirectTargetSnapshot:
    candidate: DirectPortfolio
    base: DirectPortfolio
    series: tuple[EtfTargetSeries, ...]
    common_origin: date
    price_vintage_id: str
    price_manifest_sha256: str
    retrieved_at: datetime


@dataclass(frozen=True)
class DirectForecastResult:
    forecast_id: str
    output_dir: Path
    common_origin: date
    candidate_sha256: str
    base_sha256: str


def _direct_component(source: ComponentSpec | Any, *, name: str | None = None, exchange: str | None = None, weight: float | None = None) -> DirectComponent:
    return DirectComponent(
        source.component_id if isinstance(source, ComponentSpec) else source.study_id,
        name if name is not None else (source.component_id if isinstance(source, ComponentSpec) else source.name),
        source.ticker, source.isin, exchange if exchange is not None else exchange_for_ticker(source.ticker),
        source.quote_currency, getattr(source, "weight", 0.0) if weight is None else weight,
    )


def _base_portfolio(paths: DashboardPaths) -> DirectPortfolio:
    return DirectPortfolio("Portafoglio base", tuple(_direct_component(item) for item in load_portfolio_config(paths.default_config).components))


def _sources(paths: DashboardPaths, state: DashboardState) -> dict[str, DirectComponent]:
    base = _base_portfolio(paths)
    sources = {item.component_id: item for item in base.components}
    for item in state.catalog:
        sources.setdefault(item.study_id, _direct_component(item, exchange=item.exchange))
    return sources


def _validate_components(components: tuple[DirectComponent, ...]) -> None:
    if len(components) != 4:
        raise ValueError("candidate must contain exactly four ETFs")
    if len({item.component_id for item in components}) != 4 or len({item.ticker for item in components}) != 4 or len({item.isin for item in components}) != 4:
        raise ValueError("candidate ETF identity is duplicated")
    if any(item.currency != "EUR" or type(item.weight) not in (int, float) or not math.isfinite(item.weight) or item.weight <= 0 for item in components):
        raise ValueError("candidate ETF identity or weight is invalid")
    if abs(sum((Decimal(str(item.weight)) for item in components), Decimal("0")) - Decimal("1")) > Decimal("0.0001"):
        raise ValueError("candidate weights must total 100% within 0.01 percentage points")


def _candidate_payload(portfolio: DirectPortfolio) -> bytes:
    return canonical_json({
        "schema_version": _SCHEMA,
        "label": portfolio.label,
        "components": [{
            "id": item.component_id, "name": item.name, "ticker": item.ticker, "isin": item.isin,
            "exchange": item.exchange, "currency": item.currency, "weight": item.weight,
        } for item in portfolio.components],
    })


def _load_candidate(path: Path, sources: Mapping[str, DirectComponent]) -> DirectPortfolio:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"schema_version", "label", "components"} or payload["schema_version"] != _SCHEMA or payload["label"] != "Portafoglio da studiare" or not isinstance(payload["components"], list):
            raise ValueError
        components = tuple(DirectComponent(
            item["id"], item["name"], item["ticker"], item["isin"], item["exchange"], item["currency"], item["weight"],
        ) for item in payload["components"] if isinstance(item, dict) and set(item) == {"id", "name", "ticker", "isin", "exchange", "currency", "weight"})
        if len(components) != len(payload["components"]):
            raise ValueError
        _validate_components(components)
        for item in components:
            source = sources.get(item.component_id)
            if source is None or item != DirectComponent(source.component_id, source.name, source.ticker, source.isin, source.exchange, source.currency, item.weight):
                raise ValueError
        return DirectPortfolio("Portafoglio da studiare", components)
    except Exception as error:
        raise ValueError("candidate portfolio is malformed or identity is no longer confirmed") from error


def load_direct_portfolios(paths: DashboardPaths, state: DashboardState) -> tuple[DirectPortfolio, DirectPortfolio]:
    base = _base_portfolio(paths)
    sources = _sources(paths, state)
    if not paths.chronos_candidate.exists():
        return DirectPortfolio("Portafoglio da studiare", base.components), base
    return _load_candidate(paths.chronos_candidate, sources), base


def save_candidate_portfolio(paths: DashboardPaths, state: DashboardState, rows: Any) -> DirectPortfolio:
    if not isinstance(rows, (list, tuple)):
        raise ValueError("candidate rows are invalid")
    sources = _sources(paths, state)
    try:
        components = tuple(DirectComponent(
            sources[row["id"]].component_id, sources[row["id"]].name, sources[row["id"]].ticker,
            sources[row["id"]].isin, sources[row["id"]].exchange, sources[row["id"]].currency, row["weight"],
        ) for row in rows if isinstance(row, dict) and set(row) == {"id", "weight"})
    except (KeyError, TypeError) as error:
        raise ValueError("candidate ETF identity is unknown") from error
    if len(components) != len(rows):
        raise ValueError("candidate rows are invalid")
    _validate_components(components)
    candidate = DirectPortfolio("Portafoglio da studiare", components)
    _atomic_write(paths.chronos_candidate, _candidate_payload(candidate))
    return candidate


def reset_candidate_portfolio(paths: DashboardPaths, state: DashboardState) -> DirectPortfolio:
    base = _base_portfolio(paths)
    candidate = DirectPortfolio("Portafoglio da studiare", base.components)
    _atomic_write(paths.chronos_candidate, _candidate_payload(candidate))
    return candidate


def _month_end(value: date) -> date:
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1) - timedelta(days=1)


def _next_month_end(value: date) -> date:
    return _month_end(date(value.year + (value.month == 12), value.month % 12 + 1, 1))


def _select_price(prices: Mapping[date, float], month: date, staleness: int) -> tuple[date, float] | None:
    eligible = [(day, value) for day, value in prices.items() if day <= month and (month - day).days <= staleness]
    return max(eligible, default=None)


def _monthly_returns(prices: Mapping[date, float], staleness: int, as_of: date) -> tuple[tuple[date, ...], np.ndarray]:
    if not prices:
        return (), np.asarray((), dtype=float)
    end = date(as_of.year, as_of.month, 1) - timedelta(days=1)
    selected: dict[date, float] = {}
    month = _month_end(min(prices))
    while month <= end:
        observation = _select_price(prices, month, staleness)
        if observation is not None:
            selected[month] = observation[1]
        month = _next_month_end(month)
    result_months = []
    returns = []
    for month in sorted(selected):
        previous = _month_end(date(month.year, month.month, 1) - timedelta(days=1))
        if previous in selected:
            result_months.append(month)
            returns.append(selected[month] / selected[previous] - 1.0)
    if any(not math.isfinite(value) for value in returns):
        raise ValueError("ETF monthly return is not finite")
    return tuple(result_months), np.asarray(returns, dtype=float)


def _series_csv(months: tuple[date, ...], returns: np.ndarray) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("month", "return"))
    writer.writerows((month.isoformat(), format(float(value), ".17g")) for month, value in zip(months, returns))
    return output.getvalue().encode()


def load_etf_target_snapshot(paths: DashboardPaths, state: DashboardState) -> DirectTargetSnapshot:
    candidate, base = load_direct_portfolios(paths, state)
    runtime = paths.runtime_config
    from perpetual_engine.dashboard_service import materialize_runtime_config
    materialize_runtime_config(paths.default_config, state, runtime, project_root=paths.project_root)
    config, raw, manifest = load_current_portfolio_prices(runtime, project_root=paths.project_root)
    retrieved_at = datetime.fromisoformat(manifest["retrieved_at"])
    components: list[DirectComponent] = []
    seen: set[tuple[str, str]] = set()
    for item in (*candidate.components, *base.components):
        key = (item.ticker, item.isin)
        if key not in seen:
            seen.add(key)
            components.append(item)
    unbounded: list[tuple[DirectComponent, tuple[date, ...], np.ndarray]] = []
    for component in components:
        if component.ticker not in raw:
            raise ValueError(f"{component.ticker}: 0 observed monthly returns; price history is unavailable")
        monthly, returns = _monthly_returns(raw[component.ticker], config.max_staleness_days, retrieved_at.date())
        unbounded.append((component, monthly, returns))
    for component, _monthly, returns in unbounded:
        if len(returns) < 12:
            raise ValueError(f"{component.ticker}: {len(returns)} observed monthly returns; DATI_INSUFFICIENTI")
    common = set(unbounded[0][1])
    for _component, monthly, _returns in unbounded[1:]:
        common.intersection_update(monthly)
    if not common:
        raise ValueError("ETF histories have no common origin")
    common_origin = max(common)
    series: list[EtfTargetSeries] = []
    for component, monthly, returns in unbounded:
        values = dict(zip(monthly, returns))
        kept = []
        month = common_origin
        while month in values:
            kept.append((month, values[month]))
            month = _month_end(date(month.year, month.month, 1) - timedelta(days=1))
        kept.reverse()
        kept_months = tuple(month for month, _value in kept)
        kept_returns = np.asarray([value for _month, value in kept], dtype=float)
        if len(kept_returns) < 12:
            raise ValueError(
                f"{component.ticker}: {len(kept_returns)} observed monthly returns; DATI_INSUFFICIENTI at common origin"
            )
        series.append(EtfTargetSeries(
            component, kept_months, kept_returns, "STORICO_BREVE" if len(kept_returns) < 60 else "SUFFICIENT_HISTORY",
            hashlib.sha256(_series_csv(kept_months, kept_returns)).hexdigest(),
        ))
    manifest_path = config.data_root / "vintages" / manifest["vintage_id"] / "manifest.json"
    return DirectTargetSnapshot(
        candidate, base, tuple(series), common_origin, manifest["vintage_id"],
        hashlib.sha256(manifest_path.read_bytes()).hexdigest(), retrieved_at,
    )


def _aligned_etf_values(series: EtfTargetSeries, covariates: MonthlyTable, origin: date) -> tuple[np.ndarray, np.ndarray]:
    if covariates.names != REQUIRED_V1_COVARIATES or covariates.values.shape != (
        len(REQUIRED_V1_COVARIATES), len(covariates.months)
    ):
        raise ValueError(f"{series.component.ticker}: macro table columns or shape are invalid")
    if series.returns.shape != (len(series.months),):
        raise ValueError(f"{series.component.ticker}: ETF return history shape is invalid")
    covariate_index = {month: index for index, month in enumerate(covariates.months)}
    if not series.months or series.months[-1] != origin or any(month not in covariate_index for month in series.months):
        raise ValueError(f"{series.component.ticker}: macro history must cover every retained ETF month through common_origin")
    target = np.asarray(series.returns, dtype=np.float32)
    past = np.asarray(covariates.values[:, [covariate_index[month] for month in series.months]], dtype=np.float32)
    if len(target) < 12 or not np.isfinite(target).all() or not np.isfinite(past).all():
        raise ValueError(f"{series.component.ticker}: aligned forecast history must contain at least 12 finite returns")
    return target, past


def run_direct_scenario_forecasts(
    config: ChronosConfig,
    snapshot: DirectTargetSnapshot,
    covariates: MonthlyTable,
    predictor: Callable,
) -> tuple[ForecastRow, ...]:
    _require_v1(config)
    if not snapshot.series:
        raise ValueError("direct ETF forecast has no components")
    try:
        origin_index = covariates.months.index(snapshot.common_origin)
        last_rate = float(covariates.values[REQUIRED_V1_COVARIATES.index("ECB_DFR"), origin_index])
    except (IndexError, ValueError) as error:
        raise ValueError("shared common_origin is unavailable in macro history") from error
    scenarios = ecb_scenarios(last_rate, config.scenario_basis_points, config.prediction_length)
    work: list[tuple[EtfTargetSeries, str, dict[str, object]]] = []
    for series in snapshot.series:
        target, past = _aligned_etf_values(series, covariates, snapshot.common_origin)
        past_covariates = {
            name: np.asarray(past[index], dtype=np.float32)
            for index, name in enumerate(REQUIRED_V1_COVARIATES)
        }
        for scenario in SCENARIO_NAMES:
            future = np.asarray(scenarios[scenario], dtype=np.float32)
            if future.shape != (12,) or not np.isfinite(future).all():
                raise ValueError(f"{series.component.ticker}: ECB future scenario must contain 12 finite values")
            work.append((series, scenario, {
                "target": target.reshape(1, -1),
                "past_covariates": past_covariates,
                "future_covariates": {"ECB_DFR": future},
            }))
    raw = predictor(
        [item for _series, _scenario, item in work],
        prediction_length=config.prediction_length,
        quantile_levels=list(config.quantiles),
    )
    if not isinstance(raw, (list, tuple)) or len(raw) != len(work):
        raise ValueError(f"{work[0][0].component.ticker}: predictor must return exactly {len(work)} direct ETF results")
    arrays = []
    for (series, scenario, _item), result in zip(work, raw):
        array = np.asarray(result, dtype=float)
        if array.shape != (1, 12, 3) or not np.isfinite(array).all():
            raise ValueError(f"{series.component.ticker}: {scenario} result must be a finite (1, 12, 3) array")
        if np.any(array[:, :, 0] > array[:, :, 1]) or np.any(array[:, :, 1] > array[:, :, 2]):
            raise ValueError(f"{series.component.ticker}: {scenario} Chronos quantiles are crossed")
        arrays.append(array)
    return tuple(
        ForecastRow(
            snapshot.common_origin,
            scenario,
            series.component.ticker,
            _month_end_after(snapshot.common_origin, horizon),
            horizon,
            float(array[0, horizon - 1, 0]),
            float(array[0, horizon - 1, 1]),
            float(array[0, horizon - 1, 2]),
        )
        for (series, scenario, _item), array in zip(work, arrays)
        for horizon in range(1, 13)
    )


def _component_payload(component: DirectComponent) -> dict[str, object]:
    return {
        "id": component.component_id,
        "name": component.name,
        "ticker": component.ticker,
        "isin": component.isin,
        "exchange": component.exchange,
        "currency": component.currency,
        "weight": component.weight,
    }


def _portfolio_payload(portfolio: DirectPortfolio) -> dict[str, object]:
    return {"label": portfolio.label, "components": [_component_payload(item) for item in portfolio.components]}


def direct_portfolio_sha256(portfolio: DirectPortfolio) -> str:
    return hashlib.sha256(canonical_json(_portfolio_payload(portfolio))).hexdigest()


def _target_series_payload(series: EtfTargetSeries) -> dict[str, object]:
    return {
        **_component_payload(series.component),
        "history_count": len(series.returns),
        "history_status": series.history_status,
        "series_sha256": series.series_sha256,
        "trailing_volatility_12m": float(np.std(series.returns[-12:], ddof=1) * math.sqrt(12.0)),
    }


def _direct_identity(
    model: Mapping[str, object],
    config_hash: str,
    portfolios: Mapping[str, object],
    target_series: list[dict[str, object]],
    price_vintage: Mapping[str, object],
    macro_vintage_id: str,
    common_origin: str,
    scenarios: Mapping[str, object],
) -> dict[str, object]:
    return {
        "model": dict(model),
        "config_sha256": config_hash,
        "portfolios": dict(portfolios),
        "target_series": target_series,
        "price_vintage": dict(price_vintage),
        "macro_vintage_id": macro_vintage_id,
        "common_origin": common_origin,
        "scenarios": dict(scenarios),
    }


def _portfolio_rows(candidate: DirectPortfolio, base: DirectPortfolio, rows: tuple[ForecastRow, ...]) -> list[dict[str, object]]:
    by_key = {(row.target, row.scenario, row.horizon): row for row in rows}
    output: list[dict[str, object]] = []
    for name, portfolio in (("CANDIDATE", candidate), ("BASE", base)):
        for scenario in SCENARIO_NAMES:
            value = 100.0
            for horizon in range(1, 13):
                try:
                    selected = [by_key[(component.ticker, scenario, horizon)] for component in portfolio.components]
                except KeyError as error:
                    ticker = next(
                        component.ticker for component in portfolio.components
                        if (component.ticker, scenario, horizon) not in by_key
                    )
                    raise ValueError(f"{ticker}: component forecast is missing; portfolio was not renormalized") from error
                central = sum(component.weight * row.q50 for component, row in zip(portfolio.components, selected))
                value *= 1.0 + central
                if not math.isfinite(central) or not math.isfinite(value):
                    raise ValueError(f"{name}: central portfolio path is not finite")
                output.append({
                    "portfolio": name,
                    "scenario": scenario,
                    "forecast_month": selected[0].forecast_month.isoformat(),
                    "horizon": horizon,
                    "central_return": central,
                    "cumulative_eur_100": value,
                })
    return output


def _sensitivity_rows(target_series: list[dict[str, object]], rows: tuple[ForecastRow, ...], paths: list[dict[str, object]]) -> list[dict[str, object]]:
    by_key = {(row.target, row.scenario, row.horizon): row for row in rows}
    output = []
    for series in target_series:
        for scenario in SCENARIO_NAMES[1:]:
            for horizon in range(1, 13):
                row = by_key[(series["ticker"], scenario, horizon)]
                flat = by_key[(series["ticker"], "ECB_FLAT", horizon)]
                output.append({
                    "scope": "ETF", "portfolio": "", "symbol": series["ticker"],
                    "isin": series["isin"], "forecast_month": row.forecast_month.isoformat(),
                    "horizon": horizon, "scenario": scenario, "q50": row.q50, "flat_q50": flat.q50,
                    "q50_delta": row.q50 - flat.q50, "interval_width": row.q90 - row.q10,
                    "flat_interval_width": flat.q90 - flat.q10,
                    "interval_width_delta": (row.q90 - row.q10) - (flat.q90 - flat.q10),
                })
    path_index = {(row["portfolio"], row["scenario"], row["horizon"]): row for row in paths}
    for portfolio in ("CANDIDATE", "BASE"):
        for scenario in SCENARIO_NAMES[1:]:
            for horizon in range(1, 13):
                row = path_index[(portfolio, scenario, horizon)]
                flat = path_index[(portfolio, "ECB_FLAT", horizon)]
                output.append({
                    "scope": "PORTFOLIO", "portfolio": portfolio, "symbol": "", "isin": "",
                    "forecast_month": row["forecast_month"], "horizon": horizon, "scenario": scenario,
                    "q50": row["central_return"], "flat_q50": flat["central_return"],
                    "q50_delta": row["central_return"] - flat["central_return"],
                    "interval_width": "", "flat_interval_width": "", "interval_width_delta": "",
                })
    return output


def _volatility_rows(target_series: list[dict[str, object]], rows: tuple[ForecastRow, ...], origin: date) -> list[dict[str, object]]:
    series_by_ticker = {series["ticker"]: series for series in target_series}
    output = []
    for row in rows:
        series = series_by_ticker[row.target]
        output.append({
            "symbol": series["ticker"],
            "isin": series["isin"],
            "origin": origin.isoformat(),
            "history_count": series["history_count"],
            "history_status": series["history_status"],
            "trailing_volatility_12m": series["trailing_volatility_12m"],
            "scenario": row.scenario,
            "forecast_month": row.forecast_month.isoformat(),
            "horizon": row.horizon,
            "interval_width": row.q90 - row.q10,
        })
    return output


def _read_csv(content: bytes, columns: tuple[str, ...], label: str) -> tuple[dict[str, str], ...]:
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8"), newline=""))
        if tuple(reader.fieldnames or ()) != columns:
            raise ValueError(f"{label} header is invalid")
        rows = tuple(reader)
    except UnicodeDecodeError as error:
        raise ValueError(f"{label} is malformed") from error
    if any(None in row or set(row) != set(columns) for row in rows):
        raise ValueError(f"{label} rows are malformed")
    return rows


_ETF_COLUMNS = (
    "scenario", "symbol", "isin", "exchange", "currency", "forecast_month", "horizon",
    "q10", "q50", "q90", "history_count", "history_status",
)
_PORTFOLIO_COLUMNS = ("portfolio", "scenario", "forecast_month", "horizon", "central_return", "cumulative_eur_100")
_SENSITIVITY_COLUMNS = (
    "scope", "portfolio", "symbol", "isin", "forecast_month", "horizon", "scenario", "q50", "flat_q50",
    "q50_delta", "interval_width", "flat_interval_width", "interval_width_delta",
)
_VOLATILITY_COLUMNS = (
    "symbol", "isin", "origin", "history_count", "history_status", "trailing_volatility_12m",
    "scenario", "forecast_month", "horizon", "interval_width",
)
_DIRECT_FILES = {"etf_forecast.csv", "portfolio_paths.csv", "scenario_sensitivity.csv", "volatility_snapshot.csv", "manifest.json"}
MONITORING_COLUMNS = (
    "forecast_id", "issued_at", "origin", "scope", "portfolio_sha256",
    "scenario", "symbol", "isin", "forecast_month", "horizon",
    "q10", "q50", "q90", "actual", "signed_error",
    "absolute_error", "squared_error", "interval_hit",
)
_METRIC_COLUMNS = (
    "scope", "scenario", "symbol", "isin", "portfolio_sha256", "horizon",
    "count", "bias", "mae", "rmse", "interval_80_coverage",
)
_MONITORING_FILES = {"forecast_vs_actual.csv", "pending_forecasts.csv", "live_metrics.csv", "manifest.json"}


def _component_from_payload(value: object, label: str) -> DirectComponent:
    fields = {"id", "name", "ticker", "isin", "exchange", "currency", "weight"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"{label} component schema is invalid")
    strings = tuple(value[field] for field in ("id", "name", "ticker", "isin", "exchange", "currency"))
    weight = value["weight"]
    if (
        not all(isinstance(item, str) and item for item in strings)
        or strings[-1] != "EUR"
        or type(weight) not in (int, float)
        or not math.isfinite(weight)
        or weight <= 0
    ):
        raise ValueError(f"{label} component identity is invalid")
    return DirectComponent(*strings, weight)


def _portfolio_from_payload(value: object, key: str) -> DirectPortfolio:
    label = "Portafoglio da studiare" if key == "candidate" else "Portafoglio base"
    if not isinstance(value, dict) or set(value) != {"label", "components"} or value.get("label") != label:
        raise ValueError(f"direct forecast {key} portfolio schema is invalid")
    raw = value.get("components")
    if not isinstance(raw, list):
        raise ValueError(f"direct forecast {key} portfolio components are invalid")
    portfolio = DirectPortfolio(label, tuple(_component_from_payload(item, key) for item in raw))
    _validate_components(portfolio.components)
    return portfolio


def _validated_target_series(
    value: object,
    candidate: DirectPortfolio,
    base: DirectPortfolio,
) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError("direct forecast target-series schema is invalid")
    expected_components = []
    seen = set()
    for component in (*candidate.components, *base.components):
        key = (component.ticker, component.isin)
        if key not in seen:
            seen.add(key)
            expected_components.append(component)
    fields = {
        "id", "name", "ticker", "isin", "exchange", "currency", "weight", "history_count",
        "history_status", "series_sha256", "trailing_volatility_12m",
    }
    result = []
    for raw, expected in zip(value, expected_components):
        if not isinstance(raw, dict) or set(raw) != fields:
            raise ValueError("direct forecast target-series schema is invalid")
        component = _component_from_payload({name: raw[name] for name in fields if name in {
            "id", "name", "ticker", "isin", "exchange", "currency", "weight",
        }}, "target-series")
        count, status, series_hash, volatility = (
            raw["history_count"], raw["history_status"], raw["series_sha256"], raw["trailing_volatility_12m"],
        )
        expected_status = "STORICO_BREVE" if type(count) is int and 12 <= count < 60 else "SUFFICIENT_HISTORY"
        if (
            component != expected
            or type(count) is not int
            or count < 12
            or status != expected_status
            or not _is_sha256(series_hash)
            or type(volatility) not in (int, float)
            or not math.isfinite(volatility)
            or volatility < 0
        ):
            raise ValueError("direct forecast target-series identity is invalid")
        result.append(raw)
    if len(result) != len(value) or len(result) != len(expected_components):
        raise ValueError("direct forecast target-series count is invalid")
    return result


def read_direct_forecast(
    path: Path,
    output_root: Path,
    *,
    include_manifest_hash: bool = False,
) -> tuple[dict[str, Any], tuple[dict[str, str], ...]] | tuple[dict[str, Any], tuple[dict[str, str], ...], str]:
    output_root = output_root.resolve()
    forecast_root = (output_root / "forecasts").resolve()
    resolved = path.resolve()
    if not _is_sha256(path.name) or not path.is_dir() or resolved.parent != forecast_root or resolved.name != path.name:
        raise ValueError("direct forecast path escapes forecast root")
    children = {item.name: item for item in resolved.iterdir()}
    if set(children) != _DIRECT_FILES or any(not item.is_file() or item.resolve().parent != resolved for item in children.values()):
        raise ValueError("direct forecast archive has unexpected files")
    try:
        payloads = {name: children[name].read_bytes() for name in _DIRECT_FILES}
        manifest = json.loads(payloads["manifest.json"].decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("direct forecast manifest is malformed") from error
    required = {
        "schema_version", "forecast_id", "issued_at", "common_origin", "model", "config_sha256",
        "portfolios", "candidate_sha256", "base_sha256", "target_series", "price_vintage",
        "macro_vintage_id", "scenarios", "warnings", "labels", "generated_sha256",
    }
    if not isinstance(manifest, dict) or set(manifest) != required or manifest.get("schema_version") != "DIRECT_CHRONOS_FORECAST_V1":
        raise ValueError("direct forecast manifest schema is invalid")
    generated = manifest.get("generated_sha256")
    if not isinstance(generated, dict) or set(generated) != _DIRECT_FILES - {"manifest.json"}:
        raise ValueError("direct forecast generated hashes are invalid")
    if any(not _is_sha256(digest) or hashlib.sha256(payloads[name]).hexdigest() != digest for name, digest in generated.items()):
        raise ValueError("direct forecast generated hash mismatch")
    model = manifest.get("model")
    if (
        not isinstance(model, dict)
        or set(model) != {"id", "revision", "package", "package_version", "device"}
        or (model.get("id"), model.get("revision"), model.get("package"), model.get("device")) != (
            "amazon/chronos-2", "29ec3766d36d6f73f0696f85560a422f50e8498c", "chronos-forecasting", "cpu",
        )
        or not isinstance(model.get("package_version"), str)
        or not model["package_version"]
    ):
        raise ValueError("direct forecast model identity is invalid")
    if manifest.get("labels") != {
        "forecast": "PROSPECTIVE_SCENARIO_FORECAST",
        "target_history": "RETROSPECTIVE_INPUT_ONLY",
        "portfolio_paths": "CENTRAL_Q50_ONLY",
        "usage": "RESEARCH_ONLY",
    }:
        raise ValueError("direct forecast labels are invalid")
    if not _is_sha256(manifest.get("config_sha256")) or not _is_sha256(manifest.get("macro_vintage_id")):
        raise ValueError("direct forecast configuration or macro identity is invalid")
    price_vintage = manifest.get("price_vintage")
    if (
        not isinstance(price_vintage, dict)
        or set(price_vintage) != {"vintage_id", "manifest_sha256"}
        or not all(_is_sha256(price_vintage.get(name)) for name in ("vintage_id", "manifest_sha256"))
    ):
        raise ValueError("direct forecast price-vintage identity is invalid")
    try:
        _parse_utc(manifest["issued_at"], "direct forecast issued_at")
        origin = date.fromisoformat(manifest["common_origin"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("direct forecast identity is invalid") from error
    if origin != _month_end(origin):
        raise ValueError("direct forecast common origin is invalid")
    portfolios = manifest.get("portfolios")
    if not isinstance(portfolios, dict) or set(portfolios) != {"candidate", "base"}:
        raise ValueError("direct forecast portfolio schema is invalid")
    candidate = _portfolio_from_payload(portfolios["candidate"], "candidate")
    base = _portfolio_from_payload(portfolios["base"], "base")
    target_series = _validated_target_series(manifest.get("target_series"), candidate, base)
    if (
        hashlib.sha256(canonical_json(portfolios["candidate"])).hexdigest() != manifest.get("candidate_sha256")
        or hashlib.sha256(canonical_json(portfolios["base"])).hexdigest() != manifest.get("base_sha256")
    ):
        raise ValueError("direct forecast portfolio hash mismatch")
    scenarios = manifest.get("scenarios")
    if not isinstance(scenarios, dict) or set(scenarios) != set(SCENARIO_NAMES):
        raise ValueError("direct forecast scenarios are invalid")
    try:
        scenario_arrays = {name: np.asarray(scenarios[name], dtype=float) for name in SCENARIO_NAMES}
        expected_scenarios = ecb_scenarios(
            float(scenario_arrays["ECB_FLAT"][0]), 100, 12,
        )
    except (IndexError, TypeError, ValueError) as error:
        raise ValueError("direct forecast scenarios are invalid") from error
    if any(
        values.shape != (12,) or not np.isfinite(values).all()
        or not np.array_equal(values, expected_scenarios[name])
        for name, values in scenario_arrays.items()
    ):
        raise ValueError("direct forecast scenarios are invalid")
    expected_warnings = [
        {"symbol": item["ticker"], "warning": "STORICO_BREVE"}
        for item in target_series if item["history_status"] == "STORICO_BREVE"
    ]
    if manifest.get("warnings") != expected_warnings:
        raise ValueError("direct forecast warnings are invalid")
    identity = _direct_identity(
        model, manifest["config_sha256"], portfolios, target_series, price_vintage,
        manifest["macro_vintage_id"], manifest["common_origin"], scenarios,
    )
    expected_id = hashlib.sha256(canonical_json(identity)).hexdigest()
    if manifest["forecast_id"] != path.name or expected_id != path.name:
        raise ValueError("direct forecast identity/path mismatch")
    rows = _read_csv(payloads["etf_forecast.csv"], _ETF_COLUMNS, "direct ETF forecast CSV")
    identities = {item["ticker"]: item for item in target_series}
    keys = set()
    forecast_rows = []
    for row in rows:
        try:
            identity_row = identities[row["symbol"]]
            horizon = int(row["horizon"])
            forecast_month = date.fromisoformat(row["forecast_month"])
            quantiles = tuple(float(row[name]) for name in ("q10", "q50", "q90"))
            key = (row["symbol"], row["scenario"], horizon)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("direct ETF forecast CSV is malformed") from error
        if (
            row["scenario"] not in SCENARIO_NAMES or horizon not in range(1, 13)
            or forecast_month != _month_end_after(origin, horizon)
            or (row["isin"], row["exchange"], row["currency"]) != (
                identity_row["isin"], identity_row["exchange"], identity_row["currency"]
            )
            or row["history_count"] != str(identity_row["history_count"])
            or row["history_status"] != identity_row["history_status"]
            or not all(math.isfinite(value) for value in quantiles)
            or not quantiles[0] <= quantiles[1] <= quantiles[2] or key in keys
        ):
            raise ValueError("direct ETF forecast CSV row is invalid or duplicate")
        keys.add(key)
        forecast_rows.append(ForecastRow(
            origin, row["scenario"], row["symbol"], forecast_month, horizon, *quantiles,
        ))
    expected_keys = {
        (item["ticker"], scenario, horizon)
        for item in target_series for scenario in SCENARIO_NAMES for horizon in range(1, 13)
    }
    if keys != expected_keys:
        raise ValueError("direct ETF forecast CSV row count is invalid")
    forecast_rows = tuple(forecast_rows)
    portfolio_rows = _portfolio_rows(candidate, base, forecast_rows)
    if payloads["portfolio_paths.csv"] != _csv_bytes(portfolio_rows, _PORTFOLIO_COLUMNS):
        raise ValueError("direct portfolio paths do not match ETF forecasts")
    sensitivity_rows = _sensitivity_rows(target_series, forecast_rows, portfolio_rows)
    if payloads["scenario_sensitivity.csv"] != _csv_bytes(sensitivity_rows, _SENSITIVITY_COLUMNS):
        raise ValueError("direct sensitivity does not match ETF forecasts")
    volatility_rows = _volatility_rows(target_series, forecast_rows, origin)
    if payloads["volatility_snapshot.csv"] != _csv_bytes(volatility_rows, _VOLATILITY_COLUMNS):
        raise ValueError("direct volatility does not match bound target history and ETF forecasts")
    result = (manifest, rows)
    return (*result, hashlib.sha256(payloads["manifest.json"]).hexdigest()) if include_manifest_hash else result


def _direct_forecast_archives(output_root: Path) -> tuple[tuple[dict[str, Any], tuple[dict[str, str], ...], str], ...]:
    forecast_root = output_root / "forecasts"
    if not forecast_root.exists():
        return ()
    if (
        forecast_root.is_symlink() or not forecast_root.is_dir()
        or forecast_root.resolve().parent != output_root
    ):
        raise ValueError("direct forecast root is not a real direct child of output root")
    archives = []
    for archive in sorted(forecast_root.iterdir(), key=lambda item: item.name):
        if archive.is_symlink() or not archive.is_dir() or archive.resolve().parent != forecast_root.resolve():
            raise ValueError("direct forecast archive is not a real direct child of forecast root")
        manifest, rows, manifest_hash = read_direct_forecast(archive, output_root, include_manifest_hash=True)
        archives.append((manifest, rows, manifest_hash))
    return tuple(archives)


def _current_price_vintage(
    paths: DashboardPaths,
    state: DashboardState,
) -> tuple[dict[str, dict[date, float]], Any, datetime, str, str]:
    from perpetual_engine.dashboard_service import materialize_runtime_config

    runtime = materialize_runtime_config(
        paths.default_config, state, paths.runtime_config, project_root=paths.project_root,
    )
    config, raw, manifest, manifest_bytes = load_current_portfolio_prices(
        runtime, project_root=paths.project_root, include_manifest_bytes=True,
    )
    try:
        vintage_id = manifest["vintage_id"]
        retrieved_at = _parse_utc(manifest["retrieved_at"], "price vintage retrieved_at")
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("current price vintage is malformed") from error
    if not _is_sha256(vintage_id):
        raise ValueError("current price vintage identity is invalid")
    return raw, config, retrieved_at, vintage_id, hashlib.sha256(manifest_bytes).hexdigest()


def _actual_returns(
    archives: tuple[tuple[dict[str, Any], tuple[dict[str, str], ...], str], ...],
    raw: Mapping[str, Mapping[date, float]],
    config: Any,
    retrieved_at: datetime,
) -> dict[str, dict[date, float]]:
    symbols = {
        row["symbol"]
        for _manifest, rows, _manifest_hash in archives
        for row in rows
    }
    actuals = {}
    for symbol in sorted(symbols):
        if symbol not in raw:
            raise ValueError(f"{symbol}: current price vintage is missing archived ETF history")
        months, returns = _monthly_returns(raw[symbol], config.max_staleness_days, retrieved_at.date())
        actuals[symbol] = dict(zip(months, returns))
    return actuals


def _monitoring_row(
    *,
    forecast_id: str,
    issued_at: str,
    origin: str,
    scope: str,
    portfolio_sha256: str,
    scenario: str,
    symbol: str,
    isin: str,
    forecast_month: str,
    horizon: int,
    q10: float | str,
    q50: float,
    q90: float | str,
    actual: float | None,
) -> dict[str, object]:
    if actual is None:
        return {
            "forecast_id": forecast_id, "issued_at": issued_at, "origin": origin, "scope": scope,
            "portfolio_sha256": portfolio_sha256, "scenario": scenario, "symbol": symbol, "isin": isin,
            "forecast_month": forecast_month, "horizon": horizon, "q10": q10, "q50": q50, "q90": q90,
            "actual": "", "signed_error": "", "absolute_error": "", "squared_error": "", "interval_hit": "",
        }
    error = actual - q50
    return {
        "forecast_id": forecast_id, "issued_at": issued_at, "origin": origin, "scope": scope,
        "portfolio_sha256": portfolio_sha256, "scenario": scenario, "symbol": symbol, "isin": isin,
        "forecast_month": forecast_month, "horizon": horizon, "q10": q10, "q50": q50, "q90": q90,
        "actual": actual, "signed_error": error, "absolute_error": abs(error), "squared_error": error * error,
        "interval_hit": "" if scope != "ETF" else str(q10 <= actual <= q90).lower(),
    }


def _reconciled_direct_rows(
    archives: tuple[tuple[dict[str, Any], tuple[dict[str, str], ...], str], ...],
    actuals: Mapping[str, Mapping[date, float]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    realized: list[dict[str, object]] = []
    pending: list[dict[str, object]] = []
    for manifest, rows, _manifest_hash in archives:
        by_key = {(row["symbol"], row["scenario"], int(row["horizon"])): row for row in rows}
        for row in rows:
            month = date.fromisoformat(row["forecast_month"])
            output = _monitoring_row(
                forecast_id=manifest["forecast_id"], issued_at=manifest["issued_at"], origin=manifest["common_origin"],
                scope="ETF", portfolio_sha256="", scenario=row["scenario"], symbol=row["symbol"], isin=row["isin"],
                forecast_month=row["forecast_month"], horizon=int(row["horizon"]), q10=float(row["q10"]),
                q50=float(row["q50"]), q90=float(row["q90"]), actual=actuals[row["symbol"]].get(month),
            )
            (realized if output["actual"] != "" else pending).append(output)
        for scope, key, digest in (
            ("CANDIDATE_PORTFOLIO", "candidate", manifest["candidate_sha256"]),
            ("BASE_PORTFOLIO", "base", manifest["base_sha256"]),
        ):
            portfolio = _portfolio_from_payload(manifest["portfolios"][key], key)
            for scenario in SCENARIO_NAMES:
                for horizon in range(1, 13):
                    selected = [by_key[(component.ticker, scenario, horizon)] for component in portfolio.components]
                    month = date.fromisoformat(selected[0]["forecast_month"])
                    q50 = sum(component.weight * float(row["q50"]) for component, row in zip(portfolio.components, selected))
                    values = [actuals[component.ticker].get(month) for component in portfolio.components]
                    actual = None if any(value is None for value in values) else sum(
                        component.weight * value for component, value in zip(portfolio.components, values)
                    )
                    output = _monitoring_row(
                        forecast_id=manifest["forecast_id"], issued_at=manifest["issued_at"], origin=manifest["common_origin"],
                        scope=scope, portfolio_sha256=digest, scenario=scenario, symbol="", isin="",
                        forecast_month=selected[0]["forecast_month"], horizon=horizon, q10="", q50=q50, q90="", actual=actual,
                    )
                    (realized if output["actual"] != "" else pending).append(output)
    sort_key = lambda row: (row["forecast_id"], row["scope"], row["scenario"], row["symbol"], row["horizon"])
    return sorted(realized, key=sort_key), sorted(pending, key=sort_key)


def _monitoring_metrics(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    groups: dict[tuple[object, ...], list[dict[str, object]]] = {}
    for row in rows:
        key = tuple(row[name] for name in ("scope", "scenario", "symbol", "isin", "portfolio_sha256", "horizon"))
        groups.setdefault(key, []).append(row)
    output = []
    for key, selected in sorted(groups.items(), key=lambda item: tuple(str(value) for value in item[0])):
        errors = np.asarray([row["signed_error"] for row in selected], dtype=float)
        scope = key[0]
        output.append({
            **dict(zip(_METRIC_COLUMNS[:6], key)), "count": len(selected), "bias": float(np.mean(errors)),
            "mae": float(np.mean(np.abs(errors))), "rmse": float(np.sqrt(np.mean(errors * errors))),
            "interval_80_coverage": "" if scope != "ETF" else float(np.mean([
                row["interval_hit"] == "true" for row in selected
            ])),
        })
    return output


def read_direct_monitoring(
    path: Path,
    output_root: Path,
) -> tuple[dict[str, Any], dict[str, tuple[dict[str, str], ...]]]:
    path = Path(path)
    monitoring_root = (Path(output_root).resolve() / "monitoring").resolve()
    resolved = path.resolve()
    if not _is_sha256(path.name) or not path.is_dir() or resolved.parent != monitoring_root or resolved.name != path.name:
        raise ValueError("direct monitoring path escapes monitoring root")
    children = {item.name: item for item in resolved.iterdir()}
    if set(children) != _MONITORING_FILES or any(
        not item.is_file() or item.resolve().parent != resolved for item in children.values()
    ):
        raise ValueError("direct monitoring archive has unexpected files")
    try:
        payloads = {name: children[name].read_bytes() for name in _MONITORING_FILES}
        manifest = json.loads(payloads["manifest.json"].decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("direct monitoring manifest is malformed") from error
    required = {
        "schema_version", "monitoring_id", "label", "price_vintage",
        "included_forecast_manifest_sha256", "forecast_ids", "generated_sha256",
    }
    if (
        not isinstance(manifest, dict)
        or set(manifest) != required
        or manifest.get("schema_version") != "DIRECT_CHRONOS_MONITORING_V1"
        or manifest.get("label") != "PROSPECTIVE_TRACK_RECORD"
        or canonical_json(manifest) != payloads["manifest.json"]
    ):
        raise ValueError("direct monitoring manifest schema is invalid")
    generated = manifest.get("generated_sha256")
    csv_names = _MONITORING_FILES - {"manifest.json"}
    if not isinstance(generated, dict) or set(generated) != csv_names or any(
        not _is_sha256(digest) or hashlib.sha256(payloads[name]).hexdigest() != digest
        for name, digest in generated.items()
    ):
        raise ValueError("direct monitoring generated hash mismatch")
    price_vintage = manifest.get("price_vintage")
    forecast_hashes = manifest.get("included_forecast_manifest_sha256")
    forecast_ids = manifest.get("forecast_ids")
    if (
        not isinstance(price_vintage, dict)
        or set(price_vintage) != {"vintage_id", "manifest_sha256"}
        or not all(_is_sha256(price_vintage.get(name)) for name in ("vintage_id", "manifest_sha256"))
        or not isinstance(forecast_hashes, list)
        or not all(_is_sha256(value) for value in forecast_hashes)
        or not isinstance(forecast_ids, list)
        or len(forecast_ids) != len(forecast_hashes)
        or len(set(forecast_ids)) != len(forecast_ids)
        or not all(_is_sha256(value) for value in forecast_ids)
    ):
        raise ValueError("direct monitoring manifest identity is invalid")
    identity = {
        "price_vintage_manifest_sha256": price_vintage["manifest_sha256"],
        "included_forecast_manifest_sha256": forecast_hashes,
    }
    if manifest.get("monitoring_id") != path.name or hashlib.sha256(canonical_json(identity)).hexdigest() != path.name:
        raise ValueError("direct monitoring identity/path mismatch")
    tables = {
        "forecast_vs_actual.csv": _read_csv(payloads["forecast_vs_actual.csv"], MONITORING_COLUMNS, "direct monitoring actual CSV"),
        "pending_forecasts.csv": _read_csv(payloads["pending_forecasts.csv"], MONITORING_COLUMNS, "direct monitoring pending CSV"),
        "live_metrics.csv": _read_csv(payloads["live_metrics.csv"], _METRIC_COLUMNS, "direct monitoring metrics CSV"),
    }
    if any(row["forecast_id"] not in forecast_ids for name in tuple(tables)[:2] for row in tables[name]):
        raise ValueError("direct monitoring rows reference an unknown forecast")
    return manifest, tables


def reconcile_direct_forecasts(paths: DashboardPaths, state: DashboardState) -> Path:
    output_root = paths.chronos_output_root.resolve()
    project_root = paths.project_root.resolve()
    if output_root == project_root or not output_root.is_relative_to(project_root):
        raise ValueError("direct monitoring output escapes project root")
    with _publication_lock(output_root):
        archives = _direct_forecast_archives(output_root)
        raw, config, retrieved_at, vintage_id, price_hash = _current_price_vintage(paths, state)
        actuals = _actual_returns(archives, raw, config, retrieved_at)
        realized, pending = _reconciled_direct_rows(archives, actuals)
        hashes = [item[2] for item in archives]
        identity = {
            "price_vintage_manifest_sha256": price_hash,
            "included_forecast_manifest_sha256": hashes,
        }
        monitoring_id = hashlib.sha256(canonical_json(identity)).hexdigest()
        csv_files = {
            "forecast_vs_actual.csv": _csv_bytes(realized, MONITORING_COLUMNS),
            "pending_forecasts.csv": _csv_bytes(pending, MONITORING_COLUMNS),
            "live_metrics.csv": _csv_bytes(_monitoring_metrics(realized), _METRIC_COLUMNS),
        }
        manifest = {
            "schema_version": "DIRECT_CHRONOS_MONITORING_V1", "monitoring_id": monitoring_id,
            "label": "PROSPECTIVE_TRACK_RECORD", "price_vintage": {
                "vintage_id": vintage_id, "manifest_sha256": price_hash,
            }, "included_forecast_manifest_sha256": hashes,
            "forecast_ids": [item[0]["forecast_id"] for item in archives],
            "generated_sha256": {name: hashlib.sha256(payload).hexdigest() for name, payload in csv_files.items()},
        }

        def inputs_unchanged() -> None:
            current_archives = _direct_forecast_archives(output_root)
            _raw, _config, _retrieved, _vintage, current_price_hash = _current_price_vintage(paths, state)
            if current_price_hash != price_hash or [item[2] for item in current_archives] != hashes:
                raise ValueError("direct monitoring inputs changed during reconciliation")

        monitoring_root = output_root / "monitoring"
        if monitoring_root.exists() and (
            monitoring_root.is_symlink() or not monitoring_root.is_dir() or monitoring_root.resolve().parent != output_root
        ):
            raise ValueError("direct monitoring root is not a real direct child of output root")
        _atomic_snapshot(
            monitoring_root / monitoring_id, monitoring_root / ".staging",
            {**csv_files, "manifest.json": canonical_json(manifest)}, "direct-monitoring", precommit=inputs_unchanged,
        )
        return (monitoring_root / monitoring_id).resolve()


def publish_direct_forecast(
    paths: DashboardPaths,
    state: DashboardState,
    *,
    predictor: Callable | None = None,
    issued_at: datetime | None = None,
) -> DirectForecastResult:
    config = load_chronos_config(paths.chronos_config)
    _require_v1(config)
    issued_at = _parse_utc(issued_at or datetime.now(timezone.utc), "issued_at")
    snapshot = load_etf_target_snapshot(paths, state)
    covariates, macro_vintage_id = load_covariate_table(config)
    if not _is_sha256(macro_vintage_id):
        raise ValueError("macro vintage ID is invalid")
    try:
        origin_index = covariates.months.index(snapshot.common_origin)
        rate = float(covariates.values[REQUIRED_V1_COVARIATES.index("ECB_DFR"), origin_index])
    except (IndexError, ValueError) as error:
        raise ValueError("shared common_origin is unavailable in macro history") from error
    for series in snapshot.series:
        _aligned_etf_values(series, covariates, snapshot.common_origin)
    scenarios = ecb_scenarios(rate, config.scenario_basis_points, config.prediction_length)
    scenario_payload = {name: values.tolist() for name, values in scenarios.items()}
    candidate_payload = _portfolio_payload(snapshot.candidate)
    base_payload = _portfolio_payload(snapshot.base)
    portfolios = {"candidate": candidate_payload, "base": base_payload}
    candidate_hash = direct_portfolio_sha256(snapshot.candidate)
    base_hash = direct_portfolio_sha256(snapshot.base)
    target_series = [_target_series_payload(series) for series in snapshot.series]
    model = {
        "id": config.model_id,
        "revision": config.model_revision,
        "package": "chronos-forecasting",
        "package_version": _chronos_package_version(),
        "device": config.device,
    }
    price_vintage = {"vintage_id": snapshot.price_vintage_id, "manifest_sha256": snapshot.price_manifest_sha256}
    identity = _direct_identity(
        model, config.config_hash, portfolios, target_series, price_vintage, macro_vintage_id,
        snapshot.common_origin.isoformat(), scenario_payload,
    )
    identifier = hashlib.sha256(canonical_json(identity)).hexdigest()
    output_root = paths.chronos_output_root.resolve()
    if output_root == paths.project_root.resolve() or not output_root.is_relative_to(paths.project_root.resolve()):
        raise ValueError("direct forecast output escapes project root")
    destination = output_root / "forecasts" / identifier
    with _publication_lock(output_root):
        if destination.exists():
            manifest, _rows = read_direct_forecast(destination, output_root)
            if manifest["candidate_sha256"] != candidate_hash or manifest["base_sha256"] != base_hash:
                raise ValueError("direct forecast collision")
            result = DirectForecastResult(identifier, destination.resolve(), snapshot.common_origin, candidate_hash, base_hash)
        else:
            predict = predictor or load_chronos_predictor(config)
            rows = run_direct_scenario_forecasts(config, snapshot, covariates, predict)
            expected_rows = len(snapshot.series) * len(SCENARIO_NAMES) * config.prediction_length
            if len(rows) != expected_rows or any(row.origin != snapshot.common_origin for row in rows):
                raise ValueError("direct ETF forecast rows do not match their bound origin")
            series_by_ticker = {series.component.ticker: series for series in snapshot.series}
            etf_rows = [{
                "scenario": row.scenario,
                "symbol": row.target,
                "isin": series_by_ticker[row.target].component.isin,
                "exchange": series_by_ticker[row.target].component.exchange,
                "currency": series_by_ticker[row.target].component.currency,
                "forecast_month": row.forecast_month.isoformat(),
                "horizon": row.horizon,
                "q10": row.q10,
                "q50": row.q50,
                "q90": row.q90,
                "history_count": len(series_by_ticker[row.target].returns),
                "history_status": series_by_ticker[row.target].history_status,
            } for row in rows]
            portfolio_rows = _portfolio_rows(snapshot.candidate, snapshot.base, rows)
            sensitivity_rows = _sensitivity_rows(target_series, rows, portfolio_rows)
            volatility_rows = _volatility_rows(target_series, rows, snapshot.common_origin)
            csv_files = {
                "etf_forecast.csv": _csv_bytes(etf_rows, _ETF_COLUMNS),
                "portfolio_paths.csv": _csv_bytes(portfolio_rows, _PORTFOLIO_COLUMNS),
                "scenario_sensitivity.csv": _csv_bytes(sensitivity_rows, _SENSITIVITY_COLUMNS),
                "volatility_snapshot.csv": _csv_bytes(volatility_rows, _VOLATILITY_COLUMNS),
            }
            generated = {name: hashlib.sha256(payload).hexdigest() for name, payload in csv_files.items()}
            manifest = {
                "schema_version": "DIRECT_CHRONOS_FORECAST_V1",
                "forecast_id": identifier,
                "issued_at": issued_at.isoformat(),
                "common_origin": snapshot.common_origin.isoformat(),
                "model": model,
                "config_sha256": config.config_hash,
                "portfolios": portfolios,
                "candidate_sha256": candidate_hash,
                "base_sha256": base_hash,
                "target_series": target_series,
                "price_vintage": price_vintage,
                "macro_vintage_id": macro_vintage_id,
                "scenarios": scenario_payload,
                "warnings": [
                    {"symbol": series.component.ticker, "warning": "STORICO_BREVE"}
                    for series in snapshot.series if series.history_status == "STORICO_BREVE"
                ],
                "labels": {
                    "forecast": "PROSPECTIVE_SCENARIO_FORECAST",
                    "target_history": "RETROSPECTIVE_INPUT_ONLY",
                    "portfolio_paths": "CENTRAL_Q50_ONLY",
                    "usage": "RESEARCH_ONLY",
                },
                "generated_sha256": generated,
            }
            files = {**csv_files, "manifest.json": canonical_json(manifest)}

            def inputs_unchanged() -> None:
                current = load_etf_target_snapshot(paths, state)
                current_covariates, current_vintage = load_covariate_table(config)
                try:
                    current_index = current_covariates.months.index(current.common_origin)
                    current_rate = float(current_covariates.values[0, current_index])
                except (IndexError, ValueError) as error:
                    raise ValueError("direct forecast inputs changed during publication") from error
                current_scenarios = {
                    name: values.tolist() for name, values in ecb_scenarios(
                        current_rate, config.scenario_basis_points, config.prediction_length,
                    ).items()
                }
                current_identity = _direct_identity(
                    model,
                    config.config_hash,
                    {"candidate": _portfolio_payload(current.candidate), "base": _portfolio_payload(current.base)},
                    [_target_series_payload(series) for series in current.series],
                    {"vintage_id": current.price_vintage_id, "manifest_sha256": current.price_manifest_sha256},
                    current_vintage,
                    current.common_origin.isoformat(),
                    current_scenarios,
                )
                if hashlib.sha256(canonical_json(current_identity)).hexdigest() != identifier:
                    raise ValueError("direct forecast inputs changed during publication")

            _atomic_snapshot(destination, output_root / ".staging", files, "direct-forecast", precommit=inputs_unchanged)
            read_direct_forecast(destination, output_root)
            result = DirectForecastResult(identifier, destination.resolve(), snapshot.common_origin, candidate_hash, base_hash)
    reconcile_direct_forecasts(paths, state)
    return result


PREDICTION_COLUMNS = (
    "variant", "origin", "scope", "symbol", "isin", "forecast_month", "horizon",
    "actual", "q10", "q50", "q90",
)
EVALUATION_METRIC_COLUMNS = (
    "variant", "scope", "symbol", "isin", "horizon", "origin_count",
    "mae_q50", "mean_pinball_loss", "interval_80_coverage",
)
CONTRIBUTION_COLUMNS = (
    "covariate", "comparison", "scope", "horizon", "loss_metric", "origin_count",
    "loss_without", "loss_with", "improvement", "ci_low", "ci_high", "classification",
)
SIGNIFICANCE_COLUMNS = (
    "as_of_month", "covariate", "comparison", "scope", "horizon", "loss_metric",
    "origin_count", "mean_improvement", "p_value", "status",
)
EVALUATION_VOLATILITY_COLUMNS = (
    "origin", "scope", "symbol", "isin", "horizon", "origin_count",
    "trailing_volatility_12m", "volatility_tercile", "q10", "q50", "q90",
    "interval_width", "actual", "signed_error", "absolute_error", "interval_hit",
)
_EVALUATION_FILES = {
    "predictions.csv", "metrics.csv", "covariate_contribution.csv",
    "volatility_diagnostics.csv", "monthly_significance.csv", "manifest.json",
}
_REQUEST_SCHEMA = "DIRECT_CHRONOS_EVALUATION_REQUEST_V1"
_EVALUATION_SCHEMA = "DIRECT_CHRONOS_EVALUATION_V1"


def _project_relative(project_root: Path, path: Path, label: str) -> str:
    root = project_root.resolve()
    resolved = path.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError(f"{label} escapes project root")
    return resolved.relative_to(root).as_posix()


def _resolved_request_path(project_root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError(f"{label} must be a project-relative path")
    root = project_root.resolve()
    resolved = (root / value).resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ValueError(f"{label} escapes project root")
    return resolved


def _candidate_evaluation_series(snapshot: DirectTargetSnapshot) -> tuple[EtfTargetSeries, ...]:
    by_identity = {(series.component.ticker, series.component.isin): series for series in snapshot.series}
    try:
        result = tuple(by_identity[(item.ticker, item.isin)] for item in snapshot.candidate.components)
    except KeyError as error:
        raise ValueError("candidate ETF series is missing from the bound target snapshot") from error
    if len(result) != 4 or len({item.component.ticker for item in result}) != 4:
        raise ValueError("evaluation requires exactly four unique candidate ETF series")
    return result


def _usable_origins(series: EtfTargetSeries, covariates: MonthlyTable) -> tuple[str, ...]:
    if covariates.names != REQUIRED_V1_COVARIATES or covariates.values.shape != (
        len(REQUIRED_V1_COVARIATES), len(covariates.months)
    ) or not np.isfinite(covariates.values).all():
        raise ValueError("evaluation macro table columns, shape, or values are invalid")
    macro_index = {month: index for index, month in enumerate(covariates.months)}
    if len(macro_index) != len(covariates.months):
        raise ValueError("evaluation macro months are duplicated")
    if any(month not in macro_index for month in series.months):
        raise ValueError(f"{series.component.ticker}: macro history must cover the complete evaluation series")
    indices = tuple(macro_index[month] for month in series.months)
    if indices != tuple(range(indices[0], indices[0] + len(indices))):
        raise ValueError(f"{series.component.ticker}: macro and ETF evaluation histories are not contiguous")
    return tuple(
        series.months[index].isoformat()
        for index in range(35, len(series.returns) - 12)
    )[-36:]


@contextmanager
def _evaluation_publication(output_root: Path):
    """Pin real directory names against Windows rename/delete through publication."""
    if not output_root.is_absolute() or '..' in output_root.parts:
        raise ValueError('evaluation output path must be absolute without traversal')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    directories = (*reversed(output_root.parents), output_root,
                   *(output_root / name for name in ('requests', 'evaluations', '.staging')))
    with ExitStack() as handles:
        for path in directories:
            # Each parent is already pinned: mkdir cannot traverse a concurrently replaced ancestor.
            path.mkdir(exist_ok=True)
            # OPEN_EXISTING, BACKUP_SEMANTICS | OPEN_REPARSE_POINT, share read/write but NOT delete.
            handle = create(str(path), 0x80000000, 3, None, 3, 0x02200000, None)
            if handle == ctypes.c_void_p(-1).value:
                if ctypes.get_last_error() == 5 and path in output_root.parents:
                    # An ancestor denied even metadata access, so this process cannot rename it either.
                    continue
                raise ctypes.WinError(ctypes.get_last_error())
            handles.callback(close, handle)
            if path.is_symlink() or path.resolve() != path or not path.is_dir():
                raise ValueError('evaluation publication directory is not a real non-link path')
        with _publication_lock(output_root):
            yield


def _immutable_request(destination: Path, payload: bytes) -> Path:
    parent = destination.parent.resolve()
    staging_root = (parent.parent / ".staging").resolve()
    try:
        parent.mkdir(parents=True, exist_ok=True)
        staging_root.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ValueError("evaluation request ancestor collision") from error
    if destination.exists():
        if destination.is_symlink() or destination.resolve().parent != parent:
            raise ValueError("evaluation request collision")
        children = tuple(destination.iterdir()) if destination.is_dir() else ()
        if (
            len(children) != 1 or children[0].name != "request.json"
            or children[0].is_symlink() or not children[0].is_file()
            or children[0].resolve().parent != destination.resolve()
            or children[0].read_bytes() != payload
        ):
            raise ValueError("evaluation request collision")
        return children[0].resolve()
    stage = Path(tempfile.mkdtemp(prefix="direct-evaluation-request-", dir=staging_root)).resolve()
    try:
        (stage / "request.json").write_bytes(payload)
        if (stage / "request.json").read_bytes() != payload:
            raise ValueError("evaluation request staged bytes changed")
        stage.replace(destination)
        return (destination / "request.json").resolve()
    except Exception:
        if stage.exists() and stage.is_relative_to(staging_root):
            remove_tree(stage)
        raise


def prepare_direct_evaluation_request(paths: DashboardPaths, state: DashboardState) -> Path:
    project_root = paths.project_root.resolve()
    output_root = paths.chronos_output_root.resolve()
    if output_root == project_root or not output_root.is_relative_to(project_root):
        raise ValueError("direct evaluation output escapes project root")
    config_bytes = paths.chronos_config.read_bytes()
    config = load_chronos_config(paths.chronos_config)
    _require_v1(config)
    snapshot = load_etf_target_snapshot(paths, state)
    covariates, macro_vintage_id = load_covariate_table(config)
    if not _is_sha256(macro_vintage_id):
        raise ValueError("macro vintage ID is invalid")
    series = _candidate_evaluation_series(snapshot)
    origins = {item.component.ticker: _usable_origins(item, covariates) for item in series}
    price_config = load_portfolio_config(paths.runtime_config, project_root=project_root)
    price_manifest = price_config.data_root / "vintages" / snapshot.price_vintage_id / "manifest.json"
    macro_manifest = config.data_root / "vintages" / macro_vintage_id / "manifest.json"
    price_bytes = price_manifest.read_bytes()
    macro_bytes = macro_manifest.read_bytes()
    if hashlib.sha256(price_bytes).hexdigest() != snapshot.price_manifest_sha256:
        raise ValueError("price manifest hash changed while preparing evaluation")
    candidate = _portfolio_payload(snapshot.candidate)
    candidate_hash = direct_portfolio_sha256(snapshot.candidate)
    model = {
        "id": config.model_id, "revision": config.model_revision, "package": "chronos-forecasting",
        "package_version": _chronos_package_version(), "device": config.device,
    }
    identity = {
        "schema_version": _REQUEST_SCHEMA,
        "paths": {
            "config": _project_relative(project_root, paths.chronos_config, "Chronos config"),
            "price_manifest": _project_relative(project_root, price_manifest, "price manifest"),
            "macro_manifest": _project_relative(project_root, macro_manifest, "macro manifest"),
            "output_root": _project_relative(project_root, output_root, "evaluation output"),
        },
        "hashes": {
            "config_file_sha256": hashlib.sha256(config_bytes).hexdigest(),
            "config_sha256": config.config_hash,
            "candidate_sha256": candidate_hash,
            "price_manifest_sha256": snapshot.price_manifest_sha256,
            "macro_manifest_sha256": hashlib.sha256(macro_bytes).hexdigest(),
        },
        "model": model,
        "macro_vintage_id": macro_vintage_id,
        "candidate": candidate,
        "series": [{
            "id": item.component.component_id,
            "name": item.component.name,
            "symbol": item.component.ticker,
            "isin": item.component.isin,
            "exchange": item.component.exchange,
            "currency": item.component.currency,
            "weight": item.component.weight,
            "months": [month.isoformat() for month in item.months],
            "returns": [float(value) for value in item.returns],
            "series_sha256": item.series_sha256,
            "eligible_origins": list(origins[item.component.ticker]),
        } for item in series],
    }
    evaluation_id = hashlib.sha256(canonical_json(identity)).hexdigest()
    payload = canonical_json({**identity, "evaluation_id": evaluation_id})
    destination = output_root / "requests" / evaluation_id
    with _evaluation_publication(output_root):
        if hashlib.sha256(paths.chronos_config.read_bytes()).hexdigest() != identity["hashes"]["config_file_sha256"]:
            raise ValueError("Chronos config hash changed while preparing evaluation")
        if hashlib.sha256(price_manifest.read_bytes()).hexdigest() != identity["hashes"]["price_manifest_sha256"]:
            raise ValueError("price manifest hash changed while preparing evaluation")
        if hashlib.sha256(macro_manifest.read_bytes()).hexdigest() != identity["hashes"]["macro_manifest_sha256"]:
            raise ValueError("macro manifest hash changed while preparing evaluation")
        return _immutable_request(destination, payload)


def _validated_request(
    project_root: Path,
    request_path: Path,
) -> tuple[dict[str, Any], ChronosConfig, DirectPortfolio, tuple[dict[str, Any], ...], MonthlyTable, str]:
    root = project_root.resolve()
    request_path = request_path.resolve()
    expected_requests = (root / "outputs" / "dashboard_chronos_v1" / "requests").resolve()
    if (
        not request_path.is_file() or request_path.name != "request.json"
        or request_path.parent.parent != expected_requests or not _is_sha256(request_path.parent.name)
    ):
        raise ValueError("evaluation request path escapes request root")
    request_bytes = request_path.read_bytes()
    try:
        payload = json.loads(request_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("evaluation request is malformed") from error
    required = {
        "schema_version", "evaluation_id", "paths", "hashes", "model",
        "macro_vintage_id", "candidate", "series",
    }
    if not isinstance(payload, dict) or set(payload) != required or payload.get("schema_version") != _REQUEST_SCHEMA:
        raise ValueError("evaluation request schema is invalid")
    if canonical_json(payload) != request_bytes:
        raise ValueError("evaluation request is not canonical")
    identity = {name: value for name, value in payload.items() if name != "evaluation_id"}
    evaluation_id = hashlib.sha256(canonical_json(identity)).hexdigest()
    if payload.get("evaluation_id") != evaluation_id or request_path.parent.name != evaluation_id:
        raise ValueError("evaluation request identity mismatch")
    paths = payload.get("paths")
    hashes = payload.get("hashes")
    if not isinstance(paths, dict) or set(paths) != {"config", "price_manifest", "macro_manifest", "output_root"}:
        raise ValueError("evaluation request paths are invalid")
    if not isinstance(hashes, dict) or set(hashes) != {
        "config_file_sha256", "config_sha256", "candidate_sha256",
        "price_manifest_sha256", "macro_manifest_sha256",
    } or any(not _is_sha256(value) for value in hashes.values()):
        raise ValueError("evaluation request hashes are invalid")
    resolved = {name: _resolved_request_path(root, value, name) for name, value in paths.items()}
    if resolved["output_root"] != (root / "outputs" / "dashboard_chronos_v1").resolve():
        raise ValueError("evaluation output root is invalid")
    for name, hash_name in (
        ("config", "config_file_sha256"),
        ("price_manifest", "price_manifest_sha256"),
        ("macro_manifest", "macro_manifest_sha256"),
    ):
        try:
            digest = hashlib.sha256(resolved[name].read_bytes()).hexdigest()
        except OSError as error:
            raise ValueError(f"evaluation {name} path is missing") from error
        if digest != hashes[hash_name]:
            raise ValueError(f"evaluation {name} hash mismatch")
    config = load_chronos_config(resolved["config"])
    _require_v1(config)
    if config.config_hash != hashes["config_sha256"]:
        raise ValueError("evaluation config hash mismatch")
    model = payload.get("model")
    expected_model = {
        "id": config.model_id, "revision": config.model_revision, "package": "chronos-forecasting",
        "package_version": _chronos_package_version(), "device": config.device,
    }
    if model != expected_model:
        raise ValueError("evaluation model identity is invalid")
    if (
        payload.get("macro_vintage_id") != resolved["macro_manifest"].parent.name
        or not _is_sha256(payload.get("macro_vintage_id"))
        or resolved["macro_manifest"] != (
            config.data_root / "vintages" / payload["macro_vintage_id"] / "manifest.json"
        ).resolve()
    ):
        raise ValueError("evaluation macro vintage identity is invalid")
    candidate = _portfolio_from_payload(payload.get("candidate"), "candidate")
    if direct_portfolio_sha256(candidate) != hashes["candidate_sha256"]:
        raise ValueError("evaluation candidate hash mismatch")
    raw_series = payload.get("series")
    if not isinstance(raw_series, list) or len(raw_series) != 4:
        raise ValueError("evaluation requires exactly four candidate ETF series")
    series: list[dict[str, Any]] = []
    fields = {
        "id", "name", "symbol", "isin", "exchange", "currency", "weight",
        "months", "returns", "series_sha256", "eligible_origins",
    }
    for raw, component in zip(raw_series, candidate.components):
        if not isinstance(raw, dict) or set(raw) != fields:
            raise ValueError("evaluation ETF series schema is invalid")
        if tuple(raw[name] for name in ("id", "name", "symbol", "isin", "exchange", "currency", "weight")) != (
            component.component_id, component.name, component.ticker, component.isin,
            component.exchange, component.currency, component.weight,
        ):
            raise ValueError("evaluation ETF series identity is invalid")
        if (
            not isinstance(raw["months"], list) or not all(isinstance(value, str) for value in raw["months"])
            or not isinstance(raw["returns"], list)
            or not all(type(value) in (int, float) and math.isfinite(value) for value in raw["returns"])
            or len(raw["months"]) != len(raw["returns"])
            or not isinstance(raw["eligible_origins"], list)
            or not all(isinstance(value, str) for value in raw["eligible_origins"])
        ):
            raise ValueError("evaluation ETF series values are invalid")
        try:
            parsed_months = tuple(date.fromisoformat(value) for value in raw["months"])
            returns = np.asarray(raw["returns"], dtype=float)
        except (TypeError, ValueError) as error:
            raise ValueError("evaluation ETF series values are invalid") from error
        if (
            len(parsed_months) < 12 or returns.shape != (len(parsed_months),) or not np.isfinite(returns).all()
            or any(month != _month_end(month) for month in parsed_months)
            or any(current != _next_month_end(previous) for previous, current in zip(parsed_months, parsed_months[1:]))
            or not _is_sha256(raw["series_sha256"])
            or hashlib.sha256(_series_csv(parsed_months, returns)).hexdigest() != raw["series_sha256"]
        ):
            raise ValueError("evaluation ETF series values or hash are invalid")
        series.append({**raw, "parsed_months": parsed_months, "array": returns})
    macro = load_covariate_vintage(config, payload["macro_vintage_id"])
    for item in series:
        target = EtfTargetSeries(
            DirectComponent(item["id"], item["name"], item["symbol"], item["isin"], item["exchange"], item["currency"], item["weight"]),
            item["parsed_months"], item["array"],
            "STORICO_BREVE" if len(item["array"]) < 60 else "SUFFICIENT_HISTORY", item["series_sha256"],
        )
        if list(_usable_origins(target, macro)) != item["eligible_origins"]:
            raise ValueError(f"{item['symbol']}: evaluation eligible origins changed")
    return payload, config, candidate, tuple(series), macro, hashlib.sha256(request_bytes).hexdigest()


def _scope_origins(series: tuple[dict[str, Any], ...]) -> tuple[dict[str, tuple[str, ...]], dict[str, bool]]:
    origins = {item["symbol"]: tuple(item["eligible_origins"]) for item in series}
    eligible = {
        item["symbol"]: len(item["returns"]) >= 60 and len(item["eligible_origins"]) >= 12
        for item in series
    }
    shared = set(origins[series[0]["symbol"]])
    for item in series[1:]:
        shared.intersection_update(origins[item["symbol"]])
    origins["CANDIDATE_PORTFOLIO"] = tuple(sorted(shared))[-36:]
    eligible["CANDIDATE_PORTFOLIO"] = all(eligible[item["symbol"]] for item in series) and len(origins["CANDIDATE_PORTFOLIO"]) >= 12
    return origins, eligible


def _direct_prediction_chunk(predictor: Callable, items: list[dict[str, object]], config: ChronosConfig) -> list[np.ndarray]:
    raw = predictor(items, prediction_length=12, quantile_levels=list(config.quantiles))
    results = raw[0] if isinstance(raw, tuple) and len(raw) == 2 else raw
    if not isinstance(results, (list, tuple)) or len(results) != len(items):
        raise ValueError("predictor must return one result per direct evaluation item")
    arrays = [np.asarray(result, dtype=float) for result in results]
    if any(
        array.shape != (1, 12, 3) or not np.isfinite(array).all()
        or np.any(array[:, :, 0] > array[:, :, 1]) or np.any(array[:, :, 1] > array[:, :, 2])
        for array in arrays
    ):
        raise ValueError("direct evaluation result must be finite ordered (1, 12, 3) quantiles")
    return arrays


def _mean_pinball(rows: list[dict[str, object]]) -> float:
    actual = np.asarray([row["actual"] for row in rows], dtype=float)
    return float(np.mean([
        pinball_loss(actual, np.asarray([row[name] for row in rows], dtype=float), quantile)
        for name, quantile in (("q10", 0.1), ("q50", 0.5), ("q90", 0.9))
    ]))


def _selected_horizon(rows: list[dict[str, object]], horizon: int | str) -> list[dict[str, object]]:
    return rows if horizon == "ALL" else [row for row in rows if row["horizon"] == horizon]


def _evaluation_derived(
    predictions: list[dict[str, object]],
    series: tuple[dict[str, Any], ...],
    candidate: DirectPortfolio,
    config: ChronosConfig,
) -> tuple[
    list[dict[str, object]], list[dict[str, object]], list[dict[str, object]],
    list[dict[str, object]], dict[str, dict[str, object]],
]:
    origins, eligible = _scope_origins(series)
    variants = tuple(name for name, _enabled in evaluation_variants(REQUIRED_V1_COVARIATES))
    all_variants = (*variants, "ZERO_RETURN_BASELINE")
    by_scope_variant = {
        (scope, variant): [row for row in predictions if row["scope"] == scope and row["variant"] == variant]
        for scope in origins for variant in all_variants
    }
    component_index = {item["symbol"]: item for item in series}
    prediction_index = {
        (row["scope"], row["variant"], row["origin"], row["horizon"]): row
        for row in predictions
    }
    portfolio_rows: dict[str, list[dict[str, object]]] = {variant: [] for variant in all_variants}
    if eligible["CANDIDATE_PORTFOLIO"]:
        for variant in all_variants:
            for origin in origins["CANDIDATE_PORTFOLIO"]:
                for horizon in range(1, 13):
                    selected = [prediction_index[(item.ticker, variant, origin, horizon)] for item in candidate.components]
                    portfolio_rows[variant].append({
                        "origin": origin,
                        "horizon": horizon,
                        "actual": sum(item.weight * float(row["actual"]) for item, row in zip(candidate.components, selected)),
                        "q50": sum(item.weight * float(row["q50"]) for item, row in zip(candidate.components, selected)),
                    })
    scopes: tuple[int | str, ...] = (*config.reported_horizons, "ALL")
    metrics: list[dict[str, object]] = []
    for scope in origins:
        if not eligible[scope]:
            continue
        identity = component_index.get(scope)
        for variant in all_variants:
            rows = portfolio_rows[variant] if scope == "CANDIDATE_PORTFOLIO" else by_scope_variant[(scope, variant)]
            for horizon in scopes:
                selected = _selected_horizon(rows, horizon)
                actual = np.asarray([row["actual"] for row in selected], dtype=float)
                q50 = np.asarray([row["q50"] for row in selected], dtype=float)
                metrics.append({
                    "variant": variant, "scope": scope,
                    "symbol": "" if identity is None else identity["symbol"],
                    "isin": "" if identity is None else identity["isin"],
                    "horizon": horizon, "origin_count": len({row["origin"] for row in selected}),
                    "mae_q50": float(np.mean(np.abs(actual - q50))),
                    "mean_pinball_loss": "" if identity is None else _mean_pinball(selected),
                    "interval_80_coverage": "" if identity is None else float(np.mean([
                        row["q10"] <= row["actual"] <= row["q90"] for row in selected
                    ])),
                })
    contributions: list[dict[str, object]] = []
    significance: list[dict[str, object]] = []
    for covariate in REQUIRED_V1_COVARIATES:
        for comparison, without_variant, with_variant in (
            ("STANDALONE", "TARGET_ONLY", f"TARGET_PLUS_{covariate}"),
            ("CONDITIONAL", f"FULL_MINUS_{covariate}", "FULL"),
        ):
            for scope in origins:
                loss_metric = "MAE_Q50" if scope == "CANDIDATE_PORTFOLIO" else "PINBALL"
                for horizon in scopes:
                    base = {
                        "covariate": covariate, "comparison": comparison, "scope": scope,
                        "horizon": horizon, "loss_metric": loss_metric, "origin_count": len(origins[scope]),
                    }
                    if not eligible[scope]:
                        contributions.append({
                            **base, "loss_without": "", "loss_with": "", "improvement": "",
                            "ci_low": "", "ci_high": "", "classification": "INSUFFICIENT_HISTORY",
                        })
                        continue
                    without = portfolio_rows[without_variant] if scope == "CANDIDATE_PORTFOLIO" else by_scope_variant[(scope, without_variant)]
                    with_rows = portfolio_rows[with_variant] if scope == "CANDIDATE_PORTFOLIO" else by_scope_variant[(scope, with_variant)]
                    without = _selected_horizon(without, horizon)
                    with_rows = _selected_horizon(with_rows, horizon)
                    differences = []
                    for origin in origins[scope]:
                        left = [row for row in without if row["origin"] == origin]
                        right = [row for row in with_rows if row["origin"] == origin]
                        if scope == "CANDIDATE_PORTFOLIO":
                            loss_left = float(np.mean([abs(row["actual"] - row["q50"]) for row in left]))
                            loss_right = float(np.mean([abs(row["actual"] - row["q50"]) for row in right]))
                        else:
                            loss_left, loss_right = _mean_pinball(left), _mean_pinball(right)
                        differences.append(loss_left - loss_right)
                    if comparison == "CONDITIONAL" and scope == "CANDIDATE_PORTFOLIO":
                        for count, as_of in enumerate(origins[scope], start=1):
                            prefix = np.asarray(differences[:count], dtype=float)
                            enough = count >= config.bootstrap_block_months
                            p_value = moving_block_p_value(prefix, config) if enough else ""
                            mean_improvement = float(prefix.mean())
                            significance.append({
                                "as_of_month": as_of, **base, "origin_count": count,
                                "mean_improvement": mean_improvement, "p_value": p_value,
                                "status": (
                                    "INSUFFICIENT_HISTORY" if not enough else
                                    "SIGNIFICANT_IMPROVEMENT" if mean_improvement > 0 and p_value < 1.0 - config.bootstrap_confidence else
                                    "NO_SIGNIFICANT_IMPROVEMENT"
                                ),
                            })
                    low, high = moving_block_interval(np.asarray(differences), config)
                    if scope == "CANDIDATE_PORTFOLIO":
                        loss_without = float(np.mean([abs(row["actual"] - row["q50"]) for row in without]))
                        loss_with = float(np.mean([abs(row["actual"] - row["q50"]) for row in with_rows]))
                    else:
                        loss_without, loss_with = _mean_pinball(without), _mean_pinball(with_rows)
                    classification = "NOT_CLASSIFIED"
                    if comparison == "CONDITIONAL":
                        classification = "USEFUL" if low > 0 else "HARMFUL" if high < 0 else "INCONCLUSIVE"
                    contributions.append({
                        **base, "loss_without": loss_without, "loss_with": loss_with,
                        "improvement": loss_without - loss_with, "ci_low": low, "ci_high": high,
                        "classification": classification,
                    })
    volatility: list[dict[str, object]] = []
    for item in series:
        scope = item["symbol"]
        if not eligible[scope]:
            continue
        month_index = {month.isoformat(): index for index, month in enumerate(item["parsed_months"])}
        values = {
            origin: float(np.std(item["array"][month_index[origin] - 11:month_index[origin] + 1], ddof=1) * math.sqrt(12))
            for origin in origins[scope]
        }
        low_threshold, high_threshold = np.quantile(tuple(values.values()), (1 / 3, 2 / 3), method="linear")
        for row in by_scope_variant[(scope, "FULL")]:
            realized_volatility = values[str(row["origin"])]
            error = float(row["actual"] - row["q50"])
            volatility.append({
                "origin": row["origin"], "scope": scope, "symbol": item["symbol"], "isin": item["isin"],
                "horizon": row["horizon"], "origin_count": len(origins[scope]),
                "trailing_volatility_12m": realized_volatility,
                "volatility_tercile": "LOW" if realized_volatility <= low_threshold else "MIDDLE" if realized_volatility <= high_threshold else "HIGH",
                "q10": row["q10"], "q50": row["q50"], "q90": row["q90"],
                "interval_width": float(row["q90"] - row["q10"]), "actual": row["actual"],
                "signed_error": error, "absolute_error": abs(error),
                "interval_hit": "true" if row["q10"] <= row["actual"] <= row["q90"] else "false",
            })
    origin_manifest = {
        scope: {
            "count": len(values), "first": values[0] if values else None, "last": values[-1] if values else None,
            "eligible": eligible[scope],
        }
        for scope, values in origins.items()
    }
    return metrics, contributions, volatility, significance, origin_manifest


def _validated_prediction_rows(
    content: bytes,
    series: tuple[dict[str, Any], ...],
) -> list[dict[str, object]]:
    raw_rows = _read_csv(content, PREDICTION_COLUMNS, "direct evaluation predictions")
    origins, eligible = _scope_origins(series)
    variants = (*tuple(name for name, _enabled in evaluation_variants(REQUIRED_V1_COVARIATES)), "ZERO_RETURN_BASELINE")
    identities = {item["symbol"]: item for item in series}
    rows: list[dict[str, object]] = []
    keys = set()
    for raw in raw_rows:
        try:
            item = identities[raw["scope"]]
            origin = date.fromisoformat(raw["origin"])
            forecast_month = date.fromisoformat(raw["forecast_month"])
            horizon = int(raw["horizon"])
            values = tuple(float(raw[name]) for name in ("actual", "q10", "q50", "q90"))
            position = item["parsed_months"].index(origin)
            key = (raw["variant"], raw["scope"], raw["origin"], horizon)
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("direct evaluation prediction row is malformed") from error
        if (
            not eligible[raw["scope"]] or raw["variant"] not in variants or raw["origin"] not in origins[raw["scope"]]
            or (raw["symbol"], raw["isin"]) != (item["symbol"], item["isin"])
            or horizon not in range(1, 13) or forecast_month != item["parsed_months"][position + horizon]
            or not all(math.isfinite(value) for value in values) or not values[1] <= values[2] <= values[3]
            or values[0] != float(item["array"][position + horizon]) or key in keys
            or (raw["variant"] == "ZERO_RETURN_BASELINE" and values[1:] != (0.0, 0.0, 0.0))
        ):
            raise ValueError("direct evaluation prediction row is invalid or duplicate")
        keys.add(key)
        rows.append({
            **raw, "origin": raw["origin"], "horizon": horizon, "actual": values[0],
            "q10": values[1], "q50": values[2], "q90": values[3],
        })
    expected = {
        (variant, item["symbol"], origin, horizon)
        for item in series if eligible[item["symbol"]]
        for variant in variants for origin in origins[item["symbol"]] for horizon in range(1, 13)
    }
    if keys != expected:
        raise ValueError("direct evaluation prediction rows are incomplete")
    return rows


def _read_direct_evaluation(
    path: Path,
    output_root: Path,
    validated: tuple[dict[str, Any], ChronosConfig, DirectPortfolio, tuple[dict[str, Any], ...], MonthlyTable, str] | None = None,
) -> tuple[dict[str, Any], tuple[dict[str, str], ...]]:
    output_root = output_root.resolve()
    path = path.resolve()
    if not _is_sha256(path.name) or path.parent != (output_root / "evaluations").resolve() or not path.is_dir():
        raise ValueError("direct evaluation path escapes evaluation root")
    children = {item.name: item for item in path.iterdir()}
    if set(children) != _EVALUATION_FILES or any(not item.is_file() or item.resolve().parent != path for item in children.values()):
        raise ValueError("direct evaluation archive has unexpected files")
    payloads = {name: children[name].read_bytes() for name in _EVALUATION_FILES}
    try:
        manifest = json.loads(payloads["manifest.json"].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("direct evaluation manifest is malformed") from error
    fields = {
        "schema_version", "evaluation_id", "request_path", "request_sha256", "model",
        "config_sha256", "candidate_sha256", "evaluation_origins", "bootstrap", "labels", "generated_sha256",
    }
    if not isinstance(manifest, dict) or set(manifest) != fields or manifest.get("schema_version") != _EVALUATION_SCHEMA:
        raise ValueError("direct evaluation manifest schema is invalid")
    if canonical_json(manifest) != payloads["manifest.json"]:
        raise ValueError("direct evaluation manifest is not canonical")
    generated = manifest.get("generated_sha256")
    if not isinstance(generated, dict) or set(generated) != _EVALUATION_FILES - {"manifest.json"} or any(
        not _is_sha256(digest) or hashlib.sha256(payloads[name]).hexdigest() != digest
        for name, digest in generated.items()
    ):
        raise ValueError("direct evaluation generated hash mismatch")
    project_root = output_root.parent.parent.resolve()
    request_path = _resolved_request_path(project_root, manifest.get("request_path"), "evaluation request")
    validated = validated or _validated_request(project_root, request_path)
    request, config, candidate, series, _macro, request_hash = validated
    if (
        manifest.get("evaluation_id") != path.name or request["evaluation_id"] != path.name
        or manifest.get("request_sha256") != request_hash
        or manifest.get("model") != request["model"]
        or manifest.get("config_sha256") != request["hashes"]["config_sha256"]
        or manifest.get("candidate_sha256") != request["hashes"]["candidate_sha256"]
        or manifest.get("bootstrap") != {"block_months": 6, "resamples": 2000, "seed": 42, "confidence": 0.95}
        or manifest.get("labels") != {
            "evaluation": "RETROSPECTIVE_WALK_FORWARD_RESEARCH",
            "origin_coverage": "SCOPE_SPECIFIC_NOT_LIKE_FOR_LIKE",
            "usage": "RESEARCH_ONLY",
        }
    ):
        raise ValueError("direct evaluation manifest identity is invalid")
    predictions = _validated_prediction_rows(payloads["predictions.csv"], series)
    metrics, contributions, volatility, significance, origin_manifest = _evaluation_derived(predictions, series, candidate, config)
    expected = {
        "metrics.csv": _csv_bytes(metrics, EVALUATION_METRIC_COLUMNS),
        "covariate_contribution.csv": _csv_bytes(contributions, CONTRIBUTION_COLUMNS),
        "volatility_diagnostics.csv": _csv_bytes(volatility, EVALUATION_VOLATILITY_COLUMNS),
        "monthly_significance.csv": _csv_bytes(significance, SIGNIFICANCE_COLUMNS),
    }
    for name, content in expected.items():
        if payloads[name] != content:
            raise ValueError(f"direct evaluation {name.removesuffix('.csv')} does not match predictions")
    if manifest.get("evaluation_origins") != origin_manifest:
        raise ValueError("direct evaluation origin counts are invalid")
    return manifest, tuple({name: str(value) for name, value in row.items()} for row in predictions)


def read_direct_evaluation(path: Path, output_root: Path) -> tuple[dict[str, Any], tuple[dict[str, str], ...]]:
    return _read_direct_evaluation(path, output_root)


def evaluate_direct_request(
    project_root: Path,
    request_path: Path,
    *,
    predictor: Callable | None = None,
    progress: Callable[[float, str], None] | None = None,
) -> Path:
    validated = _validated_request(project_root, request_path)
    request, config, candidate, series, macro, request_hash = validated
    output_root = _resolved_request_path(project_root.resolve(), request["paths"]["output_root"], "evaluation output")
    destination = output_root / "evaluations" / request["evaluation_id"]
    with _publication_lock(output_root):
        if destination.exists():
            _read_direct_evaluation(destination, output_root, validated)
            return destination.resolve()
    origins, eligible = _scope_origins(series)
    variants = evaluation_variants(REQUIRED_V1_COVARIATES)
    work: list[tuple[dict[str, Any], str, int, dict[str, object]]] = []
    macro_index = {month: index for index, month in enumerate(macro.months)}
    for variant, enabled in variants:
        for item in series:
            if not eligible[item["symbol"]]:
                continue
            month_index = {month.isoformat(): index for index, month in enumerate(item["parsed_months"])}
            for origin in origins[item["symbol"]]:
                position = month_index[origin]
                prediction_item: dict[str, object] = {
                    "target": np.asarray(item["array"][:position + 1], dtype=np.float32).reshape(1, -1),
                }
                if enabled:
                    prediction_item["past_covariates"] = {
                        name: np.asarray([
                            macro.values[index, macro_index[month]]
                            for month in item["parsed_months"][:position + 1]
                        ], dtype=np.float32)
                        for index, name in enumerate(REQUIRED_V1_COVARIATES) if name in enabled
                    }
                work.append((item, variant, position, prediction_item))
    predictions: list[dict[str, object]] = []
    if work:
        predict = predictor or load_chronos_predictor(config)
        for start in range(0, len(work), 36):
            chunk = work[start:start + 36]
            arrays = _direct_prediction_chunk(predict, [item[3] for item in chunk], config)
            for (series_item, variant, position, _prediction_item), array in zip(chunk, arrays):
                for horizon in range(1, 13):
                    predictions.append({
                        "variant": variant,
                        "origin": series_item["parsed_months"][position].isoformat(),
                        "scope": series_item["symbol"], "symbol": series_item["symbol"], "isin": series_item["isin"],
                        "forecast_month": series_item["parsed_months"][position + horizon].isoformat(),
                        "horizon": horizon, "actual": float(series_item["array"][position + horizon]),
                        "q10": float(array[0, horizon - 1, 0]), "q50": float(array[0, horizon - 1, 1]),
                        "q90": float(array[0, horizon - 1, 2]),
                    })
            if progress is not None:
                progress(min(start + len(chunk), len(work)) / len(work), "Valutazione variabili Chronos")
    for item in series:
        if not eligible[item["symbol"]]:
            continue
        month_index = {month.isoformat(): index for index, month in enumerate(item["parsed_months"])}
        for origin in origins[item["symbol"]]:
            position = month_index[origin]
            for horizon in range(1, 13):
                predictions.append({
                    "variant": "ZERO_RETURN_BASELINE", "origin": origin,
                    "scope": item["symbol"], "symbol": item["symbol"], "isin": item["isin"],
                    "forecast_month": item["parsed_months"][position + horizon].isoformat(),
                    "horizon": horizon, "actual": float(item["array"][position + horizon]),
                    "q10": 0.0, "q50": 0.0, "q90": 0.0,
                })
    metrics, contributions, volatility, significance, origin_manifest = _evaluation_derived(predictions, series, candidate, config)
    csv_files = {
        "predictions.csv": _csv_bytes(predictions, PREDICTION_COLUMNS),
        "metrics.csv": _csv_bytes(metrics, EVALUATION_METRIC_COLUMNS),
        "covariate_contribution.csv": _csv_bytes(contributions, CONTRIBUTION_COLUMNS),
        "volatility_diagnostics.csv": _csv_bytes(volatility, EVALUATION_VOLATILITY_COLUMNS),
        "monthly_significance.csv": _csv_bytes(significance, SIGNIFICANCE_COLUMNS),
    }
    manifest = {
        "schema_version": _EVALUATION_SCHEMA, "evaluation_id": request["evaluation_id"],
        "request_path": _project_relative(project_root.resolve(), request_path.resolve(), "evaluation request"),
        "request_sha256": request_hash, "model": request["model"],
        "config_sha256": request["hashes"]["config_sha256"],
        "candidate_sha256": request["hashes"]["candidate_sha256"],
        "evaluation_origins": origin_manifest,
        "bootstrap": {"block_months": 6, "resamples": 2000, "seed": 42, "confidence": 0.95},
        "labels": {
            "evaluation": "RETROSPECTIVE_WALK_FORWARD_RESEARCH",
            "origin_coverage": "SCOPE_SPECIFIC_NOT_LIKE_FOR_LIKE",
            "usage": "RESEARCH_ONLY",
        },
        "generated_sha256": {name: hashlib.sha256(content).hexdigest() for name, content in csv_files.items()},
    }
    files = {**csv_files, "manifest.json": canonical_json(manifest)}

    def inputs_unchanged() -> None:
        if hashlib.sha256(request_path.read_bytes()).hexdigest() != request_hash:
            raise ValueError("evaluation request changed during evaluation")
        _validated_request(project_root, request_path)

    with _evaluation_publication(output_root):
        _atomic_snapshot(
            destination, output_root / ".staging", files, "direct-evaluation", precommit=inputs_unchanged,
        )
        _read_direct_evaluation(destination, output_root, validated)
    return destination.resolve()
