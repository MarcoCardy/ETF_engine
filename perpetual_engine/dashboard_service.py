from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from perpetual_engine.io import canonical_json
from perpetual_engine.portfolio_monitor import (
    ComponentSpec,
    HistoricalComparison,
    StudySpec,
    compare_single_etf,
    load_current_portfolio_prices,
    load_portfolio_config,
    portfolio_rows,
    refresh_portfolio_prices,
    valid_isin,
)


_ID_RE = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,31}")
_TICKER_RE = re.compile(r"[A-Z0-9][A-Z0-9.=^-]{0,31}")
_STATE_SCHEMA = "ETF_DASHBOARD_STATE_V1"
_COMPONENT_KEYS = {"id", "ticker", "isin", "quote_currency", "weight"}
_CATALOG_KEYS = {"id", "name", "ticker", "isin", "exchange", "quote_currency", "identity_source_url"}
_ISIN_SHAPE_RE = re.compile(r"[A-Z]{2}[A-Z0-9]{10}")


@dataclass(frozen=True)
class CatalogEtf:
    study_id: str
    name: str
    ticker: str
    isin: str
    exchange: str
    quote_currency: str
    identity_source_url: str


@dataclass(frozen=True)
class EtfCandidate:
    name: str
    ticker: str
    isin: str
    exchange: str
    quote_currency: str
    identity_source_url: str


@dataclass(frozen=True)
class DashboardState:
    components: tuple[ComponentSpec, ...]
    catalog: tuple[CatalogEtf, ...]


@dataclass(frozen=True)
class DashboardPaths:
    project_root: Path
    default_config: Path
    state: Path
    runtime_config: Path
    output_root: Path

    @classmethod
    def from_root(cls, root: Path) -> "DashboardPaths":
        root = root.resolve()
        return cls(
            root,
            root / "config" / "portfolio_p_v1.json",
            root / "data" / "dashboard_v1" / "state.json",
            root / "data" / "dashboard_v1" / "runtime_portfolio.json",
            root / "outputs" / "dashboard_v1" / "comparisons",
        )


@dataclass(frozen=True)
class DashboardDataStatus:
    available: bool
    retrieved_at: datetime | None
    first_month: date | None
    last_month: date | None
    vintage_id: str | None
    pointer_path: Path
    reason: str | None


@dataclass(frozen=True)
class DashboardReport:
    comparison_id: str
    output_dir: Path
    comparison: HistoricalComparison


def search_etfs(
    query: str,
    *,
    search_factory: Callable[..., Any] | None = None,
    ticker_factory: Callable[[str], Any] | None = None,
) -> tuple[EtfCandidate, ...]:
    query = query.strip() if isinstance(query, str) else ""
    if not query:
        raise ValueError("ETF query is required")
    if search_factory is None or ticker_factory is None:
        import yfinance as yf

        search_factory = search_factory or yf.Search
        ticker_factory = ticker_factory or yf.Ticker
    try:
        quotes = getattr(
            search_factory(query, max_results=8, news_count=0, lists_count=0, include_cb=False),
            "quotes",
            (),
        )
    except Exception as error:
        raise ValueError("ETF search failed") from error
    if not isinstance(quotes, (list, tuple)):
        raise ValueError("ETF search returned no verified results")

    isin_query = _ISIN_SHAPE_RE.fullmatch(query) is not None
    seen_tickers: set[str] = set()
    seen_isins: set[str] = set()
    found: list[EtfCandidate] = []
    for raw_quote in quotes:
        if not isinstance(raw_quote, dict) or raw_quote.get("quoteType") != "ETF":
            continue
        ticker = raw_quote.get("symbol")
        if not isinstance(ticker, str) or _TICKER_RE.fullmatch(ticker) is None or ticker in seen_tickers:
            continue
        seen_tickers.add(ticker)
        try:
            instrument = ticker_factory(ticker)
            metadata = instrument.get_history_metadata()
            isin = instrument.get_isin()
        except Exception:
            continue
        if not isinstance(metadata, dict) or metadata.get("currency") != "EUR" or not valid_isin(isin):
            continue
        if isin_query and isin != query:
            continue
        if isin in seen_isins:
            continue
        name = raw_quote.get("longname") or raw_quote.get("shortname")
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            continue
        exchange = metadata.get("exchangeName")
        if not isinstance(exchange, str) or not exchange.strip():
            continue
        exchange = exchange.strip()
        seen_isins.add(isin)
        found.append(
            EtfCandidate(
                name.strip(), ticker, isin, exchange, "EUR",
                f"https://finance.yahoo.com/quote/{quote(ticker, safe='.^=-')}",
            )
        )
    if not found:
        raise ValueError("no verified EUR ETF matches query")
    return tuple(sorted(found, key=lambda item: item.ticker))


