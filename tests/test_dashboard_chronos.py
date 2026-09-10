from __future__ import annotations

import hashlib
import csv
import json
import math
import statistics
from datetime import date, datetime, timezone
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from perpetual_engine.dashboard_service import DashboardPaths, load_dashboard_state, refresh_dashboard_data
from perpetual_engine.io import canonical_json
from tests.test_dashboard_service import DashboardServiceFixture
from tests.test_portfolio_monitor import daily_bytes, months, prices_from_returns


_USE_FAKE_PREDICTOR = object()


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
        self.paths.chronos_config.write_bytes(Path("config/chronos_v1.json").read_bytes())
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

    def state_with_grid_candidate(self):
        from perpetual_engine.dashboard_chronos import save_candidate_portfolio

        save_candidate_portfolio(
            self.paths, self.state,
            self.rows(("SWDA", 0.55), ("IWMO", 0.15), ("IWQU", 0.15), ("GRID", 0.15)),
        )
        return self.state

    def covariates(self):
        from perpetual_engine.chronos_data import MonthlyTable, REQUIRED_V1_COVARIATES

        dates = months(62, date(2021, 1, 31))[1:]
        values = np.vstack([
            np.arange(len(dates), dtype=float) + offset * 100.0
            for offset in range(len(REQUIRED_V1_COVARIATES))
        ])
        return MonthlyTable(dates, REQUIRED_V1_COVARIATES, values)

    @staticmethod
    def forecast_predictor(items, prediction_length, quantile_levels):
        assert prediction_length == 12
        assert quantile_levels == [0.1, 0.5, 0.9]
        results = []
        for index, _item in enumerate(items):
            median = 0.01 * (index // 3 + 1) + 0.001 * (index % 3)
            results.append(np.tile(np.asarray([[[median - 0.01, median, median + 0.01]]]), (1, 12, 1)))
        return results


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


class DirectForecastTests(DirectChronosFixture):
    def setUp(self):
        super().setUp()
        self.refresh_prices()
        self.state_with_grid_candidate()
        self.macro_vintage_id = "a" * 64

    def publish(self, *, predictor=_USE_FAKE_PREDICTOR, issued_at=datetime(2026, 3, 3, tzinfo=timezone.utc)):
        from perpetual_engine.dashboard_chronos import publish_direct_forecast

        with patch(
            "perpetual_engine.dashboard_chronos.load_covariate_table",
            return_value=(self.covariates(), self.macro_vintage_id),
        ):
            return publish_direct_forecast(
                self.paths,
                self.state,
                predictor=self.forecast_predictor if predictor is _USE_FAKE_PREDICTOR else predictor,
                issued_at=issued_at,
            )

    @staticmethod
    def csv_rows(path):
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    @staticmethod
    def write_csv_rows(path, rows):
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=tuple(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    def rehash_generated(self, output_dir, name):
        manifest_path = output_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["generated_sha256"][name] = hashlib.sha256((output_dir / name).read_bytes()).hexdigest()
        manifest_path.write_bytes(canonical_json(manifest))

    def test_direct_forecast_batches_unique_etfs_once_preserves_histories_and_has_only_future_ecb(self):
        calls = []

        def predictor(items, prediction_length, quantile_levels):
            calls.append(items)
            return self.forecast_predictor(items, prediction_length, quantile_levels)

        result = self.publish(predictor=predictor)
        forecasts = self.csv_rows(result.output_dir / "etf_forecast.csv")
        paths = self.csv_rows(result.output_dir / "portfolio_paths.csv")

        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0]), 15)
        self.assertEqual([item["target"].shape[1] for item in calls[0][::3]], [61, 61, 61, 61, 49])
        for item in calls[0]:
            self.assertEqual(tuple(item["past_covariates"]), (
                "ECB_DFR", "US_TREASURY_10Y", "BRENT_RETURN", "BIS_USD_CREDIT_YOY", "US_CPI_YOY",
            ))
            self.assertEqual(tuple(item["future_covariates"]), ("ECB_DFR",))
            self.assertEqual(item["future_covariates"]["ECB_DFR"].shape, (12,))
        self.assertEqual(len(forecasts), 5 * 3 * 12)
        self.assertEqual(set(paths[0]), {
            "portfolio", "scenario", "forecast_month", "horizon", "central_return", "cumulative_eur_100",
        })
        self.assertNotIn("q10", paths[0])
        self.assertNotIn("q90", paths[0])
        self.assertEqual({row["portfolio"] for row in paths}, {"CANDIDATE", "BASE"})
        candidate = next(row for row in paths if row["portfolio"] == "CANDIDATE" and row["scenario"] == "ECB_FLAT" and row["horizon"] == "1")
        base = next(row for row in paths if row["portfolio"] == "BASE" and row["scenario"] == "ECB_FLAT" and row["horizon"] == "1")
        self.assertAlmostEqual(float(candidate["central_return"]), 0.019)
        self.assertAlmostEqual(float(candidate["cumulative_eur_100"]), 101.9)
        candidate_second = next(row for row in paths if row["portfolio"] == "CANDIDATE" and row["scenario"] == "ECB_FLAT" and row["horizon"] == "2")
        self.assertAlmostEqual(float(candidate_second["cumulative_eur_100"]), 100.0 * 1.019**2)
        self.assertAlmostEqual(float(base["central_return"]), 0.0185)
        self.assertEqual({path.name for path in result.output_dir.iterdir()}, {
            "etf_forecast.csv", "portfolio_paths.csv", "scenario_sensitivity.csv", "volatility_snapshot.csv", "manifest.json",
        })

    def test_direct_forecast_rejects_bad_result_count_shape_values_and_quantile_order_with_ticker(self):
        good = np.zeros((1, 12, 3))
        bad_results = (
            [good] * 14,
            [np.zeros((1, 11, 3)), *([good] * 14)],
            [np.full((1, 12, 3), np.nan), *([good] * 14)],
            [np.tile(np.asarray([[[1.0, 0.0, 2.0]]]), (1, 12, 1)), *([good] * 14)],
        )
        for raw in bad_results:
            with self.subTest(count=len(raw), shape=np.asarray(raw[0]).shape), self.assertRaisesRegex(ValueError, r"SWDA\.MI"):
                self.publish(predictor=lambda *_args, raw=raw, **_kwargs: raw)
            self.assertFalse((self.paths.chronos_output_root / "forecasts").exists())

    def test_direct_forecast_rejects_one_invalid_component_without_renormalizing(self):
        from perpetual_engine import dashboard_chronos
        from perpetual_engine.portfolio_monitor import load_current_portfolio_prices

        from perpetual_engine.dashboard_service import materialize_runtime_config
        materialize_runtime_config(
            self.paths.default_config, self.state, self.paths.runtime_config, project_root=self.paths.project_root,
        )
        config, raw, manifest = load_current_portfolio_prices(self.paths.runtime_config, project_root=self.paths.project_root)
        raw.pop("GRID.MI")
        with patch.object(dashboard_chronos, "load_current_portfolio_prices", return_value=(config, raw, manifest)), patch(
            "perpetual_engine.dashboard_chronos.load_covariate_table",
            return_value=(self.covariates(), self.macro_vintage_id),
        ), self.assertRaisesRegex(ValueError, r"GRID\.MI"):
            dashboard_chronos.publish_direct_forecast(
                self.paths, self.state, predictor=self.forecast_predictor,
            )
        self.assertFalse((self.paths.chronos_output_root / "forecasts").exists())

    def test_direct_forecast_rejects_macro_vintage_that_does_not_cover_every_retained_etf_month(self):
        from perpetual_engine.dashboard_chronos import publish_direct_forecast
        from perpetual_engine.chronos_data import MonthlyTable

        macro = self.covariates()
        incomplete = MonthlyTable(macro.months[20:], macro.names, macro.values[:, 20:])
        with patch(
            "perpetual_engine.dashboard_chronos.load_covariate_table",
            return_value=(incomplete, self.macro_vintage_id),
        ), self.assertRaisesRegex(ValueError, r"SWDA\.MI.*macro history"):
            publish_direct_forecast(
                self.paths, self.state, predictor=self.forecast_predictor,
                issued_at=datetime(2026, 3, 3, tzinfo=timezone.utc),
            )
        self.assertFalse((self.paths.chronos_output_root / "forecasts").exists())

    def test_direct_forecast_identity_manifest_sensitivity_and_byte_identical_reuse(self):
        from perpetual_engine.dashboard_chronos import read_direct_forecast, save_candidate_portfolio

        first = self.publish()
        original = {path.name: path.read_bytes() for path in first.output_dir.iterdir()}
        manifest, rows = read_direct_forecast(first.output_dir, self.paths.chronos_output_root)
        self.assertEqual(len(rows), 180)
        self.assertEqual(manifest["forecast_id"], first.forecast_id)
        price_manifest = (
            self.root / "data" / "portfolio_p_v1" / "vintages"
            / manifest["price_vintage"]["vintage_id"] / "manifest.json"
        )
        self.assertEqual(
            manifest["price_vintage"]["manifest_sha256"], hashlib.sha256(price_manifest.read_bytes()).hexdigest(),
        )
        self.assertEqual(tuple(item["ticker"] for item in manifest["target_series"]), (
            "SWDA.MI", "IWMO.MI", "IWQU.MI", "GRID.MI", "DBMFE.PA",
        ))
        self.assertEqual(manifest["macro_vintage_id"], self.macro_vintage_id)
        self.assertEqual(manifest["warnings"], [{"symbol": "DBMFE.PA", "warning": "STORICO_BREVE"}])
        self.assertEqual(set(manifest["generated_sha256"]), {
            "etf_forecast.csv", "portfolio_paths.csv", "scenario_sensitivity.csv", "volatility_snapshot.csv",
        })
        for name, digest in manifest["generated_sha256"].items():
            self.assertEqual(hashlib.sha256((first.output_dir / name).read_bytes()).hexdigest(), digest)

        repeated = self.publish(
            predictor=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("existing forecast must be reused")),
            issued_at=datetime(2026, 3, 4, tzinfo=timezone.utc),
        )
        self.assertEqual(repeated.forecast_id, first.forecast_id)
        self.assertEqual(original, {path.name: path.read_bytes() for path in repeated.output_dir.iterdir()})

        (first.output_dir / "etf_forecast.csv").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "hash"):
            self.publish()
        for name, content in original.items():
            (first.output_dir / name).write_bytes(content)

        save_candidate_portfolio(
            self.paths, self.state,
            self.rows(("SWDA", 0.50), ("IWMO", 0.20), ("IWQU", 0.15), ("GRID", 0.15)),
        )
        changed = self.publish()
        self.assertNotEqual(changed.forecast_id, first.forecast_id)

    def test_direct_forecast_loads_predictor_once_and_publishes_etf_and_portfolio_sensitivity(self):
        from perpetual_engine.dashboard_chronos import load_etf_target_snapshot

        long_dates = months(62, date(2021, 1, 31))
        short_dates = long_dates[12:]
        dates_by_ticker = {
            "SWDA.MI": long_dates, "IWMO.MI": long_dates, "IWQU.MI": long_dates,
            "GRID.MI": long_dates, "DBMFE.PA": short_dates,
        }
        varying = {
            ticker: daily_bytes(list(prices_from_returns(
                dates, tuple(0.01 if index % 2 else 0.03 for index in range(len(dates) - 1)),
            ).items()))
            for ticker, dates in dates_by_ticker.items()
        }
        self.refresh_prices(varying)
        loader_calls = []

        def load_predictor(config):
            loader_calls.append(config)
            return self.forecast_predictor

        with patch("perpetual_engine.dashboard_chronos.load_chronos_predictor", side_effect=load_predictor):
            result = self.publish(predictor=None)

        self.assertEqual(len(loader_calls), 1)
        sensitivity = self.csv_rows(result.output_dir / "scenario_sensitivity.csv")
        self.assertEqual({row["scope"] for row in sensitivity}, {"ETF", "PORTFOLIO"})
        portfolio = next(row for row in sensitivity if row["scope"] == "PORTFOLIO" and row["portfolio"] == "CANDIDATE" and row["scenario"] == "ECB_DOWN_100BP" and row["horizon"] == "1")
        self.assertAlmostEqual(float(portfolio["q50_delta"]), 0.001)

        volatility = self.csv_rows(result.output_dir / "volatility_snapshot.csv")
        self.assertEqual(len(volatility), 5 * 3 * 12)
        swda = next(row for row in volatility if row["symbol"] == "SWDA.MI" and row["scenario"] == "ECB_FLAT" and row["horizon"] == "1")
        swda_returns = next(
            series.returns for series in load_etf_target_snapshot(self.paths, self.state).series
            if series.component.ticker == "SWDA.MI"
        )
        self.assertAlmostEqual(float(swda["trailing_volatility_12m"]), float(np.std(swda_returns[-12:], ddof=1) * math.sqrt(12)))
        self.assertAlmostEqual(float(swda["interval_width"]), 0.02)

    def test_direct_forecast_reader_recomputes_tampered_rehashed_derived_artifacts(self):
        from perpetual_engine.dashboard_chronos import read_direct_forecast

        result = self.publish()
        original = {path.name: path.read_bytes() for path in result.output_dir.iterdir()}
        mutations = {
            "portfolio_paths.csv": ("central_return", "nan"),
            "scenario_sensitivity.csv": ("q50_delta", "0.123"),
            "volatility_snapshot.csv": ("trailing_volatility_12m", "999"),
        }
        for name, (field, value) in mutations.items():
            with self.subTest(name=name):
                rows = self.csv_rows(result.output_dir / name)
                rows[0][field] = value
                self.write_csv_rows(result.output_dir / name, rows)
                self.rehash_generated(result.output_dir, name)
                with self.assertRaisesRegex(ValueError, r"portfolio|sensitivity|volatility"):
                    read_direct_forecast(result.output_dir, self.paths.chronos_output_root)
                for filename, content in original.items():
                    (result.output_dir / filename).write_bytes(content)

    def test_direct_forecast_reader_rejects_unexpected_warning_even_when_outputs_are_rehashed(self):
        from perpetual_engine.dashboard_chronos import read_direct_forecast

        result = self.publish()
        manifest_path = result.output_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["warnings"] = []
        manifest_path.write_bytes(canonical_json(manifest))
        with self.assertRaisesRegex(ValueError, "warning"):
            read_direct_forecast(result.output_dir, self.paths.chronos_output_root)


