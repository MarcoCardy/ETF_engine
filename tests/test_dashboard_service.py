from __future__ import annotations

import json
import math
import csv
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from perpetual_engine.io import canonical_json, sha256_file
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

    def test_portfolio_reset_preserves_the_current_catalog(self):
        from perpetual_engine.dashboard_service import (
            EtfCandidate,
            add_catalog_entry,
            load_dashboard_state,
            reset_dashboard_state,
            save_dashboard_state,
        )

        original = load_dashboard_state(self.default_config, self.state_path)
        changed = add_catalog_entry(
            replace(original, components=(replace(original.components[0], weight=1.0),)),
            EtfCandidate(
                "Grid ETF", "GRID.MI", "IE000J80JTL1", "Milan", "EUR",
                "https://finance.yahoo.com/quote/GRID.MI",
            ),
        )
        save_dashboard_state(self.state_path, changed, default_config_path=self.default_config)

        restored = reset_dashboard_state(self.default_config, self.state_path)

        self.assertEqual(restored.components, original.components)
        self.assertEqual(restored.catalog, changed.catalog)

    def test_corrupted_state_can_be_fully_reset(self):
        from perpetual_engine.dashboard_service import load_dashboard_state, reset_dashboard_state

        self.state_path.parent.mkdir(parents=True)
        self.state_path.write_bytes(b"{")

        restored = reset_dashboard_state(self.default_config, self.state_path, preserve_catalog=False)

        self.assertEqual(load_dashboard_state(self.default_config, self.state_path), restored)
        self.assertEqual(tuple(item.component_id for item in restored.components), ("SWDA", "IWMO", "IWQU", "DBMFE"))

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


