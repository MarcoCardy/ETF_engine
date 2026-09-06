from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timezone
from dataclasses import replace
from unittest.mock import patch

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

    def refresh_prices(self, payloads=None):
        refresh_dashboard_data(
            self.paths, self.state, downloader=(payloads or self.current_prices()).__getitem__, retrieved_at=self.retrieved_at,
        )

    @staticmethod
    def payloads_for_dates(dates_by_ticker):
        return {
            ticker: daily_bytes(list(prices_from_returns(dates, (0.01,) * (len(dates) - 1)).items()))
            for ticker, dates in dates_by_ticker.items()
        }


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

    def test_candidate_rejects_non_eur_non_finite_non_positive_and_shared_listing_identities(self):
        from perpetual_engine.dashboard_chronos import load_direct_portfolios, save_candidate_portfolio

        valid = self.rows(("SWDA", 0.55), ("IWMO", 0.15), ("IWQU", 0.15), ("GRID", 0.15))
        save_candidate_portfolio(self.paths, self.state, valid)
        payload = json.loads(self.paths.chronos_candidate.read_text(encoding="utf-8"))
        payload["components"][-1]["currency"] = "USD"
        self.paths.chronos_candidate.write_bytes(canonical_json(payload))
        with self.assertRaises(ValueError):
            load_direct_portfolios(self.paths, self.state)

        for weight in (math.nan, math.inf, 0.0, -0.15):
            rows = list(valid)
            rows[-1] = {"id": "GRID", "weight": weight}
            with self.subTest(weight=weight), self.assertRaises(ValueError):
                save_candidate_portfolio(self.paths, self.state, tuple(rows))

        alias = replace(self.state.catalog[0], study_id="GRID_ALIAS")
        state = replace(self.state, catalog=(*self.state.catalog, alias))
        with self.assertRaises(ValueError):
            save_candidate_portfolio(
                self.paths, state,
                self.rows(("SWDA", 0.40), ("IWMO", 0.15), ("GRID", 0.15), ("GRID_ALIAS", 0.30)),
            )

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

    def test_malformed_candidate_is_rejected_and_reset_replaces_it_with_base(self):
        from perpetual_engine.dashboard_chronos import load_direct_portfolios, reset_candidate_portfolio

        self.paths.chronos_candidate.parent.mkdir(parents=True, exist_ok=True)
        self.paths.chronos_candidate.write_bytes(b"{")
        with self.assertRaises(ValueError):
            load_direct_portfolios(self.paths, self.state)

        reset = reset_candidate_portfolio(self.paths, self.state)
        candidate, base = load_direct_portfolios(self.paths, self.state)
        self.assertEqual((reset.components, candidate.components), (base.components, base.components))


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
        self.assertEqual(tuple(series.component.ticker for series in snapshot.series), (
            "SWDA.MI", "IWMO.MI", "IWQU.MI", "GRID.MI", "DBMFE.PA",
        ))

    def test_targets_exclude_incomplete_month_and_apply_seven_day_staleness_limit(self):
        from perpetual_engine.dashboard_chronos import load_etf_target_snapshot

        dates = months(62, date(2021, 1, 31))
        current = {ticker: (*dates, date(2026, 3, 1)) for ticker in self.current_prices()}
        self.refresh_prices(self.payloads_for_dates(current))
        self.assertEqual(load_etf_target_snapshot(self.paths, self.state).common_origin, date(2026, 2, 28))

        for stale_days, expected in ((7, date(2026, 2, 28)), (8, date(2026, 1, 31))):
            with self.subTest(stale_days=stale_days):
                stale = {ticker: dates for ticker in self.current_prices()}
                stale["DBMFE.PA"] = (*dates[:-1], date(2026, 2, 28).replace(day=28 - stale_days))
                self.refresh_prices(self.payloads_for_dates(stale))
                self.assertEqual(load_etf_target_snapshot(self.paths, self.state).common_origin, expected)

    def test_targets_keep_only_the_contiguous_suffix_after_an_interior_stale_gap(self):
        from perpetual_engine.dashboard_chronos import load_etf_target_snapshot

        dates = months(62, date(2021, 1, 31))
        stale = {ticker: dates for ticker in self.current_prices()}
        stale["DBMFE.PA"] = tuple(date(2025, 5, 23) if day == date(2025, 5, 31) else day for day in dates)
        with self.assertRaisesRegex(ValueError, "common monthly prices"):
            self.refresh_prices(self.payloads_for_dates(stale))
        with self.assertRaisesRegex(ValueError, r"DBMFE\.PA.*8 observed"):
            load_etf_target_snapshot(self.paths, self.state)

    def test_targets_keep_history_ending_at_common_origin_before_a_later_gap(self):
        from perpetual_engine.dashboard_chronos import load_etf_target_snapshot

        dates = months(62, date(2021, 1, 31))
        histories = {ticker: dates for ticker in self.current_prices()}
        histories["DBMFE.PA"] = dates[:57]
        histories["SWDA.MI"] = tuple(date(2025, 12, 23) if day == date(2025, 12, 31) else day for day in dates)
        self.refresh_prices(self.payloads_for_dates(histories))

        snapshot = load_etf_target_snapshot(self.paths, self.state)
        self.assertEqual(snapshot.common_origin, date(2025, 9, 30))
        self.assertEqual(len(next(series for series in snapshot.series if series.component.ticker == "SWDA.MI").returns), 56)

    def test_targets_enforce_exact_history_boundaries_and_identify_missing_prices(self):
        from perpetual_engine import dashboard_chronos
        from perpetual_engine.dashboard_chronos import load_etf_target_snapshot
        from perpetual_engine.portfolio_monitor import load_current_portfolio_prices

        for returns, status in ((11, None), (12, "STORICO_BREVE"), (59, "STORICO_BREVE"), (60, "SUFFICIENT_HISTORY")):
            with self.subTest(returns=returns):
                dates = months(returns + 1, date(2021, 1, 31))
                self.refresh_prices(self.payloads_for_dates({ticker: dates for ticker in self.current_prices()}))
                if status is None:
                    with self.assertRaisesRegex(ValueError, r"SWDA\.MI.*11 observed"):
                        load_etf_target_snapshot(self.paths, self.state)
                else:
                    snapshot = load_etf_target_snapshot(self.paths, self.state)
                    self.assertTrue(all(series.history_status == status for series in snapshot.series))

        self.refresh_prices()
        from perpetual_engine.dashboard_service import materialize_runtime_config
        materialize_runtime_config(self.paths.default_config, self.state, self.paths.runtime_config, project_root=self.paths.project_root)
        config, raw, manifest = load_current_portfolio_prices(self.paths.runtime_config, project_root=self.paths.project_root)
        raw.pop("SWDA.MI")
        with patch.object(dashboard_chronos, "load_current_portfolio_prices", return_value=(config, raw, manifest)):
            with self.assertRaisesRegex(ValueError, r"SWDA\.MI.*0 observed"):
                load_etf_target_snapshot(self.paths, self.state)

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
