from __future__ import annotations

import json
import re
import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping

from perpetual_engine.io import sha256_file


DRO_UNIVERSE = ("EQAC", "IWVL", "EWSA", "DFNS", "WENE", "INFR", "SGLD", "CMOD", "IBCI", "DTEH")
DRO_EXPOSURES: Mapping[str, str] = MappingProxyType(
    {
        "EQAC": "NASDAQ_100",
        "IWVL": "WORLD_VALUE",
        "EWSA": "WORLD_SMALL_CAP",
        "DFNS": "DEFENCE",
        "WENE": "WORLD_ENERGY",
        "INFR": "GLOBAL_INFRASTRUCTURE",
        "SGLD": "GOLD",
        "CMOD": "BROAD_COMMODITIES",
        "IBCI": "EURO_INFLATION_LINKED_GOVERNMENT_BONDS",
        "DTEH": "EUR_HEDGED_LONG_US_TREASURIES",
    }
)
_HASH = re.compile(r"[0-9a-f]{64}")
_FROZEN_V1_DATES = (date(2018, 12, 31), date(2020, 1, 31), date(2026, 7, 31), date(2026, 8, 28))
_NORMALIZED_VINTAGE_FILES = ("monthly_total_return_eur.csv", "monthly_benchmark_total_return_eur.csv", "monthly_cash_return_eur.csv")


def _month_end(value: date) -> bool:
    return value == date(value.year + (value.month == 12), value.month % 12 + 1, 1) - timedelta(days=1)


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} is required")
    return value


def _date(value: object, field: str) -> date:
    try:
        return date.fromisoformat(_text(value, field))
    except ValueError as error:
        raise ValueError(f"{field} must be an ISO date") from error


def parse_raw_level_csv(content: bytes) -> tuple[tuple[date, Decimal], ...]:
    """Parse the frozen two-column raw-data format; providers are normalized before freezing."""
    try:
        reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    except UnicodeDecodeError as error:
        raise ValueError("raw level CSV must be UTF-8") from error
    if reader.fieldnames != ["date", "level"]:
        raise ValueError("raw level CSV must use canonical date,level columns")
    rows: list[tuple[date, Decimal]] = []
    for row in reader:
        try:
            observed, level = date.fromisoformat(row["date"]), Decimal(row["level"])
        except (KeyError, ValueError, TypeError) as error:
            raise ValueError("raw level CSV row is malformed") from error
        if not level.is_finite() or level <= 0 or rows and observed <= rows[-1][0]:
            raise ValueError("raw level CSV dates and levels must be strictly increasing and positive")
        rows.append((observed, level))
    if not rows:
        raise ValueError("raw level CSV has no rows")
    return tuple(rows)


def _month_targets(start: date, full_end: date) -> tuple[date, ...]:
    targets = []
    current = date(start.year, start.month, 1)
    while current <= full_end:
        target = date(current.year + (current.month == 12), current.month % 12 + 1, 1) - timedelta(days=1)
        if target >= start:
            targets.append(target)
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
    return tuple(targets)


def normalize_month_end_observations(
    rows: Iterable[tuple[date, Decimal]], *, start: date, full_end: date, partial_as_of: date, max_staleness_days: int
) -> dict[date, tuple[date, Decimal]]:
    """Select the final available level in every required calendar month, without truncation."""
    if not (start <= full_end < partial_as_of) or not _month_end(start) or not _month_end(full_end):
        raise ValueError("monthly limits are invalid")
    values = tuple(rows)
    if not values or any(not isinstance(observed, date) or isinstance(observed, datetime) or not isinstance(level, Decimal) or not level.is_finite() or level <= 0 for observed, level in values):
        raise ValueError("levels must contain positive Decimal dates and values")
    if any(current[0] <= previous[0] for previous, current in zip(values, values[1:])):
        raise ValueError("levels must be strictly date ordered")
    output: dict[date, tuple[date, Decimal]] = {}
    for target in (*_month_targets(start, full_end), partial_as_of):
        candidates = [item for item in values if item[0].year == target.year and item[0].month == target.month and item[0] <= target]
        if not candidates:
            raise ValueError("partial observation is missing" if target == partial_as_of else "missing month in required coverage")
        observed, level = candidates[-1]
        if target - observed > timedelta(days=max_staleness_days):
            raise ValueError("month-end observation is stale")
        output[target] = (observed, level)
    return output


