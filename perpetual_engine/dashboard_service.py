from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from perpetual_engine.io import canonical_json
from perpetual_engine.portfolio_monitor import ComponentSpec, StudySpec, load_portfolio_config, valid_isin


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