def add_catalog_entry(state: DashboardState, candidate: EtfCandidate) -> DashboardState:
    if not isinstance(state, DashboardState) or not isinstance(candidate, EtfCandidate):
        raise ValueError("dashboard catalog identity is invalid")
    study_id = re.sub(r"[^A-Za-z0-9]", "_", candidate.ticker)
    exchange = candidate.exchange.strip() if isinstance(candidate.exchange, str) else candidate.exchange
    catalog = CatalogEtf(
        study_id, candidate.name, candidate.ticker, candidate.isin, exchange,
        candidate.quote_currency, candidate.identity_source_url,
    )
    _validate_catalog_entry(catalog)
    if (
        any(item.study_id == study_id for item in state.catalog)
        or any(item.component_id == study_id for item in state.components)
        or any(item.ticker == candidate.ticker or item.isin == candidate.isin for item in state.catalog)
        or any(item.ticker == candidate.ticker or item.isin == candidate.isin for item in state.components)
    ):
        raise ValueError("dashboard catalog identity is duplicated")
    return DashboardState(state.components, (*state.catalog, catalog))


def remove_catalog_entry(state: DashboardState, study_id: str) -> DashboardState:
    if not isinstance(state, DashboardState) or not isinstance(study_id, str):
        raise ValueError("dashboard catalog identity is unknown")
    match = next((item for item in state.catalog if item.study_id == study_id), None)
    if match is None:
        raise ValueError("dashboard catalog identity is unknown")
    if any(item.ticker == match.ticker or item.isin == match.isin for item in state.components):
        raise ValueError("dashboard catalog ETF is held")
    return DashboardState(state.components, tuple(item for item in state.catalog if item.study_id != study_id))


def _exchange_for_ticker(ticker: str) -> str:
    return {".MI": "Borsa Italiana", ".PA": "Euronext Paris"}.get(ticker[-3:], "")


def _catalog_from_study(study: StudySpec) -> CatalogEtf:
    return CatalogEtf(
        study.study_id,
        study.name,
        study.ticker,
        study.isin,
        _exchange_for_ticker(study.ticker),
        study.quote_currency,
        study.identity_source_url,
    )


def _default_state(default_config_path: Path) -> DashboardState:
    config = load_portfolio_config(default_config_path)
    return DashboardState(config.components, tuple(_catalog_from_study(study) for study in config.studies))


def _state_payload(state: DashboardState) -> dict[str, Any]:
    return {
        "schema_version": _STATE_SCHEMA,
        "components": [
            {
                "id": item.component_id,
                "ticker": item.ticker,
                "isin": item.isin,
                "quote_currency": item.quote_currency,
                "weight": item.weight,
            }
            for item in state.components
        ],
        "catalog": [
            {
                "id": item.study_id,
                "name": item.name,
                "ticker": item.ticker,
                "isin": item.isin,
                "exchange": item.exchange,
                "quote_currency": item.quote_currency,
                "identity_source_url": item.identity_source_url,
            }
            for item in state.catalog
        ],
    }


def _state_bytes(state: DashboardState) -> bytes:
    return canonical_json(_state_payload(state))


