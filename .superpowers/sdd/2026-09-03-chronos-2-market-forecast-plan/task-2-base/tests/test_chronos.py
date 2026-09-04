import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

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
        )
        for index, field, value in invalid_mappings:
            with self.subTest(index=index, field=field):
                changed = json.loads(json.dumps(config))
                changed["sources"][index][field] = value
                self.config_path.write_bytes(canonical_json(changed))
                with self.assertRaisesRegex(ValueError, "source mapping"):
                    load_chronos_config(self.config_path)
        changed = json.loads(json.dumps(config))
        changed["sources"].append({"id": "EXTRA", "url": "https://example.test/extra", "parser": "ecb_dfr", "role": "known_future"})
        self.config_path.write_bytes(canonical_json(changed))
        with self.assertRaisesRegex(ValueError, "source mapping"):
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
