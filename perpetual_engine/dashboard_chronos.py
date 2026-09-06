from __future__ import annotations

import csv
import hashlib
import io
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from perpetual_engine.dashboard_service import DashboardPaths, DashboardState, _atomic_write, exchange_for_ticker
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
    contiguous = [max(selected)] if selected else []
    while contiguous:
        previous = _month_end(date(contiguous[-1].year, contiguous[-1].month, 1) - timedelta(days=1))
        if previous not in selected:
            break
        contiguous.append(previous)
    contiguous.reverse()
    result_months = contiguous[1:]
    returns = [selected[month] / selected[previous] - 1.0 for previous, month in zip(contiguous, contiguous[1:])]
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
        kept = [(month, value) for month, value in zip(monthly, returns) if month <= common_origin]
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