def normalize_month_end_levels(
    rows: Iterable[tuple[date, Decimal]], *, start: date, full_end: date, partial_as_of: date, max_staleness_days: int
) -> dict[date, Decimal]:
    return {target: level for target, (_, level) in normalize_month_end_observations(rows, start=start, full_end=full_end, partial_as_of=partial_as_of, max_staleness_days=max_staleness_days).items()}


def convert_usd_monthly_levels_to_eur(usd_levels: Mapping[date, Decimal], usd_per_eur: Mapping[date, Decimal]) -> dict[date, Decimal]:
    if set(usd_levels) != set(usd_per_eur):
        raise ValueError("USD levels require same-month FX coverage")
    if any(not value.is_finite() or value <= 0 for value in (*usd_levels.values(), *usd_per_eur.values())):
        raise ValueError("USD and FX levels must be positive finite Decimals")
    return {month: usd_levels[month] / usd_per_eur[month] for month in usd_levels}


def validate_vintage_manifest(
    manifest: Mapping[str, object], *, expected_sources: tuple[str, ...], warmup_start: date, full_end: date, partial_as_of: date, vintage_root: Path | None = None
) -> None:
    if manifest.get("full_end") != full_end.isoformat() or manifest.get("partial_as_of") != partial_as_of.isoformat():
        raise ValueError("manifest final limits do not match the frozen vintage")
    sources = manifest.get("sources")
    coverage = manifest.get("coverage")
    if not isinstance(sources, list) or not isinstance(coverage, dict):
        raise ValueError("manifest sources and coverage are required")
    ids = tuple(item.get("source_id") for item in sources if isinstance(item, dict))
    if ids != expected_sources or len(ids) != len(sources):
        raise ValueError("manifest source coverage is not exact")
    for item in sources:
        if not isinstance(item, dict) or not isinstance(item.get("source_hash"), str) or not _HASH.fullmatch(item["source_hash"]):
            raise ValueError("manifest source hashes are required")
    expected_coverage = [warmup_start.isoformat(), full_end.isoformat(), partial_as_of.isoformat()]
    if set(coverage) != set(expected_sources) or any(coverage[source_id] != expected_coverage for source_id in expected_sources):
        raise ValueError("manifest coverage is incomplete or silently truncated")
    normalized = manifest.get("normalized_files")
    if normalized is None and vintage_root is None:
        return
    if not isinstance(vintage_root, Path) or not vintage_root.is_dir() or not isinstance(normalized, list):
        raise ValueError("normalized file validation requires the vintage root")
    raw_root = (vintage_root / "raw").resolve()
    for item in sources:
        source_id, raw_path = item.get("source_id"), item.get("path")
        if not isinstance(source_id, str) or not isinstance(raw_path, str):
            raise ValueError("manifest raw path is required")
        relative = Path(raw_path)
        if relative.as_posix() != raw_path or relative.parent != Path("raw") or relative.name != f"{source_id}.csv":
            raise ValueError("manifest raw path must be the exact raw child")
        path = (vintage_root / relative).resolve()
        if path.parent != raw_root or not path.is_file() or not isinstance(item.get("byte_count"), int) or isinstance(item["byte_count"], bool) or path.stat().st_size != item["byte_count"] or sha256_file(path) != item["source_hash"]:
            raise ValueError("manifest raw bytes do not reconcile")
    paths = tuple(item.get("path") for item in normalized if isinstance(item, dict))
    if paths != _NORMALIZED_VINTAGE_FILES or len(normalized) != len(_NORMALIZED_VINTAGE_FILES):
        raise ValueError("normalized file paths must be exact")
    for item in normalized:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or Path(item["path"]).name != item["path"]:
            raise ValueError("normalized file path is invalid")
        path = vintage_root / item["path"]
        if not path.is_file() or sha256_file(path) != item.get("sha256") or path.stat().st_size != item.get("byte_count"):
            raise ValueError("normalized file bytes do not reconcile")
        with path.open("r", encoding="utf-8", newline="") as handle:
            row_count = sum(1 for _ in csv.DictReader(handle))
        if row_count != item.get("row_count"):
            raise ValueError("normalized file row count does not reconcile")


