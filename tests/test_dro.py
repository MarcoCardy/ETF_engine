from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import csv
import json
import hashlib
import shutil
import tempfile
from pathlib import Path
from unittest import TestCase

from perpetual_engine.dro_report import run_dro_report
from perpetual_engine.dro import (
    DRO_EXPOSURES,
    DRO_UNIVERSE,
    MonthlyTotalReturnObservation,
    convert_usd_monthly_levels_to_eur,
    normalize_month_end_observations,
    normalize_month_end_levels,
    parse_raw_level_csv,
    validate_vintage_manifest,
    calculate_monthly_decision,
    load_dro_config,
    summarize_strategy_returns,
    validate_monthly_observation,
)
from perpetual_engine.dro_refresh import monthly_euro_cash_returns


UTC = timezone.utc
CONFIG = Path(__file__).parents[1] / "config" / "dro_v1.json"
VINTAGE = Path(__file__).parents[1] / "data" / "dro_v1"
EXPECTED_UNIVERSE = (
    "EQAC",
    "IWVL",
    "EWSA",
    "DFNS",
    "WENE",
    "INFR",
    "SGLD",
    "CMOD",
    "IBCI",
    "DTEH",
)
EXPECTED_EXPOSURES = {
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
EXPECTED_CONDITIONAL_SOURCES = {
    "EQAC": "FRED_NASDAQXNDXNNR",
    "IWVL": "IWVL_OFFICIAL_NAV",
    "EWSA": "WSML_OFFICIAL_NAV",
    "DFNS": "FRED_NASDAQNQUSB50201020N",
    "WENE": "XDW0_OFFICIAL_NAV",
    "INFR": "INFR_OFFICIAL_NAV",
    "SGLD": "LBMA_GOLD_PM",
    "CMOD": "CMOD_OFFICIAL_NAV",
    "IBCI": "IBCI_OFFICIAL_NAV",
    "DTEH": "DTLE_OFFICIAL_NAV",
}
EXPECTED_TRADABLE_INCEPTIONS = {
    "EQAC": date(2018, 9, 24),
    "IWVL": date(2014, 10, 3),
    "EWSA": date(2021, 12, 8),
    "DFNS": date(2023, 3, 31),
    "WENE": date(2022, 4, 7),
    "INFR": date(2006, 10, 20),
    "SGLD": date(2009, 6, 24),
    "CMOD": date(2017, 1, 9),
    "IBCI": date(2005, 11, 18),
    "DTEH": date(2026, 3, 26),
}


class DroReportEndToEndTests(TestCase):
    def test_publishes_complete_history_partial_august_and_byte_identical_artifacts(self):
        """Breaks if the runner truncates, counts August as full, or serializes nondeterministically."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = root / "first", root / "second"
            run_dro_report(CONFIG, VINTAGE, first)
            run_dro_report(CONFIG, VINTAGE, second)

            self.assertEqual(
                {path.name: path.read_bytes() for path in first.iterdir()},
                {path.name: path.read_bytes() for path in second.iterdir()},
            )
            with (first / "monthly.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            result = json.loads((first / "result.json").read_text(encoding="utf-8"))
            report = (first / "report.md").read_text(encoding="utf-8")

        self.assertEqual(len(rows), 80)
        self.assertEqual((rows[0]["signal_month"], rows[-1]["signal_month"]), ("2020-01-31", "2026-08-28"))
        self.assertEqual(sum(row["period_status"] == "COMPLETE" for row in rows), 78)
        self.assertEqual(
            [(row["signal_month"], row["period_status"], row["gross_return"]) for row in rows[-2:]],
            [
                ("2026-07-31", "PARTIAL_AS_OF_2026-08-28", rows[-2]["gross_return"]),
                ("2026-08-28", "PROVISIONAL_NEXT_EXECUTION", ""),
            ],
        )
        self.assertNotIn("2026-08-31", {row["execution_month"] for row in rows})
        self.assertTrue(all(row["swda_benchmark_return"] for row in rows[:-1]))
        self.assertEqual(rows[-1]["swda_benchmark_return"], "")
        self.assertEqual(result["selection_history"], rows)
        self.assertEqual(result["metrics"]["month_count"], 78)
        self.assertEqual(result["partial_label"], "PARTIAL_AS_OF_2026-08-28")
        self.assertIn("SWDA", result["comparators"])
        self.assertIn("equal_weight_monthly", result["comparators"])
        self.assertIn("equal_weight_buy_and_hold", result["comparators"])
        self.assertEqual(result["commission_sensitivity"]["5000"]["commission_per_leg_eur"], "19")
        self.assertEqual(result["commission_sensitivity"]["80000"]["commission_per_leg_eur"], "19")
        self.assertEqual(result["commission_sensitivity"]["5000"]["per_leg_fraction"], "0.0038")
        self.assertIn("survivorship", result["caveats"][0].lower())
        self.assertIn("survivorship", report.lower())
        self.assertIn("Net volatility", report)
        self.assertIn("Calendar returns", report)
        self.assertIn("Comparators", report)
        self.assertIn("10 bps per leg", report)
        self.assertIn("€19 per leg", report)
        self.assertIn("Monthly selections", report)
        self.assertIn("2020-01-31 ->", report)
        self.assertIn("2026-08-28 ->", report)

    def test_refuses_a_vintage_with_an_unbound_normalized_file(self):
        with tempfile.TemporaryDirectory() as directory:
            vintage = Path(directory) / "vintage"
            shutil.copytree(VINTAGE, vintage)
            manifest = json.loads((VINTAGE / "manifest.json").read_text(encoding="utf-8"))
            manifest["normalized_files"][0]["sha256"] = "0" * 64
            (vintage / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "normalized file"):
                run_dro_report(CONFIG, vintage, vintage / "out")

    def test_rejects_benchmark_and_cash_metadata_or_manifest_identity_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            vintage = Path(directory) / "vintage"
            shutil.copytree(VINTAGE, vintage)
            manifest_path = vintage / "manifest.json"
            original_manifest = manifest_path.read_bytes()

            def reject_csv(filename, mutate):
                path = vintage / filename
                original = path.read_bytes()
                with path.open(newline="", encoding="utf-8") as handle:
                    rows = list(csv.DictReader(handle))
                    fields = tuple(csv.DictReader(original.decode("utf-8").splitlines()).fieldnames or ())
                mutate(rows[0])
                with path.open("w", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(rows)
                manifest = json.loads(original_manifest)
                record = next(item for item in manifest["normalized_files"] if item["path"] == filename)
                record.update(byte_count=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest(), row_count=len(rows))
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaises(ValueError):
                    run_dro_report(CONFIG, vintage, vintage / "out")
                path.write_bytes(original)
                manifest_path.write_bytes(original_manifest)

            for change in (
                lambda row: row.update(observation_date="2019-01-01"),
                lambda row: row.update(observation_date="2018-10-01"),
                lambda row: row.update(source_url="https://invalid.example/benchmark"),
                lambda row: row.update(retrieved_at="not-a-timestamp"),
            ):
                reject_csv("monthly_benchmark_total_return_eur.csv", change)
            for change in (
                lambda row: row.update(observation_date="2019-01-01"),
                lambda row: row.update(observation_date="2018-10-01"),
                lambda row: row.update(source_id="FRED_NASDAQXNDXNNR", source_hash=json.loads(original_manifest)["sources"][0]["source_hash"], source_url=json.loads(original_manifest)["sources"][0]["source_url"]),
                lambda row: row.update(retrieved_at="not-a-timestamp"),
            ):
                reject_csv("monthly_cash_return_eur.csv", change)
            manifest = json.loads(original_manifest)
            next(item for item in manifest["sources"] if item["source_id"] == "SWDA_OFFICIAL_NAV")["candidate_id"] = "EQAC"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ValueError):
                run_dro_report(CONFIG, vintage, vintage / "out")

    def test_manifest_binds_raw_files_when_a_vintage_root_is_supplied(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw"
            raw.mkdir()
            raw_path = raw / "EQAC.csv"
            raw_path.write_bytes(b"raw\n")
            normalized = []
            for filename in ("monthly_total_return_eur.csv", "monthly_benchmark_total_return_eur.csv", "monthly_cash_return_eur.csv"):
                path = root / filename
                path.write_bytes(b"header\nrow\n")
                normalized.append({"path": filename, "byte_count": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "row_count": 1})
            manifest = {
                "full_end": "2026-07-31", "partial_as_of": "2026-08-28",
                "sources": [{"source_id": "EQAC", "path": "raw/EQAC.csv", "source_hash": hashlib.sha256(raw_path.read_bytes()).hexdigest(), "byte_count": raw_path.stat().st_size}],
                "coverage": {"EQAC": ["2018-12-31", "2026-07-31", "2026-08-28"]}, "normalized_files": normalized,
            }
            args = dict(expected_sources=("EQAC",), warmup_start=date(2018, 12, 31), full_end=date(2026, 7, 31), partial_as_of=date(2026, 8, 28), vintage_root=root)
            self.assertIsNone(validate_vintage_manifest(manifest, **args))
            for broken in (
                {**manifest, "sources": [{**manifest["sources"][0], "path": "../EQAC.csv"}]},
                {**manifest, "sources": [{**manifest["sources"][0], "byte_count": 1}]},
                {**manifest, "sources": [{**manifest["sources"][0], "source_hash": "0" * 64}]},
            ):
                with self.assertRaises(ValueError):
                    validate_vintage_manifest(broken, **args)
            raw_path.write_bytes(b"edited\n")
            with self.assertRaises(ValueError):
                validate_vintage_manifest(manifest, **args)


class DroContractTests(TestCase):
    def test_config_freezes_the_complete_distinct_price_only_universe_and_history_limits(self):
        config = load_dro_config(CONFIG)

        self.assertEqual(DRO_UNIVERSE, EXPECTED_UNIVERSE)
        self.assertEqual(dict(DRO_EXPOSURES), EXPECTED_EXPOSURES)
        self.assertEqual(config.candidates, EXPECTED_UNIVERSE)
        self.assertEqual(config.warmup_start, date(2018, 12, 31))
        self.assertEqual(config.full_start, date(2020, 1, 31))
        self.assertEqual(config.full_end, date(2026, 7, 31))
        self.assertEqual((config.partial_as_of, config.partial_label), (date(2026, 8, 28), "PARTIAL_AS_OF_2026-08-28"))
        self.assertEqual(config.max_staleness_days, 45)
        self.assertEqual(config.benchmark_id, "SWDA")
        self.assertNotIn(config.benchmark_id, config.candidates)
        self.assertEqual(len(config.candidates), len(set(config.candidates)))
        self.assertEqual(len(config.sources), 21)
        self.assertEqual(dict(config.candidate_exposures), EXPECTED_EXPOSURES)
        self.assertEqual(
            {source.candidate_id: source.source_id for source in config.sources if source.track == "CONDITIONAL_INDEX_PROXY"},
            EXPECTED_CONDITIONAL_SOURCES,
        )
        self.assertEqual(
            {source.candidate_id: source.inception_date for source in config.sources if source.track == "TRADABLE_ETF"},
            EXPECTED_TRADABLE_INCEPTIONS,
        )
        self.assertEqual(next(source.coverage_caveat for source in config.sources if source.source_id == "FRED_NASDAQNQUSB50201020N"), "US_ONLY_PROXY")
        self.assertTrue(all(source.inception_date <= config.warmup_start for source in config.sources if source.track == "CONDITIONAL_INDEX_PROXY"))
        self.assertTrue(all(source.content_hash_algorithm == "SHA256" for source in config.sources))
        self.assertEqual(
            [(source.source_id, source.total_return_kind, source.total_return_required) for source in config.sources if source.total_return_kind != "ADJUSTED_TOTAL_RETURN"],
            [("LBMA_GOLD_PM", "PRICE_ONLY_NO_INCOME_ASSET", False)],
        )
        self.assertTrue(all(source.max_staleness_days == config.max_staleness_days for source in config.sources))
        self.assertEqual(config.scoring_inputs, "PRICE_BASED_TOTAL_RETURN_ONLY")
        self.assertFalse(config.shares_scores_with_tce)
        self.assertFalse(config.automatic_funding_from_structural)
        self.assertEqual(config.ranking_currency, "EUR")
        self.assertEqual(config.eur_conversion_policy, "DIVIDE_USD_LEVEL_BY_USD_PER_EUR_AT_SAME_MONTH_END")
        self.assertEqual(
            (config.fx_source.source_id, config.fx_source.pair, config.fx_source.source_url, config.fx_source.inception_date, config.fx_source.content_hash_algorithm, config.fx_source.monthly_asof_policy),
            ("FRED_DEXUSEU", "USD_PER_EUR", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXUSEU", date(1999, 1, 4), "SHA256", "MONTH_END_LAST_AVAILABLE_OBSERVATION"),
        )
        self.assertTrue(
            all(
                source.fx_conversion == ("DIVIDE_BY_USD_PER_EUR_DEXUSEU_AT_SAME_MONTH_END" if source.source_currency == "USD" else "NONE_EUR_BASE")
                for source in config.sources
            )
        )
        dteh = next(source for source in config.sources if source.source_id == "DTLE_OFFICIAL_NAV")
        self.assertEqual((dteh.ticker, dteh.inception_date, dteh.source_url), ("IE00BD8PGZ49", date(2017, 9, 21), "https://www.ishares.com/uk/individual/en/products/290713/ishares-treasury-bond-20%20yr-ucits-etf-eur-hedged-dist-fund"))
        tradable_dteh = next(source for source in config.sources if source.source_id == "DTEH_TRADABLE_OFFICIAL_NAV")
        self.assertEqual((tradable_dteh.ticker, tradable_dteh.inception_date, tradable_dteh.source_url), ("IE000T8E6SX8", date(2026, 3, 26), "https://www.ishares.com/uk/individual/en/products/349958/ishares-treasury-bond-20%20yr-ucits-etf"))

    def test_monthly_observation_is_immutable_and_requires_hashed_adjusted_total_return_provenance(self):
        observation = MonthlyTotalReturnObservation(
            candidate_id="EQAC",
            month_end=date(2020, 1, 31),
            total_return_level=Decimal("101.25"),
            source_id="QQQ_ADJUSTED_TOTAL_RETURN",
            observation_date=date(2020, 1, 31),
            available_at=date(2020, 1, 31),
            retrieved_at=datetime(2020, 2, 1, tzinfo=UTC),
            source_hash="a" * 64,
        )

        self.assertEqual(observation.total_return_level, Decimal("101.25"))
        self.assertEqual(observation.currency, "EUR")
        with self.assertRaisesRegex(ValueError, "source_hash"):
            MonthlyTotalReturnObservation(
                candidate_id="EQAC",
                month_end=date(2020, 1, 31),
                total_return_level=Decimal("101.25"),
                source_id="QQQ_ADJUSTED_TOTAL_RETURN",
                observation_date=date(2020, 1, 31),
                available_at=date(2020, 1, 31),
                retrieved_at=datetime(2020, 2, 1, tzinfo=UTC),
                source_hash="not-a-hash",
            )

    def test_config_rejects_duplicate_candidate_within_a_track_and_exposure_drift(self):
        config = load_dro_config(CONFIG)
        with self.assertRaisesRegex(ValueError, "duplicate candidate within track"):
            replace(config, sources=(*config.sources, config.sources[0]))

        tradable = next(source for source in config.sources if source.track == "TRADABLE_ETF" and source.candidate_id == "EQAC")
        changed = tuple(replace(source, exposure="WRONG") if source is tradable else source for source in config.sources)
        with self.assertRaisesRegex(ValueError, "exposure must match"):
            replace(config, sources=changed)

        both_tracks_changed = tuple(replace(source, exposure="WRONG") if source.candidate_id == "EQAC" else source for source in config.sources)
        with self.assertRaisesRegex(ValueError, "frozen candidate exposure"):
            replace(config, sources=both_tracks_changed)

        with self.assertRaisesRegex(ValueError, "fx_conversion"):
            replace(config.sources[0], fx_conversion="CONVERT_NON_EUR_LEVELS_TO_EUR_AT_SAME_MONTH_END_WITH_DECLARED_FX_SOURCE")

    def test_config_rejects_any_drift_from_the_frozen_v1_dates(self):
        config = load_dro_config(CONFIG)
        with self.assertRaisesRegex(ValueError, "frozen DRO v1 dates"):
            replace(config, full_start=date(2020, 2, 29))

    def test_config_freezes_the_august_28_partial_observation_without_extending_complete_history(self):
        config = load_dro_config(CONFIG)

        self.assertEqual((config.full_end, config.partial_as_of, config.partial_label), (date(2026, 7, 31), date(2026, 8, 28), "PARTIAL_AS_OF_2026-08-28"))

    def test_config_declares_pre_return_yfinance_fallbacks_and_ecb_cash_history(self):
        raw = json.loads(CONFIG.read_text(encoding="utf-8"))
        conditional = {source["source_id"]: source for source in raw["sources"] if source["track"] == "CONDITIONAL_INDEX_PROXY"}
        expected_fallbacks = {
            "IWVL_OFFICIAL_NAV": "IS3S.DE",
            "WSML_OFFICIAL_NAV": "IUSN.DE",
            "XDW0_OFFICIAL_NAV": "XDW0.DE",
            "INFR_OFFICIAL_NAV": "INFR.MI",
            "CMOD_OFFICIAL_NAV": "CMOD.MI",
            "IBCI_OFFICIAL_NAV": "IBCI.AS",
            "DTLE_OFFICIAL_NAV": "DTLE.L",
        }
        self.assertEqual(
            {source_id: source["programmatic_fallback"]["ticker"] for source_id, source in conditional.items() if "programmatic_fallback" in source},
            expected_fallbacks,
        )
        self.assertTrue(all(source["programmatic_fallback"]["frozen_before_return_observation"] for source in conditional.values() if "programmatic_fallback" in source))
        self.assertEqual((conditional["LBMA_GOLD_PM"]["source_url"], conditional["LBMA_GOLD_PM"]["inception_date"]), ("https://prices.lbma.org.uk/json/gold_pm.json", "1968-04-01"))
        self.assertEqual(
            {source_id: source["inception_date"] for source_id, source in conditional.items() if source_id.startswith("FRED_")},
            {"FRED_NASDAQXNDXNNR": "2011-10-10", "FRED_NASDAQNQUSB50201020N": "2012-12-03"},
        )
        benchmark = next(source for source in raw["sources"] if source["source_id"] == "SWDA_OFFICIAL_NAV")
        self.assertEqual((benchmark["programmatic_fallback"]["ticker"], benchmark["programmatic_fallback"]["listing_currency"]), ("SWDA.MI", "EUR"))
        self.assertEqual(
            [(source["source_id"], source["source_url"], source["cash_role"]) for source in raw["cash_sources"]],
            [
                ("ECB_ESTR", "https://data-api.ecb.europa.eu/service/data/EST/B.EU000A2X2A25.WT?format=csvdata", "EUR_OVERNIGHT_FROM_2019_10_01"),
                ("ECB_DEPOSIT_FACILITY", "https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV?format=csvdata", "EUR_OVERNIGHT_PREDECESSOR_BEFORE_2019_10_01"),
            ],
        )


class DroVintageTests(TestCase):
    def test_compounds_ecb_predecessor_then_estr_into_explicit_monthly_cash_returns(self):
        returns = monthly_euro_cash_returns(
            ((date(2019, 9, 1), Decimal("3.60")),),
            ((date(2019, 10, 1), Decimal("-0.50")),),
            start=date(2019, 9, 30),
            full_end=date(2019, 10, 31),
            partial_as_of=date(2019, 11, 1),
        )

        self.assertEqual(returns[date(2019, 9, 30)][0], "ECB_DEPOSIT_FACILITY")
        self.assertEqual(returns[date(2019, 10, 31)][0], "ECB_ESTR")
        self.assertNotEqual(returns[date(2019, 10, 31)][2], Decimal("0"))

    def test_parses_only_canonical_raw_level_rows(self):
        rows = parse_raw_level_csv(b"date,level\n2026-07-30,100.25\n2026-07-31,101.50\n")

        self.assertEqual(rows[-1], (date(2026, 7, 31), Decimal("101.50")))
        with self.assertRaisesRegex(ValueError, "canonical"):
            parse_raw_level_csv(b"Date,Adj Close\n2026-07-31,101.50\n")

    def test_normalizes_to_last_available_month_end_and_rejects_gaps_staleness_or_silent_final_truncation(self):
        rows = ((date(2026, 6, 30), Decimal("100")), (date(2026, 7, 31), Decimal("101")), (date(2026, 8, 28), Decimal("102")))

        normalized = normalize_month_end_levels(rows, start=date(2026, 6, 30), full_end=date(2026, 7, 31), partial_as_of=date(2026, 8, 28), max_staleness_days=45)
        self.assertEqual(normalized, {date(2026, 6, 30): Decimal("100"), date(2026, 7, 31): Decimal("101"), date(2026, 8, 28): Decimal("102")})
        self.assertEqual(normalize_month_end_observations(rows, start=date(2026, 6, 30), full_end=date(2026, 7, 31), partial_as_of=date(2026, 8, 28), max_staleness_days=45)[date(2026, 8, 28)], (date(2026, 8, 28), Decimal("102")))
        with self.assertRaisesRegex(ValueError, "missing month"):
            normalize_month_end_levels(rows[1:], start=date(2026, 6, 30), full_end=date(2026, 7, 31), partial_as_of=date(2026, 8, 28), max_staleness_days=45)
        with self.assertRaisesRegex(ValueError, "stale"):
            normalize_month_end_levels(((date(2026, 6, 1), Decimal("100")), *rows[1:]), start=date(2026, 6, 30), full_end=date(2026, 7, 31), partial_as_of=date(2026, 8, 28), max_staleness_days=20)
        with self.assertRaisesRegex(ValueError, "partial"):
            normalize_month_end_levels(rows[:-1], start=date(2026, 6, 30), full_end=date(2026, 7, 31), partial_as_of=date(2026, 8, 28), max_staleness_days=45)

    def test_converts_usd_levels_with_the_same_month_end_fx_fixing(self):
        month = date(2026, 7, 31)
        self.assertEqual(convert_usd_monthly_levels_to_eur({month: Decimal("120")}, {month: Decimal("1.2")}), {month: Decimal("100")})
        with self.assertRaisesRegex(ValueError, "FX"):
            convert_usd_monthly_levels_to_eur({month: Decimal("120")}, {})

    def test_manifest_requires_hashes_exact_coverage_and_explicit_final_limit(self):
        manifest = {
            "full_end": "2026-07-31",
            "partial_as_of": "2026-08-28",
            "sources": [{"source_id": "EQAC", "source_hash": "a" * 64}],
            "coverage": {"EQAC": ["2018-12-31", "2026-07-31", "2026-08-28"]},
        }
        self.assertIsNone(validate_vintage_manifest(manifest, expected_sources=("EQAC",), warmup_start=date(2018, 12, 31), full_end=date(2026, 7, 31), partial_as_of=date(2026, 8, 28)))
        with self.assertRaisesRegex(ValueError, "coverage"):
            validate_vintage_manifest({**manifest, "coverage": {"EQAC": ["2018-12-31", "2026-06-30", "2026-08-28"]}}, expected_sources=("EQAC",), warmup_start=date(2018, 12, 31), full_end=date(2026, 7, 31), partial_as_of=date(2026, 8, 28))

    def test_manifest_binds_all_normalized_files_to_their_actual_bytes_paths_counts_and_hashes(self):
        filenames = ("monthly_total_return_eur.csv", "monthly_benchmark_total_return_eur.csv", "monthly_cash_return_eur.csv")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "raw"
            raw.mkdir()
            raw_path = raw / "EQAC.csv"
            raw_path.write_bytes(b"raw\n")
            normalized_files = []
            for filename in filenames:
                content = b"header\nrow\n"
                path = root / filename
                path.write_bytes(content)
                normalized_files.append({"path": filename, "byte_count": len(content), "sha256": hashlib.sha256(content).hexdigest(), "row_count": 1})
            manifest = {
                "full_end": "2026-07-31", "partial_as_of": "2026-08-28",
                "sources": [{"source_id": "EQAC", "path": "raw/EQAC.csv", "source_hash": hashlib.sha256(raw_path.read_bytes()).hexdigest(), "byte_count": raw_path.stat().st_size}],
                "coverage": {"EQAC": ["2018-12-31", "2026-07-31", "2026-08-28"]},
                "normalized_files": normalized_files,
            }
            arguments = dict(expected_sources=("EQAC",), warmup_start=date(2018, 12, 31), full_end=date(2026, 7, 31), partial_as_of=date(2026, 8, 28), vintage_root=root)
            self.assertIsNone(validate_vintage_manifest(manifest, **arguments))
            path.write_bytes(b"edited\n")
            with self.assertRaisesRegex(ValueError, "normalized file"):
                validate_vintage_manifest(manifest, **arguments)
            path.write_bytes(content)
            broken = {**manifest, "normalized_files": [{**normalized_files[0], "path": "wrong.csv"}, *normalized_files[1:]]}
            with self.assertRaisesRegex(ValueError, "normalized file"):
                validate_vintage_manifest(broken, **arguments)
            broken = {**manifest, "normalized_files": [{**normalized_files[0], "row_count": 2}, *normalized_files[1:]]}
            with self.assertRaisesRegex(ValueError, "normalized file"):
                validate_vintage_manifest(broken, **arguments)
            broken = {**manifest, "normalized_files": [{**normalized_files[0], "sha256": "b" * 64}, *normalized_files[1:]]}
            with self.assertRaisesRegex(ValueError, "normalized file"):
                validate_vintage_manifest(broken, **arguments)

    def test_observation_must_link_to_its_declared_adjusted_total_return_source_and_type_safe_retrieval(self):
        config = load_dro_config(CONFIG)
        observation = MonthlyTotalReturnObservation(
            candidate_id="EQAC",
            month_end=date(2020, 1, 31),
            total_return_level=Decimal("101.25"),
            source_id="FRED_NASDAQXNDXNNR",
            observation_date=date(2020, 1, 31),
            available_at=date(2020, 1, 31),
            retrieved_at=datetime(2020, 2, 1, tzinfo=UTC),
            source_hash="a" * 64,
        )
        self.assertIsNone(validate_monthly_observation(config, observation))

        with self.assertRaisesRegex(ValueError, "not declared for candidate"):
            validate_monthly_observation(config, replace(observation, source_id="IWVL_OFFICIAL_NAV"))
        with self.assertRaisesRegex(ValueError, "retrieved_at"):
            MonthlyTotalReturnObservation(
                candidate_id="EQAC",
                month_end=date(2020, 1, 31),
                total_return_level=Decimal("101.25"),
                source_id="FRED_NASDAQXNDXNNR",
                observation_date=date(2020, 1, 31),
                available_at=date(2020, 1, 31),
                retrieved_at=date(2020, 2, 1),  # type: ignore[arg-type]
                source_hash="a" * 64,
            )


class DroMonthlySelectorTests(TestCase):
    signal_month = date(2021, 1, 31)
    execution_month = date(2021, 2, 28)

    def setUp(self):
        self.config = load_dro_config(CONFIG)
        self.source_ids = {
            source.candidate_id: source.source_id
            for source in self.config.sources
            if source.track == "CONDITIONAL_INDEX_PROXY"
        }

    def observations(self, overrides=None, order=None):
        levels = {
            date(2020, 1, 31): Decimal("100"),
            date(2020, 4, 30): Decimal("100"),
            date(2020, 5, 31): Decimal("100"),
            date(2020, 6, 30): Decimal("100"),
            date(2020, 7, 31): Decimal("100"),
            date(2020, 8, 31): Decimal("100"),
            date(2020, 9, 30): Decimal("100"),
            date(2020, 10, 31): Decimal("100"),
            date(2020, 11, 30): Decimal("100"),
            date(2020, 12, 31): Decimal("110"),
            self.signal_month: Decimal("115"),
            self.execution_month: Decimal("126.5"),
        }
        overrides = overrides or {}
        result = []
        for candidate_id in order or tuple(reversed(DRO_UNIVERSE)):
            candidate_levels = {**levels, **overrides.get(candidate_id, {})}
            for month_end, level in candidate_levels.items():
                observation = MonthlyTotalReturnObservation(
                    candidate_id=candidate_id,
                    month_end=month_end,
                    total_return_level=level,
                    source_id=self.source_ids[candidate_id],
                    observation_date=month_end,
                    available_at=month_end,
                    retrieved_at=datetime.combine(month_end, datetime.min.time(), tzinfo=UTC),
                    source_hash="a" * 64,
                )
                validate_monthly_observation(self.config, observation)
                result.append(observation)
        return tuple(result)

    def decision(self, *, cash_return, overrides=None, previous_position="CASH", partial=False):
        return calculate_monthly_decision(
            self.config,
            self.observations(overrides),
            self.signal_month,
            self.execution_month,
            previous_position=previous_position,
            cash_return=cash_return,
            is_partial=partial,
        )

    def test_ranks_on_twelve_minus_one_total_return(self):
        decision = self.decision(
            cash_return=Decimal("0"),
            overrides={
                "EQAC": {date(2020, 12, 31): Decimal("130")},
                "IWVL": {date(2020, 12, 31): Decimal("120")},
            }
        )

        self.assertEqual(decision.selected_candidate, "EQAC")
        self.assertEqual(next(item.ranking_return for item in decision.diagnostics if item.candidate_id == "EQAC"), Decimal("0.30"))

    def test_requires_current_level_above_ten_month_mean(self):
        decision = self.decision(
            cash_return=Decimal("0"),
            overrides={
                "EQAC": {
                    date(2020, 12, 31): Decimal("140"),
                    self.signal_month: Decimal("90"),
                },
                "IWVL": {date(2020, 12, 31): Decimal("120")},
            }
        )

        eqac = next(item for item in decision.diagnostics if item.candidate_id == "EQAC")
        self.assertFalse(eqac.trend_ok)
        self.assertEqual(decision.selected_candidate, "IWVL")

    def test_requires_positive_absolute_return(self):
        decision = self.decision(
            cash_return=Decimal("0"),
            overrides={
                "EQAC": {date(2020, 12, 31): Decimal("90")},
                "IWVL": {date(2020, 12, 31): Decimal("120")},
            }
        )

        eqac = next(item for item in decision.diagnostics if item.candidate_id == "EQAC")
        self.assertFalse(eqac.absolute_ok)
        self.assertEqual(decision.selected_candidate, "IWVL")

    def test_breaks_ranking_ties_by_frozen_universe_order(self):
        decision = self.decision(
            cash_return=Decimal("0"),
            overrides={
                "EQAC": {date(2020, 12, 31): Decimal("120")},
                "IWVL": {date(2020, 12, 31): Decimal("120")},
            }
        )

        self.assertEqual(decision.selected_candidate, "EQAC")

    def test_uses_cash_when_no_candidate_passes_filters(self):
        decision = self.decision(
            overrides={candidate_id: {date(2020, 12, 31): Decimal("90")} for candidate_id in DRO_UNIVERSE},
            cash_return=Decimal("0.002"),
        )

        self.assertEqual((decision.selected_candidate, decision.gross_return, decision.net_return, decision.turnover), ("CASH", Decimal("0.002"), Decimal("0.002"), Decimal("0")))

    def test_applies_selected_return_in_next_period_not_signal_period(self):
        decision = self.decision(
            cash_return=Decimal("0"),
            overrides={"EQAC": {date(2020, 12, 31): Decimal("130"), self.execution_month: Decimal("126.5")}}
        )

        self.assertEqual((decision.signal_month, decision.execution_month, decision.gross_return), (self.signal_month, self.execution_month, Decimal("0.10")))

    def test_charges_ten_basis_points_for_every_entry_and_exit_leg(self):
        decision = self.decision(
            cash_return=Decimal("0"),
            overrides={"EQAC": {date(2020, 12, 31): Decimal("130"), self.execution_month: Decimal("115")}},
            previous_position="IWVL",
        )

        self.assertEqual((decision.turnover, decision.transaction_cost, decision.net_return), (Decimal("2"), Decimal("0.002"), Decimal("-0.002")))

    def test_excludes_partial_decisions_from_strategy_metrics(self):
        complete = self.decision(cash_return=Decimal("0"), overrides={"EQAC": {date(2020, 12, 31): Decimal("130")}})
        partial = replace(complete, gross_return=Decimal("0.50"), net_return=Decimal("0.50"), is_partial=True)

        summary = summarize_strategy_returns((complete, partial))

        self.assertEqual((summary.month_count, summary.gross_compound_return, summary.net_compound_return), (1, Decimal("0.10"), Decimal("0.09890")))

    def test_rejects_stale_or_future_available_ranking_observations(self):
        observation = next(item for item in self.observations() if item.candidate_id == "EQAC" and item.month_end == self.signal_month)

        with self.assertRaisesRegex(ValueError, "stale"):
            validate_monthly_observation(
                self.config,
                replace(observation, observation_date=self.signal_month - timedelta(days=46), available_at=self.signal_month),
                signal_as_of=self.signal_month,
            )
        with self.assertRaisesRegex(ValueError, "available_at"):
            validate_monthly_observation(
                self.config,
                replace(observation, available_at=self.signal_month + timedelta(days=1)),
                signal_as_of=self.signal_month,
            )

    def test_allows_later_retrieval_when_historical_availability_is_valid(self):
        observation = next(item for item in self.observations() if item.candidate_id == "EQAC" and item.month_end == self.signal_month)

        self.assertIsNone(
            validate_monthly_observation(
                self.config,
                replace(observation, retrieved_at=datetime(2026, 8, 27, tzinfo=UTC)),
                signal_as_of=self.signal_month,
            )
        )

    def test_allows_delayed_lookback_data_available_by_the_decision_month(self):
        observations = list(self.observations())
        index = next(index for index, item in enumerate(observations) if item.candidate_id == "EQAC" and item.month_end == date(2020, 12, 31))
        observations[index] = replace(observations[index], available_at=date(2021, 1, 15))

        decision = calculate_monthly_decision(
            self.config,
            observations,
            self.signal_month,
            self.execution_month,
            cash_return=Decimal("0"),
        )

        self.assertEqual(decision.selected_candidate, "EQAC")
    def test_rejects_tradable_source_in_ranking_history(self):
        observations = list(self.observations())
        index = next(index for index, item in enumerate(observations) if item.candidate_id == "EQAC" and item.month_end == date(2020, 1, 31))
        observations[index] = replace(observations[index], source_id="EQAC_TRADABLE_OFFICIAL_NAV")

        with self.assertRaisesRegex(ValueError, "CONDITIONAL_INDEX_PROXY"):
            calculate_monthly_decision(
                self.config,
                observations,
                self.signal_month,
                self.execution_month,
                cash_return=Decimal("0"),
            )

    def test_requires_explicit_cash_return(self):
        with self.assertRaises(TypeError):
            calculate_monthly_decision(self.config, self.observations(), self.signal_month, self.execution_month)
