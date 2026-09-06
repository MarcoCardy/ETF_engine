from __future__ import annotations

import csv
import hashlib
import io
import json
import math
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
    load_chronos_predictor,
)
from perpetual_engine.chronos_data import (
    ChronosConfig,
    MonthlyTable,
    REQUIRED_V1_COVARIATES,
    load_chronos_config,
    load_covariate_table,
)
from perpetual_engine.io import canonical_json
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


def _portfolio_hash(portfolio: DirectPortfolio) -> str:
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


def read_direct_forecast(path: Path, output_root: Path) -> tuple[dict[str, Any], tuple[dict[str, str], ...]]:
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
    return manifest, rows


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
    candidate_hash = _portfolio_hash(snapshot.candidate)
    base_hash = _portfolio_hash(snapshot.base)
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
            return DirectForecastResult(identifier, destination.resolve(), snapshot.common_origin, candidate_hash, base_hash)

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
        return DirectForecastResult(identifier, destination.resolve(), snapshot.common_origin, candidate_hash, base_hash)