@dataclass(frozen=True)
class SourceDefinition:
    candidate_id: str
    exposure: str
    source_id: str
    track: str
    vendor: str
    ticker: str
    source_url: str
    inception_date: date
    total_return_required: bool
    total_return_kind: str
    content_hash_algorithm: str
    max_staleness_days: int
    source_currency: str
    fx_conversion: str
    coverage_caveat: str = "NONE"

    def __post_init__(self) -> None:
        for field in ("candidate_id", "exposure", "source_id", "vendor", "ticker", "source_url", "total_return_kind"):
            _text(getattr(self, field), field)
        if self.track not in {"CONDITIONAL_INDEX_PROXY", "TRADABLE_ETF", "BENCHMARK_ONLY"}:
            raise ValueError("track is invalid")
        if not isinstance(self.inception_date, date) or isinstance(self.inception_date, datetime):
            raise ValueError("inception_date must be a date")
        if self.total_return_kind == "ADJUSTED_TOTAL_RETURN":
            if self.total_return_required is not True:
                raise ValueError("adjusted total return is required")
        elif not (self.candidate_id == "SGLD" and self.source_id == "LBMA_GOLD_PM" and self.total_return_kind == "PRICE_ONLY_NO_INCOME_ASSET" and self.total_return_required is False):
            raise ValueError("only LBMA Gold PM may be a price-only no-income asset")
        if self.content_hash_algorithm != "SHA256":
            raise ValueError("content_hash_algorithm must be SHA256")
        if not isinstance(self.max_staleness_days, int) or isinstance(self.max_staleness_days, bool) or self.max_staleness_days < 0:
            raise ValueError("max_staleness_days must be a non-negative integer")
        if self.source_currency not in {"EUR", "USD"}:
            raise ValueError("source_currency must be EUR or USD")
        expected_conversion = "NONE_EUR_BASE" if self.source_currency == "EUR" else "DIVIDE_BY_USD_PER_EUR_DEXUSEU_AT_SAME_MONTH_END"
        if self.fx_conversion != expected_conversion:
            raise ValueError(f"fx_conversion must be {expected_conversion}")
        if self.coverage_caveat not in {"NONE", "US_ONLY_PROXY"}:
            raise ValueError("coverage_caveat is invalid")


