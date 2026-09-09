from __future__ import annotations

import csv
import json
import tempfile
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Mapping

from perpetual_engine.dro import (
    DRO_UNIVERSE,
    MonthlyDecision,
    MonthlyTotalReturnObservation,
    _month_targets,
    calculate_monthly_decision,
    load_dro_config,
    validate_vintage_manifest,
)
from perpetual_engine.io import canonical_json, remove_tree


_TOTAL_COLUMNS = (
    "month_end", "candidate_id", "source_id", "observation_date", "available_at", "retrieved_at",
    "source_hash", "source_url", "total_return_level_eur",
)
_BENCHMARK_COLUMNS = (
    "month_end", "benchmark_id", "source_id", "observation_date", "available_at", "retrieved_at",
    "source_hash", "source_url", "total_return_level_eur",
)
_CASH_COLUMNS = (
    "month_end", "source_id", "observation_date", "available_at", "retrieved_at", "source_hash",
    "source_url", "cash_return_eur",
)
_MONTHLY_COLUMNS = (
    "signal_month", "execution_month", "period_status", "previous_position", "selected_candidate",
    "eligible_candidates", "ranking_returns", "trend_flags", "turnover", "transaction_cost",
    "gross_return", "net_return", "swda_benchmark_return", "gross_value", "net_value",
)


def _decimal(value: str, name: str) -> Decimal:
    try:
        result = Decimal(value)
    except Exception as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not result.is_finite():
        raise ValueError(f"{name} must be a finite decimal")
    return result