class DiscoveryAndCatalogTests(DashboardServiceFixture):
    def test_search_uses_borsa_italiana_when_yahoo_omits_isin(self):
        from perpetual_engine.dashboard_service import search_etfs

        class FakeTicker:
            def get_history_metadata(self):
                return {"currency": "EUR", "exchangeName": "MIL"}

            def get_isin(self):
                return "-"

        found = search_etfs(
            "EQQQ.MI",
            search_factory=lambda value, **kwargs: SimpleNamespace(quotes=[{
                "symbol": "EQQQ.MI",
                "quoteType": "ETF",
                "longname": "Invesco EQQQ NASDAQ-100 UCITS ETF",
            }]),
            ticker_factory=lambda symbol: FakeTicker(),
            official_search=lambda query: {
                "quotes": [{
                    "title": "Invesco Eqqq Nasdaq-100 Ucits Etf",
                    "symbol": "IE0032077012",
                    "mic": "ETFP",
                    "typeLabel": "ETF",
                    "link": "https://www.borsaitaliana.it/borsa/search/scheda.html?code=IE0032077012&mic=ETFP&lang=it",
                }],
            },
        )

        self.assertEqual(found[0].isin, "IE0032077012")
        self.assertIn("borsaitaliana.it", found[0].identity_source_url)

    def test_search_accepts_symbol_or_isin_and_keeps_only_verified_eur_etfs(self):
        from perpetual_engine.dashboard_service import search_etfs

        quotes = [
            {"symbol": "GRID.MI", "quoteType": "ETF", "longname": "First Trust Smart Grid", "exchange": "MIL"},
            {"symbol": "NOTETF.MI", "quoteType": "EQUITY", "longname": "Not an ETF", "exchange": "MIL"},
        ]

        class FakeTicker:
            def get_history_metadata(self):
                return {"currency": "EUR", "exchangeName": "Milan"}

            def get_isin(self):
                return "IE000J80JTL1"

        for query in ("GRID.MI", "IE000J80JTL1"):
            with self.subTest(query=query):
                found = search_etfs(
                    query,
                    search_factory=lambda value, **kwargs: SimpleNamespace(quotes=quotes),
                    ticker_factory=lambda symbol: FakeTicker(),
                )
                self.assertEqual(tuple(item.ticker for item in found), ("GRID.MI",))
                self.assertEqual(found[0].isin, "IE000J80JTL1")
                self.assertEqual(found[0].quote_currency, "EUR")

    def test_search_rejects_unverified_queries(self):
        from perpetual_engine.dashboard_service import search_etfs

        def run(query, *, currency="EUR", isin="IE000J80JTL1", exchange="Milan"):
            quotes = [{"symbol": "GRID.MI", "quoteType": "ETF", "longname": "Grid ETF"}]

            class FakeTicker:
                def get_history_metadata(self):
                    return {"currency": currency, "exchangeName": exchange}

                def get_isin(self):
                    return isin

            return search_etfs(
                query,
                search_factory=lambda value, **kwargs: SimpleNamespace(quotes=quotes),
                ticker_factory=lambda symbol: FakeTicker(),
            )

        with self.assertRaisesRegex(ValueError, "query"):
            run(" ")
        for case in (
            ("GRID.MI", "USD", "IE000J80JTL1"),
            ("GRID.MI", "EUR", None),
            ("GRID.MI", "EUR", "-"),
            ("GRID.MI", "EUR", "IE000J80JTL2"),
            ("IE000J80JTL2", "EUR", "IE000J80JTL1"),
        ):
            with self.subTest(case=case):
                with self.assertRaisesRegex(ValueError, "verified|ISIN"):
                    run(case[0], currency=case[1], isin=case[2])
        for exchange in (None, "", "   ", 17):
            with self.subTest(exchange=exchange):
                with self.assertRaisesRegex(ValueError, "exchange|verified"):
                    run("GRID.MI", exchange=exchange)

        self.assertEqual(run("GRID.MI", exchange="  Milan  ")[0].exchange, "Milan")

    def test_search_keeps_distinct_listings_with_the_same_isin(self):
        from perpetual_engine.dashboard_service import search_etfs

        quotes = [
            {"symbol": "GRID.MI", "quoteType": "ETF", "longname": "Grid ETF"},
            {"symbol": "GRID.MI", "quoteType": "ETF", "longname": "Grid ETF duplicate"},
            {"symbol": "GRID.PA", "quoteType": "ETF", "longname": "Grid ETF Paris"},
        ]

        class FakeTicker:
            def __init__(self, ticker):
                self.ticker = ticker

            def get_history_metadata(self):
                return {"currency": "EUR", "exchangeName": "Milan" if self.ticker.endswith(".MI") else "Paris"}

            def get_isin(self):
                return "IE000J80JTL1"

        found = search_etfs(
            "GRID",
            search_factory=lambda value, **kwargs: SimpleNamespace(quotes=quotes),
            ticker_factory=FakeTicker,
        )

        self.assertEqual(tuple(item.ticker for item in found), ("GRID.MI", "GRID.PA"))

    def test_catalog_mutations_are_immutable_and_persist_exchange(self):
        from perpetual_engine.dashboard_service import (
            EtfCandidate,
            add_catalog_entry,
            load_dashboard_state,
            remove_catalog_entry,
            save_dashboard_state,
        )

        original = load_dashboard_state(self.default_config, self.state_path)
        candidate = EtfCandidate(
            "Grid ETF", "GRID.MI", "IE000J80JTL1", "  Milan  ", "EUR",
            "https://finance.yahoo.com/quote/GRID.MI",
        )
        changed = add_catalog_entry(original, candidate)

        self.assertEqual(original.catalog, load_dashboard_state(self.default_config, self.state_path).catalog)
        self.assertEqual(changed.catalog[-1].study_id, "GRID_MI")
        self.assertEqual(changed.catalog[-1].exchange, "Milan")
        self.assertEqual(remove_catalog_entry(changed, "GRID_MI").catalog, original.catalog)
        self.assertEqual(changed.catalog[-1].ticker, "GRID.MI")
        save_dashboard_state(self.state_path, changed, default_config_path=self.default_config)
        self.assertEqual(load_dashboard_state(self.default_config, self.state_path).catalog[-1].exchange, "Milan")

    def test_direct_catalog_exchange_must_already_be_normalized(self):
        from perpetual_engine.dashboard_service import load_dashboard_state, save_dashboard_state

        original = load_dashboard_state(self.default_config, self.state_path)
        invalid = replace(
            original,
            catalog=(replace(original.catalog[0], exchange=f" {original.catalog[0].exchange} "),),
        )

        with self.assertRaisesRegex(ValueError, "identity"):
            save_dashboard_state(self.state_path, invalid, default_config_path=self.default_config)

    def test_catalog_mutations_reject_duplicate_identity_and_held_removal(self):
        from perpetual_engine.dashboard_service import (
            EtfCandidate,
            add_catalog_entry,
            load_dashboard_state,
            remove_catalog_entry,
        )

        original = load_dashboard_state(self.default_config, self.state_path)
        candidate = EtfCandidate(
            "Grid ETF", "GRID.MI", "IE000J80JTL1", "Milan", "EUR",
            "https://finance.yahoo.com/quote/GRID.MI",
        )
        added = add_catalog_entry(original, candidate)
        for duplicate in (
            replace(candidate, ticker="TEST.MI", isin="IE000J80JTM9"),
            replace(candidate, ticker="OTHER.MI", isin="IE00B3XXRP09"),
        ):
            with self.subTest(duplicate=duplicate):
                with self.assertRaisesRegex(ValueError, "duplicated"):
                    add_catalog_entry(added, duplicate)
                self.assertEqual(added, add_catalog_entry(original, candidate))

        held_catalog = replace(original.catalog[0], study_id="SWDA", ticker="SWDA.MI", isin="IE00B4L5Y983")
        held = replace(original, catalog=(held_catalog,))
        with self.assertRaisesRegex(ValueError, "held"):
            remove_catalog_entry(held, "SWDA")
        self.assertEqual(held.catalog, (held_catalog,))


