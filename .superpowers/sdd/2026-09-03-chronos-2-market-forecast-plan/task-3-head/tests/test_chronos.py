import hashlib
import io
import json
import math
import shutil
import tempfile
import unittest
import zipfile
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

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