def _date(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an ISO date") from error


def _datetime(value: str, name: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as error:
        raise ValueError(f"{name} must be an ISO timestamp") from error
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return result


def _read_rows(path: Path, columns: tuple[str, ...]) -> tuple[dict[str, str], ...]:
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != columns:
                raise ValueError(f"{path.name} columns do not match the frozen contract")
            rows = tuple(reader)
    except FileNotFoundError as error:
        raise ValueError(f"{path.name} is missing") from error
    if not rows or any(set(row) != set(columns) or any(value is None for value in row.values()) for row in rows):
        raise ValueError(f"{path.name} has malformed rows")
    return rows


def _expected_sources(config_path: Path) -> tuple[str, ...]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    return tuple(
        item["source_id"] for item in raw["sources"] if item["track"] in {"CONDITIONAL_INDEX_PROXY", "BENCHMARK_ONLY"}
    ) + (raw["fx_source"]["source_id"],) + tuple(item["source_id"] for item in raw["cash_sources"])


def _source_records(manifest: Mapping[str, object]) -> Mapping[str, Mapping[str, object]]:
    sources = manifest["sources"]
    return {item["source_id"]: item for item in sources if isinstance(item, dict)}  # type: ignore[index,union-attr]


def _validate_manifest_source_contracts(config_path: Path, config, records: Mapping[str, Mapping[str, object]]) -> Mapping[str, str]:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    configured = {item["source_id"]: item for item in raw["sources"]}
    for source in config.sources:
        if source.track not in {"CONDITIONAL_INDEX_PROXY", "BENCHMARK_ONLY"}:
            continue
        record, declared = records[source.source_id], configured[source.source_id]
        urls = {source.source_url}
        if source.vendor == "FRED":
            urls.add(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={source.ticker}")
        if "programmatic_fallback" in declared:
            urls.add(declared["programmatic_fallback"]["source_url"])
        if record.get("candidate_id") != source.candidate_id or record.get("source_url") not in urls:
            raise ValueError("manifest source declaration does not reconcile with config")
    fx = raw["fx_source"]
    if records[fx["source_id"]].get("source_url") != fx["source_url"]:
        raise ValueError("manifest FX source declaration does not reconcile with config")
    cash_urls = {item["source_id"]: item["source_url"] for item in raw["cash_sources"]}
    if any(records[source_id].get("source_url") != source_url for source_id, source_url in cash_urls.items()):
        raise ValueError("manifest cash source declaration does not reconcile with config")
    return cash_urls


def _validate_row_metadata(row: Mapping[str, str], source: Mapping[str, object], reference: date, max_staleness_days: int, label: str) -> None:
    if row["source_hash"] != source.get("source_hash") or row["source_url"] != source.get("source_url"):
        raise ValueError(f"{label} source metadata does not reconcile")
    observed = _date(row["observation_date"], f"{label} observation_date")
    available = _date(row["available_at"], f"{label} available_at")
    _datetime(row["retrieved_at"], f"{label} retrieved_at")
    if observed > reference or available < observed or available > reference:
        raise ValueError(f"{label} observation availability is invalid")
    if reference - observed > timedelta(days=max_staleness_days):
        raise ValueError(f"{label} observation is stale")


def _input_data(config_path: Path, vintage_root: Path):
    config = load_dro_config(config_path)
    manifest = json.loads((vintage_root / "manifest.json").read_text(encoding="utf-8"))
    expected_sources = _expected_sources(config_path)
    validate_vintage_manifest(
        manifest,
        expected_sources=expected_sources,
        warmup_start=config.warmup_start,
        full_end=config.full_end,
        partial_as_of=config.partial_as_of,
        vintage_root=vintage_root,
    )
    records = _source_records(manifest)
    cash_urls = _validate_manifest_source_contracts(config_path, config, records)
    periods = (*_month_targets(config.warmup_start, config.full_end), config.partial_as_of)
    conditional = {source.candidate_id: source.source_id for source in config.sources if source.track == "CONDITIONAL_INDEX_PROXY"}
    observations: list[MonthlyTotalReturnObservation] = []
    for row in _read_rows(vintage_root / "monthly_total_return_eur.csv", _TOTAL_COLUMNS):
        candidate, source_id, month = row["candidate_id"], row["source_id"], _date(row["month_end"], "month_end")
        source = records.get(source_id)
        if candidate not in conditional or conditional[candidate] != source_id or source is None or row["source_hash"] != source["source_hash"] or row["source_url"] != source["source_url"]:
            raise ValueError("monthly total-return source declaration does not reconcile")
        observations.append(MonthlyTotalReturnObservation(
            candidate_id=candidate, month_end=month, total_return_level=_decimal(row["total_return_level_eur"], "total_return_level_eur"),
            source_id=source_id, observation_date=_date(row["observation_date"], "observation_date"),
            available_at=_date(row["available_at"], "available_at"), retrieved_at=_datetime(row["retrieved_at"], "retrieved_at"),
            source_hash=row["source_hash"],
        ))
    expected_keys = {(candidate, period) for candidate in DRO_UNIVERSE for period in periods}
    actual_keys = {(item.candidate_id, item.month_end) for item in observations}
    if actual_keys != expected_keys or len(observations) != len(expected_keys):
        raise ValueError("monthly total-return coverage is incomplete or silently truncated")
    benchmark_source = next(source for source in config.sources if source.track == "BENCHMARK_ONLY")
    benchmark: dict[date, Decimal] = {}
    for row in _read_rows(vintage_root / "monthly_benchmark_total_return_eur.csv", _BENCHMARK_COLUMNS):
        month, source_id = _date(row["month_end"], "benchmark month_end"), row["source_id"]
        source = records.get(source_id)
        if row["benchmark_id"] != config.benchmark_id or source_id != benchmark_source.source_id or source is None:
            raise ValueError("benchmark source declaration does not reconcile")
        _validate_row_metadata(row, source, month, config.max_staleness_days, "benchmark")
        if month in benchmark:
            raise ValueError("benchmark has duplicate months")
        benchmark[month] = _decimal(row["total_return_level_eur"], "benchmark total_return_level_eur")
    if set(benchmark) != set(periods):
        raise ValueError("benchmark coverage is incomplete or silently truncated")
    cash: dict[date, Decimal] = {}
    for row in _read_rows(vintage_root / "monthly_cash_return_eur.csv", _CASH_COLUMNS):
        month, source_id = _date(row["month_end"], "cash month_end"), row["source_id"]
        source = records.get(source_id)
        expected_cash_source = "ECB_ESTR" if month >= date(2019, 10, 1) else "ECB_DEPOSIT_FACILITY"
        if source_id != expected_cash_source or source_id not in cash_urls or source is None or row["source_url"] != cash_urls[source_id]:
            raise ValueError("cash source declaration does not reconcile")
        _validate_row_metadata(row, source, month, config.max_staleness_days, "cash")
        if month in cash:
            raise ValueError("cash has duplicate months")
        cash[month] = _decimal(row["cash_return_eur"], "cash_return_eur")
    if set(cash) != set(periods):
        raise ValueError("cash coverage is incomplete or silently truncated")
    return config, tuple(observations), benchmark, cash


def _text(value: Decimal | None) -> str:
    return "" if value is None else format(value, "f")


def _next_month_end(value: date) -> date:
    start = date(value.year + (value.month == 12), value.month % 12 + 1, 1)
    return date(start.year + (start.month == 12), start.month % 12 + 1, 1) - timedelta(days=1)


def _series_metrics(returns: Iterable[Decimal], cash_returns: Iterable[Decimal]) -> Mapping[str, str | None]:
    values, cash = tuple(returns), tuple(cash_returns)
    if not values or len(values) != len(cash):
        raise ValueError("metric returns must be complete and aligned")
    factor = Decimal("1")
    peak = Decimal("1")
    max_drawdown = Decimal("0")
    for value in values:
        factor *= Decimal("1") + value
        peak = max(peak, factor)
        max_drawdown = min(max_drawdown, factor / peak - Decimal("1"))
    mean = sum(values) / Decimal(len(values))
    variance = sum((value - mean) ** 2 for value in values) / Decimal(len(values))
    volatility = variance.sqrt() * Decimal("12").sqrt()
    excess = tuple(value - rate for value, rate in zip(values, cash))
    excess_mean = sum(excess) / Decimal(len(excess))
    excess_variance = sum((value - excess_mean) ** 2 for value in excess) / Decimal(len(excess))
    excess_volatility = excess_variance.sqrt()
    return {
        "cagr": _text((factor.ln() * Decimal("12") / Decimal(len(values))).exp() - Decimal("1")),
        "annualized_volatility": _text(volatility),
        "sharpe": None if not excess_volatility else _text(excess_mean / excess_volatility * Decimal("12").sqrt()),
        "max_drawdown": _text(max_drawdown),
        "compound_return": _text(factor - Decimal("1")),
    }


def _calendar_returns(gross: Iterable[Decimal], net: Iterable[Decimal], months: Iterable[date]) -> Mapping[str, Mapping[str, str | int]]:
    buckets: dict[int, list[tuple[Decimal, Decimal]]] = {}
    for gross_return, net_return, month in zip(gross, net, months):
        buckets.setdefault(month.year, []).append((gross_return, net_return))
    result = {}
    for year, values in sorted(buckets.items()):
        gross_factor = net_factor = Decimal("1")
        for gross_return, net_return in values:
            gross_factor *= Decimal("1") + gross_return
            net_factor *= Decimal("1") + net_return
        result[str(year)] = {"months": len(values), "gross_return": _text(gross_factor - Decimal("1")), "net_return": _text(net_factor - Decimal("1"))}
    return result


def _decision_row(decision: MonthlyDecision, previous_position: str, gross_value: Decimal, net_value: Decimal, status: str, benchmark_return: Decimal | None) -> Mapping[str, str]:
    rankings = {item.candidate_id: _text(item.ranking_return) for item in decision.diagnostics}
    trends = {item.candidate_id: item.trend_ok for item in decision.diagnostics}
    return {
        "signal_month": decision.signal_month.isoformat(), "execution_month": "" if decision.execution_month is None else decision.execution_month.isoformat(),
        "period_status": status, "previous_position": previous_position, "selected_candidate": decision.selected_candidate,
        "eligible_candidates": "|".join(item.candidate_id for item in decision.diagnostics if item.eligible),
        "ranking_returns": json.dumps(rankings, sort_keys=True, separators=(",", ":")),
        "trend_flags": json.dumps(trends, sort_keys=True, separators=(",", ":")), "turnover": _text(decision.turnover),
        "transaction_cost": _text(decision.transaction_cost), "gross_return": _text(decision.gross_return), "net_return": _text(decision.net_return), "swda_benchmark_return": _text(benchmark_return),
        "gross_value": _text(gross_value), "net_value": _text(net_value),
    }


def _write_csv(path: Path, rows: Iterable[Mapping[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_MONTHLY_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def run_dro_report(config_path: Path, vintage_root: Path, output_dir: Path) -> int:
    """Validate frozen DRO inputs, calculate offline, and atomically publish deterministic outputs."""
    config_path, vintage_root, output_dir = Path(config_path), Path(vintage_root), Path(output_dir)
    config, observations, benchmark, cash = _input_data(config_path, vintage_root)
    decisions: list[MonthlyDecision] = []
    previous = "CASH"
    for signal_month in _month_targets(config.full_start, config.full_end):
        execution_month = config.partial_as_of if signal_month == config.full_end else _next_month_end(signal_month)
        decision = calculate_monthly_decision(config, observations, signal_month, execution_month, previous_position=previous, cash_return=cash[execution_month], is_partial=signal_month == config.full_end)
        decisions.append(decision)
        previous = decision.selected_candidate
    provisional = calculate_monthly_decision(config, observations, config.partial_as_of, None, previous_position=previous, cash_return=cash[config.partial_as_of], is_partial=True)
    decisions.append(provisional)
    complete = tuple(decision for decision in decisions if not decision.is_partial)
    gross = tuple(decision.gross_return for decision in complete)
    net = tuple(decision.net_return for decision in complete)
    if any(value is None for value in (*gross, *net)):
        raise ValueError("complete DRO decisions require realized returns")
    gross, net = tuple(gross), tuple(net)  # type: ignore[assignment]
    execution_months = tuple(decision.execution_month for decision in complete)
    if any(month is None for month in execution_months):
        raise ValueError("complete DRO decisions require execution months")
    execution_months = tuple(execution_months)  # type: ignore[assignment]
    cash_returns = tuple(cash[month] for month in execution_months)
    benchmark_returns = tuple(benchmark[month] / benchmark[decision.signal_month] - Decimal("1") for decision, month in zip(complete, execution_months))
    levels = {(item.candidate_id, item.month_end): item.total_return_level for item in observations}
    equal_weight = tuple(sum((levels[(candidate, month)] / levels[(candidate, decision.signal_month)] - Decimal("1") for candidate in DRO_UNIVERSE), Decimal("0")) / Decimal(len(DRO_UNIVERSE)) for decision, month in zip(complete, execution_months))
    buy_hold = []
    prior_value = Decimal("1")
    for month in execution_months:
        value = sum((levels[(candidate, month)] / levels[(candidate, config.full_start)] for candidate in DRO_UNIVERSE), Decimal("0")) / Decimal(len(DRO_UNIVERSE))
        buy_hold.append(value / prior_value - Decimal("1"))
        prior_value = value
    gross_value = net_value = Decimal("1")
    monthly_rows = []
    previous = "CASH"
    for decision in decisions:
        if decision.gross_return is not None:
            gross_value *= Decimal("1") + decision.gross_return
        if decision.net_return is not None:
            net_value *= Decimal("1") + decision.net_return
        status = "COMPLETE" if not decision.is_partial else config.partial_label if decision.signal_month == config.full_end else "PROVISIONAL_NEXT_EXECUTION"
        benchmark_return = None if decision.execution_month is None else benchmark[decision.execution_month] / benchmark[decision.signal_month] - Decimal("1")
        monthly_rows.append(_decision_row(decision, previous, gross_value, net_value, status, benchmark_return))
        previous = decision.selected_candidate
    turnover = sum((decision.turnover for decision in complete), Decimal("0"))
    result = {
        "schema_version": "DRO_REPORT_V1", "research_label": "RETROSPECTIVE_RESEARCH_NOT_TRADING_AUTHORITY", "partial_label": config.partial_label,
        "metrics": {"month_count": len(complete), "gross": _series_metrics(gross, cash_returns), "net": _series_metrics(net, cash_returns), "turnover": _text(turnover), "transaction_cost": _text(sum((decision.transaction_cost for decision in complete), Decimal("0"))), "calendar_returns": _calendar_returns(gross, net, execution_months)},
        "comparators": {"SWDA": _series_metrics(benchmark_returns, cash_returns), "cash": _series_metrics(cash_returns, cash_returns), "equal_weight_monthly": _series_metrics(equal_weight, cash_returns), "equal_weight_buy_and_hold": _series_metrics(tuple(buy_hold), cash_returns)},
        "cost_model": {"spread_slippage_per_leg_bps": 10, "fixed_commission_sensitivity_eur": "19"},
        "commission_sensitivity": {str(notional): {"commission_per_leg_eur": "19", "per_leg_fraction": _text(Decimal("19") / Decimal(notional)), "total_legs": _text(turnover), "total_commission_eur": _text(Decimal("19") * turnover), "total_commission_fraction_of_initial": _text(Decimal("19") * turnover / Decimal(notional))} for notional in (5000, 80000)},
        "caveats": ["Survivorship limitation: the universe was frozen in 2026 and may not represent exposures available in earlier periods."],
        "selection_history": monthly_rows,
    }
    net_metrics = result["metrics"]["net"]
    calendar = "; ".join(f"{year}: {values['net_return']} ({values['months']} months)" for year, values in result["metrics"]["calendar_returns"].items())
    comparisons = "; ".join(f"{name} CAGR {values['cagr']}" for name, values in result["comparators"].items())
    commissions = "; ".join(f"€{notional}: €19 per leg, total €{values['total_commission_eur']}" for notional, values in result["commission_sensitivity"].items())
    selections = "\n".join(f"{row['signal_month']} -> {row['selected_candidate']} -> {row['execution_month'] or 'NEXT_EXECUTION'} ({row['period_status']})" for row in monthly_rows)
    report = "\n".join(("# Deterministic Ranking Overlay v1", "", "Retrospective research, separate from TCE-MA.", "", f"Complete realized months: {len(complete)}", f"Partial observation: {config.partial_label}", f"Net CAGR: {net_metrics['cagr']}", f"Net volatility: {net_metrics['annualized_volatility']}", f"Net Sharpe: {net_metrics['sharpe']}", f"Net maximum drawdown: {net_metrics['max_drawdown']}", f"Turnover (legs): {result['metrics']['turnover']}", f"Cost model: 10 bps per leg; transaction costs {result['metrics']['transaction_cost']}", f"Commission sensitivity: {commissions}", f"Calendar returns (net): {calendar}", f"Comparators: {comparisons}", "", "## Monthly selections", selections, "", "August is not included in complete-month metrics.", "Survivorship limitation: the universe was frozen in 2026 and may not represent earlier investable exposures.")) + "\n"
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=output_dir.parent))
    try:
        _write_csv(stage / "monthly.csv", monthly_rows)
        (stage / "result.json").write_bytes(canonical_json(result))
        (stage / "report.md").write_text(report, encoding="utf-8", newline="")
        if output_dir.exists():
            remove_tree(output_dir)
        stage.replace(output_dir)
    except Exception:
        if stage.exists():
            remove_tree(stage)
        raise
    return 0
