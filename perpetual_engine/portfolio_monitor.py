from __future__ import annotations

import bisect
import csv
import hashlib
import io
import json
import math
import re
import shutil
import statistics
import tempfile
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping

import numpy as np

from perpetual_engine.io import canonical_json


_COMPONENTS = (
    ("SWDA", "SWDA.MI", "IE00B4L5Y983", "EUR", 0.60),
    ("IWMO", "IWMO.MI", "IE00BP3QZ825", "EUR", 0.15),
    ("IWQU", "IWQU.MI", "IE00BP3QZ601", "EUR", 0.15),
    ("DBMFE", "DBMFE.PA", "LU2951555403", "EUR", 0.10),
)
_PAIRS = ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))
_TARGET_MAPPING = {"WORLD": "SWDA", "MOMENTUM": "IWMO", "QUALITY": "IWQU", "TREND": "DBMFE"}
_PROXY_CAVEAT = "Chronos long historical targets are factor proxies; this report monitors exact investable ETF returns."


@dataclass(frozen=True)
class ComponentSpec:
    component_id: str
    ticker: str
    isin: str
    quote_currency: str
    weight: float


@dataclass(frozen=True)
class StudySpec:
    study_id: str
    name: str
    ticker: str
    isin: str
    quote_currency: str
    identity_source_url: str


@dataclass(frozen=True)
class PortfolioConfig:
    path: Path
    project_root: Path
    portfolio_id: str
    base_currency: str
    rebalance: str
    starting_value: float
    data_root: Path
    max_staleness_days: int
    short_history_returns: int
    listing_currency_rule: str
    prelaunch_rule: str
    investable_target_mapping: Mapping[str, str]
    proxy_caveat: str
    components: tuple[ComponentSpec, ...]
    studies: tuple[StudySpec, ...]
    config_hash: str


@dataclass(frozen=True)
class PortfolioRow:
    month: date
    component_returns: tuple[float, ...]
    portfolio_return: float
    cumulative_value: float
    drawdown: float
    trailing_volatility_12m: float | None


def _month_end(value: date) -> date:
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1) - timedelta(days=1)


def _next_month_end(value: date) -> date:
    return _month_end(date(value.year + (value.month == 12), value.month % 12 + 1, 1))


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _valid_isin(value: str) -> bool:
    expanded = "".join(str(int(character, 36)) if character.isalpha() else character for character in value)
    total = 0
    for index, character in enumerate(reversed(expanded)):
        digit = int(character) * (2 if index % 2 else 1)
        total += digit - 9 if digit > 9 else digit
    return total % 10 == 0