def state_sha256(state: DashboardState) -> str:
    return hashlib.sha256(_state_bytes(state)).hexdigest()


def _validate_catalog_entry(item: Any) -> None:
    if (
        not isinstance(item, CatalogEtf)
        or not isinstance(item.study_id, str)
        or _ID_RE.fullmatch(item.study_id) is None
        or not isinstance(item.name, str)
        or not item.name.strip()
        or len(item.name) > 100
        or not isinstance(item.ticker, str)
        or _TICKER_RE.fullmatch(item.ticker) is None
        or not isinstance(item.isin, str)
        or not valid_isin(item.isin)
        or not isinstance(item.exchange, str)
        or not item.exchange.strip()
        or item.quote_currency != "EUR"
        or not isinstance(item.identity_source_url, str)
        or not item.identity_source_url.startswith("https://")
    ):
        raise ValueError("dashboard catalog identity is invalid")


def _validate_state(state: DashboardState, default_config_path: Path) -> None:
    if (
        not isinstance(state, DashboardState)
        or not isinstance(state.components, tuple)
        or not isinstance(state.catalog, tuple)
    ):
        raise ValueError("dashboard state is invalid")
    default = load_portfolio_config(default_config_path)
    defaults = {item.component_id: item for item in default.components}
    for item in state.catalog:
        _validate_catalog_entry(item)
    catalogs = {item.study_id: item for item in state.catalog}
    if len(catalogs) != len(state.catalog):
        raise ValueError("dashboard catalog identity is duplicated")
    catalog_tickers: set[str] = set()
    catalog_isins: set[str] = set()
    for item in state.catalog:
        if item.ticker in catalog_tickers or item.isin in catalog_isins:
            raise ValueError("dashboard catalog identity is duplicated")
        catalog_tickers.add(item.ticker)
        catalog_isins.add(item.isin)

    component_ids: set[str] = set()
    component_tickers: set[str] = set()
    component_isins: set[str] = set()
    for item in state.components:
        if (
            not isinstance(item, ComponentSpec)
            or not isinstance(item.component_id, str)
            or _ID_RE.fullmatch(item.component_id) is None
            or not isinstance(item.ticker, str)
            or _TICKER_RE.fullmatch(item.ticker) is None
            or not isinstance(item.isin, str)
            or not valid_isin(item.isin)
            or item.quote_currency != "EUR"
        ):
            raise ValueError("dashboard holding identity is invalid")
        if (
            item.component_id in component_ids
            or item.ticker in component_tickers
            or item.isin in component_isins
        ):
            raise ValueError("dashboard holding identity is duplicated")
        if type(item.weight) not in (int, float) or not math.isfinite(item.weight) or item.weight <= 0:
            raise ValueError("dashboard holding weight is invalid")
        expected = defaults.get(item.component_id) or catalogs.get(item.component_id)
        if expected is None or (item.ticker, item.isin, item.quote_currency) != (
            expected.ticker,
            expected.isin,
            expected.quote_currency,
        ):
            raise ValueError("dashboard holding identity is unknown or invalid")
        component_ids.add(item.component_id)
        component_tickers.add(item.ticker)
        component_isins.add(item.isin)

    for ticker in component_tickers & catalog_tickers:
        if not any(item.ticker == ticker and item.study_id in component_ids for item in state.catalog):
            raise ValueError("dashboard catalog ticker is duplicated")
    for isin in component_isins & catalog_isins:
        if not any(item.isin == isin and item.study_id in component_ids for item in state.catalog):
            raise ValueError("dashboard catalog ISIN is duplicated")

    total = sum((Decimal(str(item.weight)) for item in state.components), Decimal("0"))
    if abs(total - Decimal("1")) > Decimal("0.0001"):
        raise ValueError("portfolio weights must total 100% within 0.01 percentage points")