@dataclass(frozen=True)
class MonthlyTotalReturnObservation:
    candidate_id: str
    month_end: date
    total_return_level: Decimal
    source_id: str
    observation_date: date
    available_at: date
    retrieved_at: datetime
    source_hash: str
    currency: str = "EUR"

    def __post_init__(self) -> None:
        if self.candidate_id not in DRO_UNIVERSE:
            raise ValueError("candidate_id is not in the frozen universe")
        if not isinstance(self.month_end, date) or isinstance(self.month_end, datetime) or (not _month_end(self.month_end) and self.month_end != _FROZEN_V1_DATES[-1]):
            raise ValueError("month_end must be a month-end date or the frozen partial observation")
        if not isinstance(self.observation_date, date) or isinstance(self.observation_date, datetime):
            raise ValueError("observation_date must be a date")
        if not isinstance(self.available_at, date) or isinstance(self.available_at, datetime):
            raise ValueError("available_at must be a date")
        if self.available_at < self.observation_date:
            raise ValueError("available_at must not precede observation_date")
        if not isinstance(self.total_return_level, Decimal) or not self.total_return_level.is_finite() or self.total_return_level <= 0:
            raise ValueError("total_return_level must be a positive finite Decimal")
        _text(self.source_id, "source_id")
        if not isinstance(self.retrieved_at, datetime) or self.retrieved_at.tzinfo is None or self.retrieved_at.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        object.__setattr__(self, "retrieved_at", self.retrieved_at.astimezone(timezone.utc))
        if not isinstance(self.source_hash, str) or not _HASH.fullmatch(self.source_hash):
            raise ValueError("source_hash must be a 64-character lowercase hexadecimal SHA-256")
        if self.currency != "EUR":
            raise ValueError("monthly ranking observations must be expressed in EUR")


@dataclass(frozen=True)
class CandidateDiagnostic:
    candidate_id: str
    ranking_return: Decimal | None
    trend_ok: bool
    absolute_ok: bool
    eligible: bool
    rejection_reason: str | None = None


@dataclass(frozen=True)
class MonthlyDecision:
    signal_month: date
    execution_month: date
    selected_candidate: str
    diagnostics: tuple[CandidateDiagnostic, ...]
    turnover: Decimal
    gross_return: Decimal | None
    transaction_cost: Decimal
    net_return: Decimal | None
    is_partial: bool = False


@dataclass(frozen=True)
class StrategyReturnSummary:
    month_count: int
    gross_compound_return: Decimal
    net_compound_return: Decimal
    transaction_cost: Decimal


@dataclass(frozen=True)
class FxSourceDefinition:
    source_id: str
    pair: str
    source_url: str
    inception_date: date
    content_hash_algorithm: str
    monthly_asof_policy: str

    def __post_init__(self) -> None:
        if self.source_id != "FRED_DEXUSEU" or self.pair != "USD_PER_EUR":
            raise ValueError("DRO FX source must be FRED DEXUSEU USD per EUR")
        if self.source_url != "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXUSEU":
            raise ValueError("DRO FX source URL is invalid")
        if self.inception_date != date(1999, 1, 4) or self.content_hash_algorithm != "SHA256":
            raise ValueError("DRO FX source inception or hash contract is invalid")
        if self.monthly_asof_policy != "MONTH_END_LAST_AVAILABLE_OBSERVATION":
            raise ValueError("DRO FX monthly as-of policy is invalid")