def load_portfolio_config(path: Path) -> PortfolioConfig:
    path = path.resolve()
    project_root = path.parent.parent.resolve()
    try:
        payload = path.read_bytes()
        data = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("portfolio configuration is malformed") from error
    expected_keys = {
        "schema_version", "portfolio_id", "base_currency", "rebalance", "starting_value",
        "data_root", "max_staleness_calendar_days", "short_live_history_returns",
        "listing_currency_rule", "prelaunch_rule", "investable_target_mapping", "proxy_caveat", "components", "studies",
    }
    if not isinstance(data, dict) or set(data) != expected_keys:
        raise ValueError("portfolio configuration schema is invalid")
    if (
        data.get("schema_version") != "PORTFOLIO_CONFIG_V1"
        or data.get("portfolio_id") != "P_WORLD_FACTOR_TREND"
        or data.get("base_currency") != "EUR"
        or data.get("rebalance") != "MONTHLY_TARGET_WEIGHT"
        or type(data.get("starting_value")) not in (int, float)
        or float(data["starting_value"]) != 100.0
        or data.get("max_staleness_calendar_days") != 7
        or data.get("short_live_history_returns") != 36
        or data.get("listing_currency_rule") != "ALL_COMPONENTS_EUR_NO_FX"
        or data.get("prelaunch_rule") != "NO_DBMFE_BACKFILL_OR_SUBSTITUTION"
        or data.get("investable_target_mapping") != _TARGET_MAPPING
        or data.get("proxy_caveat") != _PROXY_CAVEAT
    ):
        raise ValueError("portfolio configuration contract is invalid")
    raw_components = data.get("components")
    if not isinstance(raw_components, list) or len(raw_components) != len(_COMPONENTS):
        raise ValueError("portfolio components are invalid")
    components: list[ComponentSpec] = []
    for raw, expected in zip(raw_components, _COMPONENTS):
        if not isinstance(raw, dict) or set(raw) != {"id", "ticker", "isin", "quote_currency", "weight"}:
            raise ValueError("portfolio component schema is invalid")
        values = (raw.get("id"), raw.get("ticker"), raw.get("isin"), raw.get("quote_currency"), raw.get("weight"))
        if values != expected or type(raw.get("weight")) not in (int, float):
            raise ValueError("portfolio identifiers, order, or weights are invalid")
        components.append(ComponentSpec(*expected))
    if sum(Decimal(str(component.weight)) for component in components) != Decimal("1.00"):
        raise ValueError("portfolio weights must sum exactly to one")
    raw_studies = data.get("studies")
    if not isinstance(raw_studies, list):
        raise ValueError("portfolio studies are invalid")
    studies: list[StudySpec] = []
    identities: set[str] = set()
    tickers: set[str] = set()
    isins: set[str] = set()
    for raw in raw_studies:
        if not isinstance(raw, dict) or set(raw) != {"id", "name", "ticker", "isin", "quote_currency", "identity_source_url"}:
            raise ValueError("portfolio study schema is invalid")
        study_id, name, ticker, isin, currency, identity_source_url = (
            raw.get("id"), raw.get("name"), raw.get("ticker"), raw.get("isin"), raw.get("quote_currency"),
            raw.get("identity_source_url"),
        )
        if (
            not isinstance(study_id, str)
            or re.fullmatch(r"[A-Z0-9][A-Z0-9._-]{0,31}", study_id) is None
            or not isinstance(name, str)
            or not name.strip()
            or len(name) > 100
            or not isinstance(ticker, str)
            or re.fullmatch(r"[A-Z0-9][A-Z0-9.=^-]{0,31}", ticker) is None
            or not isinstance(isin, str)
            or re.fullmatch(r"[A-Z]{2}[A-Z0-9]{10}", isin) is None
            or not _valid_isin(isin)
            or currency != "EUR"
            or not isinstance(identity_source_url, str)
            or not identity_source_url.startswith("https://")
            or study_id in identities
            or ticker in tickers
            or isin in isins
        ):
            raise ValueError("portfolio study identity is invalid or duplicated")
        studies.append(StudySpec(study_id, name.strip(), ticker, isin, currency, identity_source_url))
        identities.add(study_id)
        tickers.add(ticker)
        isins.add(isin)
    if data.get("data_root") != "data/portfolio_p_v1":
        raise ValueError("portfolio data root is invalid")
    data_root = (project_root / data["data_root"]).resolve()
    if not data_root.is_relative_to(project_root):
        raise ValueError("portfolio data root escapes project root")
    return PortfolioConfig(
        path, project_root, "P_WORLD_FACTOR_TREND", "EUR", "MONTHLY_TARGET_WEIGHT", 100.0,
        data_root, 7, 36, "ALL_COMPONENTS_EUR_NO_FX", "NO_DBMFE_BACKFILL_OR_SUBSTITUTION",
        MappingProxyType(dict(_TARGET_MAPPING)), _PROXY_CAVEAT, tuple(components), tuple(studies), _sha256(payload),
    )


def _daily_rows(payload: bytes, ticker: str) -> dict[date, float]:
    if not isinstance(payload, bytes):
        raise ValueError(f"downloader for {ticker} must return bytes")
    try:
        rows = list(csv.reader(io.StringIO(payload.decode("utf-8-sig"), newline="")))
    except UnicodeError as error:
        raise ValueError(f"daily prices for {ticker} are malformed") from error
    if not rows or tuple(rows[0]) != ("date", "adjusted_close"):
        raise ValueError(f"daily prices for {ticker} require date,adjusted_close")
    result: dict[date, float] = {}
    previous: date | None = None
    for row in rows[1:]:
        if len(row) != 2:
            raise ValueError(f"daily prices for {ticker} are malformed")
        try:
            observed, value = date.fromisoformat(row[0]), float(row[1])
        except ValueError as error:
            raise ValueError(f"daily prices for {ticker} are malformed") from error
        if observed in result:
            raise ValueError(f"daily prices for {ticker} contain a duplicate date")
        if previous is not None and observed <= previous:
            raise ValueError(f"daily prices for {ticker} must be strictly ordered")
        if not math.isfinite(value):
            raise ValueError(f"daily prices for {ticker} must be finite")
        if value <= 0:
            raise ValueError(f"daily prices for {ticker} must be positive")
        result[observed] = value
        previous = observed
    if not result:
        raise ValueError(f"daily prices for {ticker} have no usable observations")
    return result