class DashboardOrchestrationTests(DashboardServiceFixture):
    def test_failed_refresh_preserves_the_current_vintage(self):
        from perpetual_engine.dashboard_service import DashboardPaths, load_dashboard_state, refresh_dashboard_data

        paths = DashboardPaths.from_root(self.root)
        state = load_dashboard_state(self.default_config, self.state_path)
        first = refresh_dashboard_data(
            paths, state, downloader=self.price_payloads().__getitem__, retrieved_at=self.retrieved_at,
        )
        pointer = first.pointer_path.read_bytes()

        with self.assertRaisesRegex(ValueError, "SWDA.MI"):
            refresh_dashboard_data(paths, state, downloader=lambda ticker: (_ for _ in ()).throw(ValueError(ticker)))

        self.assertEqual(first.pointer_path.read_bytes(), pointer)

    def test_refresh_reports_each_current_instrument_before_download(self):
        from perpetual_engine.dashboard_service import DashboardPaths, load_dashboard_state, refresh_dashboard_data

        paths = DashboardPaths.from_root(self.root)
        state = load_dashboard_state(self.default_config, self.state_path)
        payloads = self.price_payloads()
        current: list[str] = []

        def downloader(ticker: str) -> bytes:
            self.assertEqual(current[-1], ticker)
            return payloads[ticker]

        refresh_dashboard_data(
            paths,
            state,
            downloader=downloader,
            retrieved_at=self.retrieved_at,
            progress=current.append,
        )

        self.assertEqual(current, ["SWDA.MI", "IWMO.MI", "IWQU.MI", "DBMFE.PA", "TEST.MI"])

    def test_publication_is_immutable_and_reconciles_contributions(self):
        from perpetual_engine.dashboard_service import (
            DashboardPaths,
            load_dashboard_state,
            publish_dashboard_comparison,
            refresh_dashboard_data,
        )

        paths = DashboardPaths.from_root(self.root)
        original = load_dashboard_state(self.default_config, self.state_path)
        state = replace(
            original,
            components=(
                replace(original.components[0], weight=0.50),
                replace(original.components[1], weight=0.20),
                replace(original.components[2], weight=0.20),
                replace(original.components[3], weight=0.10),
            ),
        )
        refresh_dashboard_data(paths, state, downloader=self.price_payloads().__getitem__, retrieved_at=self.retrieved_at)

        report = publish_dashboard_comparison(paths, state, "TEST")
        self.assertEqual(report.output_dir.name, report.comparison_id)
        for name in ("comparison_monthly.csv", "comparison_summary.csv", "component_contributions.csv"):
            self.assertTrue((report.output_dir / name).is_file())
        first_files = {path.name: path.read_bytes() for path in report.output_dir.iterdir()}
        self.assertEqual(publish_dashboard_comparison(paths, state, "TEST").comparison_id, report.comparison_id)
        self.assertEqual({path.name: path.read_bytes() for path in report.output_dir.iterdir()}, first_files)

        with (report.output_dir / "component_contributions.csv").open(newline="", encoding="utf-8") as handle:
            contributions = list(csv.DictReader(handle))
        with (report.output_dir / "comparison_monthly.csv").open(newline="", encoding="utf-8") as handle:
            monthly_by_month = {row["month"]: row for row in csv.DictReader(handle)}
        component_ids = tuple(item.component_id for item in state.components)
        for row in contributions:
            self.assertAlmostEqual(sum(float(row[item]) for item in component_ids), float(row["portfolio_return"]))
            self.assertAlmostEqual(
                sum(float(row[f"{item}_cumulative"]) for item in component_ids),
                float(monthly_by_month[row["month"]]["portfolio_cumulative_value"]) / 100.0 - 1.0,
            )
        manifest = json.loads((report.output_dir / "manifest.json").read_text(encoding="utf-8"))
        for name, digest in manifest["generated_sha256"].items():
            self.assertEqual(sha256_file(report.output_dir / name), digest)

        changed = replace(state, components=(replace(state.components[0], weight=0.49), replace(state.components[1], weight=0.21), *state.components[2:]))
        self.assertNotEqual(publish_dashboard_comparison(paths, changed, "TEST").comparison_id, report.comparison_id)

    def test_status_reports_missing_current_data_and_stale_catalog(self):
        from perpetual_engine.dashboard_service import (
            DashboardPaths,
            current_data_status,
            load_dashboard_state,
            refresh_dashboard_data,
        )

        paths = DashboardPaths.from_root(self.root)
        state = load_dashboard_state(self.default_config, self.state_path)
        self.assertFalse(current_data_status(paths, state).available)
        refresh_dashboard_data(paths, state, downloader=self.price_payloads().__getitem__, retrieved_at=self.retrieved_at)
        status = current_data_status(paths, state)
        self.assertTrue(status.available)
        self.assertEqual((status.retrieved_at, status.first_month, status.last_month), (
            self.retrieved_at, months(14)[1], months(14)[-1],
        ))

        stale = replace(state, catalog=(replace(state.catalog[0], name="Renamed ETF"),))
        self.assertEqual(current_data_status(paths, stale).reason, "Aggiorna i dati per includere il nuovo catalogo")