class DirectReconciliationTests(DirectChronosFixture):
    def setUp(self):
        super().setUp()
        self.refresh_prices()
        self.state_with_grid_candidate()
        self.macro_vintage_id = "a" * 64

    def publish_known_forecast(self):
        from perpetual_engine.dashboard_chronos import publish_direct_forecast

        with patch(
            "perpetual_engine.dashboard_chronos.load_covariate_table",
            return_value=(self.covariates(), self.macro_vintage_id),
        ):
            return publish_direct_forecast(
                self.paths, self.state, predictor=self.forecast_predictor,
                issued_at=datetime(2026, 3, 3, tzinfo=timezone.utc),
            )

    def add_march_returns(self):
        dates = (*months(62, date(2021, 1, 31)), date(2026, 3, 31))
        payloads = {}
        for ticker in self.current_prices():
            returns = [0.01] * (len(dates) - 1)
            if ticker == "SWDA.MI":
                returns[-1] = 0.03
            payloads[ticker] = daily_bytes(list(prices_from_returns(dates, tuple(returns)).items()))
        self.retrieved_at = datetime(2026, 4, 2, tzinfo=timezone.utc)
        self.refresh_prices(payloads)

    @staticmethod
    def csv_rows(path):
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    def test_reconciliation_records_actual_minus_q50_and_keeps_future_rows_pending(self):
        from perpetual_engine.dashboard_chronos import MONITORING_COLUMNS, reconcile_direct_forecasts

        forecast = self.publish_known_forecast()
        self.add_march_returns()
        monitoring = reconcile_direct_forecasts(self.paths, self.state)
        actual = self.csv_rows(monitoring / "forecast_vs_actual.csv")
        row = next(
            item for item in actual
            if item["scope"] == "ETF" and item["symbol"] == "SWDA.MI"
            and item["scenario"] == "ECB_FLAT" and item["horizon"] == "1"
        )
        self.assertEqual(row["forecast_id"], forecast.forecast_id)
        self.assertAlmostEqual(float(row["signed_error"]), 0.02)
        self.assertAlmostEqual(float(row["absolute_error"]), 0.02)
        self.assertAlmostEqual(float(row["squared_error"]), 0.0004)
        self.assertEqual(tuple(row), MONITORING_COLUMNS)
        pending = self.csv_rows(monitoring / "pending_forecasts.csv")
        self.assertEqual(tuple(pending[0]), MONITORING_COLUMNS)
        self.assertIn("2026-04-30", {item["forecast_month"] for item in pending})
        metrics = self.csv_rows(monitoring / "live_metrics.csv")
        component = next(item for item in metrics if item["scope"] == "ETF" and item["symbol"] == "SWDA.MI")
        self.assertEqual(component["count"], "1")
        self.assertNotEqual(component["interval_80_coverage"], "")

    def test_monitoring_reader_rejects_unapproved_or_tampered_archives(self):
        from perpetual_engine.dashboard_chronos import read_direct_monitoring, reconcile_direct_forecasts

        self.publish_known_forecast()
        monitoring = reconcile_direct_forecasts(self.paths, self.state)
        manifest, tables = read_direct_monitoring(monitoring, self.paths.chronos_output_root)
        self.assertEqual(manifest["monitoring_id"], monitoring.name)
        self.assertEqual(set(tables), {"forecast_vs_actual.csv", "pending_forecasts.csv", "live_metrics.csv"})

        with self.assertRaisesRegex(ValueError, "escapes monitoring root"):
            read_direct_monitoring(monitoring, self.root / "unapproved")

        (monitoring / "live_metrics.csv").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash"):
            read_direct_monitoring(monitoring, self.paths.chronos_output_root)

    def test_reconciliation_uses_archived_weights_for_candidate_and_base(self):
        from perpetual_engine.dashboard_chronos import reconcile_direct_forecasts, save_candidate_portfolio

        forecast = self.publish_known_forecast()
        save_candidate_portfolio(
            self.paths, self.state,
            self.rows(("SWDA", 0.50), ("IWMO", 0.20), ("IWQU", 0.15), ("GRID", 0.15)),
        )
        self.add_march_returns()
        monitoring = reconcile_direct_forecasts(self.paths, self.state)
        row = next(
            item for item in self.csv_rows(monitoring / "forecast_vs_actual.csv")
            if item["scope"] == "CANDIDATE_PORTFOLIO" and item["scenario"] == "ECB_FLAT"
            and item["horizon"] == "1"
        )
        self.assertEqual(row["portfolio_sha256"], forecast.candidate_sha256)
        self.assertAlmostEqual(float(row["q50"]), 0.019)
        self.assertEqual((row["q10"], row["q90"], row["interval_hit"]), ("", "", ""))

    def test_reconciliation_rejects_tampered_forecast_before_publishing_monitoring(self):
        from perpetual_engine.dashboard_chronos import reconcile_direct_forecasts

        forecast = self.publish_known_forecast()
        monitoring_root = self.paths.chronos_output_root / "monitoring"
        before = {path.name for path in monitoring_root.iterdir()}
        (forecast.output_dir / "etf_forecast.csv").write_text("tampered", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "hash"):
            reconcile_direct_forecasts(self.paths, self.state)
        self.assertEqual({path.name for path in monitoring_root.iterdir()}, before)

    def test_forecast_publication_keeps_its_archive_when_reconciliation_fails(self):
        from perpetual_engine.dashboard_chronos import (
            publish_direct_forecast, read_direct_forecast, save_candidate_portfolio,
        )

        old = self.publish_known_forecast()
        (old.output_dir / "etf_forecast.csv").write_text("tampered", encoding="utf-8")
        save_candidate_portfolio(
            self.paths, self.state,
            self.rows(("SWDA", 0.50), ("IWMO", 0.20), ("IWQU", 0.15), ("GRID", 0.15)),
        )
        with patch(
            "perpetual_engine.dashboard_chronos.load_covariate_table",
            return_value=(self.covariates(), self.macro_vintage_id),
        ), self.assertRaisesRegex(ValueError, "hash"):
            publish_direct_forecast(
                self.paths, self.state, predictor=self.forecast_predictor,
                issued_at=datetime(2026, 3, 3, tzinfo=timezone.utc),
            )
        archives = sorted((self.paths.chronos_output_root / "forecasts").iterdir())
        self.assertEqual(len(archives), 2)
        read_direct_forecast(next(path for path in archives if path != old.output_dir), self.paths.chronos_output_root)

    def test_reconciliation_binds_the_price_manifest_bytes_it_validated(self):
        from perpetual_engine import dashboard_chronos
        from perpetual_engine.dashboard_service import materialize_runtime_config
        from perpetual_engine.portfolio_monitor import load_current_portfolio_prices

        self.publish_known_forecast()
        runtime = materialize_runtime_config(
            self.paths.default_config, self.state, self.paths.runtime_config, project_root=self.paths.project_root,
        )
        config, raw, manifest = load_current_portfolio_prices(runtime, project_root=self.paths.project_root)
        manifest_path = config.data_root / "vintages" / manifest["vintage_id"] / "manifest.json"
        validated_bytes = manifest_path.read_bytes()

        def validated_loader(*_args, include_manifest_bytes=False, **_kwargs):
            values = (config, raw, manifest)
            return (*values, validated_bytes) if include_manifest_bytes else values

        manifest_path.write_bytes(b"{}")
        with patch.object(dashboard_chronos, "load_current_portfolio_prices", side_effect=validated_loader):
            monitoring = dashboard_chronos.reconcile_direct_forecasts(self.paths, self.state)
        published = json.loads((monitoring / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            published["price_vintage"]["manifest_sha256"], hashlib.sha256(validated_bytes).hexdigest(),
        )

    def test_reconciliation_rejects_archive_changed_after_validation_before_identity_capture(self):
        from perpetual_engine import dashboard_chronos

        forecast = self.publish_known_forecast()
        original = dashboard_chronos.read_direct_forecast
        changed = False

        def read_then_change(*args, **kwargs):
            nonlocal changed
            result = original(*args, **kwargs)
            if not changed:
                changed = True
                manifest_path = forecast.output_dir / "manifest.json"
                manifest_path.write_bytes(b"\n" + manifest_path.read_bytes())
            return result

        with patch.object(dashboard_chronos, "read_direct_forecast", side_effect=read_then_change), self.assertRaisesRegex(
            ValueError, "inputs changed",
        ):
            dashboard_chronos.reconcile_direct_forecasts(self.paths, self.state)


class DirectEvaluationTests(DirectChronosFixture):
    def setUp(self):
        super().setUp()
        self.state_with_grid_candidate()
        self.macro_vintage_id = "b" * 64
        self.macro = self._evaluation_covariates()
        manifest = self.paths.chronos_config.parent.parent / "data" / "chronos_v1" / "vintages" / self.macro_vintage_id / "manifest.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_bytes(b"frozen macro manifest\n")

    @staticmethod
    def csv_rows(path):
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))

    @staticmethod
    def _master_dates():
        return months(100, date(2017, 11, 30))

    def _evaluation_covariates(self):
        from perpetual_engine.chronos_data import MonthlyTable, REQUIRED_V1_COVARIATES

        dates = self._master_dates()[1:]
        values = np.vstack([
            np.arange(len(dates), dtype=float) / 100.0 + offset
            for offset in range(len(REQUIRED_V1_COVARIATES))
        ])
        return MonthlyTable(dates, REQUIRED_V1_COVARIATES, values)

    def refresh_counts(self, **counts):
        master = self._master_dates()
        defaults = {ticker: 90 for ticker in self.current_prices()}
        defaults.update(counts)
        payloads = {
            ticker: daily_bytes(list(prices_from_returns(
                master[-(count + 1):],
                tuple(0.005 + ((index % 5) - 2) * 0.002 for index in range(count)),
            ).items()))
            for ticker, count in defaults.items()
        }
        self.refresh_prices(payloads)

    def prepare(self):
        from perpetual_engine.dashboard_chronos import prepare_direct_evaluation_request

        with patch(
            "perpetual_engine.dashboard_chronos.load_covariate_table",
            return_value=(self.macro, self.macro_vintage_id),
        ):
            return prepare_direct_evaluation_request(self.paths, self.state)

    def evaluate(self, request, predictor):
        from perpetual_engine.dashboard_chronos import evaluate_direct_request

        with patch(
            "perpetual_engine.dashboard_chronos.load_covariate_vintage",
            return_value=self.macro,
        ):
            return evaluate_direct_request(self.root, request, predictor=predictor)

    @staticmethod
    def variant_predictor(items, prediction_length, quantile_levels):
        from perpetual_engine.chronos_data import REQUIRED_V1_COVARIATES

        assert prediction_length == 12
        assert quantile_levels == [0.1, 0.5, 0.9]
        results = []
        for item in items:
            enabled = tuple(item.get("past_covariates", ()))
            if not enabled:
                offset = 0.004
            elif len(enabled) == 1:
                offset = 0.002
            elif len(enabled) == 5:
                offset = 0.001
            else:
                missing = next(name for name in REQUIRED_V1_COVARIATES if name not in enabled)
                offset = {
                    "ECB_DFR": 0.004,
                    "US_TREASURY_10Y": 0.0,
                    "BRENT_RETURN": 0.001,
                }.get(missing, 0.004)
            median = 0.005 + offset
            results.append(np.tile(np.asarray([[[median - 0.002, median, median + 0.002]]]), (1, 12, 1)))
        return results

    def test_evaluation_caps_each_scope_at_36_safe_origins_and_chunks_predictions(self):
        self.refresh_counts()
        request = self.prepare()
        calls = []

        def predictor(items, prediction_length, quantile_levels):
            calls.append(items)
            return self.variant_predictor(items, prediction_length, quantile_levels)

        output = self.evaluate(request, predictor)
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest["evaluation_origins"]), {
            "SWDA.MI", "IWMO.MI", "IWQU.MI", "GRID.MI", "CANDIDATE_PORTFOLIO",
        })
        self.assertEqual({value["count"] for value in manifest["evaluation_origins"].values()}, {36})
        self.assertTrue(calls)
        self.assertLessEqual(max(map(len, calls)), 36)
        self.assertGreaterEqual(min(item["target"].shape[1] for call in calls for item in call), 36)
        self.assertTrue(all("future_covariates" not in item for call in calls for item in call))
        self.assertTrue(all(
            all(len(values) == item["target"].shape[1] for values in item.get("past_covariates", {}).values())
            for call in calls for item in call
        ))
        predictions = self.csv_rows(output / "predictions.csv")
        self.assertNotIn("ORACLE_FUTURE_RATE_UPPER_BOUND", {row["variant"] for row in predictions})
        self.assertIn("ZERO_RETURN_BASELINE", {row["variant"] for row in predictions})

    def test_mixed_history_evaluates_each_eligible_etf_and_gates_only_ineligible_scopes(self):
        self.refresh_counts(**{"IWQU.MI": 59, "GRID.MI": 49})
        output = self.evaluate(self.prepare(), self.variant_predictor)
        rows = self.csv_rows(output / "covariate_contribution.csv")
        by_scope = {}
        for row in rows:
            by_scope.setdefault(row["scope"], set()).add(row["classification"])
        self.assertNotEqual(by_scope["SWDA.MI"], {"INSUFFICIENT_HISTORY"})
        self.assertNotEqual(by_scope["IWMO.MI"], {"INSUFFICIENT_HISTORY"})
        self.assertEqual(by_scope["IWQU.MI"], {"INSUFFICIENT_HISTORY"})
        self.assertEqual(by_scope["GRID.MI"], {"INSUFFICIENT_HISTORY"})
        self.assertEqual(by_scope["CANDIDATE_PORTFOLIO"], {"INSUFFICIENT_HISTORY"})
        self.assertEqual({row["origin_count"] for row in rows if row["scope"] == "SWDA.MI"}, {"36"})
        self.assertEqual({row["origin_count"] for row in rows if row["scope"] == "GRID.MI"}, {"2"})

    def test_all_short_history_publishes_insufficient_history_without_loading_model(self):
        self.refresh_counts(**{ticker: 49 for ticker in self.current_prices()})
        output = self.evaluate(
            self.prepare(), Mock(side_effect=AssertionError("model must not run")),
        )
        rows = self.csv_rows(output / "covariate_contribution.csv")
        self.assertEqual({row["classification"] for row in rows}, {"INSUFFICIENT_HISTORY"})
        self.assertEqual({row["loss_metric"] for row in rows}, {"PINBALL", "MAE_Q50"})
        self.assertEqual({row["origin_count"] for row in rows}, {"2"})

    def test_contribution_metrics_and_volatility_use_scope_specific_origins(self):
        self.refresh_counts()
        output = self.evaluate(self.prepare(), self.variant_predictor)
        contributions = self.csv_rows(output / "covariate_contribution.csv")
        self.assertEqual(
            {row["loss_metric"] for row in contributions if row["scope"] != "CANDIDATE_PORTFOLIO"},
            {"PINBALL"},
        )
        self.assertEqual(
            {row["loss_metric"] for row in contributions if row["scope"] == "CANDIDATE_PORTFOLIO"},
            {"MAE_Q50"},
        )
        conditional = [row for row in contributions if row["comparison"] == "CONDITIONAL"]
        self.assertEqual({row["classification"] for row in conditional}, {"USEFUL", "HARMFUL", "INCONCLUSIVE"})
        diagnostics = self.csv_rows(output / "volatility_diagnostics.csv")
        first = diagnostics[0]
        request = json.loads((self.prepare()).read_text(encoding="utf-8"))
        series = next(item for item in request["series"] if item["symbol"] == first["symbol"])
        position = series["months"].index(first["origin"])
        self.assertAlmostEqual(
            float(first["trailing_volatility_12m"]),
            statistics.stdev(series["returns"][position - 11:position + 1]) * math.sqrt(12),
        )
        metrics = self.csv_rows(output / "metrics.csv")
        self.assertEqual({row["origin_count"] for row in metrics}, {"36"})
        significance = self.csv_rows(output / "monthly_significance.csv")
        self.assertEqual({row["scope"] for row in significance}, {"CANDIDATE_PORTFOLIO"})
        self.assertEqual({row["comparison"] for row in significance}, {"CONDITIONAL"})
        grouped = {}
        for row in significance:
            grouped.setdefault((row["covariate"], row["horizon"]), []).append(row)
        self.assertTrue(all([int(row["origin_count"]) for row in rows] == list(range(1, 37)) for rows in grouped.values()))
        self.assertTrue(all(row["p_value"] == "" for rows in grouped.values() for row in rows[:5]))
        self.assertTrue(all(row["p_value"] != "" for rows in grouped.values() for row in rows[5:]))

    def test_request_binds_only_candidate_series_and_worker_revalidates_before_model_load(self):
        self.refresh_counts()
        request_path = self.prepare()
        request = json.loads(request_path.read_text(encoding="utf-8"))
        self.assertEqual(tuple(item["symbol"] for item in request["series"]), (
            "SWDA.MI", "IWMO.MI", "IWQU.MI", "GRID.MI",
        ))
        self.assertTrue(all(not Path(value).is_absolute() for value in request["paths"].values()))
        from perpetual_engine.dashboard_chronos import evaluate_direct_request
        for name in ("config", "price_manifest", "macro_manifest"):
            with self.subTest(name=name):
                bound = self.root / request["paths"][name]
                original = bound.read_bytes()
                bound.write_bytes(original + b"\n")
                loader = Mock(side_effect=AssertionError("model must not load"))
                try:
                    with patch("perpetual_engine.dashboard_chronos.load_chronos_predictor", loader), patch(
                        "perpetual_engine.dashboard_chronos.load_covariate_vintage",
                        return_value=self.macro,
                    ), self.assertRaisesRegex(ValueError, f"{name.split('_')[0]}.*hash"):
                        evaluate_direct_request(self.root, request_path)
                finally:
                    bound.write_bytes(original)
                loader.assert_not_called()

    def test_evaluation_loads_model_once_and_reuses_only_byte_identical_five_file_archive(self):
        self.refresh_counts()
        request = self.prepare()
        loader = Mock(return_value=self.variant_predictor)
        with patch("perpetual_engine.dashboard_chronos.load_chronos_predictor", loader), patch(
            "perpetual_engine.dashboard_chronos.load_covariate_vintage",
            return_value=self.macro,
        ):
            first = __import__("perpetual_engine.dashboard_chronos", fromlist=["evaluate_direct_request"]).evaluate_direct_request(
                self.root, request,
            )
        loader.assert_called_once()
        self.assertEqual({path.name for path in first.iterdir()}, {
            "predictions.csv", "metrics.csv", "covariate_contribution.csv", "volatility_diagnostics.csv",
            "monthly_significance.csv", "manifest.json",
        })
        before = {path.name: path.read_bytes() for path in first.iterdir()}
        repeated = self.evaluate(request, Mock(side_effect=AssertionError("archive must be reused")))
        self.assertEqual(repeated, first)
        self.assertEqual(before, {path.name: path.read_bytes() for path in repeated.iterdir()})
        (first / "predictions.csv").write_bytes(b"corrupt")
        with self.assertRaisesRegex(ValueError, "hash|collision"):
            self.evaluate(request, self.variant_predictor)

    def test_evaluation_reader_rejects_rehashed_non_finite_metric(self):
        from perpetual_engine.dashboard_chronos import read_direct_evaluation

        self.refresh_counts()
        output = self.evaluate(self.prepare(), self.variant_predictor)
        rows = self.csv_rows(output / "metrics.csv")
        rows[0]["mae_q50"] = "nan"
        DirectForecastTests.write_csv_rows(output / "metrics.csv", rows)
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["generated_sha256"]["metrics.csv"] = hashlib.sha256((output / "metrics.csv").read_bytes()).hexdigest()
        manifest_path.write_bytes(canonical_json(manifest))
        with patch(
            "perpetual_engine.dashboard_chronos.load_covariate_vintage", return_value=self.macro,
        ), self.assertRaisesRegex(ValueError, "metric"):
            read_direct_evaluation(output, self.paths.chronos_output_root)

    def test_existing_evaluation_request_rejects_symlinked_request_file(self):
        self.refresh_counts()
        request_path = self.prepare()
        payload = request_path.read_bytes()
        outside = self.root / "outside-request.json"
        outside.write_bytes(payload)
        request_path.unlink()
        try:
            request_path.symlink_to(outside)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f"file symlinks unavailable: {error}")
        with self.assertRaisesRegex(ValueError, "request collision"):
            self.prepare()

    def test_evaluation_reader_rejects_noncanonical_manifest_bytes(self):
        from perpetual_engine.dashboard_chronos import read_direct_evaluation

        self.refresh_counts()
        output = self.evaluate(self.prepare(), self.variant_predictor)
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        with patch(
            "perpetual_engine.dashboard_chronos.load_covariate_vintage", return_value=self.macro,
        ), self.assertRaisesRegex(ValueError, "canonical"):
            read_direct_evaluation(output, self.paths.chronos_output_root)