@dataclass(frozen=True)
class DroConfig:
    warmup_start: date
    full_start: date
    full_end: date
    partial_as_of: date
    partial_label: str
    max_staleness_days: int
    benchmark_id: str
    scoring_inputs: str
    shares_scores_with_tce: bool
    automatic_funding_from_structural: bool
    ranking_currency: str
    eur_conversion_policy: str
    fx_source: FxSourceDefinition
    sources: tuple[SourceDefinition, ...]

    @property
    def candidates(self) -> tuple[str, ...]:
        return tuple(source.candidate_id for source in self.sources if source.track == "CONDITIONAL_INDEX_PROXY")

    @property
    def candidate_exposures(self) -> Mapping[str, str]:
        return DRO_EXPOSURES

    def __post_init__(self) -> None:
        if not all(_month_end(value) for value in (self.warmup_start, self.full_start, self.full_end)):
            raise ValueError("history limits must be month-end dates")
        if (self.warmup_start, self.full_start, self.full_end, self.partial_as_of) != _FROZEN_V1_DATES or self.partial_label != "PARTIAL_AS_OF_2026-08-28":
            raise ValueError("frozen DRO v1 dates are invalid")
        if not isinstance(self.max_staleness_days, int) or isinstance(self.max_staleness_days, bool) or self.max_staleness_days < 0:
            raise ValueError("max_staleness_days must be a non-negative integer")
        if self.benchmark_id != "SWDA" or self.scoring_inputs != "PRICE_BASED_TOTAL_RETURN_ONLY":
            raise ValueError("DRO benchmark or scoring contract is invalid")
        if self.ranking_currency != "EUR" or self.eur_conversion_policy != "DIVIDE_USD_LEVEL_BY_USD_PER_EUR_AT_SAME_MONTH_END":
            raise ValueError("DRO ranking returns must be normalized to EUR")
        if self.shares_scores_with_tce or self.automatic_funding_from_structural:
            raise ValueError("DRO must remain separate from TCE and structural funding")
        for track in ("CONDITIONAL_INDEX_PROXY", "TRADABLE_ETF"):
            tracked = tuple(source for source in self.sources if source.track == track)
            if len({source.candidate_id for source in tracked}) != len(tracked):
                raise ValueError("duplicate candidate within track")
            if {source.candidate_id for source in tracked} != set(DRO_UNIVERSE):
                raise ValueError(f"{track} candidates must match the frozen universe")
        if self.candidates != DRO_UNIVERSE:
            raise ValueError("conditional proxy candidates must match the frozen universe")
        if len({source.source_id for source in self.sources}) != len(self.sources):
            raise ValueError("source_id values must be unique")
        for source in self.sources:
            if source.max_staleness_days != self.max_staleness_days:
                raise ValueError("source max_staleness_days must match config")
            if source.track == "CONDITIONAL_INDEX_PROXY" and source.inception_date > self.warmup_start:
                raise ValueError("conditional source inception must reach warm-up")
            if source.track == "CONDITIONAL_INDEX_PROXY" and source.exposure != DRO_EXPOSURES[source.candidate_id]:
                raise ValueError("frozen candidate exposure must match the v1 contract")
        for source in (source for source in self.sources if source.track == "TRADABLE_ETF"):
            if source.exposure != self.candidate_exposures[source.candidate_id]:
                raise ValueError("tradable exposure must match the frozen candidate exposure")
        benchmark = tuple(source for source in self.sources if source.track == "BENCHMARK_ONLY")
        if len(benchmark) != 1 or benchmark[0].candidate_id != self.benchmark_id:
            raise ValueError("SWDA must be the sole benchmark-only source")


def validate_monthly_observation(config: DroConfig, observation: MonthlyTotalReturnObservation, *, signal_as_of: date | None = None) -> None:
    if not isinstance(config, DroConfig) or not isinstance(observation, MonthlyTotalReturnObservation):
        raise ValueError("config and observation types are required")
    declared = tuple(source for source in config.sources if source.source_id == observation.source_id)
    if len(declared) != 1 or declared[0].candidate_id != observation.candidate_id:
        raise ValueError("source_id is not declared for candidate")
    source = declared[0]
    if not source.total_return_required and source.total_return_kind != "PRICE_ONLY_NO_INCOME_ASSET":
        raise ValueError("source must declare adjusted total return")
    if signal_as_of is None:
        return
    if not isinstance(signal_as_of, date) or isinstance(signal_as_of, datetime):
        raise ValueError("signal_as_of must be a date")
    reference_date = min(observation.month_end, signal_as_of)
    if observation.observation_date > reference_date:
        raise ValueError("observation_date must not follow its reference date")
    if observation.available_at > signal_as_of:
        raise ValueError("available_at must not follow signal_as_of")
    if reference_date - observation.observation_date > timedelta(days=config.max_staleness_days):
        raise ValueError("observation is stale at signal_as_of")


def _next_month_end(value: date) -> date:
    next_month_start = date(value.year + (value.month == 12), value.month % 12 + 1, 1)
    return date(next_month_start.year + (next_month_start.month == 12), next_month_start.month % 12 + 1, 1) - timedelta(days=1)


