from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Mapping

from perpetual_engine.data_sources import SourceArtifact, parse_fred_csv
from perpetual_engine.four_sleeve import (
    CANDIDATES,
    FourSleeveConfig,
    normalized_four_sleeve_json,
    run_four_sleeve,
)
from perpetual_engine.io import canonical_json
from perpetual_engine.market_proxy import normalize_fx_monthly, parse_french_archive


ROLE_SOURCES = {
    "WORLD": ("FRENCH_DEVELOPED_MONTHLY", "french_developed_factor_archive"),
    "RF": ("FRENCH_DEVELOPED_MONTHLY", "french_developed_rf_archive"),
    "MOMENTUM": ("FRENCH_DEVELOPED_MOMENTUM_MONTHLY", "french_developed_momentum_archive"),
    "QUALITY": ("FRENCH_DEVELOPED_QUALITY_MONTHLY", "french_developed_quality_archive"),
    "TREND": ("AQR_TSMOM_MONTHLY", "french_aqr_tsmom_archive"),
}


def _month_end(value: date) -> date:
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1) - timedelta(days=1)


def _previous_month(value: date) -> date:
    return _month_end(value.replace(day=1) - timedelta(days=1))


def _calendar(start: date, end: date) -> tuple[date, ...]:
    months, current = [], start
    while current <= end:
        months.append(current)
        current = _month_end(current + timedelta(days=1))
    return tuple(months)


def eur_sleeve_returns(
    equity_total_usd: Mapping[str, Mapping[date, Decimal]],
    trend_excess_usd: Mapping[date, Decimal],
    risk_free_usd: Mapping[date, Decimal],
    eur_per_usd: Mapping[date, Decimal],
    start: date,
    end: date,
) -> dict[str, dict[date, Decimal]]:
    months = _calendar(start, end)
    required_fx = (_previous_month(start), *months)
    if tuple(equity_total_usd) != ("WORLD", "MOMENTUM", "QUALITY"):
        raise ValueError("equity sleeves must be in frozen order")
    if any(month not in series for series in equity_total_usd.values() for month in months) or any(
        month not in trend_excess_usd or month not in risk_free_usd for month in months
    ) or any(month not in eur_per_usd for month in required_fx):
        raise ValueError("all inputs must cover the complete explicit calendar")

    output = {name: {} for name in ("WORLD", "MOMENTUM", "QUALITY", "TREND")}
    previous = _previous_month(start)
    for month in months:
        fx_ratio = eur_per_usd[month] / eur_per_usd[previous]
        totals = {name: equity_total_usd[name][month] for name in equity_total_usd}
        totals["TREND"] = trend_excess_usd[month] + risk_free_usd[month]
        for name, return_usd in totals.items():
            if not return_usd.is_finite() or return_usd <= Decimal("-1"):
                raise ValueError("USD returns must be finite and greater than -100%")
            output[name][month] = (Decimal("1") + return_usd) * fx_ratio - Decimal("1")
        previous = month
    return output


def _artifact(raw_dir: Path, filename: str, url: str, retrieved_at: datetime) -> SourceArtifact:
    path = raw_dir / filename
    content = path.read_bytes()
    return SourceArtifact(url, retrieved_at, hashlib.sha256(content).hexdigest(), path, len(content), "1")


def _rows(raw_dir: Path, sources_config_path: Path, retrieved_at: datetime):
    catalog = json.loads(sources_config_path.read_text(encoding="utf-8"))
    if catalog.get("schema_version") != "FOUR_SLEEVE_SOURCES_V1":
        raise ValueError("source config must be FOUR_SLEEVE_SOURCES_V1")
    sources = {source["source_id"]: source for source in catalog["sources"]}
    parsed = {
        name: parse_french_archive(
            _artifact(raw_dir, sources[source_id]["raw_filename"], sources[source_id]["url"], retrieved_at), parser
        )
        for name, (source_id, parser) in ROLE_SOURCES.items()
    }
    fx_source = sources["FRED_DEXUSEU"]
    fx = parse_fred_csv(
        _artifact(raw_dir, fx_source["raw_filename"], fx_source["url"], retrieved_at),
        series_id=fx_source["series_id"],
        unit=fx_source["unit"],
        source_scale=fx_source["source_scale"],
        frequency=fx_source["frequency"],
        availability=fx_source["availability"],
    )
    return parsed, normalize_fx_monthly(fx)


