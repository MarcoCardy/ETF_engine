from __future__ import annotations

import json
import math
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from perpetual_engine.io import canonical_json
from tests.test_portfolio_monitor import STUDY, config_payload, daily_bytes, months, prices_from_returns


class DashboardServiceFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.default_config = self.root / "config" / "portfolio_p_v1.json"
        self.default_config.parent.mkdir(parents=True)
        payload = config_payload()
        payload["studies"] = [{**STUDY}]
        self.default_config.write_bytes(canonical_json(payload))
        self.default_bytes = self.default_config.read_bytes()
        self.state_path = self.root / "data" / "dashboard_v1" / "state.json"
        self.retrieved_at = datetime(2026, 3, 2, tzinfo=timezone.utc)

    def tearDown(self):
        self.directory.cleanup()

    def price_payloads(self):
        dates = months(14)
        returns = {
            "SWDA.MI": (0.01,) * 13,
            "IWMO.MI": (0.02,) * 13,
            "IWQU.MI": (0.005,) * 13,
            "DBMFE.PA": (-0.002, 0.01) * 6 + (-0.002,),
            "TEST.MI": (0.03, -0.01) * 6 + (0.03,),
        }
        return {
            ticker: daily_bytes(list(prices_from_returns(dates, changes).items()))
            for ticker, changes in returns.items()
        }


class DashboardStateTests(DashboardServiceFixture):
    def test_first_load_copies_default_and_reset_restores_it(self):
        from perpetual_engine.dashboard_service import load_dashboard_state, reset_dashboard_state, save_dashboard_state

        state = load_dashboard_state(self.default_config, self.state_path)
        self.assertEqual(tuple(item.component_id for item in state.components), ("SWDA", "IWMO", "IWQU", "DBMFE"))
        self.assertEqual(tuple(item.weight for item in state.components), (0.60, 0.15, 0.15, 0.10))
        changed = replace(state, components=(replace(state.components[0], weight=1.0),))
        save_dashboard_state(self.state_path, changed, default_config_path=self.default_config)

        restored = reset_dashboard_state(self.default_config, self.state_path)

        self.assertEqual(restored, state)
        self.assertEqual(self.default_config.read_bytes(), self.default_bytes)

    def test_invalid_save_preserves_last_valid_state(self):
        from perpetual_engine.dashboard_service import load_dashboard_state, save_dashboard_state

        original = load_dashboard_state(self.default_config, self.state_path)
        invalid = replace(original, components=(replace(original.components[0], weight=0.50),))

        with self.assertRaisesRegex(ValueError, "100%"):
            save_dashboard_state(self.state_path, invalid, default_config_path=self.default_config)

        self.assertEqual(load_dashboard_state(self.default_config, self.state_path), original)

    def test_rejects_duplicate_ticker_and_isin(self):
        from perpetual_engine.dashboard_service import load_dashboard_state, save_dashboard_state

        original = load_dashboard_state(self.default_config, self.state_path)
        duplicate_ticker = replace(
            original,
            catalog=(replace(original.catalog[0], ticker=original.components[0].ticker),),
        )
        duplicate_isin = replace(
            original,
            catalog=(replace(original.catalog[0], isin=original.components[0].isin),),
        )
        for candidate in (duplicate_ticker, duplicate_isin):
            with self.subTest(candidate=candidate):
                with self.assertRaisesRegex(ValueError, "duplicated"):
                    save_dashboard_state(self.state_path, candidate, default_config_path=self.default_config)

    def test_rejects_non_finite_or_zero_weights(self):
        from perpetual_engine.dashboard_service import load_dashboard_state, save_dashboard_state

        original = load_dashboard_state(self.default_config, self.state_path)
        for weight in (0.0, math.inf, math.nan, -0.1):
            with self.subTest(weight=weight):
                invalid = replace(original, components=(replace(original.components[0], weight=weight),))
                with self.assertRaisesRegex(ValueError, "weight"):
                    save_dashboard_state(self.state_path, invalid, default_config_path=self.default_config)

    def test_rejects_unknown_holding_identity(self):
        from perpetual_engine.dashboard_service import load_dashboard_state, save_dashboard_state
        from perpetual_engine.portfolio_monitor import ComponentSpec

        original = load_dashboard_state(self.default_config, self.state_path)
        invalid = replace(
            original,
            components=(ComponentSpec("OTHER", "OTHER.MI", "IE0000000002", "EUR", 1.0),),
        )
        with self.assertRaisesRegex(ValueError, "identity"):
            save_dashboard_state(self.state_path, invalid, default_config_path=self.default_config)

    def test_rejects_malformed_existing_state_without_replacing_it(self):
        from perpetual_engine.dashboard_service import load_dashboard_state

        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_bytes(b"{")
        with self.assertRaisesRegex(ValueError, "dashboard state is malformed"):
            load_dashboard_state(self.default_config, self.state_path)
        self.assertEqual(self.state_path.read_bytes(), b"{")

    def test_accepts_exact_weight_tolerance_boundary(self):
        from perpetual_engine.dashboard_service import load_dashboard_state, save_dashboard_state

        original = load_dashboard_state(self.default_config, self.state_path)
        boundary = replace(
            original,
            components=(
                replace(original.components[0], weight=0.6001),
                replace(original.components[1], weight=0.15),
                replace(original.components[2], weight=0.15),
                replace(original.components[3], weight=0.10),
            ),
        )
        save_dashboard_state(self.state_path, boundary, default_config_path=self.default_config)
        self.assertEqual(load_dashboard_state(self.default_config, self.state_path), boundary)

    def test_rejects_weight_just_outside_tolerance_boundary(self):
        from perpetual_engine.dashboard_service import load_dashboard_state, save_dashboard_state

        original = load_dashboard_state(self.default_config, self.state_path)
        outside = replace(original, components=(replace(original.components[0], weight=0.60011),))
        with self.assertRaisesRegex(ValueError, "0.01 percentage points"):
            save_dashboard_state(self.state_path, outside, default_config_path=self.default_config)

    def test_state_has_exact_schema_and_stable_hash(self):
        from perpetual_engine.dashboard_service import load_dashboard_state, state_sha256

        state = load_dashboard_state(self.default_config, self.state_path)
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(set(payload), {"schema_version", "components", "catalog"})
        self.assertEqual(payload["schema_version"], "ETF_DASHBOARD_STATE_V1")
        self.assertEqual(state_sha256(state), state_sha256(state))


