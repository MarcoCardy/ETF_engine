import csv
import hashlib
import io
import json
import math
import shutil
import tempfile
import types
import unittest
import zipfile
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import numpy as np

from perpetual_engine.io import canonical_json, sha256_file


class ChronosConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.config_path = self.root / "config" / "chronos_v1.json"
        self.csv_path = self.root / "outputs" / "four_sleeve_v1" / "monthly_returns.csv"
        self.manifest_path = self.csv_path.parent / "manifest.json"
        self.csv_path.parent.mkdir(parents=True)
        self._write_csv()
        self._write_manifest()
        self._write_config()

    def tearDown(self):
        self.directory.cleanup()

    def _write_csv(self, rows=None, header="month,WORLD,MOMENTUM,QUALITY,TREND,EXTRA"):
        rows = rows or (
            "2024-01-31,0.01,0.02,0.03,0.04,0.05",
            "2024-02-29,0.06,0.07,0.08,0.09,0.10",
            "2024-03-31,0.11,0.12,0.13,0.14,0.15",
        )
        self.csv_path.write_text("\n".join((header, *rows)) + "\n", encoding="utf-8")

    def _write_manifest(self):
        self.manifest_path.write_bytes(canonical_json({
            "schema_version": "FOUR_SLEEVE_REPORT_V1",
            "generated_sha256": {"monthly_returns.csv": sha256_file(self.csv_path)},
        }))

    def _write_config(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_bytes(canonical_json({
            "schema_version": "CHRONOS_CONFIG_V1",
            "model": {"id": "amazon/chronos-2", "revision": "29ec3766d36d6f73f0696f85560a422f50e8498c", "device": "cpu"},
            "target": {"csv": "outputs/four_sleeve_v1/monthly_returns.csv", "manifest": "outputs/four_sleeve_v1/manifest.json", "columns": ["WORLD", "MOMENTUM", "QUALITY", "TREND"]},
            "prediction_length": 12,
            "quantiles": [0.1, 0.5, 0.9],
            "scenario_basis_points": 100,
            "data_root": "data/chronos_v1",
            "sources": [
                {"id": "ECB_DFR", "url": "https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV?format=csvdata", "parser": "ecb_dfr", "role": "known_future"},
                {"id": "US_TREASURY_10Y", "url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10", "parser": "fred_dgs10", "role": "past_only"},
                {"id": "BRENT_RETURN", "url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU", "parser": "fred_brent", "role": "past_only"},
                {"id": "BIS_USD_CREDIT_YOY", "url": "https://data.bis.org/static/bulk/WS_GLI_csv_flat.zip", "parser": "bis_gli", "role": "past_only"},
                {"id": "US_CPI_YOY", "url": "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCNS", "parser": "fred_cpi", "role": "past_only"},
            ],
            "evaluation": {"origins": 36, "reported_horizons": [1, 3, 6, 12]},
            "bootstrap": {"block_months": 6, "resamples": 2000, "seed": 42, "confidence": 0.95},
        }))

    def test_load_chronos_config_freezes_v1_contract(self):
        from perpetual_engine.chronos_data import load_chronos_config

        config = load_chronos_config(self.config_path)
        self.assertEqual(config.path, self.config_path.resolve())
        self.assertEqual(config.project_root, self.root.resolve())
        self.assertEqual(config.model_id, "amazon/chronos-2")
        self.assertEqual(config.model_revision, "29ec3766d36d6f73f0696f85560a422f50e8498c")
        self.assertEqual((config.device, config.prediction_length, config.quantiles), ("cpu", 12, (0.1, 0.5, 0.9)))
        self.assertEqual(config.target_csv, self.csv_path.resolve())
        self.assertEqual(config.target_manifest, self.manifest_path.resolve())
        self.assertEqual(config.targets, ("WORLD", "MOMENTUM", "QUALITY", "TREND"))
        self.assertEqual(config.scenario_basis_points, 100)
        self.assertEqual(config.data_root, (self.root / "data" / "chronos_v1").resolve())
        self.assertEqual(
            tuple((source.source_id, source.url, source.parser, source.role) for source in config.sources),
            (
                ("ECB_DFR", "https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV?format=csvdata", "ecb_dfr", "known_future"),
                ("US_TREASURY_10Y", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10", "fred_dgs10", "past_only"),
                ("BRENT_RETURN", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU", "fred_brent", "past_only"),
                ("BIS_USD_CREDIT_YOY", "https://data.bis.org/static/bulk/WS_GLI_csv_flat.zip", "bis_gli", "past_only"),
                ("US_CPI_YOY", "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCNS", "fred_cpi", "past_only"),
            ),
        )
        self.assertEqual((config.evaluation_origins, config.reported_horizons), (36, (1, 3, 6, 12)))
        self.assertEqual(
            (config.bootstrap_block_months, config.bootstrap_resamples, config.bootstrap_seed, config.bootstrap_confidence),
            (6, 2000, 42, 0.95),
        )
        self.assertEqual(config.config_hash, hashlib.sha256(self.config_path.read_bytes()).hexdigest())

    def test_load_chronos_config_rejects_invalid_required_source_mappings(self):
        from perpetual_engine.chronos_data import load_chronos_config

        config = json.loads(self.config_path.read_text(encoding="utf-8"))
        invalid_mappings = (
            (0, "parser", "fred_dgs10"),
            (1, "role", "known_future"),
            (2, "role", "known_future"),
            (3, "role", "known_future"),
            (4, "role", "known_future"),
        )
        for index, field, value in invalid_mappings:
            with self.subTest(index=index, field=field):
                changed = json.loads(json.dumps(config))
                changed["sources"][index][field] = value
                self.config_path.write_bytes(canonical_json(changed))
                with self.assertRaisesRegex(ValueError, "source mapping"):
                    load_chronos_config(self.config_path)
        source_sets = {
            "extra registered past-only source": [
                *config["sources"],
                {"id": "EXTRA", "url": "https://example.test/extra", "parser": "ecb_dfr", "role": "past_only"},
            ],
            "reordered required sources": list(reversed(config["sources"])),
        }
        for name, sources in source_sets.items():
            with self.subTest(name=name):
                changed = json.loads(json.dumps(config))
                changed["sources"] = sources
                self.config_path.write_bytes(canonical_json(changed))
                with self.assertRaisesRegex(ValueError, "each v1 source"):
                    load_chronos_config(self.config_path)

    def test_load_target_table_validates_manifest_and_order(self):
        from perpetual_engine.chronos_data import load_chronos_config, load_target_table

        config = load_chronos_config(self.config_path)
        table = load_target_table(config)
        self.assertEqual(table.names, ("WORLD", "MOMENTUM", "QUALITY", "TREND"))
        self.assertEqual(table.values.shape, (4, 3))
        self.assertEqual(table.months, (date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 31)))
        self.assertAlmostEqual(float(table.values[0, 0]), 0.01)
        self.assertFalse(table.values.flags.writeable)

    def test_load_target_table_rejects_manifest_hash_mismatch(self):
        from perpetual_engine.chronos_data import load_chronos_config, load_target_table

        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        manifest["generated_sha256"]["monthly_returns.csv"] = "0" * 64
        self.manifest_path.write_bytes(canonical_json(manifest))
        with self.assertRaisesRegex(ValueError, "target hash"):
            load_target_table(load_chronos_config(self.config_path))

    def test_load_target_table_rejects_invalid_months_values_and_header(self):
        from perpetual_engine.chronos_data import load_chronos_config, load_target_table

        cases = {
            "duplicate month": ("month,WORLD,MOMENTUM,QUALITY,TREND", ("2024-01-31,0.01,0.02,0.03,0.04", "2024-01-31,0.06,0.07,0.08,0.09", "2024-03-31,0.11,0.12,0.13,0.14")),
            "missing month": ("month,WORLD,MOMENTUM,QUALITY,TREND", ("2024-01-31,0.01,0.02,0.03,0.04", "2024-03-31,0.11,0.12,0.13,0.14")),
            "non-month-end": ("month,WORLD,MOMENTUM,QUALITY,TREND", ("2024-01-30,0.01,0.02,0.03,0.04", "2024-02-29,0.06,0.07,0.08,0.09", "2024-03-31,0.11,0.12,0.13,0.14")),
            "non-finite value": ("month,WORLD,MOMENTUM,QUALITY,TREND", ("2024-01-31,nan,0.02,0.03,0.04", "2024-02-29,0.06,0.07,0.08,0.09", "2024-03-31,0.11,0.12,0.13,0.14")),
            "reordered required column": ("month,MOMENTUM,WORLD,QUALITY,TREND", ("2024-01-31,0.02,0.01,0.03,0.04", "2024-02-29,0.07,0.06,0.08,0.09", "2024-03-31,0.12,0.11,0.13,0.14")),
        }
        for name, (header, rows) in cases.items():
            with self.subTest(name=name):
                self._write_csv(rows, header)
                self._write_manifest()
                with self.assertRaises(ValueError):
                    load_target_table(load_chronos_config(self.config_path))


class ChronosCovariateTests(unittest.TestCase):
    ECB_URL = "https://data-api.ecb.europa.eu/service/data/FM/D.U2.EUR.4F.KR.DFR.LEV?format=csvdata"
    BIS_URL = "https://data.bis.org/static/bulk/WS_GLI_csv_flat.zip"
    BIS_HEADERS = (
        "FREQ:Frequency",
        "CURR_DENOM:Currency of denomination",
        "BORROWERS_CTY:Borrowers' country",
        "BORROWERS_SECTOR:Borrowers' sector",
        "LENDERS_SECTOR:Lending sector",
        "L_POS_TYPE:Position type",
        "L_INSTR:Type of instruments",
        "UNIT_MEASURE:Unit of measure",
        "TIME_PERIOD:Time period or range",
        "OBS_VALUE:Observation Value",
        "TITLE:Title",
    )

    def setUp(self):
        from perpetual_engine.chronos_data import SourceSpec, load_chronos_config

        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        config = load_chronos_config(Path("config/chronos_v1.json"))
        if len(config.sources) == 4:
            config = replace(config, sources=(*config.sources, SourceSpec(
                "US_CPI_YOY",
                "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCNS",
                "fred_cpi",
                "past_only",
            )))
        self.config = config

    def tearDown(self):
        self.directory.cleanup()

    def artifact(self, url, content, name):
        from perpetual_engine.data_sources import SourceArtifact

        path = self.root / name
        path.write_bytes(content)
        return SourceArtifact(
            url,
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            hashlib.sha256(content).hexdigest(),
            path,
            len(content),
            "test-v1",
        )

    def fred_rows(self, series_id, observations):
        from perpetual_engine.chronos_data import parse_fred_covariate

        content = ("observation_date," + series_id + "\n" + "\n".join(
            f"{observed},{value}" for observed, value in observations
        ) + "\n").encode()
        return parse_fred_covariate(self.artifact(
            f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}", content, f"{series_id}.csv"
        ), series_id)

    def ecb_artifact(self, rows):
        content = ("KEY,TIME_PERIOD,OBS_VALUE\n" + "\n".join(
            f"{key},{period},{value}" for key, period, value in rows
        ) + "\n").encode()
        return self.artifact(self.ECB_URL, content, "ecb.csv")

    def bis_artifact(self, period, value, *, dimensions=("Q", "USD", "3P", "N", "A", "I", "B"), unit="771", extra_record=None):
        path = self.root / "bis.zip"
        record = (*dimensions, unit, period, value, "US dollar credit")
        with zipfile.ZipFile(path, "w") as archive:
            records = (record,) if extra_record is None else (extra_record, record)
            archive.writestr("WS_GLI_csv_flat.csv", ",".join(self.BIS_HEADERS) + "\n" + "\n".join(map(",".join, records)) + "\n")
        content = path.read_bytes()
        return self.artifact(self.BIS_URL, content, "bis-artifact.zip")

    def rows_with(self, *, ecb=None, dgs10=None, brent=None, bis=None, cpi=None):
        from perpetual_engine.chronos_data import parse_bis_gli, parse_ecb_dfr

        return {
            "ECB_DFR": ecb or parse_ecb_dfr(self.ecb_artifact((
                ("D.U2.EUR.4F.KR.DFR.LEV", "2024-01-01", "3.0"),
                ("D.U2.EUR.4F.KR.DFR.LEV", "2024-02-15", "4.0"),
            ))),
            "US_TREASURY_10Y": dgs10 or self.fred_rows("DGS10", (
                ("2024-01-02", "4.20"), ("2024-01-03", "4.30"), ("2024-02-01", "4.40"),
                ("2024-03-01", "4.50"), ("2024-04-01", "4.60"), ("2024-05-01", "4.70"),
            )),
            "BRENT_RETURN": brent or self.fred_rows("DCOILBRENTEU", (
                ("2023-12-01", "100"), ("2024-01-01", "110"), ("2024-02-01", "121"),
                ("2024-03-01", "133.1"), ("2024-04-01", "146.41"), ("2024-05-01", "161.051"),
            )),
            "BIS_USD_CREDIT_YOY": bis or parse_bis_gli(self.bis_artifact("2023-Q3", "5.6")),
            "US_CPI_YOY": cpi or self.fred_rows("CPIAUCNS", (
                ("2022-12-01", "100"), ("2023-01-01", "100"), ("2023-02-01", "100"),
                ("2023-03-01", "100"), ("2023-04-01", "100"),
                ("2023-12-01", "110"), ("2024-01-01", "120"), ("2024-02-01", "110"),
                ("2024-03-01", "110"), ("2024-04-01", "110"),
            )),
        }

    def test_ecb_uses_last_effective_rate_and_rejects_bad_rows(self):
        from perpetual_engine.chronos_data import normalize_covariates, parse_ecb_dfr

        rows = parse_ecb_dfr(self.ecb_artifact((
            ("D.U2.EUR.4F.KR.DFR.LEV", "2024-01-01", "3.0"),
            ("D.U2.EUR.4F.KR.DFR.LEV", "2024-01-20", "3.5"),
            ("D.U2.EUR.4F.KR.DFR.LEV", "2024-02-15", "4.0"),
        )))
        table = normalize_covariates(self.config, (date(2024, 1, 31), date(2024, 2, 29)), self.rows_with(ecb=rows))
        self.assertEqual(table.values[table.names.index("ECB_DFR")].tolist(), [3.5, 4.0])
        for bad_rows in (
            (("WRONG", "2024-01-01", "3.0"),),
            (("D.U2.EUR.4F.KR.DFR.LEV", "2024-01-01", "bad"),),
            (("D.U2.EUR.4F.KR.DFR.LEV", "2024-01-01", "3.0"), ("D.U2.EUR.4F.KR.DFR.LEV", "2024-01-01", "3.1")),
        ):
            with self.subTest(bad_rows=bad_rows), self.assertRaises(ValueError):
                parse_ecb_dfr(self.ecb_artifact(bad_rows))

    def test_dgs10_stays_in_published_percentage_points(self):
        from perpetual_engine.chronos_data import normalize_covariates

        rows = self.fred_rows("DGS10", (("2024-01-02", "4.20"), ("2024-01-03", "4.30")))
        table = normalize_covariates(self.config, (date(2024, 1, 31),), self.rows_with(dgs10=rows))
        self.assertAlmostEqual(float(table.values[table.names.index("US_TREASURY_10Y"), 0]), 4.25)

    def test_dgs10_month_end_observation_is_not_available_until_next_business_day(self):
        from perpetual_engine.chronos_data import normalize_covariates
        from perpetual_engine.data_sources import treasury_available_at

        rows = self.fred_rows("DGS10", (("2024-01-31", "4.30"),))
        self.assertGreater(treasury_available_at(date(2024, 1, 31)), datetime(2024, 1, 31, 23, 59, 59, tzinfo=timezone.utc))
        with self.assertRaisesRegex(ValueError, "daily series.*unavailable"):
            normalize_covariates(self.config, (date(2024, 1, 31),), self.rows_with(dgs10=rows))

    def test_brent_uses_log_monthly_mean_return_and_seven_day_lag(self):
        from perpetual_engine.chronos_data import normalize_covariates

        rows = self.fred_rows("DCOILBRENTEU", (
            ("2023-12-01", "100"), ("2024-01-01", "110"), ("2024-01-30", "999"),
        ))
        table = normalize_covariates(self.config, (date(2024, 1, 31),), self.rows_with(brent=rows))
        self.assertAlmostEqual(float(table.values[table.names.index("BRENT_RETURN"), 0]), math.log(1.1))

    def test_bis_release_lag_and_monthly_step_carry(self):
        from perpetual_engine.chronos_data import normalize_covariates, parse_bis_gli

        rows = parse_bis_gli(self.bis_artifact("2023-Q4", "5.6"))
        self.assertEqual(rows[0].available_at, datetime(2024, 4, 30, 23, 59, 59, tzinfo=timezone.utc))
        with self.assertRaisesRegex(ValueError, "BIS.*unavailable"):
            normalize_covariates(self.config, (date(2024, 3, 31),), self.rows_with(bis=rows))
        table = normalize_covariates(self.config, (date(2024, 4, 30), date(2024, 5, 31)), self.rows_with(bis=rows))
        self.assertEqual(table.values[table.names.index("BIS_USD_CREDIT_YOY")].tolist(), [5.6, 5.6])

    def test_bis_rejects_unexpected_key_malformed_period_and_number(self):
        from perpetual_engine.chronos_data import parse_bis_gli

        unrelated = ("Q", "EUR", "3P", "N", "A", "I", "B", "771", "2023-Q4", "9.9", "unrelated")
        selected = parse_bis_gli(self.bis_artifact("2023-Q4", "5.6", extra_record=unrelated))
        self.assertEqual([float(row.value) for row in selected], [5.6])
        artifacts = (
            self.bis_artifact("2023-Q4", "5.6", dimensions=("Q", "EUR", "3P", "N", "A", "I", "B")),
            self.bis_artifact("2023-Q5", "5.6"),
            self.bis_artifact("2023-Q4", "nan"),
        )
        for artifact in artifacts:
            with self.subTest(path=artifact.local_path), self.assertRaises(ValueError):
                parse_bis_gli(artifact)

    def test_cpi_yoy_uses_headline_index_only_after_following_month_end(self):
        from perpetual_engine.chronos_data import normalize_covariates

        rows = self.fred_rows("CPIAUCNS", (("2023-01-01", "100"), ("2024-01-01", "110")))
        with self.assertRaisesRegex(ValueError, "CPI.*unavailable"):
            normalize_covariates(self.config, (date(2024, 1, 31),), self.rows_with(cpi=rows))
        table = normalize_covariates(self.config, (date(2024, 2, 29),), self.rows_with(cpi=rows))
        self.assertAlmostEqual(float(table.values[table.names.index("US_CPI_YOY"), 0]), 10.0)
        with self.assertRaises(ValueError):
            self.fred_rows("CPILFESL", (("2023-01-01", "100"), ("2024-01-01", "110")))

    def test_normalization_rejects_missing_duplicate_and_nonfinite_data(self):
        from perpetual_engine.chronos_data import normalize_covariates

        with self.assertRaisesRegex(ValueError, "target months"):
            normalize_covariates(self.config, (date(2024, 1, 31), date(2024, 3, 31)), self.rows_with())
        rows = self.fred_rows("DGS10", (("2024-01-02", "4.20"),))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            normalize_covariates(self.config, (date(2024, 1, 31),), self.rows_with(dgs10=rows + rows))
        bad_brent = self.fred_rows("DCOILBRENTEU", (("2023-12-01", "100"), ("2024-01-01", "0")))
        with self.assertRaisesRegex(ValueError, "finite"):
            normalize_covariates(self.config, (date(2024, 1, 31),), self.rows_with(brent=bad_brent))

    def test_normalization_rejects_duplicate_dates_across_artifact_hashes(self):
        from perpetual_engine.chronos_data import normalize_covariates

        rows = self.rows_with()
        for source_id, source_rows in rows.items():
            with self.subTest(source_id=source_id), self.assertRaisesRegex(ValueError, "duplicate observation date"):
                duplicate = replace(source_rows[0], source_hash="0" * 64 if source_rows[0].source_hash != "0" * 64 else "1" * 64)
                normalize_covariates(
                    self.config,
                    (date(2024, 1, 31),),
                    {**rows, source_id: (*source_rows, duplicate)},
                )

    def test_normalized_table_is_ordered_finite_and_read_only(self):
        from perpetual_engine.chronos_data import normalize_covariates

        table = normalize_covariates(self.config, (date(2024, 1, 31), date(2024, 2, 29)), self.rows_with())
        self.assertEqual(table.names, ("ECB_DFR", "US_TREASURY_10Y", "BRENT_RETURN", "BIS_USD_CREDIT_YOY", "US_CPI_YOY"))
        self.assertEqual(table.values.shape, (5, 2))
        self.assertTrue(all(math.isfinite(value) for value in table.values.flat))
        self.assertFalse(table.values.flags.writeable)


class ChronosRefreshTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.config_path = self.root / "config" / "chronos_v1.json"
        self.config_path.parent.mkdir(parents=True)
        self.config_path.write_bytes(Path("config/chronos_v1.json").read_bytes())
        self.target_csv = self.root / "outputs" / "four_sleeve_v1" / "monthly_returns.csv"
        self.target_csv.parent.mkdir(parents=True)
        self.target_csv.write_text(
            "month,WORLD,MOMENTUM,QUALITY,TREND\n"
            "2024-01-31,0.01,0.02,0.03,0.04\n"
            "2024-02-29,0.05,0.06,0.07,0.08\n",
            encoding="utf-8",
        )
        (self.target_csv.parent / "manifest.json").write_bytes(canonical_json({
            "schema_version": "FOUR_SLEEVE_REPORT_V1",
            "generated_sha256": {"monthly_returns.csv": sha256_file(self.target_csv)},
        }))
        self.at = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
        self.payloads = self._payloads()

    def tearDown(self):
        self.directory.cleanup()

    @staticmethod
    def _fred(series_id, rows):
        return ("observation_date," + series_id + "\n" + "\n".join(
            f"{observed},{value}" for observed, value in rows
        ) + "\n").encode()

    @staticmethod
    def _bis():
        fields = ChronosCovariateTests.BIS_HEADERS
        row = ("Q", "USD", "3P", "N", "A", "I", "B", "771", "2023-Q3", "5.6", "US dollar credit")
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("WS_GLI_csv_flat.csv", ",".join(fields) + "\n" + ",".join(row) + "\n")
        return output.getvalue()

    def _payloads(self):
        return {
            ChronosCovariateTests.ECB_URL: (
                "KEY,TIME_PERIOD,OBS_VALUE\n"
                "D.U2.EUR.4F.KR.DFR.LEV,2024-01-01,3.0\n"
                "D.U2.EUR.4F.KR.DFR.LEV,2024-02-15,4.0\n"
            ).encode(),
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10": self._fred("DGS10", (
                ("2024-01-02", "4.20"), ("2024-01-03", "4.30"), ("2024-02-01", "4.40"),
            )),
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DCOILBRENTEU": self._fred("DCOILBRENTEU", (
                ("2023-12-01", "100"), ("2024-01-01", "110"), ("2024-02-01", "121"),
            )),
            ChronosCovariateTests.BIS_URL: self._bis(),
            "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCNS": self._fred("CPIAUCNS", (
                ("2022-12-01", "100"), ("2023-01-01", "100"),
                ("2023-12-01", "110"), ("2024-01-01", "120"),
            )),
        }

    def test_refresh_freezes_five_sources_and_reuses_identical_vintage(self):
        from perpetual_engine.chronos_data import load_chronos_config, load_covariate_table, refresh_chronos_data

        calls = []

        def fetcher(url):
            calls.append(url)
            return self.payloads[url]

        vintage_id = refresh_chronos_data(self.config_path, fetcher=fetcher, retrieved_at=self.at)
        config = load_chronos_config(self.config_path)
        vintage = config.data_root / "vintages" / vintage_id
        manifest = json.loads((vintage / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(calls, [source.url for source in config.sources])
        self.assertEqual(manifest["availability"], {
            "ECB_DFR": "effective_date",
            "US_TREASURY_10Y": "treasury_next_business_day",
            "BRENT_RETURN": "observation_plus_7_days",
            "BIS_USD_CREDIT_YOY": "quarter_end_plus_4_months",
            "US_CPI_YOY": "following_month_end",
        })
        self.assertEqual(
            (vintage / "covariates.csv").read_text(encoding="utf-8").splitlines()[0],
            "month,ECB_DFR,US_TREASURY_10Y,BRENT_RETURN,BIS_USD_CREDIT_YOY,US_CPI_YOY",
        )
        for source in manifest["sources"]:
            self.assertEqual(sha256_file(vintage / source["raw_path"]), source["sha256"])
        self.assertEqual(sha256_file(vintage / "covariates.csv"), manifest["generated_sha256"]["covariates.csv"])
        before = {str(path.relative_to(vintage)): path.read_bytes() for path in vintage.rglob("*") if path.is_file()}

        calls.clear()
        self.assertEqual(refresh_chronos_data(self.config_path, fetcher=fetcher, retrieved_at=self.at), vintage_id)
        self.assertEqual(calls, [source.url for source in config.sources])
        self.assertEqual(before, {str(path.relative_to(vintage)): path.read_bytes() for path in vintage.rglob("*") if path.is_file()})
        table, loaded_id = load_covariate_table(config)
        self.assertEqual(loaded_id, vintage_id)
        self.assertEqual(table.names, ("ECB_DFR", "US_TREASURY_10Y", "BRENT_RETURN", "BIS_USD_CREDIT_YOY", "US_CPI_YOY"))
        self.assertEqual(table.months, (date(2024, 1, 31), date(2024, 2, 29)))
        self.assertEqual(table.values.shape, (5, 2))
        self.assertFalse(table.values.flags.writeable)

    def test_refresh_identity_binds_retrieval_metadata(self):
        from perpetual_engine.chronos_data import refresh_chronos_data

        first = refresh_chronos_data(self.config_path, fetcher=self.payloads.__getitem__, retrieved_at=self.at)
        later = refresh_chronos_data(
            self.config_path,
            fetcher=self.payloads.__getitem__,
            retrieved_at=self.at + timedelta(seconds=1),
        )

        self.assertNotEqual(later, first)

    def test_offline_load_rejects_corrupt_raw_metadata(self):
        from perpetual_engine.chronos_data import load_chronos_config, load_covariate_table, refresh_chronos_data

        vintage_id = refresh_chronos_data(self.config_path, fetcher=self.payloads.__getitem__, retrieved_at=self.at)
        config = load_chronos_config(self.config_path)
        vintage = config.data_root / "vintages" / vintage_id
        metadata = next(path for path in (vintage / "raw").rglob("*.json"))
        metadata.write_bytes(metadata.read_bytes() + b"corrupt")

        with self.assertRaisesRegex(ValueError, "raw metadata|raw files"):
            load_covariate_table(config)

    def test_staging_validation_rejects_an_unrelated_dot_staging_directory(self):
        from perpetual_engine.chronos_data import _load_vintage, load_chronos_config, refresh_chronos_data

        vintage_id = refresh_chronos_data(self.config_path, fetcher=self.payloads.__getitem__, retrieved_at=self.at)
        config = load_chronos_config(self.config_path)
        outside = self.root / "outside" / ".staging" / vintage_id
        shutil.copytree(config.data_root / "vintages" / vintage_id, outside)

        with self.assertRaisesRegex(ValueError, "staging|escapes"):
            _load_vintage(config, outside, vintage_id)

    def test_refresh_is_atomic_when_fifth_source_or_pointer_fails(self):
        from perpetual_engine.chronos_data import refresh_chronos_data

        first_id = refresh_chronos_data(self.config_path, fetcher=self.payloads.__getitem__, retrieved_at=self.at)
        data_root = self.root / "data" / "chronos_v1"
        pointer = data_root / "current_manifest.json"
        original_pointer = pointer.read_bytes()
        original_vintages = {path.name for path in (data_root / "vintages").iterdir()}
        bad = dict(self.payloads)
        bad["https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCNS"] = b"not,cpi\n"
        with self.assertRaises(ValueError):
            refresh_chronos_data(self.config_path, fetcher=bad.__getitem__, retrieved_at=self.at)
        self.assertEqual(pointer.read_bytes(), original_pointer)
        self.assertEqual({path.name for path in (data_root / "vintages").iterdir()}, original_vintages)
        self.assertTrue((data_root / "vintages" / first_id).is_dir())

        changed = dict(self.payloads)
        changed["https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"] = self._fred("DGS10", (
            ("2024-01-02", "4.10"), ("2024-02-01", "4.20"),
        ))
        with patch("perpetual_engine.chronos_data._replace_current_pointer", side_effect=OSError("pointer failed")):
            with self.assertRaises(ValueError):
                refresh_chronos_data(self.config_path, fetcher=changed.__getitem__, retrieved_at=self.at)
        self.assertEqual(pointer.read_bytes(), original_pointer)
        self.assertEqual({path.name for path in (data_root / "vintages").iterdir()}, original_vintages)

    def test_offline_load_rejects_corrupt_raw_normalized_manifest_and_path_escape(self):
        from perpetual_engine.chronos_data import load_chronos_config, load_covariate_table, refresh_chronos_data

        vintage_id = refresh_chronos_data(self.config_path, fetcher=self.payloads.__getitem__, retrieved_at=self.at)
        config = load_chronos_config(self.config_path)
        vintage = config.data_root / "vintages" / vintage_id
        manifest_path = vintage / "manifest.json"
        pointer_path = config.data_root / "current_manifest.json"

        source = json.loads(manifest_path.read_text(encoding="utf-8"))["sources"][0]
        raw_path = vintage / source["raw_path"]
        original = raw_path.read_bytes()
        raw_path.write_bytes(original + b"corrupt")
        with self.assertRaisesRegex(ValueError, "raw hash"):
            load_covariate_table(config)
        raw_path.write_bytes(original)

        csv_path = vintage / "covariates.csv"
        original = csv_path.read_bytes()
        csv_path.write_bytes(original + b"corrupt")
        with self.assertRaisesRegex(ValueError, "normalized hash"):
            load_covariate_table(config)
        csv_path.write_bytes(original)

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["config_hash"] = "0" * 64
        manifest_path.write_bytes(canonical_json(manifest))
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        pointer["manifest_sha256"] = sha256_file(manifest_path)
        pointer_path.write_bytes(canonical_json(pointer))
        with self.assertRaisesRegex(ValueError, "config hash"):
            load_covariate_table(config)

        pointer["vintage_id"] = "../outside"
        pointer_path.write_bytes(canonical_json(pointer))
        with self.assertRaisesRegex(ValueError, "vintage ID"):
            load_covariate_table(config)

    def test_offline_load_rejects_self_consistent_bad_header_and_nonfinite_value(self):
        from perpetual_engine.chronos_data import load_chronos_config, load_covariate_table, refresh_chronos_data

        original_id = refresh_chronos_data(self.config_path, fetcher=self.payloads.__getitem__, retrieved_at=self.at)
        config = load_chronos_config(self.config_path)
        vintages = config.data_root / "vintages"
        availability = {
            "ECB_DFR": "effective_date",
            "US_TREASURY_10Y": "treasury_next_business_day",
            "BRENT_RETURN": "observation_plus_7_days",
            "BIS_USD_CREDIT_YOY": "quarter_end_plus_4_months",
            "US_CPI_YOY": "following_month_end",
        }
        cases = (
            ("header", lambda text: text.replace("month,ECB_DFR", "month,US_TREASURY_10Y", 1)),
            ("finite", lambda text: text.replace("2024-01-31,3", "2024-01-31,nan", 1)),
        )
        for index, (message, mutate) in enumerate(cases):
            with self.subTest(message=message):
                candidate = vintages / f"candidate-{index}"
                shutil.copytree(vintages / original_id, candidate)
                csv_path = candidate / "covariates.csv"
                csv_path.write_text(mutate(csv_path.read_text(encoding="utf-8")), encoding="utf-8", newline="")
                manifest = json.loads((candidate / "manifest.json").read_text(encoding="utf-8"))
                normalized_hash = sha256_file(csv_path)
                manifest["generated_sha256"]["covariates.csv"] = normalized_hash
                identity = {
                    "config_hash": config.config_hash,
                    "sources": [
                        {"id": source["id"], "url": source["url"], "sha256": source["sha256"]}
                        for source in manifest["sources"]
                    ],
                    "raw_files_sha256": manifest["raw_files_sha256"],
                    "availability": availability,
                    "covariates_sha256": normalized_hash,
                }
                candidate_id = hashlib.sha256(canonical_json(identity)).hexdigest()
                manifest["vintage_id"] = candidate_id
                manifest_path = candidate / "manifest.json"
                manifest_path.write_bytes(canonical_json(manifest))
                destination = vintages / candidate_id
                candidate.rename(destination)
                (config.data_root / "current_manifest.json").write_bytes(canonical_json({
                    "schema_version": "CHRONOS_CURRENT_V1",
                    "vintage_id": candidate_id,
                    "manifest_sha256": sha256_file(destination / "manifest.json"),
                }))
                with self.assertRaisesRegex(ValueError, message):
                    load_covariate_table(config)


class ChronosForecastTests(unittest.TestCase):
    TARGETS = ("WORLD", "MOMENTUM", "QUALITY", "TREND")
    COVARIATES = ("ECB_DFR", "US_TREASURY_10Y", "BRENT_RETURN", "BIS_USD_CREDIT_YOY", "US_CPI_YOY")

    @staticmethod
    def _tables():
        from perpetual_engine.chronos_data import MonthlyTable

        targets = MonthlyTable(
            (date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 31), date(2024, 4, 30)),
            ChronosForecastTests.TARGETS,
            np.arange(16, dtype=float).reshape(4, 4),
        )
        covariates = MonthlyTable(
            (date(2024, 2, 29), date(2024, 3, 31), date(2024, 4, 30), date(2024, 5, 31)),
            ChronosForecastTests.COVARIATES,
            np.arange(20, dtype=float).reshape(5, 4),
        )
        return targets, covariates

    def test_ecb_scenarios_are_the_frozen_twelve_month_paths(self):
        from perpetual_engine.chronos import ecb_scenarios

        scenarios = ecb_scenarios(3.0, 100, 12)

        self.assertEqual(tuple(scenarios), ("ECB_FLAT", "ECB_DOWN_100BP", "ECB_UP_100BP"))
        np.testing.assert_allclose(scenarios["ECB_FLAT"], np.full(12, 3.0))
        np.testing.assert_allclose(scenarios["ECB_DOWN_100BP"], 3.0 - np.arange(1, 13) / 12)
        np.testing.assert_allclose(scenarios["ECB_UP_100BP"], 3.0 + np.arange(1, 13) / 12)
        self.assertEqual(scenarios["ECB_DOWN_100BP"][-1], 2.0)
        self.assertEqual(scenarios["ECB_UP_100BP"][-1], 4.0)
        for args in ((3.0, 50, 12), (3.0, 100, 6), (float("nan"), 100, 12)):
            with self.subTest(args=args), self.assertRaises(ValueError):
                ecb_scenarios(*args)

    def test_inputs_use_only_complete_common_history_and_future_ecb(self):
        from perpetual_engine.chronos import build_forecast_inputs, ecb_scenarios

        targets, covariates = self._tables()
        items = build_forecast_inputs(targets, covariates, ecb_scenarios(2.5, 100, 12))

        self.assertEqual(len(items), 3)
        for item in items:
            self.assertEqual(set(item), {"target", "past_covariates", "future_covariates"})
            self.assertEqual(item["target"].shape, (4, 3))
            self.assertEqual(tuple(item["past_covariates"]), self.COVARIATES)
            self.assertTrue(all(values.shape == (3,) for values in item["past_covariates"].values()))
            self.assertEqual(tuple(item["future_covariates"]), ("ECB_DFR",))
            self.assertEqual(item["future_covariates"]["ECB_DFR"].shape, (12,))
            self.assertEqual(item["target"].dtype, np.float32)
        np.testing.assert_array_equal(items[0]["target"], targets.values[:, 1:])
        np.testing.assert_array_equal(items[0]["past_covariates"]["ECB_DFR"], covariates.values[0, :3])

    def test_fake_scenario_forecast_produces_144_ordered_month_end_rows(self):
        from perpetual_engine.chronos import run_scenario_forecasts
        from perpetual_engine.chronos_data import load_chronos_config

        config = load_chronos_config(Path("config/chronos_v1.json"))
        targets, covariates = self._tables()
        calls = []

        def fake_predictor(items, prediction_length, quantile_levels):
            calls.append(items)
            self.assertEqual(prediction_length, 12)
            self.assertEqual(quantile_levels, [0.1, 0.5, 0.9])
            return [
                np.stack((np.full((4, 12), -0.01), np.zeros((4, 12)), np.full((4, 12), 0.01)), axis=-1)
                for _ in items
            ]

        with patch("perpetual_engine.chronos.load_target_table", return_value=targets), patch(
            "perpetual_engine.chronos.load_covariate_table", return_value=(covariates, "a" * 64)
        ):
            rows = run_scenario_forecasts(config, fake_predictor)

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(rows), 144)
        self.assertEqual(
            [(row.scenario, row.target, row.horizon) for row in rows],
            [
                (scenario, target, horizon)
                for scenario in ("ECB_FLAT", "ECB_DOWN_100BP", "ECB_UP_100BP")
                for target in self.TARGETS
                for horizon in range(1, 13)
            ],
        )
        self.assertEqual(rows[0].origin, date(2024, 4, 30))
        self.assertEqual(rows[0].forecast_month, date(2024, 5, 31))
        self.assertEqual(rows[11].forecast_month, date(2025, 4, 30))
        self.assertEqual((rows[0].q10, rows[0].q50, rows[0].q90), (-0.01, 0.0, 0.01))

    def test_scenario_forecast_rejects_bad_predictor_results(self):
        from perpetual_engine.chronos import run_scenario_forecasts
        from perpetual_engine.chronos_data import load_chronos_config

        config = load_chronos_config(Path("config/chronos_v1.json"))
        targets, covariates = self._tables()
        good = np.zeros((4, 12, 3))
        bad_results = (
            [good, good],
            [np.zeros((4, 11, 3))] * 3,
            [np.full((4, 12, 3), np.nan)] * 3,
            [np.stack((np.ones((4, 12)), np.zeros((4, 12)), np.ones((4, 12))), axis=-1)] * 3,
        )
        with patch("perpetual_engine.chronos.load_target_table", return_value=targets), patch(
            "perpetual_engine.chronos.load_covariate_table", return_value=(covariates, "a" * 64)
        ):
            for result in bad_results:
                with self.subTest(shape=np.asarray(result[0]).shape), self.assertRaises(ValueError):
                    run_scenario_forecasts(config, lambda *_args, result=result, **_kwargs: result)

    def test_model_loader_is_exact_offline_cpu_and_normalizes_installed_tuple(self):
        from perpetual_engine.chronos import load_chronos_predictor
        from perpetual_engine.chronos_data import load_chronos_config

        calls = {}

        class FakeTensor:
            def __init__(self, values):
                self.values = values

            def detach(self):
                calls["detach"] = True
                return self

            def cpu(self):
                calls["cpu"] = True
                return self

            def numpy(self):
                return self.values

        class FakePipeline:
            @classmethod
            def from_pretrained(cls, model_id, **kwargs):
                calls["load"] = (model_id, kwargs)
                return cls()

            def predict_quantiles(self, items, **kwargs):
                calls["predict"] = (items, kwargs)
                return [FakeTensor(np.zeros((4, 12, 3))) for _ in items], None

        fake_module = types.ModuleType("chronos")
        fake_module.Chronos2Pipeline = FakePipeline
        config = load_chronos_config(Path("config/chronos_v1.json"))
        with patch.dict("sys.modules", {"chronos": fake_module}):
            predictor = load_chronos_predictor(config)
            result = predictor([{"target": np.zeros((4, 3))}], 12, [0.1, 0.5, 0.9])

        self.assertEqual(calls["load"], (
            "amazon/chronos-2",
            {
                "revision": "29ec3766d36d6f73f0696f85560a422f50e8498c",
                "device_map": "cpu",
                "local_files_only": True,
            },
        ))
        self.assertEqual(calls["predict"][1], {
            "prediction_length": 12,
            "quantile_levels": [0.1, 0.5, 0.9],
            "batch_size": 1,
        })
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].shape, (4, 12, 3))
        self.assertTrue(calls["detach"] and calls["cpu"])