def build_report(config_path: Path, sources_config_path: Path, raw_dir: Path, output_dir: Path, retrieved_at: datetime) -> None:
    raw_config = json.loads(config_path.read_text(encoding="utf-8"))
    start, end = date.fromisoformat(raw_config["explicit_start"]), date.fromisoformat(raw_config["explicit_end"])
    expected = {name: [str(value) for value in weights] for name, weights in CANDIDATES.items()}
    if raw_config.get("strategies") != expected or raw_config.get("research_labels", {}).get("optimization") is not False:
        raise ValueError("config must preserve frozen candidates without optimization")
    parsed, fx_rows = _rows(raw_dir, sources_config_path, retrieved_at)
    mapping = lambda rows: {row.observation_date: row.value for row in rows}
    returns = eur_sleeve_returns(
        {name: mapping(parsed[name]) for name in ("WORLD", "MOMENTUM", "QUALITY")},
        mapping(parsed["TREND"]),
        mapping(parsed["RF"]),
        {quote.month: Decimal(str(quote.eur_per_usd)) for quote in fx_rows},
        start,
        end,
    )
    config = FourSleeveConfig(
        start,
        end,
        retrieved_at,
        int(raw_config["max_staleness_days"]),
        Decimal(raw_config["initial_capital_eur"]),
        Decimal(raw_config["commission_per_executed_sleeve_order_eur"]),
        Decimal(raw_config["spread_slippage_bps"]),
    )
    result = run_four_sleeve(returns, config)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "result.json").write_bytes(normalized_four_sleeve_json(result))
    with (output_dir / "summary.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("strategy", "observations", "ending_nav_eur", "cagr", "annualized_volatility", "return_volatility_ratio_zero_rate", "max_drawdown", "turnover", "commissions_eur", "spread_eur"))
        for item in result.summaries:
            writer.writerow((item.strategy, item.observations, item.ending_nav, item.cagr, item.annualized_volatility, item.return_volatility_ratio, item.max_drawdown, item.turnover, item.commissions_eur, item.spread_eur))
    with (output_dir / "monthly_returns.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(("month", *returns, *result.strategies))
        by_strategy = {strategy: {row.month: row.portfolio_return for row in result.rows_for(strategy)} for strategy in result.strategies}
        for month in _calendar(start, end):
            writer.writerow((month, *(returns[name][month] for name in returns), *(by_strategy[name][month] for name in result.strategies)))
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(raw_dir.iterdir()) if path.is_file()}
    generated = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output_dir.iterdir())
        if path.is_file() and path.name != "manifest.json"
    }
    project_root = Path(__file__).resolve().parents[1]
    manifest = {
        "schema_version": "FOUR_SLEEVE_REPORT_V1",
        "retrieved_at": retrieved_at.isoformat(),
        "research_label": "CURRENT_VINTAGE_RETROSPECTIVE_RESEARCH_NOT_PIT_SIGNAL",
        "window": {"start": start.isoformat(), "end": end.isoformat(), "months": len(_calendar(start, end))},
        "trend_total_return": "AQR_TSMOM_EXCESS_PLUS_FRENCH_DEVELOPED_RF",
        "currency": "EUR_UNHEDGED_USD_RETURN_CONVERTED_WITH_DEXUSEU_MONTH_END",
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "sources_config_sha256": hashlib.sha256(sources_config_path.read_bytes()).hexdigest(),
        "tce_config_sha256": hashlib.sha256((project_root / "config" / "tce_v1.json").read_bytes()).hexdigest(),
        "source_sha256": hashes,
        "generated_sha256": generated,
        "engine_sha256": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted((project_root / "perpetual_engine").glob("*.py"))
        },
    }
    (output_dir / "manifest.json").write_bytes(canonical_json(manifest))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--sources-config", type=Path, required=True)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--retrieved-at", type=datetime.fromisoformat, required=True)
    args = parser.parse_args()
    build_report(args.config, args.sources_config, args.raw_dir, args.out_dir, args.retrieved_at)


if __name__ == "__main__":
    main()