class RuntimeConfigTests(DashboardServiceFixture):
    def test_materializes_catalog_without_changing_default_components(self):
        from perpetual_engine.dashboard_service import load_dashboard_state, materialize_runtime_config
        from perpetual_engine.portfolio_monitor import load_portfolio_config

        state = load_dashboard_state(self.default_config, self.state_path)
        runtime = self.root / "data" / "dashboard_v1" / "runtime.json"
        result = materialize_runtime_config(self.default_config, state, runtime, project_root=self.root)

        self.assertEqual(result, runtime)
        self.assertEqual(self.default_config.read_bytes(), self.default_bytes)
        self.assertEqual(runtime.read_bytes(), self.default_bytes)
        config = load_portfolio_config(runtime, project_root=self.root)
        self.assertEqual(config.components, state.components[:4])
        self.assertEqual(tuple(item.study_id for item in config.studies), ("TEST",))

    def test_materialized_catalog_replaces_only_studies(self):
        from perpetual_engine.dashboard_service import CatalogEtf, DashboardState, load_dashboard_state, materialize_runtime_config
        from perpetual_engine.portfolio_monitor import load_portfolio_config

        original = load_dashboard_state(self.default_config, self.state_path)
        catalog = replace(original.catalog[0], study_id="OTHER", name="Other ETF")
        state = DashboardState(original.components, (catalog,))
        runtime = self.root / "data" / "dashboard_v1" / "runtime.json"
        materialize_runtime_config(self.default_config, state, runtime, project_root=self.root)

        payload = json.loads(runtime.read_text(encoding="utf-8"))
        default_payload = json.loads(self.default_bytes)
        self.assertEqual(payload["components"], default_payload["components"])
        self.assertEqual(payload["studies"], [{
            "id": "OTHER", "name": "Other ETF", "ticker": "TEST.MI", "isin": "IE00B3XXRP09",
            "quote_currency": "EUR", "identity_source_url": "https://example.test/etf",
        }])
        self.assertEqual(load_portfolio_config(runtime, project_root=self.root).studies[0].study_id, "OTHER")

    def test_first_load_derives_known_exchange_names(self):
        from perpetual_engine.dashboard_service import load_dashboard_state

        state = load_dashboard_state(self.default_config, self.state_path)
        self.assertEqual(state.catalog[0].exchange, "Borsa Italiana")