class ChronosEvaluationTests(unittest.TestCase):
    COVARIATES = ("ECB_DFR", "US_TREASURY_10Y", "BRENT_RETURN", "BIS_USD_CREDIT_YOY", "US_CPI_YOY")

    def setUp(self):
        from perpetual_engine.chronos_data import MonthlyTable, load_chronos_config

        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        self.output = self.root / "evaluation"
        base = load_chronos_config(Path("config/chronos_v1.json"))
        target_csv = self.root / "monthly_returns.csv"
        target_csv.write_text("frozen target bytes\n", encoding="utf-8")
        target_manifest = self.root / "target_manifest.json"
        target_manifest.write_text("{}\n", encoding="utf-8")
        self.config = replace(
            base,
            path=self.root / "config" / "chronos_v1.json",
            project_root=self.root,
            target_csv=target_csv,
            target_manifest=target_manifest,
            data_root=self.root / "data" / "chronos_v1",
            config_hash="c" * 64,
        )
        months = []
        current = date(2006, 6, 30)
        for _ in range(240):
            months.append(current)
            first = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
            following = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
            current = following - timedelta(days=1)
        indices = np.arange(1, 241, dtype=float)
        target_values = np.vstack([(indices ** 2) * (target + 1) * 1e-6 for target in range(4)])
        covariate_values = np.vstack([indices + covariate * 1000 for covariate in range(5)])
        self.targets = MonthlyTable(tuple(months), ("WORLD", "MOMENTUM", "QUALITY", "TREND"), target_values)
        self.covariates = MonthlyTable(tuple(months), self.COVARIATES, covariate_values)
        self.calls = []

    def tearDown(self):
        self.directory.cleanup()

    def fake_predictor(self, items, prediction_length, quantile_levels):
        self.assertEqual((prediction_length, quantile_levels), (12, [0.1, 0.5, 0.9]))
        self.assertEqual(len(items), 36)
        signatures = {
            (tuple(item.get("past_covariates", ())), tuple(item.get("future_covariates", ())))
            for item in items
        }
        self.assertEqual(len(signatures), 1)
        signature = signatures.pop()
        self.calls.append((signature, [item["target"].shape[1] for item in items]))
        past, future = signature
        results = []
        for item in items:
            origin_index = item["target"].shape[1] - 1
            for name, values in item.get("past_covariates", {}).items():
                covariate_index = self.COVARIATES.index(name)
                self.assertEqual(values.shape, (origin_index + 1,))
                self.assertEqual(values[-1], self.covariates.values[covariate_index, origin_index])
            if future:
                np.testing.assert_array_equal(
                    item["future_covariates"]["ECB_DFR"],
                    self.covariates.values[0, origin_index + 1:origin_index + 13],
                )
            exact = np.asarray([
                [((origin_index + horizon + 1) ** 2) * (target + 1) * 1e-6 for horizon in range(1, 13)]
                for target in range(4)
            ])
            if future:
                offset = 0.0
            elif not past:
                offset = 0.004
            elif len(past) == 1:
                offset = 0.002
            elif len(past) == 5:
                offset = 0.001
            else:
                missing = next(name for name in self.COVARIATES if name not in past)
                offset = {"ECB_DFR": 0.003, "US_TREASURY_10Y": 0.0, "BRENT_RETURN": 0.001}.get(missing, 0.003)
            results.append(np.stack((exact + offset - 0.002, exact + offset, exact + offset + 0.002), axis=-1))
        return results

    def _evaluate(self):
        from perpetual_engine.chronos import evaluate_chronos

        with patch("perpetual_engine.chronos.load_chronos_config", return_value=self.config), patch(
            "perpetual_engine.chronos.load_target_snapshot", return_value=(
                self.targets,
                sha256_file(self.config.target_csv),
                sha256_file(self.config.target_manifest),
            )
        ), patch("perpetual_engine.chronos.load_covariate_table", return_value=(self.covariates, "v" * 64)):
            return evaluate_chronos(self.config.path, self.output, self.fake_predictor)

    def _write_target_bundle(self):
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(("month", "WORLD", "MOMENTUM", "QUALITY", "TREND"))
        for month_index, month in enumerate(self.targets.months):
            writer.writerow((month.isoformat(), *(self.targets.values[target, month_index] for target in range(4))))
        self.config.target_csv.write_bytes(output.getvalue().encode("utf-8"))
        self.config.target_manifest.write_bytes(canonical_json({
            "schema_version": "FOUR_SLEEVE_REPORT_V1",
            "generated_sha256": {"monthly_returns.csv": sha256_file(self.config.target_csv)},
        }))

    def test_metric_variant_and_paired_bootstrap_contracts(self):
        from perpetual_engine.chronos import evaluation_variants, moving_block_interval, pinball_loss

        np.testing.assert_allclose(
            pinball_loss(np.array([1.0, -1.0]), np.array([0.0, 0.0]), 0.1),
            np.array([0.1, 0.9]),
        )
        variants = evaluation_variants(self.COVARIATES)
        self.assertEqual(len(variants), 12)
        self.assertEqual(variants[0], ("TARGET_ONLY", ()))
        self.assertIn(("TARGET_PLUS_ECB_DFR", ("ECB_DFR",)), variants)
        self.assertIn(("FULL", self.COVARIATES), variants)
        self.assertIn(("FULL_MINUS_US_CPI_YOY", self.COVARIATES[:-1]), variants)
        positive = moving_block_interval(np.ones(36), self.config)
        negative = moving_block_interval(-np.ones(36), self.config)
        crossing = moving_block_interval(np.r_[-np.ones(18), np.ones(18)], self.config)
        self.assertEqual(positive, (1.0, 1.0))
        self.assertEqual(negative, (-1.0, -1.0))
        self.assertLess(crossing[0], 0.0)
        self.assertGreater(crossing[1], 0.0)
        self.assertEqual(crossing, moving_block_interval(np.r_[-np.ones(18), np.ones(18)], self.config))

    def test_walk_forward_batches_outputs_metrics_contributions_and_volatility(self):
        destination = self._evaluate()

        self.assertEqual(destination, self.output)
        self.assertEqual(len(self.calls), 13)
        self.assertTrue(all(lengths == list(range(193, 229)) for _signature, lengths in self.calls))
        operational = [signature for signature, _lengths in self.calls if not signature[1]]
        oracle = [signature for signature, _lengths in self.calls if signature[1]]
        self.assertEqual(len(operational), 12)
        self.assertEqual(oracle, [((self.COVARIATES), ("ECB_DFR",))])

        with (destination / "predictions.csv").open(newline="", encoding="utf-8") as handle:
            predictions = list(csv.DictReader(handle))
        self.assertEqual(len(predictions), 14 * 36 * 4 * 12)
        self.assertEqual(predictions[0]["origin"], "2022-06-30")
        self.assertEqual(predictions[-1]["origin"], "2025-05-31")
        self.assertEqual({row["variant"] for row in predictions}, {
            "TARGET_ONLY", "TARGET_PLUS_ECB_DFR", "TARGET_PLUS_US_TREASURY_10Y",
            "TARGET_PLUS_BRENT_RETURN", "TARGET_PLUS_BIS_USD_CREDIT_YOY", "TARGET_PLUS_US_CPI_YOY",
            "FULL", "FULL_MINUS_ECB_DFR", "FULL_MINUS_US_TREASURY_10Y", "FULL_MINUS_BRENT_RETURN",
            "FULL_MINUS_BIS_USD_CREDIT_YOY", "FULL_MINUS_US_CPI_YOY",
            "ZERO_RETURN_BASELINE",
            "ORACLE_FUTURE_RATE_UPPER_BOUND",
        })

        with (destination / "metrics.csv").open(newline="", encoding="utf-8") as handle:
            metrics = list(csv.DictReader(handle))
        self.assertEqual(len(metrics), 14 * 4 * 5)
        baseline = next(row for row in metrics if row["variant"] == "ZERO_RETURN_BASELINE" and row["target"] == "WORLD" and row["horizon"] == "1")
        self.assertAlmostEqual(float(baseline["mae_q50"]), 0.04484016666666666)
        self.assertAlmostEqual(float(baseline["mean_pinball_loss"]), 0.02242008333333333)
        self.assertEqual(float(baseline["interval_80_coverage"]), 0.0)

        with (destination / "covariate_contribution.csv").open(newline="", encoding="utf-8") as handle:
            contributions = list(csv.DictReader(handle))
        self.assertEqual(len(contributions), 5 * 2 * 5)
        conditional = {row["covariate"]: row for row in contributions if row["comparison"] == "CONDITIONAL" and row["horizon"] == "ALL"}
        self.assertEqual(conditional["ECB_DFR"]["classification"], "USEFUL")
        self.assertEqual(conditional["US_TREASURY_10Y"]["classification"], "HARMFUL")
        self.assertEqual(conditional["BRENT_RETURN"]["classification"], "INCONCLUSIVE")
        self.assertTrue(all(row["classification"] == "NOT_CLASSIFIED" for row in contributions if row["comparison"] == "STANDALONE"))

        with (destination / "volatility_diagnostics.csv").open(newline="", encoding="utf-8") as handle:
            diagnostics = list(csv.DictReader(handle))
        self.assertEqual(len(diagnostics), 36 * 4 * 12)
        first = diagnostics[0]
        expected_volatility = np.std(self.targets.values[0, 181:193], ddof=1) * math.sqrt(12)
        self.assertAlmostEqual(float(first["trailing_volatility_12m"]), expected_volatility)
        self.assertEqual(first["volatility_tercile"], "LOW")
        self.assertAlmostEqual(float(first["interval_width"]), 0.004)
        self.assertAlmostEqual(float(first["signed_error"]), -0.001)
        self.assertAlmostEqual(float(first["absolute_error"]), 0.001)
        self.assertEqual(first["interval_hit"], "true")
        self.assertEqual(diagnostics[-1]["volatility_tercile"], "HIGH")

        manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["evaluation_origins"], {"count": 36, "first": "2022-06-30", "last": "2025-05-31"})
        self.assertEqual(manifest["labels"], {
            "evaluation": "RETROSPECTIVE_WALK_FORWARD_RESEARCH",
            "oracle": "ORACLE_FUTURE_RATE_UPPER_BOUND",
        })
        self.assertEqual(manifest["bootstrap"], {"block_months": 6, "resamples": 2000, "seed": 42, "confidence": 0.95})
        self.assertEqual(manifest["target_csv_sha256"], sha256_file(self.config.target_csv))
        self.assertEqual(manifest["target_manifest_sha256"], sha256_file(self.config.target_manifest))
        self.assertEqual(set(manifest["volatility_thresholds"]), {"WORLD", "MOMENTUM", "QUALITY", "TREND"})
        self.assertEqual(set(manifest["generated_sha256"]), {
            "predictions.csv", "metrics.csv", "covariate_contribution.csv", "volatility_diagnostics.csv"
        })
        for name, digest in manifest["generated_sha256"].items():
            self.assertEqual(sha256_file(destination / name), digest)

    def test_evaluation_publication_reuses_identical_bytes_and_rejects_collision_or_escape(self):
        first = self._evaluate()
        before = {path.name: path.read_bytes() for path in first.iterdir()}
        self._evaluate()
        self.assertEqual(before, {path.name: path.read_bytes() for path in first.iterdir()})
        (first / "predictions.csv").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "collision"):
            self._evaluate()

        from perpetual_engine.chronos import evaluate_chronos
        with patch("perpetual_engine.chronos.load_chronos_config", return_value=self.config):
            with self.assertRaisesRegex(ValueError, "escapes"):
                evaluate_chronos(self.config.path, self.root.parent / "outside", self.fake_predictor)

    def test_atomic_publisher_validates_staged_hashes_and_reports_file_collision(self):
        from perpetual_engine.chronos import _publish_evaluation

        payload = b"stable\n"
        files = {
            "data.csv": payload,
            "manifest.json": canonical_json({"generated_sha256": {"data.csv": hashlib.sha256(payload).hexdigest()}}),
        }
        real_write = Path.write_bytes

        def corrupt_stage(path, data):
            if path.name == "data.csv" and path.parent.name.startswith(".atomic-"):
                data += b"corrupt"
            return real_write(path, data)

        atomic = self.root / "atomic"
        with patch.object(Path, "write_bytes", corrupt_stage):
            with self.assertRaisesRegex(ValueError, "hash"):
                _publish_evaluation(atomic, files)
        self.assertFalse(atomic.exists())

        occupied = self.root / "occupied"
        occupied.write_text("file", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "collision"):
            _publish_evaluation(occupied, files)
        with self.assertRaisesRegex(ValueError, "collision"):
            _publish_evaluation(occupied / "child", files)

    def test_target_snapshot_returns_hashes_for_the_exact_bytes_it_parses(self):
        from perpetual_engine.chronos_data import load_target_snapshot

        self._write_target_bundle()
        table, target_hash, manifest_hash = load_target_snapshot(self.config)

        np.testing.assert_array_equal(table.values, self.targets.values)
        self.assertEqual(target_hash, sha256_file(self.config.target_csv))
        self.assertEqual(manifest_hash, sha256_file(self.config.target_manifest))

    def test_evaluation_rejects_target_changed_after_analysis_before_publication(self):
        from perpetual_engine.chronos import evaluate_chronos

        self._write_target_bundle()
        mutated = False

        def mutate_after_load(items, prediction_length, quantile_levels):
            nonlocal mutated
            if not mutated:
                self.config.target_csv.write_bytes(self.config.target_csv.read_bytes() + b"changed")
                mutated = True
            return self.fake_predictor(items, prediction_length, quantile_levels)

        with patch("perpetual_engine.chronos.load_chronos_config", return_value=self.config), patch(
            "perpetual_engine.chronos.load_covariate_table", return_value=(self.covariates, "v" * 64)
        ):
            with self.assertRaisesRegex(ValueError, "target.*changed"):
                evaluate_chronos(self.config.path, self.output, mutate_after_load)
        self.assertFalse(self.output.exists())


