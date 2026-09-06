from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

from perpetual_engine.dashboard_service import DashboardPaths, load_dashboard_state, refresh_dashboard_data
from perpetual_engine.io import canonical_json
from tests.test_dashboard_service import DashboardServiceFixture
from tests.test_portfolio_monitor import daily_bytes, months, prices_from_returns


class DirectChronosFixture(DashboardServiceFixture):
    def setUp(self):
        super().setUp()
        payload = json.loads(self.default_config.read_text(encoding="utf-8"))
        payload["studies"] = [{
            "id": "GRID", "name": "Grid ETF", "ticker": "GRID.MI", "isin": "IE000J80JTL1",
            "quote_currency": "EUR", "identity_source_url": "https://example.test/grid",
        }]
        self.default_config.write_bytes(canonical_json(payload))
        self.paths = DashboardPaths.from_root(self.root)
        self.state = load_dashboard_state(self.paths.default_config, self.paths.state)
        self.retrieved_at = datetime(2026, 3, 2, tzinfo=timezone.utc)

    @staticmethod
    def rows(*items):
        return tuple({"id": component_id, "weight": weight} for component_id, weight in items)

    def current_prices(self):
        long_dates = months(62, date(2021, 1, 31))
        short_dates = long_dates[12:]
        values = {
            "SWDA.MI": long_dates,
            "IWMO.MI": long_dates,
            "IWQU.MI": long_dates,
            "GRID.MI": long_dates,
            "DBMFE.PA": short_dates,
        }
        return {
            ticker: daily_bytes(list(prices_from_returns(dates, (0.01,) * (len(dates) - 1)).items()))
            for ticker, dates in values.items()
        }

    def refresh_prices(self):
        refresh_dashboard_data(
            self.paths, self.state, downloader=self.current_prices().__getitem__, retrieved_at=self.retrieved_at,
        )


class DirectPortfolioTests(DirectChronosFixture):
    def test_candidate_defaults_to_base_but_saves_separately(self):
        from perpetual_engine.dashboard_chronos import load_direct_portfolios, save_candidate_portfolio

        candidate, base = load_direct_portfolios(self.paths, self.state)
        self.assertEqual(candidate.components, base.components)
        self.assertFalse(self.paths.chronos_candidate.exists())

        saved = save_candidate_portfolio(
            self.paths, self.state,
            self.rows(("SWDA", 0.55), ("IWMO", 0.15), ("IWQU", 0.15), ("GRID", 0.15)),
        )
        self.assertEqual(tuple(item.component_id for item in saved.components), ("SWDA", "IWMO", "IWQU", "GRID"))
        self.assertEqual(
            tuple(item.component_id for item in load_dashboard_state(self.paths.default_config, self.paths.state).components),
            ("SWDA", "IWMO", "IWQU", "DBMFE"),
        )
        self.assertEqual(json.loads(self.paths.chronos_candidate.read_text(encoding="utf-8"))["schema_version"], "DIRECT_CHRONOS_PORTFOLIO_V1")

    def test_candidate_requires_four_unique_confirmed_eur_etfs_and_exact_weight_total(self):
        from perpetual_engine.dashboard_chronos import save_candidate_portfolio

        invalid = (
            self.rows(("SWDA", 0.70), ("IWMO", 0.15), ("IWQU", 0.15)),
            self.rows(("SWDA", 0.55), ("SWDA", 0.15), ("IWQU", 0.15), ("GRID", 0.15)),
            self.rows(("SWDA", 0.54), ("IWMO", 0.15), ("IWQU", 0.15), ("GRID", 0.15)),
        )
        for rows in invalid:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                save_candidate_portfolio(self.paths, self.state, rows)

    def test_saved_candidate_rejects_identity_that_no_longer_matches_catalog(self):
        from perpetual_engine.dashboard_chronos import load_direct_portfolios, save_candidate_portfolio

        save_candidate_portfolio(
            self.paths, self.state,
            self.rows(("SWDA", 0.55), ("IWMO", 0.15), ("IWQU", 0.15), ("GRID", 0.15)),
        )
        changed = self.state.__class__(self.state.components, (self.state.catalog[0].__class__(
            self.state.catalog[0].study_id, "Changed", self.state.catalog[0].ticker,
            self.state.catalog[0].isin, self.state.catalog[0].exchange, self.state.catalog[0].quote_currency,
            self.state.catalog[0].identity_source_url,
        ),))
        with self.assertRaises(ValueError):
            load_direct_portfolios(self.paths, changed)


class DirectTargetTests(DirectChronosFixture):
    def test_direct_targets_keep_each_etfs_own_history_and_share_latest_valid_origin(self):
        from perpetual_engine.dashboard_chronos import load_etf_target_snapshot, save_candidate_portfolio

        save_candidate_portfolio(
            self.paths, self.state,
            self.rows(("SWDA", 0.55), ("IWMO", 0.15), ("IWQU", 0.15), ("GRID", 0.15)),
        )
        self.refresh_prices()
        snapshot = load_etf_target_snapshot(self.paths, self.state)
        by_ticker = {series.component.ticker: series for series in snapshot.series}

        self.assertGreater(len(by_ticker["SWDA.MI"].returns), len(by_ticker["DBMFE.PA"].returns))
        self.assertEqual(snapshot.common_origin, date(2026, 2, 28))
        self.assertEqual(by_ticker["DBMFE.PA"].history_status, "STORICO_BREVE")
        self.assertEqual(len(snapshot.series), 5)

    def test_snapshot_binds_manifest_and_canonical_series_csv_hashes(self):
        from perpetual_engine.dashboard_chronos import load_etf_target_snapshot

        self.refresh_prices()
        snapshot = load_etf_target_snapshot(self.paths, self.state)
        self.assertEqual(
            snapshot.price_manifest_sha256,
            hashlib.sha256((self.root / "data" / "portfolio_p_v1" / "vintages" / snapshot.price_vintage_id / "manifest.json").read_bytes()).hexdigest(),
        )
        for series in snapshot.series:
            expected = ("month,return\n" + "".join(
                f"{month.isoformat()},{float(value):.17g}\n" for month, value in zip(series.months, series.returns)
            )).encode()
            self.assertEqual(series.series_sha256, hashlib.sha256(expected).hexdigest())