def _indexed_observations(config: DroConfig, observations: Iterable[MonthlyTotalReturnObservation]) -> Mapping[tuple[str, date], MonthlyTotalReturnObservation]:
    indexed: dict[tuple[str, date], MonthlyTotalReturnObservation] = {}
    for observation in observations:
        validate_monthly_observation(config, observation)
        key = (observation.candidate_id, observation.month_end)
        if key in indexed:
            raise ValueError("duplicate observation for candidate and month")
        indexed[key] = observation
    return MappingProxyType(indexed)


def _diagnostics(config: DroConfig, observations: Mapping[tuple[str, date], MonthlyTotalReturnObservation], signal_month: date) -> tuple[CandidateDiagnostic, ...]:
    ranking_start = _month_end_before(signal_month, 12)
    ranking_end = _month_end_before(signal_month, 1)
    trend_months = (*(_month_end_before(signal_month, months_back) for months_back in range(9, 0, -1)), signal_month)
    diagnostics = []
    for candidate_id in config.candidates:
        keys = ((candidate_id, ranking_start), (candidate_id, ranking_end), *((candidate_id, month) for month in trend_months))
        if any(key not in observations for key in keys):
            diagnostics.append(CandidateDiagnostic(candidate_id, None, False, False, False, "MISSING_OBSERVATION"))
            continue
        for key in keys:
            validate_monthly_observation(config, observations[key], signal_as_of=signal_month)
            source = next(source for source in config.sources if source.source_id == observations[key].source_id)
            if source.track != "CONDITIONAL_INDEX_PROXY":
                raise ValueError("ranking observations must use CONDITIONAL_INDEX_PROXY sources")
        ranking_return = observations[(candidate_id, ranking_end)].total_return_level / observations[(candidate_id, ranking_start)].total_return_level - Decimal("1")
        current_level = observations[(candidate_id, signal_month)].total_return_level
        trend_ok = current_level > sum(observations[(candidate_id, month)].total_return_level for month in trend_months) / Decimal(len(trend_months))
        absolute_ok = ranking_return > 0
        diagnostics.append(CandidateDiagnostic(candidate_id, ranking_return, trend_ok, absolute_ok, trend_ok and absolute_ok))
    return tuple(diagnostics)


def _month_end_before(value: date, months_back: int) -> date:
    year, month = value.year, value.month - months_back
    year += (month - 1) // 12
    month = (month - 1) % 12 + 1
    return date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)


def calculate_monthly_decision(
    config: DroConfig,
    observations: Iterable[MonthlyTotalReturnObservation],
    signal_month: date,
    execution_month: date | None,
    *,
    previous_position: str = "CASH",
    cash_return: Decimal,
    is_partial: bool = False,
) -> MonthlyDecision:
    partial_hold = is_partial and signal_month == config.full_end and execution_month == config.partial_as_of
    provisional = is_partial and signal_month == config.partial_as_of and execution_month is None
    if not (( _month_end(signal_month) and execution_month == _next_month_end(signal_month)) or partial_hold or provisional):
        raise ValueError("execution_month must be the next complete month-end after signal_month")
    if previous_position not in (*config.candidates, "CASH"):
        raise ValueError("previous_position must be CASH or a frozen candidate")
    if not isinstance(cash_return, Decimal) or not cash_return.is_finite():
        raise ValueError("cash_return must be a finite Decimal")
    indexed = _indexed_observations(config, observations)
    diagnostics = _diagnostics(config, indexed, signal_month)
    ranked = sorted((item for item in diagnostics if item.eligible), key=lambda item: (-item.ranking_return, config.candidates.index(item.candidate_id)))
    selected_candidate = ranked[0].candidate_id if ranked else "CASH"
    turnover = Decimal("0") if selected_candidate == previous_position else Decimal("1") if "CASH" in {selected_candidate, previous_position} else Decimal("2")
    transaction_cost = Decimal("0") if provisional else turnover * Decimal("0.001")
    if provisional:
        gross_return = None
        net_return = None
    elif selected_candidate == "CASH":
        gross_return = cash_return
        net_return = (Decimal("1") + gross_return) * (Decimal("1") - transaction_cost) - Decimal("1")
    else:
        execution_observation = indexed.get((selected_candidate, execution_month))
        if execution_observation is None:
            raise ValueError("selected candidate has no execution observation")
        validate_monthly_observation(config, execution_observation, signal_as_of=execution_month)
        gross_return = execution_observation.total_return_level / indexed[(selected_candidate, signal_month)].total_return_level - Decimal("1")
        net_return = (Decimal("1") + gross_return) * (Decimal("1") - transaction_cost) - Decimal("1")
    return MonthlyDecision(
        signal_month=signal_month,
        execution_month=execution_month,
        selected_candidate=selected_candidate,
        diagnostics=diagnostics,
        turnover=turnover,
        gross_return=gross_return,
        transaction_cost=transaction_cost,
        net_return=net_return,
        is_partial=is_partial,
    )