def _daily_bytes(rows: list[tuple[date, float]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(("date", "adjusted_close"))
    for observed, value in rows:
        writer.writerow((observed.isoformat(), format(value, ".17g")))
    return output.getvalue().encode()


def _download_yfinance(ticker: str, *, expected_currency: str, expected_isin: str | None = None) -> bytes:
    import yfinance as yf

    yf.set_tz_cache_location(str(Path(tempfile.gettempdir()) / "perpetual-engine-yfinance"))
    instrument = yf.Ticker(ticker)
    history = instrument.history(period="max", auto_adjust=False, actions=False)
    if history.empty or "Adj Close" not in history:
        raise ValueError(f"yfinance {ticker} has no adjusted-close history")
    currency = instrument.get_history_metadata().get("currency")
    isin = instrument.get_isin() if expected_isin is not None else None
    if currency != expected_currency or (expected_isin is not None and isin not in (expected_isin, None, "-")):
        raise ValueError(f"yfinance identity for {ticker} does not match configured currency/ISIN")
    rows = [(stamp.date(), float(value)) for stamp, value in history["Adj Close"].items()]
    payload = _daily_bytes(rows)
    _daily_rows(payload, ticker)
    return payload


def _contained_file(root: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError(f"{label} path is invalid")
    resolved_root = root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(resolved_root) or not path.is_file():
        raise ValueError(f"{label} path escapes its root or is missing")
    return path


def _vintage_identity(config_hash: str, retrieved_at: str, sources: list[dict[str, str]]) -> dict[str, Any]:
    return {
        "config_sha256": config_hash,
        "retrieved_at": retrieved_at,
        "sources": [{"id": source["id"], "ticker": source["ticker"], "sha256": source["sha256"]} for source in sources],
    }


def _source_specs(config: PortfolioConfig) -> tuple[tuple[str, str], ...]:
    sources = [(component.component_id, component.ticker) for component in config.components]
    seen = {ticker for _, ticker in sources}
    for study in config.studies:
        if study.ticker not in seen:
            sources.append((f"STUDY_{study.study_id}", study.ticker))
            seen.add(study.ticker)
    return tuple(sources)


def _download_identities(config: PortfolioConfig) -> dict[str, tuple[str, str | None]]:
    identities = {component.ticker: (component.quote_currency, None) for component in config.components}
    component_isins = {component.ticker: component.isin for component in config.components}
    for study in config.studies:
        if study.ticker in identities:
            if component_isins[study.ticker] != study.isin:
                raise ValueError("portfolio study ticker does not match the configured component ISIN")
        else:
            identities[study.ticker] = (study.quote_currency, study.isin)
    return identities


def _vintage_files(root: Path) -> dict[str, bytes]:
    resolved_root = root.resolve()
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if not resolved.is_relative_to(resolved_root):
            raise ValueError("portfolio vintage file path escapes its root")
        files[path.relative_to(root).as_posix()] = resolved.read_bytes()
    return files


def _load_vintage(config: PortfolioConfig, vintage: Path, vintage_id: str, *, allowed_root: Path) -> tuple[dict[str, dict[date, float]], dict[str, Any], bytes]:
    if not _is_sha256(vintage_id):
        raise ValueError("portfolio vintage ID is invalid")
    resolved_allowed = allowed_root.resolve()
    resolved_vintage = vintage.resolve()
    if not resolved_vintage.is_relative_to(resolved_allowed) or resolved_vintage.parent != resolved_allowed:
        raise ValueError("portfolio vintage path escapes its root")
    manifest_path = _contained_file(resolved_vintage, "manifest.json", "portfolio vintage manifest")
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("portfolio vintage manifest is malformed") from error
    if not isinstance(manifest, dict) or set(manifest) != {"schema_version", "vintage_id", "config_sha256", "retrieved_at", "sources"}:
        raise ValueError("portfolio vintage manifest schema is invalid")
    if manifest.get("schema_version") != "PORTFOLIO_PRICE_VINTAGE_V1" or manifest.get("config_sha256") != config.config_hash:
        raise ValueError("portfolio vintage metadata does not match configuration")
    try:
        retrieved = datetime.fromisoformat(manifest["retrieved_at"])
    except (TypeError, ValueError) as error:
        raise ValueError("portfolio vintage retrieval provenance is invalid") from error
    if retrieved.tzinfo is None or retrieved.utcoffset() is None or retrieved.utcoffset() != timedelta(0):
        raise ValueError("portfolio vintage retrieval provenance must be UTC")
    sources = manifest.get("sources")
    expected = _source_specs(config)
    if not isinstance(sources, list) or len(sources) != len(expected):
        raise ValueError("portfolio vintage sources are invalid")
    raw: dict[str, dict[date, float]] = {}
    expected_files = {"manifest.json"}
    seen_paths: set[Path] = set()
    for source, (source_id, ticker) in zip(sources, expected):
        if not isinstance(source, dict) or set(source) != {"id", "ticker", "path", "sha256"}:
            raise ValueError("portfolio vintage source schema is invalid")
        if (source.get("id"), source.get("ticker")) != (source_id, ticker) or not _is_sha256(source.get("sha256")):
            raise ValueError("portfolio vintage source identifier is invalid")
        expected_path = f"raw/{source_id}.csv"
        if source.get("path") != expected_path:
            raise ValueError("portfolio vintage source path is invalid")
        path = _contained_file(resolved_vintage, expected_path, "portfolio price source")
        if path in seen_paths:
            raise ValueError("portfolio vintage contains a duplicate source path")
        seen_paths.add(path)
        payload = path.read_bytes()
        if _sha256(payload) != source["sha256"]:
            raise ValueError("portfolio price source hash does not match")
        raw[ticker] = _daily_rows(payload, ticker)
        expected_files.add(expected_path)
    files = _vintage_files(resolved_vintage)
    if set(files) != expected_files:
        raise ValueError("portfolio vintage contains missing or unexpected files")
    identity = _vintage_identity(config.config_hash, manifest["retrieved_at"], sources)
    computed = _sha256(canonical_json(identity))
    if manifest.get("vintage_id") != vintage_id or computed != vintage_id:
        raise ValueError("portfolio vintage ID does not match immutable inputs")
    return raw, manifest, manifest_bytes


def _replace_pointer(data_root: Path, payload: bytes) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=data_root, prefix=".current-portfolio-", suffix=".tmp", delete=False) as handle:
            handle.write(payload)
            temporary = Path(handle.name)
        temporary.replace(data_root / "current_manifest.json")
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _require_config_unchanged(config: PortfolioConfig) -> None:
    try:
        current = config.path.read_bytes()
    except OSError as error:
        raise ValueError("portfolio configuration changed during publication") from error
    if _sha256(current) != config.config_hash:
        raise ValueError("portfolio configuration changed during publication")


def refresh_portfolio_prices(path: Path, *, downloader: Callable | None = None, retrieved_at: datetime | None = None) -> str:
    config = load_portfolio_config(path)
    retrieved_at = retrieved_at or datetime.now(timezone.utc)
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone-aware")
    retrieved_at = retrieved_at.astimezone(timezone.utc)
    if not config.data_root.resolve().is_relative_to(config.project_root):
        raise ValueError("portfolio data root escapes project root")
    if downloader is None:
        identities = _download_identities(config)

        def download(ticker: str) -> bytes:
            currency, isin = identities[ticker]
            return _download_yfinance(ticker, expected_currency=currency, expected_isin=isin)
    else:
        download = downloader
    config.data_root.mkdir(parents=True, exist_ok=True)
    data_root = config.data_root.resolve()
    staging_path = config.data_root / ".staging"
    staging_path.mkdir(exist_ok=True)
    staging_root = staging_path.resolve()
    if not staging_root.is_relative_to(data_root):
        raise ValueError("portfolio staging path escapes data root")
    stage = Path(tempfile.mkdtemp(prefix="refresh-portfolio-", dir=staging_root)).resolve()
    if stage.parent != staging_root:
        raise ValueError("portfolio staging directory escapes data root")
    published: Path | None = None
    try:
        (stage / "raw").mkdir()
        sources: list[dict[str, str]] = []
        expected = _source_specs(config)
        for source_id, ticker in expected:
            payload = download(ticker)
            _daily_rows(payload, ticker)
            raw_path = stage / "raw" / f"{source_id}.csv"
            raw_path.write_bytes(payload)
            sources.append({"id": source_id, "ticker": ticker, "path": f"raw/{source_id}.csv", "sha256": _sha256(payload)})
        retrieved_text = retrieved_at.isoformat()
        identity = _vintage_identity(config.config_hash, retrieved_text, sources)
        vintage_id = _sha256(canonical_json(identity))
        manifest_bytes = canonical_json({
            "schema_version": "PORTFOLIO_PRICE_VINTAGE_V1",
            "vintage_id": vintage_id,
            "config_sha256": config.config_hash,
            "retrieved_at": retrieved_text,
            "sources": sources,
        })
        (stage / "manifest.json").write_bytes(manifest_bytes)
        _load_vintage(config, stage, vintage_id, allowed_root=staging_root)
        _require_config_unchanged(config)
        vintages_path = config.data_root / "vintages"
        vintages_path.mkdir(exist_ok=True)
        vintages = vintages_path.resolve()
        if not vintages.is_relative_to(data_root):
            raise ValueError("portfolio vintages path escapes data root")
        destination = vintages / vintage_id
        if destination.exists():
            _load_vintage(config, destination, vintage_id, allowed_root=vintages)
            if _vintage_files(destination) != _vintage_files(stage):
                raise ValueError("existing portfolio vintage is not byte-identical")
        else:
            stage.replace(destination)
            published = destination
        pointer = canonical_json({
            "schema_version": "PORTFOLIO_CURRENT_V1",
            "vintage_id": vintage_id,
            "manifest_sha256": _sha256(manifest_bytes),
        })
        try:
            _require_config_unchanged(config)
            _replace_pointer(config.data_root, pointer)
        except OSError as error:
            if published is not None and published.exists():
                shutil.rmtree(published)
            raise ValueError("portfolio current pointer could not be published") from error
        except Exception:
            if published is not None and published.exists():
                shutil.rmtree(published)
            raise
        return vintage_id
    finally:
        if stage.exists() and stage.is_relative_to(staging_root):
            shutil.rmtree(stage)


def _select_price(series: Mapping[date, float], target: date, staleness: int) -> tuple[date, float] | None:
    ordered = sorted(series)
    index = bisect.bisect_right(ordered, target) - 1
    if index < 0 or (target - ordered[index]).days > staleness:
        return None
    observed = ordered[index]
    return observed, float(series[observed])


def portfolio_rows(
    config: PortfolioConfig,
    eur_prices: Mapping[str, Mapping[date, float]],
    *,
    as_of: date | None = None,
) -> tuple[PortfolioRow, ...]:
    names = tuple(component.component_id for component in config.components)
    if not isinstance(eur_prices, Mapping) or set(eur_prices) != set(names):
        raise ValueError("portfolio price identifiers are missing or unexpected")
    validated: dict[str, dict[date, float]] = {}
    for name in names:
        series = eur_prices[name]
        if not isinstance(series, Mapping) or not series:
            raise ValueError(f"{name} prices are missing")
        values: dict[date, float] = {}
        for observed, raw_value in series.items():
            if not isinstance(observed, date) or isinstance(observed, datetime):
                raise ValueError(f"{name} price date is invalid")
            try:
                value = float(raw_value)
            except (TypeError, ValueError) as error:
                raise ValueError(f"{name} price is invalid") from error
            if not math.isfinite(value):
                raise ValueError(f"{name} prices must be finite")
            if value <= 0:
                raise ValueError(f"{name} prices must be positive")
            values[observed] = value
        validated[name] = values
    start = _month_end(min(min(series) for series in validated.values()))
    end = _month_end(max(max(series) for series in validated.values()))
    if as_of is not None:
        end = min(end, date(as_of.year, as_of.month, 1) - timedelta(days=1))
    common: dict[date, tuple[float, ...]] = {}
    current = start
    while current <= end:
        selected = tuple(_select_price(validated[name], current, config.max_staleness_days) for name in names)
        if all(item is not None for item in selected):
            common[current] = tuple(item[1] for item in selected if item is not None)
        current = _next_month_end(current)
    first_return: date | None = None
    for month in sorted(common):
        previous = _month_end(date(month.year, month.month, 1) - timedelta(days=1))
        if previous in common:
            first_return = month
            break
    if first_return is None:
        raise ValueError("portfolio has no common return month")
    last_common = max(common)
    required = _month_end(date(first_return.year, first_return.month, 1) - timedelta(days=1))
    while required <= last_common:
        if required not in common:
            raise ValueError("portfolio common monthly prices are missing after inception")
        required = _next_month_end(required)
    output: list[PortfolioRow] = []
    portfolio_returns: list[float] = []
    cumulative = config.starting_value
    peak = cumulative
    month = first_return
    while month <= last_common:
        previous = _month_end(date(month.year, month.month, 1) - timedelta(days=1))
        component_returns = tuple(current / prior - 1.0 for current, prior in zip(common[month], common[previous]))
        if not all(math.isfinite(value) for value in component_returns):
            raise ValueError("portfolio component return must be finite")
        if any(value <= -1.0 for value in component_returns):
            raise ValueError("portfolio component return cannot be at or below -100%")
        portfolio_return = sum(component.weight * value for component, value in zip(config.components, component_returns))
        portfolio_returns.append(portfolio_return)
        cumulative *= 1.0 + portfolio_return
        peak = max(peak, cumulative)
        volatility = statistics.stdev(portfolio_returns[-12:]) * math.sqrt(12) if len(portfolio_returns) >= 12 else None
        output.append(PortfolioRow(month, component_returns, portfolio_return, cumulative, cumulative / peak - 1.0, volatility))
        month = _next_month_end(month)
    return tuple(output)


def _load_current(config: PortfolioConfig) -> tuple[dict[str, dict[date, float]], dict[str, Any], bytes]:
    data_root = config.data_root.resolve()
    if not data_root.is_relative_to(config.project_root) or not data_root.is_dir():
        raise ValueError("portfolio data root escapes project root or is missing")
    pointer_path = _contained_file(data_root, "current_manifest.json", "portfolio current pointer")
    pointer_bytes = pointer_path.read_bytes()
    try:
        pointer = json.loads(pointer_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("portfolio current pointer is malformed") from error
    if not isinstance(pointer, dict) or set(pointer) != {"schema_version", "vintage_id", "manifest_sha256"} or pointer.get("schema_version") != "PORTFOLIO_CURRENT_V1":
        raise ValueError("portfolio current pointer schema is invalid")
    vintage_id = pointer.get("vintage_id")
    if not _is_sha256(vintage_id) or not _is_sha256(pointer.get("manifest_sha256")):
        raise ValueError("portfolio current pointer identity is invalid")
    vintages = (data_root / "vintages").resolve()
    if not vintages.is_relative_to(data_root) or not vintages.is_dir():
        raise ValueError("portfolio vintages path escapes data root or is missing")
    vintage = (vintages / vintage_id).resolve()
    if not vintage.is_relative_to(vintages) or vintage.parent != vintages:
        raise ValueError("portfolio vintage path escapes data root")
    raw, manifest, manifest_bytes = _load_vintage(config, vintage, vintage_id, allowed_root=vintages)
    if _sha256(manifest_bytes) != pointer["manifest_sha256"]:
        raise ValueError("portfolio vintage manifest hash does not match pointer")
    return raw, manifest, manifest_bytes


def _csv_bytes(fieldnames: tuple[str, ...], rows: list[Mapping[str, Any]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({name: "" if row.get(name) is None else (format(row[name], ".17g") if isinstance(row.get(name), float) else row.get(name)) for name in fieldnames})
    return output.getvalue().encode()


def write_portfolio_report(path: Path, output: Path) -> Path:
    from perpetual_engine.chronos import _atomic_snapshot

    config = load_portfolio_config(path)
    raw, vintage_manifest, vintage_manifest_bytes = _load_current(config)
    rows = portfolio_rows(
        config,
        {component.component_id: raw[component.ticker] for component in config.components},
        as_of=datetime.fromisoformat(vintage_manifest["retrieved_at"]).date(),
    )
    names = tuple(component.component_id for component in config.components)
    monthly = _csv_bytes(
        ("month", *names, "portfolio_return", "cumulative_value", "drawdown", "trailing_volatility_12m"),
        [{
            "month": row.month.isoformat(),
            **dict(zip(names, row.component_returns)),
            "portfolio_return": row.portfolio_return,
            "cumulative_value": row.cumulative_value,
            "drawdown": row.drawdown,
            "trailing_volatility_12m": row.trailing_volatility_12m,
        } for row in rows],
    )
    annualized = statistics.stdev(row.portfolio_return for row in rows) * math.sqrt(12) if len(rows) >= 12 else None
    metrics = _csv_bytes(
        ("first_month", "last_month", "count", "cumulative_return", "annualized_volatility", "max_drawdown"),
        [{
            "first_month": rows[0].month.isoformat(),
            "last_month": rows[-1].month.isoformat(),
            "count": len(rows),
            "cumulative_return": rows[-1].cumulative_value / config.starting_value - 1.0,
            "annualized_volatility": annualized,
            "max_drawdown": min(row.drawdown for row in rows),
        }],
    )
    correlation_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        window = rows[max(0, index - 11): index + 1]
        count = len(window)
        for left, right in _PAIRS:
            correlation: float | None = None
            if count == 12:
                matrix = np.asarray([item.component_returns for item in window], dtype=float)
                value = float(np.corrcoef(matrix[:, left], matrix[:, right])[0, 1])
                correlation = value if math.isfinite(value) else None
            correlation_rows.append({
                "month": row.month.isoformat(),
                "pair": f"{names[left]}-{names[right]}",
                "count": count,
                "rolling_correlation_12m": correlation,
            })
    correlations = _csv_bytes(("month", "pair", "count", "rolling_correlation_12m"), correlation_rows)
    generated = {
        "portfolio_monthly.csv": _sha256(monthly),
        "portfolio_metrics.csv": _sha256(metrics),
        "portfolio_correlations.csv": _sha256(correlations),
    }
    input_hashes = {source["id"]: source["sha256"] for source in vintage_manifest["sources"]}
    manifest = canonical_json({
        "schema_version": "PORTFOLIO_REPORT_V1",
        "portfolio_id": config.portfolio_id,
        "config_sha256": config.config_hash,
        "input_sha256": input_hashes,
        "vintage": {
            "vintage_id": vintage_manifest["vintage_id"],
            "retrieved_at": vintage_manifest["retrieved_at"],
            "manifest_sha256": _sha256(vintage_manifest_bytes),
        },
        "components": [{
            "id": item.component_id, "ticker": item.ticker, "isin": item.isin,
            "quote_currency": item.quote_currency, "weight": item.weight,
        } for item in config.components],
        "investable_target_mapping": dict(config.investable_target_mapping),
        "listing_currency_rule": config.listing_currency_rule,
        "prelaunch_rule": config.prelaunch_rule,
        "proxy_caveat": config.proxy_caveat,
        "first_common_return_month": rows[0].month.isoformat(),
        "SHORT_LIVE_HISTORY": len(rows) < config.short_history_returns,
        "generated_sha256": generated,
    })
    destination = output.resolve()
    files = {
        "portfolio_monthly.csv": monthly,
        "portfolio_metrics.csv": metrics,
        "portfolio_correlations.csv": correlations,
        "manifest.json": manifest,
    }
    return _atomic_snapshot(
        destination,
        destination.parent / ".staging",
        files,
        "portfolio-report",
        precommit=lambda: _require_config_unchanged(config),
    )[0]


def _correlation(left: list[float], right: list[float]) -> float | None:
    if len(left) < 2 or statistics.stdev(left) == 0 or statistics.stdev(right) == 0:
        return None
    value = float(np.corrcoef(left, right)[0, 1])
    return value if math.isfinite(value) else None


def write_etf_study_report(path: Path, study_id: str, output: Path) -> Path:
    from perpetual_engine.chronos import _atomic_snapshot

    config = load_portfolio_config(path)
    study = next((item for item in config.studies if item.study_id == study_id), None)
    if study is None:
        raise ValueError(f"portfolio study {study_id!r} is not configured")
    raw, vintage_manifest, vintage_manifest_bytes = _load_current(config)
    as_of = datetime.fromisoformat(vintage_manifest["retrieved_at"]).date()
    reference = portfolio_rows(
        config,
        {component.component_id: raw[component.ticker] for component in config.components},
        as_of=as_of,
    )
    single_config = replace(
        config,
        components=(ComponentSpec(study.study_id, study.ticker, study.isin, "EUR", 1.0),),
    )
    single = portfolio_rows(single_config, {study.study_id: raw[study.ticker]}, as_of=as_of)
    reference_by_month = {row.month: row.portfolio_return for row in reference}
    single_by_month = {row.month: row.portfolio_return for row in single}
    common_months = sorted(reference_by_month.keys() & single_by_month.keys())
    if not common_months:
        raise ValueError("portfolio study has no common return month")

    etf_returns: list[float] = []
    portfolio_returns: list[float] = []
    etf_value = portfolio_value = config.starting_value
    etf_peak = portfolio_peak = config.starting_value
    monthly_rows: list[dict[str, Any]] = []
    etf_max_drawdown = portfolio_max_drawdown = 0.0
    for month in common_months:
        etf_return = single_by_month[month]
        portfolio_return = reference_by_month[month]
        etf_returns.append(etf_return)
        portfolio_returns.append(portfolio_return)
        etf_value *= 1.0 + etf_return
        portfolio_value *= 1.0 + portfolio_return
        etf_peak = max(etf_peak, etf_value)
        portfolio_peak = max(portfolio_peak, portfolio_value)
        etf_drawdown = etf_value / etf_peak - 1.0
        portfolio_drawdown = portfolio_value / portfolio_peak - 1.0
        etf_max_drawdown = min(etf_max_drawdown, etf_drawdown)
        portfolio_max_drawdown = min(portfolio_max_drawdown, portfolio_drawdown)
        rolling = _correlation(etf_returns[-12:], portfolio_returns[-12:]) if len(etf_returns) >= 12 else None
        monthly_rows.append({
            "month": month.isoformat(),
            "etf_return": etf_return,
            "portfolio_return": portfolio_return,
            "etf_cumulative_value": etf_value,
            "portfolio_cumulative_value": portfolio_value,
            "cumulative_difference": etf_value - portfolio_value,
            "rolling_correlation_12m": rolling,
        })
    monthly = _csv_bytes(
        (
            "month", "etf_return", "portfolio_return", "etf_cumulative_value",
            "portfolio_cumulative_value", "cumulative_difference", "rolling_correlation_12m",
        ),
        monthly_rows,
    )
    count = len(common_months)
    etf_cumulative_return = etf_value / config.starting_value - 1.0
    portfolio_cumulative_return = portfolio_value / config.starting_value - 1.0
    summary = _csv_bytes(
        (
            "study_id", "name", "ticker", "isin", "first_month", "last_month", "count",
            "etf_cumulative_return", "portfolio_cumulative_return", "cumulative_return_difference",
            "etf_annualized_return", "portfolio_annualized_return", "etf_annualized_volatility",
            "portfolio_annualized_volatility", "etf_max_drawdown", "portfolio_max_drawdown", "correlation",
        ),
        [{
            "study_id": study.study_id,
            "name": study.name,
            "ticker": study.ticker,
            "isin": study.isin,
            "first_month": common_months[0].isoformat(),
            "last_month": common_months[-1].isoformat(),
            "count": count,
            "etf_cumulative_return": etf_cumulative_return,
            "portfolio_cumulative_return": portfolio_cumulative_return,
            "cumulative_return_difference": etf_cumulative_return - portfolio_cumulative_return,
            "etf_annualized_return": (1.0 + etf_cumulative_return) ** (12.0 / count) - 1.0,
            "portfolio_annualized_return": (1.0 + portfolio_cumulative_return) ** (12.0 / count) - 1.0,
            "etf_annualized_volatility": statistics.stdev(etf_returns) * math.sqrt(12) if count >= 2 else None,
            "portfolio_annualized_volatility": statistics.stdev(portfolio_returns) * math.sqrt(12) if count >= 2 else None,
            "etf_max_drawdown": etf_max_drawdown,
            "portfolio_max_drawdown": portfolio_max_drawdown,
            "correlation": _correlation(etf_returns, portfolio_returns),
        }],
    )
    generated = {
        "comparison_monthly.csv": _sha256(monthly),
        "comparison_summary.csv": _sha256(summary),
    }
    manifest = canonical_json({
        "schema_version": "ETF_STUDY_REPORT_V1",
        "study": {
            "id": study.study_id,
            "name": study.name,
            "ticker": study.ticker,
            "isin": study.isin,
            "quote_currency": study.quote_currency,
            "identity_source_url": study.identity_source_url,
        },
        "reference_portfolio_id": config.portfolio_id,
        "config_sha256": config.config_hash,
        "vintage": {
            "vintage_id": vintage_manifest["vintage_id"],
            "retrieved_at": vintage_manifest["retrieved_at"],
            "manifest_sha256": _sha256(vintage_manifest_bytes),
        },
        "first_common_return_month": common_months[0].isoformat(),
        "last_common_return_month": common_months[-1].isoformat(),
        "SHORT_LIVE_HISTORY": count < config.short_history_returns,
        "generated_sha256": generated,
    })
    destination = output.resolve()
    return _atomic_snapshot(
        destination,
        destination.parent / ".staging",
        {
            "comparison_monthly.csv": monthly,
            "comparison_summary.csv": summary,
            "manifest.json": manifest,
        },
        "etf-study-report",
        precommit=lambda: _require_config_unchanged(config),
    )[0]