class ChronosMonitoringTests(unittest.TestCase):
    TARGETS = ("WORLD", "MOMENTUM", "QUALITY", "TREND")
    COVARIATES = ("ECB_DFR", "US_TREASURY_10Y", "BRENT_RETURN", "BIS_USD_CREDIT_YOY", "US_CPI_YOY")

    def setUp(self):
        from perpetual_engine.chronos_data import MonthlyTable, load_chronos_config

        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        self.output_root = self.root / "outputs" / "chronos_v1"
        base = load_chronos_config(Path("config/chronos_v1.json"))
        target_csv = self.root / "inputs" / "monthly_returns.csv"
        target_csv.parent.mkdir(parents=True)
        target_csv.write_bytes(b"target-v1\n")
        target_manifest = target_csv.parent / "manifest.json"
        target_manifest.write_bytes(b"manifest-v1\n")
        self.config = replace(
            base,
            path=self.root / "config" / "chronos_v1.json",
            project_root=self.root,
            target_csv=target_csv,
            target_manifest=target_manifest,
            data_root=self.root / "data" / "chronos_v1",
            config_hash="c" * 64,
        )
        months = []
        current = date(2022, 2, 28)
        for _ in range(24):
            months.append(current)
            first = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
            following = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
            current = following - timedelta(days=1)
        indices = np.arange(24, dtype=float)
        self.targets = MonthlyTable(
            tuple(months), self.TARGETS, np.vstack([indices / 1000 + target / 100 for target in range(4)])
        )
        self.covariates = MonthlyTable(
            tuple(months), self.COVARIATES, np.vstack([indices + covariate * 100 for covariate in range(5)])
        )
        self.vintage_id = "a" * 64
        self.predict_calls = 0

    def tearDown(self):
        self.directory.cleanup()

    def fake_predictor(self, items, prediction_length, quantile_levels):
        self.predict_calls += 1
        self.assertEqual((len(items), prediction_length, quantile_levels), (3, 12, [0.1, 0.5, 0.9]))
        results = []
        for scenario_index in range(3):
            median = np.asarray([
                [target / 100 + horizon / 1000 + scenario_index / 100 for horizon in range(1, 13)]
                for target in range(4)
            ])
            width = 0.01 + scenario_index / 100
            results.append(np.stack((median - width, median, median + width), axis=-1))
        return results

    def _patch_inputs(self, targets=None):
        targets = targets or self.targets
        return (
            patch("perpetual_engine.chronos.load_chronos_config", return_value=self.config),
            patch("perpetual_engine.chronos.load_target_snapshot", return_value=(
                targets, sha256_file(self.config.target_csv), sha256_file(self.config.target_manifest)
            )),
            patch("perpetual_engine.chronos.load_target_table", return_value=targets),
            patch("perpetual_engine.chronos.load_covariate_table", return_value=(self.covariates, self.vintage_id)),
            patch("perpetual_engine.chronos.load_covariate_vintage", return_value=self.covariates, create=True),
        )

    def _publish(self, issued_at=datetime(2024, 2, 1, 12, tzinfo=timezone.utc)):
        from perpetual_engine.chronos import publish_forecast

        patches = self._patch_inputs()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            return publish_forecast(
                self.config.path, self.output_root, predictor=self.fake_predictor, issued_at=issued_at
            )

    def _extended_targets(self, count):
        from perpetual_engine.chronos_data import MonthlyTable

        months = list(self.targets.months)
        columns = [list(row) for row in self.targets.values]
        for offset in range(1, count + 1):
            month = date(2024, offset + 1, 29 if offset == 1 else 31)
            months.append(month)
            for target in range(4):
                columns[target].append(target / 100 + offset / 1000 + 0.005)
        return MonthlyTable(tuple(months), self.TARGETS, np.asarray(columns))

    def test_identity_and_reconciliation_formulas_are_deterministic_and_fail_on_duplicates(self):
        from perpetual_engine.chronos import IssuedForecastRow, forecast_id, reconcile_rows

        first = forecast_id(self.config, "d" * 64, self.vintage_id, date(2024, 1, 31))
        second = forecast_id(self.config, "d" * 64, self.vintage_id, date(2024, 1, 31))
        changed = forecast_id(self.config, "e" * 64, self.vintage_id, date(2024, 1, 31))
        self.assertEqual(first, second)
        self.assertNotEqual(first, changed)
        self.assertRegex(first, r"^[0-9a-f]{64}$")

        issued = IssuedForecastRow(
            first, datetime(2024, 2, 1, tzinfo=timezone.utc), date(2024, 1, 31), "ECB_FLAT", "WORLD",
            date(2024, 2, 29), 1, -0.02, 0.01, 0.03,
        )
        rows = reconcile_rows((issued,), {date(2024, 2, 29): {"WORLD": 0.03}})
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0].signed_error, 0.02)
        self.assertAlmostEqual(rows[0].absolute_error, 0.02)
        self.assertAlmostEqual(rows[0].squared_error, 0.0004)
        self.assertTrue(rows[0].interval_hit)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            reconcile_rows((issued, issued), {date(2024, 2, 29): {"WORLD": 0.03}})

    def test_forecast_publication_is_immutable_preserves_first_issue_and_uses_one_inference(self):
        forecast_id = self._publish()
        destination = self.output_root / "forecasts" / forecast_id
        first_bytes = {path.name: path.read_bytes() for path in destination.iterdir()}
        manifest = json.loads(first_bytes["manifest.json"])
        self.assertEqual(manifest["forecast_id"], forecast_id)
        self.assertEqual(manifest["issued_at"], "2024-02-01T12:00:00+00:00")
        self.assertEqual(manifest["labels"], {
            "forecast": "PROSPECTIVE_SCENARIO_FORECAST",
            "target_history": "RETROSPECTIVE_INPUT_ONLY",
        })
        self.assertEqual(self.predict_calls, 1)
        with (destination / "forecast.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        with (destination / "scenario_sensitivity.csv").open(newline="", encoding="utf-8") as handle:
            sensitivity = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 144)
        self.assertEqual(len(sensitivity), 96)
        down = sensitivity[0]
        self.assertEqual((down["target"], down["scenario"], down["horizon"]), ("WORLD", "ECB_DOWN_100BP", "1"))
        self.assertAlmostEqual(float(down["q50_delta"]), 0.01)
        self.assertAlmostEqual(float(down["interval_width_delta"]), 0.02)
        for name, digest in manifest["generated_sha256"].items():
            self.assertEqual(sha256_file(destination / name), digest)

        repeated = self._publish(datetime(2024, 2, 2, 12, tzinfo=timezone.utc))
        self.assertEqual(repeated, forecast_id)
        self.assertEqual(first_bytes, {path.name: path.read_bytes() for path in destination.iterdir()})
        self.assertEqual(self.predict_calls, 2)

    def test_forecast_rejects_naive_time_target_mutation_collision_and_escape(self):
        from perpetual_engine.chronos import publish_forecast

        patches = self._patch_inputs()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with self.assertRaisesRegex(ValueError, "timezone-aware UTC"):
                publish_forecast(self.config.path, self.output_root, predictor=self.fake_predictor, issued_at=datetime(2024, 2, 1))
            with self.assertRaisesRegex(ValueError, "escapes"):
                publish_forecast(self.config.path, self.root.parent / "outside", predictor=self.fake_predictor)

        forecast_id = self._publish()
        destination = self.output_root / "forecasts" / forecast_id
        (destination / "forecast.csv").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "hash|collision"):
            self._publish()

        shutil.rmtree(self.output_root)
        patches = self._patch_inputs()

        def mutate(items, prediction_length, quantile_levels):
            result = self.fake_predictor(items, prediction_length, quantile_levels)
            self.config.target_csv.write_bytes(b"changed\n")
            return result

        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with self.assertRaisesRegex(ValueError, "target.*changed"):
                publish_forecast(
                    self.config.path, self.output_root, predictor=mutate,
                    issued_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
                )
        self.assertFalse((self.output_root / "forecasts").exists())

    def test_monitoring_separates_pending_computes_metrics_and_preserves_snapshots(self):
        from perpetual_engine.chronos import reconcile_forecasts

        forecast_id = self._publish()
        forecast_root = self.output_root / "forecasts"
        monitoring_root = self.output_root / "monitoring"
        pending_id = next(path.name for path in monitoring_root.iterdir() if path.name != ".staging")
        with (monitoring_root / pending_id / "pending_forecasts.csv").open(newline="", encoding="utf-8") as handle:
            self.assertEqual(len(list(csv.DictReader(handle))), 144)
        with (monitoring_root / pending_id / "live_metrics.csv").open(newline="", encoding="utf-8") as handle:
            self.assertEqual(list(csv.DictReader(handle)), [])

        self.config.target_csv.write_bytes(b"target-v2\n")
        self.config.target_manifest.write_bytes(b"manifest-v2\n")
        one_month = self._extended_targets(1)
        patches = self._patch_inputs(one_month)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            realized_id = reconcile_forecasts(self.config.path, forecast_root, monitoring_root)
        self.assertNotEqual(realized_id, pending_id)
        snapshot = monitoring_root / realized_id
        with (snapshot / "forecast_vs_actual.csv").open(newline="", encoding="utf-8") as handle:
            realized = list(csv.DictReader(handle))
        with (snapshot / "pending_forecasts.csv").open(newline="", encoding="utf-8") as handle:
            pending = list(csv.DictReader(handle))
        with (snapshot / "live_metrics.csv").open(newline="", encoding="utf-8") as handle:
            metrics = list(csv.DictReader(handle))
        self.assertEqual((len(realized), len(pending)), (12, 132))
        flat_world = next(row for row in realized if row["scenario"] == "ECB_FLAT" and row["target"] == "WORLD")
        self.assertAlmostEqual(float(flat_world["signed_error"]), 0.005)
        self.assertAlmostEqual(float(flat_world["absolute_error"]), 0.005)
        self.assertAlmostEqual(float(flat_world["squared_error"]), 0.000025)
        self.assertEqual(flat_world["interval_hit"], "true")
        exact = next(row for row in metrics if row["scenario"] == "ECB_FLAT" and row["target"] == "WORLD" and row["horizon"] == "1")
        self.assertEqual(exact["count"], "1")
        self.assertAlmostEqual(float(exact["bias"]), 0.005)
        self.assertAlmostEqual(float(exact["mae"]), 0.005)
        self.assertAlmostEqual(float(exact["rmse"]), 0.005)
        pooled = next(row for row in metrics if row["scenario"] == "ALL" and row["target"] == "ALL" and row["horizon"] == "ALL")
        self.assertEqual(pooled["count"], "12")
        self.assertTrue((monitoring_root / pending_id).is_dir())

        self.config.target_csv.write_bytes(b"target-v3\n")
        self.config.target_manifest.write_bytes(b"manifest-v3\n")
        two_months = self._extended_targets(2)
        patches = self._patch_inputs(two_months)
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            second_id = reconcile_forecasts(self.config.path, forecast_root, monitoring_root)
        self.assertNotEqual(second_id, realized_id)
        self.assertTrue((monitoring_root / realized_id).is_dir())
        manifest = json.loads((monitoring_root / second_id / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["label"], "PROSPECTIVE_TRACK_RECORD")
        self.assertEqual(manifest["monitoring_id"], second_id)
        self.assertEqual(manifest["included_forecast_manifest_sha256"], sorted(manifest["included_forecast_manifest_sha256"]))
        self.assertEqual(manifest["forecast_ids"], [forecast_id])

    def test_reconciliation_rejects_corrupt_archive_unexpected_child_and_path_escape(self):
        from perpetual_engine.chronos import reconcile_forecasts

        forecast_id = self._publish()
        forecast_root = self.output_root / "forecasts"
        forecast_path = forecast_root / forecast_id / "forecast.csv"
        original = forecast_path.read_bytes()
        forecast_path.write_bytes(b"corrupt")
        patches = self._patch_inputs()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with self.assertRaisesRegex(ValueError, "hash"):
                reconcile_forecasts(self.config.path, forecast_root, self.output_root / "other-monitoring")

        forecast_path.write_bytes(original)
        (forecast_root / "unexpected.txt").write_text("unexpected", encoding="utf-8")
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with self.assertRaisesRegex(ValueError, "unexpected"):
                reconcile_forecasts(self.config.path, forecast_root, self.output_root / "other-monitoring")
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with self.assertRaisesRegex(ValueError, "escapes"):
                reconcile_forecasts(self.config.path, forecast_root, self.root.parent / "outside")

    def test_reconciliation_rejects_a_nonflat_flat_scenario_in_the_manifest(self):
        from perpetual_engine.chronos import reconcile_forecasts

        forecast_id = self._publish()
        manifest_path = self.output_root / "forecasts" / forecast_id / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for scenario in manifest["scenarios"].values():
            for index in range(12):
                scenario[index] += index / 1000
        manifest_path.write_bytes(canonical_json(manifest))
        patches = self._patch_inputs()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with self.assertRaisesRegex(ValueError, "scenario definitions"):
                reconcile_forecasts(
                    self.config.path,
                    self.output_root / "forecasts",
                    self.output_root / "other-monitoring",
                )

    def test_publication_inference_uses_the_exact_snapshot_tables_bound_to_identity(self):
        from perpetual_engine.chronos import publish_forecast
        from perpetual_engine.chronos_data import MonthlyTable

        poisoned_targets = MonthlyTable(self.targets.months, self.targets.names, self.targets.values + 9)
        poisoned_covariates = MonthlyTable(self.covariates.months, self.covariates.names, self.covariates.values + 99)
        captured = {}

        def predictor(items, prediction_length, quantile_levels):
            captured["target"] = items[0]["target"].copy()
            captured["ecb"] = items[0]["past_covariates"]["ECB_DFR"].copy()
            return self.fake_predictor(items, prediction_length, quantile_levels)

        calls = 0

        def covariate_load(_config):
            nonlocal calls
            calls += 1
            return (self.covariates if calls == 1 else poisoned_covariates), self.vintage_id

        with patch("perpetual_engine.chronos.load_chronos_config", return_value=self.config), patch(
            "perpetual_engine.chronos.load_target_snapshot", return_value=(
                self.targets, sha256_file(self.config.target_csv), sha256_file(self.config.target_manifest)
            )
        ), patch("perpetual_engine.chronos.load_target_table", return_value=poisoned_targets), patch(
            "perpetual_engine.chronos.load_covariate_table", side_effect=covariate_load
        ), patch(
            "perpetual_engine.chronos.load_covariate_vintage", return_value=self.covariates, create=True
        ):
            publish_forecast(
                self.config.path, self.output_root, predictor=predictor,
                issued_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
            )

        np.testing.assert_array_equal(captured["target"], self.targets.values.astype(np.float32))
        np.testing.assert_array_equal(captured["ecb"], self.covariates.values[0].astype(np.float32))

    @staticmethod
    def _shift_csv(payload, fields, amount):
        source = io.StringIO(payload.decode("utf-8"), newline="")
        reader = csv.DictReader(source)
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=reader.fieldnames, lineterminator="\n")
        writer.writeheader()
        for row in reader:
            for field in fields:
                row[field] = repr(float(row[field]) + amount)
            writer.writerow(row)
        return output.getvalue().encode("utf-8")

    def test_archive_hash_and_parse_use_one_immutable_read_per_file(self):
        from perpetual_engine.chronos import ForecastRow, _scenario_sensitivity_bytes, reconcile_forecasts

        forecast_id = self._publish()
        archive = self.output_root / "forecasts" / forecast_id
        forecast_path = archive / "forecast.csv"
        sensitivity_path = archive / "scenario_sensitivity.csv"
        original_forecast = forecast_path.read_bytes()
        swapped_forecast = self._shift_csv(original_forecast, ("q10", "q50", "q90"), 1.0)
        reader = csv.DictReader(io.StringIO(swapped_forecast.decode("utf-8"), newline=""))
        swapped_rows = tuple(ForecastRow(
            date(2024, 1, 31), row["scenario"], row["target"], date.fromisoformat(row["forecast_month"]),
            int(row["horizon"]), float(row["q10"]), float(row["q50"]), float(row["q90"]),
        ) for row in reader)
        swapped_sensitivity = _scenario_sensitivity_bytes(swapped_rows)
        original_sha256 = sha256_file

        def swap_after_hash(path):
            digest = original_sha256(path)
            if Path(path).name == "scenario_sensitivity.csv":
                forecast_path.write_bytes(swapped_forecast)
                sensitivity_path.write_bytes(swapped_sensitivity)
            return digest

        patches = self._patch_inputs()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patch(
            "perpetual_engine.chronos.sha256_file", side_effect=swap_after_hash
        ):
            monitoring_id = reconcile_forecasts(
                self.config.path, self.output_root / "forecasts", self.output_root / "single-read-monitoring"
            )
        with (self.output_root / "single-read-monitoring" / monitoring_id / "pending_forecasts.csv").open(
            newline="", encoding="utf-8"
        ) as handle:
            first = next(csv.DictReader(handle))
        self.assertAlmostEqual(float(first["q50"]), 0.021)

    def _symlink(self, link, target, directory=False):
        try:
            link.symlink_to(target, target_is_directory=directory)
        except OSError as error:
            self.skipTest(f"symlink non disponibile: {error}")

    def test_forecast_root_cannot_be_a_link_outside_the_requested_output_root(self):
        from perpetual_engine.chronos import publish_forecast

        self.output_root.mkdir(parents=True)
        external = self.root / "external-forecasts"
        external.mkdir()
        self._symlink(self.output_root / "forecasts", external, directory=True)
        patches = self._patch_inputs()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with self.assertRaisesRegex(ValueError, "escapes"):
                publish_forecast(
                    self.config.path, self.output_root, predictor=self.fake_predictor,
                    issued_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
                )
        self.assertEqual(list(external.iterdir()), [])

    def test_archive_child_and_internal_files_cannot_link_outside_forecast_root(self):
        from perpetual_engine.chronos import reconcile_forecasts

        forecast_id = self._publish()
        forecast_root = self.output_root / "forecasts"
        archive = forecast_root / forecast_id
        external_archive = self.output_root / "external-archive"
        archive.rename(external_archive)
        self._symlink(archive, external_archive, directory=True)
        patches = self._patch_inputs()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with self.assertRaisesRegex(ValueError, "escapes"):
                reconcile_forecasts(self.config.path, forecast_root, self.output_root / "linked-monitoring")

        archive.unlink()
        external_archive.rename(archive)
        forecast_path = archive / "forecast.csv"
        external_file = self.output_root / "external-forecast.csv"
        forecast_path.replace(external_file)
        self._symlink(forecast_path, external_file)
        patches = self._patch_inputs()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with self.assertRaisesRegex(ValueError, "escapes"):
                reconcile_forecasts(self.config.path, forecast_root, self.output_root / "linked-file-monitoring")

    def test_repeat_rejects_issued_at_not_anchored_by_prior_monitoring(self):
        forecast_id = self._publish()
        manifest_path = self.output_root / "forecasts" / forecast_id / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["issued_at"] = "2030-01-01T00:00:00+00:00"
        manifest_path.write_bytes(canonical_json(manifest))
        with self.assertRaisesRegex(ValueError, "anchor"):
            self._publish(datetime(2030, 1, 2, tzinfo=timezone.utc))

    def test_new_publication_rejects_any_older_forecast_without_its_monitoring_anchor(self):
        old_id = self._publish()
        manifest_path = self.output_root / "forecasts" / old_id / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["issued_at"] = "2030-01-01T00:00:00+00:00"
        manifest_path.write_bytes(canonical_json(manifest))
        self.config.target_csv.write_bytes(b"target-new-id\n")
        self.config.target_manifest.write_bytes(b"manifest-new-id\n")
        with self.assertRaisesRegex(ValueError, "anchor"):
            self._publish(datetime(2030, 1, 2, tzinfo=timezone.utc))
        self.assertEqual([path.name for path in (self.output_root / "forecasts").iterdir()], [old_id])

    def test_archive_ecb_base_must_match_the_bound_vintage(self):
        from perpetual_engine.chronos import ecb_scenarios, reconcile_forecasts

        forecast_id = self._publish()
        manifest_path = self.output_root / "forecasts" / forecast_id / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["scenarios"] = {
            name: values.tolist() for name, values in ecb_scenarios(33.0, 100, 12).items()
        }
        manifest_path.write_bytes(canonical_json(manifest))
        patches = self._patch_inputs()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with self.assertRaisesRegex(ValueError, "vintage|scenario definitions"):
                reconcile_forecasts(
                    self.config.path, self.output_root / "forecasts", self.output_root / "wrong-base-monitoring"
                )

    def test_forecast_rechecks_inputs_inside_atomic_commit(self):
        import perpetual_engine.chronos as chronos_module

        real_atomic = chronos_module._atomic_snapshot

        def mutate_before_commit(*args, **kwargs):
            self.config.target_csv.write_bytes(b"changed-at-commit\n")
            return real_atomic(*args, **kwargs)

        patches = self._patch_inputs()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patch(
            "perpetual_engine.chronos._atomic_snapshot", side_effect=mutate_before_commit
        ), patch("perpetual_engine.chronos.reconcile_forecasts") as reconcile:
            with self.assertRaisesRegex(ValueError, "target.*changed"):
                chronos_module.publish_forecast(
                    self.config.path, self.output_root, predictor=self.fake_predictor,
                    issued_at=datetime(2024, 2, 1, tzinfo=timezone.utc),
                )
        reconcile.assert_not_called()
        self.assertFalse((self.output_root / "forecasts").exists())

    def test_monitoring_rechecks_inputs_inside_atomic_commit(self):
        import perpetual_engine.chronos as chronos_module

        self._publish()
        real_atomic = chronos_module._atomic_snapshot

        def mutate_before_commit(*args, **kwargs):
            self.config.target_csv.write_bytes(b"changed-at-monitoring-commit\n")
            return real_atomic(*args, **kwargs)

        output = self.output_root / "commit-monitoring"
        patches = self._patch_inputs()
        with patches[0], patches[1], patches[2], patches[3], patches[4], patch(
            "perpetual_engine.chronos._atomic_snapshot", side_effect=mutate_before_commit
        ):
            with self.assertRaisesRegex(ValueError, "target.*changed"):
                chronos_module.reconcile_forecasts(self.config.path, self.output_root / "forecasts", output)
        self.assertFalse(any(path.name != ".staging" for path in output.iterdir()) if output.exists() else False)

    def test_failed_automatic_reconciliation_rolls_back_only_the_new_forecast(self):
        self.output_root.mkdir(parents=True)
        (self.output_root / "monitoring").write_text("collision", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "collision"):
            self._publish()
        forecast_root = self.output_root / "forecasts"
        self.assertFalse(forecast_root.exists() and any(forecast_root.iterdir()))

    def test_archive_rejects_non_v1_model_metadata_and_extra_csv_fields(self):
        from perpetual_engine.chronos import reconcile_forecasts

        forecast_id = self._publish()
        archive = self.output_root / "forecasts" / forecast_id
        manifest_path = archive / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["model"]["device"] = "cuda"
        manifest_path.write_bytes(canonical_json(manifest))
        patches = self._patch_inputs()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with self.assertRaisesRegex(ValueError, "metadata"):
                reconcile_forecasts(self.config.path, self.output_root / "forecasts", self.output_root / "metadata-monitoring")

        manifest["model"]["device"] = self.config.device
        forecast_path = archive / "forecast.csv"
        lines = forecast_path.read_text(encoding="utf-8").splitlines()
        forecast_path.write_text("\n".join((lines[0], *(line + ",EXTRA" for line in lines[1:]))) + "\n", encoding="utf-8")
        manifest["generated_sha256"]["forecast.csv"] = sha256_file(forecast_path)
        manifest_path.write_bytes(canonical_json(manifest))
        patches = self._patch_inputs()
        with patches[0], patches[1], patches[2], patches[3], patches[4]:
            with self.assertRaisesRegex(ValueError, "extra|CSV"):
                reconcile_forecasts(self.config.path, self.output_root / "forecasts", self.output_root / "extra-monitoring")