def _parse_state(payload: Any) -> DashboardState:
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "components", "catalog"}:
        raise ValueError
    if payload["schema_version"] != _STATE_SCHEMA:
        raise ValueError
    raw_components = payload["components"]
    raw_catalog = payload["catalog"]
    if not isinstance(raw_components, list) or not isinstance(raw_catalog, list):
        raise ValueError
    components: list[ComponentSpec] = []
    for raw in raw_components:
        if not isinstance(raw, dict) or set(raw) != _COMPONENT_KEYS:
            raise ValueError
        components.append(ComponentSpec(raw["id"], raw["ticker"], raw["isin"], raw["quote_currency"], raw["weight"]))
    catalog: list[CatalogEtf] = []
    for raw in raw_catalog:
        if not isinstance(raw, dict) or set(raw) != _CATALOG_KEYS:
            raise ValueError
        catalog.append(
            CatalogEtf(
                raw["id"], raw["name"], raw["ticker"], raw["isin"], raw["exchange"],
                raw["quote_currency"], raw["identity_source_url"],
            )
        )
    return DashboardState(tuple(components), tuple(catalog))


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(handle, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def load_dashboard_state(default_config_path: Path, state_path: Path) -> DashboardState:
    state_path = Path(state_path)
    if not state_path.exists():
        state = _default_state(Path(default_config_path))
        save_dashboard_state(state_path, state, default_config_path=Path(default_config_path))
        return state
    try:
        state = _parse_state(json.loads(state_path.read_text(encoding="utf-8")))
        _validate_state(state, Path(default_config_path))
        return state
    except Exception as error:
        if isinstance(error, ValueError) and str(error) == "portfolio weights must total 100% within 0.01 percentage points":
            raise ValueError("dashboard state is malformed") from error
        raise ValueError("dashboard state is malformed") from error


def save_dashboard_state(
    state_path: Path,
    state: DashboardState,
    *,
    default_config_path: Path,
) -> Path:
    _validate_state(state, Path(default_config_path))
    _atomic_write(Path(state_path), _state_bytes(state))
    return Path(state_path)


def reset_dashboard_state(default_config_path: Path, state_path: Path) -> DashboardState:
    state = _default_state(Path(default_config_path))
    save_dashboard_state(Path(state_path), state, default_config_path=Path(default_config_path))
    return state


def materialize_runtime_config(
    default_config_path: Path,
    state: DashboardState,
    runtime_config_path: Path,
    *,
    project_root: Path,
) -> Path:
    default_config_path = Path(default_config_path)
    runtime_config_path = Path(runtime_config_path)
    project_root = Path(project_root)
    _validate_state(state, default_config_path)
    default_bytes = default_config_path.read_bytes()
    payload = json.loads(default_bytes.decode("utf-8"))
    studies = [
        {
            "id": item.study_id,
            "name": item.name,
            "ticker": item.ticker,
            "isin": item.isin,
            "quote_currency": item.quote_currency,
            "identity_source_url": item.identity_source_url,
        }
        for item in state.catalog
    ]
    output = default_bytes if payload.get("studies") == studies else canonical_json({**payload, "studies": studies})
    runtime_config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        handle, temporary = tempfile.mkstemp(prefix=f".{runtime_config_path.name}.", suffix=".tmp", dir=runtime_config_path.parent)
        with os.fdopen(handle, "wb") as file:
            file.write(output)
            file.flush()
            os.fsync(file.fileno())
        load_portfolio_config(Path(temporary), project_root=project_root)
        os.replace(temporary, runtime_config_path)
        temporary = None
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    return runtime_config_path


def _runtime_config(paths: DashboardPaths, state: DashboardState) -> Path:
    return materialize_runtime_config(
        paths.default_config, state, paths.runtime_config, project_root=paths.project_root,
    )


def _unavailable(pointer_path: Path, reason: str) -> DashboardDataStatus:
    return DashboardDataStatus(False, None, None, None, None, pointer_path, reason)


def current_data_status(paths: DashboardPaths, state: DashboardState) -> DashboardDataStatus:
    runtime = _runtime_config(paths, state)
    config = load_portfolio_config(runtime, project_root=paths.project_root)
    pointer_path = config.data_root / "current_manifest.json"
    if not pointer_path.exists():
        return _unavailable(pointer_path, "Dati non disponibili. Aggiorna i dati.")
    try:
        _config, raw, manifest = load_current_portfolio_prices(runtime, project_root=paths.project_root)
    except ValueError as error:
        if str(error) == "portfolio vintage metadata does not match configuration":
            return _unavailable(pointer_path, "Aggiorna i dati per includere il nuovo catalogo")
        raise
    retrieved_at = datetime.fromisoformat(manifest["retrieved_at"])
    rows = portfolio_rows(
        replace(config, components=state.components),
        {component.component_id: raw[component.ticker] for component in state.components},
        as_of=retrieved_at.date(),
    )
    return DashboardDataStatus(True, retrieved_at, rows[0].month, rows[-1].month, manifest["vintage_id"], pointer_path, None)


def refresh_dashboard_data(
    paths: DashboardPaths,
    state: DashboardState,
    *,
    downloader: Callable | None = None,
    retrieved_at: datetime | None = None,
) -> DashboardDataStatus:
    runtime = _runtime_config(paths, state)
    refresh_portfolio_prices(
        runtime,
        project_root=paths.project_root,
        downloader=downloader,
        retrieved_at=retrieved_at,
    )
    return current_data_status(paths, state)


def _csv_bytes(fieldnames: tuple[str, ...], rows: list[dict[str, Any]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({
            name: "" if row.get(name) is None else format(row[name], ".17g") if isinstance(row.get(name), float) else row[name]
            for name in fieldnames
        })
    return output.getvalue().encode()


def _study(config: Any, state: DashboardState, study_id: str) -> StudySpec:
    component = next((item for item in config.components if item.component_id == study_id), None)
    if component is not None:
        return StudySpec(
            component.component_id,
            component.component_id,
            component.ticker,
            component.isin,
            component.quote_currency,
            f"https://finance.yahoo.com/quote/{quote(component.ticker, safe='.^=-')}",
        )
    catalog = next((item for item in state.catalog if item.study_id == study_id), None)
    if catalog is None:
        raise ValueError(f"dashboard study {study_id!r} is unknown")
    return StudySpec(
        catalog.study_id,
        catalog.name,
        catalog.ticker,
        catalog.isin,
        catalog.quote_currency,
        catalog.identity_source_url,
    )


def _comparison_id(config_hash: str, vintage_id: str, state: DashboardState, study_id: str) -> str:
    return hashlib.sha256(canonical_json({
        "runtime_config_sha256": config_hash,
        "vintage_id": vintage_id,
        "state_sha256": state_sha256(state),
        "study_id": study_id,
    })).hexdigest()


def publish_dashboard_comparison(paths: DashboardPaths, state: DashboardState, study_id: str) -> DashboardReport:
    from perpetual_engine.chronos import _atomic_snapshot

    runtime = _runtime_config(paths, state)
    config, raw, manifest = load_current_portfolio_prices(runtime, project_root=paths.project_root)
    target = _study(config, state, study_id)
    retrieved_at = datetime.fromisoformat(manifest["retrieved_at"])
    comparison = compare_single_etf(
        config,
        raw,
        target,
        reference_components=state.components,
        as_of=retrieved_at.date(),
    )
    monthly = _csv_bytes(
        (
            "month", "etf_return", "portfolio_return", "etf_cumulative_value", "portfolio_cumulative_value",
            "etf_drawdown", "portfolio_drawdown", "etf_trailing_volatility_12m",
            "portfolio_trailing_volatility_12m", "rolling_correlation_12m",
        ),
        [{
            "month": row.month.isoformat(),
            "etf_return": row.etf_return,
            "portfolio_return": row.portfolio_return,
            "etf_cumulative_value": row.etf_cumulative_value,
            "portfolio_cumulative_value": row.portfolio_cumulative_value,
            "etf_drawdown": row.etf_drawdown,
            "portfolio_drawdown": row.portfolio_drawdown,
            "etf_trailing_volatility_12m": row.etf_trailing_volatility_12m,
            "portfolio_trailing_volatility_12m": row.portfolio_trailing_volatility_12m,
            "rolling_correlation_12m": row.rolling_correlation_12m,
        } for row in comparison.rows],
    )
    component_ids = comparison.component_ids
    contributions = _csv_bytes(
        ("month", *component_ids, *(f"{item}_cumulative" for item in component_ids), "portfolio_return"),
        [{
            "month": row.month.isoformat(),
            **dict(zip(component_ids, row.component_contributions)),
            **dict(zip((f"{item}_cumulative" for item in component_ids), row.component_cumulative_contributions)),
            "portfolio_return": row.portfolio_return,
        } for row in comparison.rows],
    )
    summary = comparison.summary
    summary_bytes = _csv_bytes(
        (
            "first_month", "last_month", "count", "etf_cumulative_return", "portfolio_cumulative_return",
            "etf_annualized_return", "portfolio_annualized_return", "etf_annualized_volatility",
            "portfolio_annualized_volatility", "etf_max_drawdown", "portfolio_max_drawdown", "correlation",
            "etf_winning_months", "portfolio_winning_months", "tied_months", "study_id", "name", "ticker", "isin",
            "cumulative_return_difference", "SHORT_LIVE_HISTORY",
        ),
        [{
            "first_month": summary.first_month.isoformat(),
            "last_month": summary.last_month.isoformat(),
            "count": summary.count,
            "etf_cumulative_return": summary.etf_cumulative_return,
            "portfolio_cumulative_return": summary.portfolio_cumulative_return,
            "etf_annualized_return": summary.etf_annualized_return,
            "portfolio_annualized_return": summary.portfolio_annualized_return,
            "etf_annualized_volatility": summary.etf_annualized_volatility,
            "portfolio_annualized_volatility": summary.portfolio_annualized_volatility,
            "etf_max_drawdown": summary.etf_max_drawdown,
            "portfolio_max_drawdown": summary.portfolio_max_drawdown,
            "correlation": summary.correlation,
            "etf_winning_months": summary.etf_winning_months,
            "portfolio_winning_months": summary.portfolio_winning_months,
            "tied_months": summary.tied_months,
            "study_id": target.study_id,
            "name": target.name,
            "ticker": target.ticker,
            "isin": target.isin,
            "cumulative_return_difference": summary.etf_cumulative_return - summary.portfolio_cumulative_return,
            "SHORT_LIVE_HISTORY": summary.count < config.short_history_returns,
        }],
    )
    comparison_id = _comparison_id(config.config_hash, manifest["vintage_id"], state, target.study_id)
    generated = {
        "comparison_monthly.csv": hashlib.sha256(monthly).hexdigest(),
        "comparison_summary.csv": hashlib.sha256(summary_bytes).hexdigest(),
        "component_contributions.csv": hashlib.sha256(contributions).hexdigest(),
    }
    manifest_bytes = canonical_json({
        "schema_version": "DASHBOARD_COMPARISON_V1",
        "comparison_id": comparison_id,
        "runtime_config_sha256": config.config_hash,
        "state_sha256": state_sha256(state),
        "study_id": target.study_id,
        "vintage": {"vintage_id": manifest["vintage_id"], "retrieved_at": manifest["retrieved_at"]},
        "generated_sha256": generated,
    })
    output_dir = paths.output_root / comparison_id
    _atomic_snapshot(
        output_dir,
        paths.output_root / ".staging",
        {
            "comparison_monthly.csv": monthly,
            "comparison_summary.csv": summary_bytes,
            "component_contributions.csv": contributions,
            "manifest.json": manifest_bytes,
        },
        "dashboard-comparison",
    )
    return DashboardReport(comparison_id, output_dir.resolve(), comparison)
