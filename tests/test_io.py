import json
import tempfile
from pathlib import Path
from unittest import TestCase

from perpetual_engine.io import load_config, load_snapshot, normalized_json, sha256_file
from perpetual_engine.models import money
from tests.helpers import sample_result


class IoTests(TestCase):
    def test_policy_config_preserves_frozen_values(self):
        config = load_config(Path("config/policy_v1.json"))
        self.assertEqual(config.hard_floor, money("600000"))
        self.assertEqual(config.initial_hwm, money("614590.86"))
        self.assertEqual(config.protected_monthly_gross, money("1800"))

    def test_loader_rejects_legacy_protected_monthly_net_key(self):
        source = json.loads(Path("config/policy_v1.json").read_text(encoding="utf-8"))
        source["protected_monthly_net"] = source.pop("protected_monthly_gross")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(KeyError, "protected_monthly_net"):
                load_config(path)

    def test_loader_rejects_legacy_key_when_gross_key_is_also_present(self):
        source = json.loads(Path("config/policy_v1.json").read_text(encoding="utf-8"))
        source["protected_monthly_net"] = "1800"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ambiguous.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(KeyError, "protected_monthly_net"):
                load_config(path)

    def test_vi_fixture_preserves_original_total(self):
        snapshot = load_snapshot(Path("data/fixtures/vi_2026-08-19.json"))
        self.assertEqual(snapshot.nominal_capital_eur, money("614590.86"))
        self.assertEqual(snapshot.cash_eur, money("1950.90"))
        self.assertEqual(len(snapshot.holdings), 13)
        self.assertEqual(str(snapshot.holdings[0].price_date), "2026-08-13")

    def test_normalized_result_is_byte_stable(self):
        first = normalized_json(sample_result())
        second = normalized_json(sample_result())
        self.assertEqual(first, second)
        self.assertIn(b'"delivered_net_real":"0.00"', first)

    def test_loader_rejects_numeric_money_fields(self):
        source = json.loads(Path("data/fixtures/vi_2026-08-19.json").read_text(encoding="utf-8"))
        source["cash_eur"] = 1950.90
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "cash_eur.*JSON string"):
                load_snapshot(path)

    def test_sha256_is_stable_hex(self):
        digest = sha256_file(Path("config/policy_v1.json"))
        self.assertEqual(len(digest), 64)
        int(digest, 16)