def summarize_strategy_returns(decisions: Iterable[MonthlyDecision]) -> StrategyReturnSummary:
    full_decisions = tuple(decision for decision in decisions if not decision.is_partial)
    gross_factor = Decimal("1")
    net_factor = Decimal("1")
    for decision in full_decisions:
        if decision.gross_return is None or decision.net_return is None:
            raise ValueError("complete decisions require realized returns")
        gross_factor *= Decimal("1") + decision.gross_return
        net_factor *= Decimal("1") + decision.net_return
    return StrategyReturnSummary(
        month_count=len(full_decisions),
        gross_compound_return=gross_factor - Decimal("1"),
        net_compound_return=net_factor - Decimal("1"),
        transaction_cost=sum((decision.transaction_cost for decision in full_decisions), Decimal("0")),
    )


def load_dro_config(path: Path) -> DroConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != "DRO_CONFIG_V1":
        raise ValueError("schema_version must be DRO_CONFIG_V1")
    sources = tuple(
        SourceDefinition(
            candidate_id=item["candidate_id"],
            exposure=item["exposure"],
            source_id=item["source_id"],
            track=item["track"],
            vendor=item["vendor"],
            ticker=item["ticker"],
            source_url=item["source_url"],
            inception_date=_date(item["inception_date"], "inception_date"),
            total_return_required=item["total_return_required"],
            total_return_kind=item["total_return_kind"],
            content_hash_algorithm=item["content_hash_algorithm"],
            max_staleness_days=item["max_staleness_days"],
            source_currency=item["source_currency"],
            fx_conversion=item["fx_conversion"],
            coverage_caveat=item.get("coverage_caveat", "NONE"),
        )
        for item in raw["sources"]
    )
    fx = raw["fx_source"]
    return DroConfig(
        warmup_start=_date(raw["warmup_start"], "warmup_start"),
        full_start=_date(raw["full_start"], "full_start"),
        full_end=_date(raw["full_end"], "full_end"),
        partial_as_of=_date(raw["partial_as_of"], "partial_as_of"),
        partial_label=raw["partial_label"],
        max_staleness_days=raw["max_staleness_days"],
        benchmark_id=raw["benchmark_id"],
        scoring_inputs=raw["scoring_inputs"],
        shares_scores_with_tce=raw["shares_scores_with_tce"],
        automatic_funding_from_structural=raw["automatic_funding_from_structural"],
        ranking_currency=raw["ranking_currency"],
        eur_conversion_policy=raw["eur_conversion_policy"],
        fx_source=FxSourceDefinition(
            source_id=fx["source_id"],
            pair=fx["pair"],
            source_url=fx["source_url"],
            inception_date=_date(fx["inception_date"], "fx_source inception_date"),
            content_hash_algorithm=fx["content_hash_algorithm"],
            monthly_asof_policy=fx["monthly_asof_policy"],
        ),
        sources=sources,
    )
