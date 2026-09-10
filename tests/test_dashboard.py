from __future__ import annotations

import csv
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest


class DashboardAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        source = Path(__file__).parents[1] / "config" / "portfolio_p_v1.json"
        destination = self.root / "config" / source.name
        destination.parent.mkdir()
        shutil.copy2(source, destination)
        self.app_path = Path(__file__).parents[1] / "perpetual_engine" / "dashboard.py"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def comparison_report(self) -> Path:
        output = self.root / "report"
        output.mkdir()
        rows = {
            "comparison_monthly.csv": ([
                "month", "etf_return", "portfolio_return", "etf_cumulative_value", "portfolio_cumulative_value",
                "etf_drawdown", "portfolio_drawdown", "etf_trailing_volatility_12m",
                "portfolio_trailing_volatility_12m", "rolling_correlation_12m",
            ], [["2026-01-31", "0.01", "0.02", "101", "102", "0", "0", "", "", ""]]),
            "component_contributions.csv": (["month", "SWDA", "SWDA_cumulative", "portfolio_return"], [["2026-01-31", "0.02", "0.02", "0.02"]]),
            "comparison_summary.csv": ([
                "first_month", "last_month", "count", "etf_cumulative_return", "portfolio_cumulative_return",
                "etf_annualized_return", "portfolio_annualized_return", "etf_annualized_volatility",
                "portfolio_annualized_volatility", "etf_max_drawdown", "portfolio_max_drawdown", "correlation",
                "etf_winning_months", "portfolio_winning_months", "tied_months", "study_id", "name", "ticker", "isin",
                "cumulative_return_difference", "SHORT_LIVE_HISTORY",
            ], [[
                "2025-08-31", "2026-01-31", "6", "0.01", "0.02", "0.02", "0.04", "0.10", "0.08",
                "-0.05", "-0.03", "0.20", "2", "3", "1", "SWDA", "SWDA", "SWDA.MI", "IE00B4L5Y983",
                "-0.01", "True",
            ]]),
        }
        for name, (fieldnames, values) in rows.items():
            with (output / name).open("w", newline="", encoding="utf-8") as target:
                writer = csv.writer(target)
                writer.writerow(fieldnames)
                writer.writerows(values)
        return output

    def direct_forecast_archive(self) -> tuple[Path, Path, dict[str, object], tuple[dict[str, str], ...]]:
        forecast = self.root / "outputs" / "dashboard_chronos_v1" / "forecasts" / ("f" * 64)
        monitoring = self.root / "outputs" / "dashboard_chronos_v1" / "monitoring" / ("a" * 64)
        forecast.mkdir(parents=True)
        monitoring.mkdir(parents=True)

        def write_csv(path: Path, fieldnames: list[str], rows: list[list[object]]) -> None:
            with path.open("w", newline="", encoding="utf-8") as target:
                writer = csv.writer(target)
                writer.writerow(fieldnames)
                writer.writerows(rows)

        etf_columns = [
            "scenario", "symbol", "isin", "exchange", "currency", "forecast_month", "horizon",
            "q10", "q50", "q90", "history_count", "history_status",
        ]
        etf_values = [
            [scenario, "SWDA.MI", "IE00B4L5Y983", "Milan", "EUR", "2026-04-30", 1, -0.02, 0.01, 0.04, 61, "SUFFICIENT_HISTORY"]
            for scenario in ("ECB_FLAT", "ECB_DOWN_100BP", "ECB_UP_100BP")
        ]
        write_csv(forecast / "etf_forecast.csv", etf_columns, etf_values)
        write_csv(
            forecast / "portfolio_paths.csv",
            ["portfolio", "scenario", "forecast_month", "horizon", "central_return", "cumulative_eur_100"],
            [[portfolio, "ECB_FLAT", "2026-04-30", 1, 0.01, value] for portfolio, value in (("CANDIDATE", 101), ("BASE", 100.5))],
        )
        write_csv(
            forecast / "scenario_sensitivity.csv",
            [
                "scope", "portfolio", "symbol", "isin", "forecast_month", "horizon", "scenario", "q50",
                "flat_q50", "q50_delta", "interval_width", "flat_interval_width", "interval_width_delta",
            ],
            [["ETF", "", "SWDA.MI", "IE00B4L5Y983", "2026-04-30", 1, "ECB_DOWN_100BP", 0.009, 0.01, -0.001, 0.06, 0.06, 0]],
        )
        write_csv(
            forecast / "volatility_snapshot.csv",
            [
                "symbol", "isin", "origin", "history_count", "history_status", "trailing_volatility_12m",
                "scenario", "forecast_month", "horizon", "interval_width",
            ],
            [["SWDA.MI", "IE00B4L5Y983", "2026-03-31", 61, "SUFFICIENT_HISTORY", 0.12, "ECB_FLAT", "2026-04-30", 1, 0.06]],
        )
        manifest = {
            "schema_version": "DIRECT_CHRONOS_FORECAST_V1",
            "forecast_id": "f" * 64,
            "issued_at": "2026-04-01T00:00:00+00:00",
            "common_origin": "2026-03-31",
            "model": {"id": "amazon/chronos-2", "revision": "model-revision", "package": "chronos-forecasting", "package_version": "2.0", "device": "cpu"},
            "config_sha256": "1" * 64,
            "portfolios": {"candidate": {}, "base": {}},
            "candidate_sha256": "2" * 64,
            "base_sha256": "3" * 64,
            "target_series": [],
            "price_vintage": {"vintage_id": "4" * 64, "manifest_sha256": "5" * 64},
            "macro_vintage_id": "6" * 64,
            "scenarios": {name: [2.0] * 12 for name in ("ECB_FLAT", "ECB_DOWN_100BP", "ECB_UP_100BP")},
            "warnings": [{"symbol": "SWDA.MI", "warning": "STORICO_BREVE"}],
            "labels": {"forecast": "PROSPECTIVE_SCENARIO_FORECAST", "target_history": "RETROSPECTIVE_INPUT_ONLY", "portfolio_paths": "CENTRAL_Q50_ONLY", "usage": "RESEARCH_ONLY"},
            "generated_sha256": {},
        }
        (forecast / "manifest.json").write_text("{}", encoding="utf-8")
        monitoring_columns = [
            "forecast_id", "issued_at", "origin", "scope", "portfolio_sha256", "scenario", "symbol", "isin",
            "forecast_month", "horizon", "q10", "q50", "q90", "actual", "signed_error", "absolute_error",
            "squared_error", "interval_hit",
        ]
        write_csv(
            monitoring / "forecast_vs_actual.csv", monitoring_columns,
            [["f" * 64, "2026-04-01T00:00:00+00:00", "2026-03-31", "ETF", "", "ECB_FLAT", "SWDA.MI", "IE00B4L5Y983", "2026-04-30", 1, -0.02, 0.01, 0.04, 0.015, 0.005, 0.005, 0.000025, "true"]],
        )
        write_csv(
            monitoring / "pending_forecasts.csv", monitoring_columns,
            [["f" * 64, "2026-04-01T00:00:00+00:00", "2026-03-31", "ETF", "", "ECB_FLAT", "SWDA.MI", "IE00B4L5Y983", "2026-05-31", 2, -0.03, 0.02, 0.05, "", "", "", "", ""]],
        )
        write_csv(
            monitoring / "live_metrics.csv",
            ["scope", "scenario", "symbol", "isin", "portfolio_sha256", "horizon", "count", "bias", "mae", "rmse", "interval_80_coverage"],
            [["ETF", "ECB_FLAT", "SWDA.MI", "IE00B4L5Y983", "", 1, 1, 0.005, 0.005, 0.005, 1]],
        )
        return forecast, monitoring, manifest, tuple(dict(zip(etf_columns, map(str, row))) for row in etf_values)

    def available_status(self, vintage_id: str = "vintage-1"):
        from perpetual_engine.dashboard_service import DashboardDataStatus

        return DashboardDataStatus(
            True,
            datetime(2026, 2, 28, tzinfo=timezone.utc),
            date(2025, 2, 28),
            date(2026, 2, 28),
            vintage_id,
            self.root / "current_manifest.json",
            None,
        )

    def comparison_cache(self, output: Path, *, state_hash: str | None = None, vintage_id: str = "vintage-1"):
        from perpetual_engine.dashboard_service import DashboardPaths, load_dashboard_state, state_sha256

        paths = DashboardPaths.from_root(self.root)
        state = load_dashboard_state(paths.default_config, paths.state)
        return {
            "target": "SWDA",
            "mode": "Confronta con il portafoglio principale",
            "state_sha256": state_hash or state_sha256(state),
            "vintage_id": vintage_id,
            "output": str(output),
        }

    def run_app(self, section: str = "Portafoglio") -> AppTest:
        with patch.dict(os.environ, {"ETF_DASHBOARD_ROOT": str(self.root)}), patch(
            "perpetual_engine.dashboard.refresh_dashboard_data",
            side_effect=AssertionError("network refresh at startup"),
        ), patch(
            "perpetual_engine.dashboard.search_etfs",
            side_effect=AssertionError("ETF search at startup"),
        ):
            app = AppTest.from_file(self.app_path, default_timeout=10).run()
            app.sidebar.radio(key="section").set_value(section).run()
        return app

    def test_startup_renders_saved_data_without_network(self) -> None:
        app = self.run_app()

        self.assertFalse(app.exception)
        self.assertEqual(app.title[0].value, "Analisi ETF")
        self.assertEqual(
            app.sidebar.radio(key="section").options,
            ["Portafoglio", "ETF", "Confronti", "Chronos rischio", "Chronos portafoglio"],
        )

    def test_streamlit_entrypoint_does_not_shadow_installed_chronos_package(self) -> None:
        code = f"""
import importlib.util
import runpy
from pathlib import Path
from streamlit.web.bootstrap import _fix_sys_path

dashboard = Path({str(self.app_path)!r})
_fix_sys_path(str(dashboard))
runpy.run_path(str(dashboard), run_name="__main__")
origin = Path(importlib.util.find_spec("chronos").origin).resolve()
raise SystemExit(0 if origin != dashboard.parent / "chronos.py" else 2)
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=self.app_path.parents[1],
            env=os.environ | {"ETF_DASHBOARD_ROOT": str(self.root)},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_portfolio_section_has_save_and_reset_controls(self) -> None:
        app = self.run_app("Portafoglio")

        self.assertFalse(app.exception)
        self.assertEqual(app.button(key="save_portfolio").label, "Salva portafoglio")
        self.assertEqual(app.button(key="reset_portfolio").label, "Ripristina portafoglio predefinito")

    def test_portfolio_reset_uses_separate_confirmation_state_and_widget_key(self) -> None:
        from perpetual_engine.dashboard_service import DashboardPaths, load_dashboard_state, save_dashboard_state

        paths = DashboardPaths.from_root(self.root)
        state = load_dashboard_state(paths.default_config, paths.state)
        save_dashboard_state(
            paths.state,
            replace(state, components=(replace(state.components[0], weight=1.0),)),
            default_config_path=paths.default_config,
        )

        with patch.dict(os.environ, {"ETF_DASHBOARD_ROOT": str(self.root)}):
            app = AppTest.from_file(self.app_path, default_timeout=10).run()
            app.button(key="reset_portfolio").click().run()

            self.assertFalse(app.exception)
            self.assertTrue(app.session_state["confirm_reset_requested"])
            self.assertEqual(app.button(key="confirm_reset_button").label, "Conferma ripristino")

            app.button(key="confirm_reset_button").click().run()

        restored = load_dashboard_state(paths.default_config, paths.state)
        self.assertFalse(app.exception)
        self.assertEqual(tuple(item.weight for item in restored.components), (0.60, 0.15, 0.15, 0.10))
        self.assertEqual([row["Peso %"] for row in app.session_state["portfolio_draft"]], [60.0, 15.0, 15.0, 10.0])
        self.assertEqual(app.session_state["portfolio_editor_generation"], 1)
        self.assertFalse(app.session_state["confirm_reset_requested"])

    def test_corrupted_state_recovery_replaces_stale_portfolio_session_state(self) -> None:
        from perpetual_engine import dashboard
        from perpetual_engine.dashboard_service import DashboardPaths, load_dashboard_state

        paths = DashboardPaths.from_root(self.root)
        with patch.dict(os.environ, {"ETF_DASHBOARD_ROOT": str(self.root)}):
            app = AppTest.from_file(self.app_path, default_timeout=10).run()
            app.session_state["portfolio_draft"] = [{"ID": "STALE", "Peso %": 100.0}]
            app.session_state["portfolio_editor_generation"] = 7
            app.session_state["confirm_reset_requested"] = True
            app.session_state["comparison_cache"] = {"output": "stale"}
            paths.state.write_bytes(b"{")
            app.run()

            self.assertFalse(app.exception)
            app.button(key="recover_state").click().run()

        restored = load_dashboard_state(paths.default_config, paths.state)
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["portfolio_draft"], dashboard._holding_rows(restored))
        self.assertEqual(app.session_state["portfolio_editor_generation"], 8)
        self.assertFalse(app.session_state["confirm_reset_requested"])
        with self.assertRaises(KeyError):
            app.session_state["comparison_cache"]

    def test_etf_section_has_search_and_confirmation_controls(self) -> None:
        app = self.run_app("ETF")

        self.assertFalse(app.exception)
        self.assertEqual(app.text_input(key="etf_query").label, "Simbolo o ISIN")
        self.assertEqual(app.button(key="search_etf").label, "Cerca")
        self.assertEqual(app.button(key="confirm_etf").label, "Conferma ETF")

    def test_comparisons_section_has_target_and_mode_controls(self) -> None:
        app = self.run_app("Confronti")

        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox(key="comparison_target").label, "ETF da studiare")
        self.assertEqual(app.radio(key="comparison_mode").options, [
            "Confronta con il portafoglio principale",
            "Portafoglio singolo (100%)",
        ])

    def test_comparison_choices_include_all_default_components_when_not_held(self) -> None:
        from perpetual_engine.dashboard_service import DashboardPaths, load_dashboard_state, save_dashboard_state

        paths = DashboardPaths.from_root(self.root)
        state = load_dashboard_state(paths.default_config, paths.state)
        save_dashboard_state(
            paths.state,
            replace(state, components=(replace(state.components[0], weight=1.0),)),
            default_config_path=paths.default_config,
        )

        app = self.run_app("Confronti")

        self.assertEqual(
            set(app.selectbox(key="comparison_target").options),
            {
                "SWDA", "IWMO", "IWQU", "DBMFE",
                "First Trust Clean Edge Smart Grid Infrastructure UCITS ETF (GRID.MI)",
            },
        )

    def test_chronos_section_exposes_shadow_mode_without_trading(self) -> None:
        app = self.run_app("Chronos rischio")

        self.assertFalse(app.exception)
        self.assertIn("modalità shadow", app.info[0].value)
        self.assertIn("non genera ordini", app.info[0].value)
        self.assertEqual(app.button(key="refresh_economic_data").label, "Aggiorna dati economici")
        self.assertEqual(app.button(key="calculate_chronos_report").label, "Genera report completo")
        self.assertEqual(app.button(key="start_ablation_evaluation").label, "Avvia valutazione ablation")
        series = set(app.dataframe[0].value["series"])
        self.assertTrue({"VIX", "US_TREASURY_10Y_REAL", "US_10Y_BREAKEVEN", "DAMODARAN_ERP", "US_CPI"} <= series)

    def test_chronos_portfolio_is_reachable_without_mutating_candidate(self) -> None:
        app = self.run_app("Chronos portafoglio")

        self.assertFalse(app.exception)
        self.assertEqual(app.button(key="save_chronos_candidate").label, "Salva portafoglio da studiare")
        self.assertEqual(app.button(key="reset_chronos_candidate").label, "Ripristina portafoglio base")
        self.assertEqual(app.button(key="calculate_direct_forecast").label, "Genera previsione portafoglio")
        self.assertFalse((self.root / "data" / "dashboard_v1" / "chronos_candidate_portfolio.json").exists())

    def test_chronos_portfolio_reset_replaces_editor_state(self) -> None:
        from perpetual_engine import dashboard

        component = SimpleNamespace(component_id="SWDA", weight=0.60)
        candidate = SimpleNamespace(components=(component,))
        base = SimpleNamespace(components=(component,))

        class SessionState(dict):
            __getattr__ = dict.__getitem__
            __setattr__ = dict.__setitem__

        edited = dashboard.pd.DataFrame([{"id": "SWDA", "weight": 100.0}])
        editor = Mock(return_value=edited)
        fake = SimpleNamespace(
            session_state=SessionState(),
            header=Mock(), info=Mock(), data_editor=editor,
            button=Mock(side_effect=lambda _label, **kwargs: kwargs["key"] == "reset_chronos_candidate"),
            dataframe=Mock(), rerun=Mock(),
            column_config=SimpleNamespace(SelectboxColumn=Mock(), NumberColumn=Mock()),
        )
        restored = SimpleNamespace(components=(SimpleNamespace(component_id="SWDA", weight=0.60),))
        with patch.object(dashboard, "st", fake), patch(
            "perpetual_engine.dashboard_chronos.load_direct_portfolios", return_value=(candidate, base),
        ), patch(
            "perpetual_engine.dashboard_chronos.reset_candidate_portfolio", return_value=restored,
        ):
            dashboard._render_chronos_portfolio(SimpleNamespace(), SimpleNamespace(catalog=()), None)

        self.assertEqual(editor.call_args.kwargs["key"], "chronos_candidate_editor_0")
        self.assertEqual(fake.session_state["chronos_candidate_editor_generation"], 1)
        self.assertEqual(fake.session_state["chronos_candidate_draft"], [{"id": "SWDA", "weight": 60.0}])

    def test_direct_forecast_renders_candidate_base_and_monitoring(self) -> None:
        from perpetual_engine import dashboard
        from perpetual_engine.dashboard_service import DashboardPaths, load_dashboard_state, refresh_dashboard_data
        from tests.test_portfolio_monitor import daily_bytes, months, prices_from_returns

        shutil.copy2(Path(__file__).parents[1] / "config" / "chronos_v1.json", self.root / "config" / "chronos_v1.json")
        paths = DashboardPaths.from_root(self.root)
        state = load_dashboard_state(paths.default_config, paths.state)
        dates = months(14)
        prices = daily_bytes(list(prices_from_returns(dates, (0.01,) * 13).items()))
        refresh_dashboard_data(paths, state, downloader=lambda _ticker: prices, retrieved_at=datetime(2026, 3, 2, tzinfo=timezone.utc))
        forecast_dir, monitoring_dir, manifest, rows = self.direct_forecast_archive()

        def app_script():
            from perpetual_engine.dashboard import main
            main()

        with patch.dict(os.environ, {"ETF_DASHBOARD_ROOT": str(self.root)}), patch.object(
            dashboard, "_cached_direct_predictor", return_value=object(),
        ), patch(
            "perpetual_engine.dashboard_chronos.publish_direct_forecast",
            return_value=SimpleNamespace(output_dir=forecast_dir),
        ), patch(
            "perpetual_engine.dashboard_chronos.read_direct_forecast", return_value=(manifest, rows),
        ), patch(
            "perpetual_engine.dashboard_chronos.reconcile_direct_forecasts", return_value=monitoring_dir,
        ):
            app = AppTest.from_function(app_script, default_timeout=10).run()
            app.sidebar.radio(key="section").set_value("Chronos portafoglio").run()
            app.button(key="calculate_direct_forecast").click().run()

        self.assertFalse(app.exception)
        self.assertIn("Percorso candidato e base", [item.value for item in app.subheader])
        self.assertIn("Differenza tra previsto e reale", [item.value for item in app.subheader])
        self.assertEqual(app.session_state["direct_forecast_path"], str(forecast_dir))

    def test_comparison_report_is_scoped_to_selection_and_shows_full_summary(self) -> None:
        output = self.comparison_report()
        mode = "Portafoglio singolo (100%)"
        cache = self.comparison_cache(output)
        cache["mode"] = mode
        with patch.dict(os.environ, {"ETF_DASHBOARD_ROOT": str(self.root)}), patch(
            "perpetual_engine.dashboard_service.current_data_status", return_value=self.available_status(),
        ):
            app = AppTest.from_file(self.app_path, default_timeout=10).run()
            app.session_state["comparison_cache"] = cache
            app.sidebar.radio(key="section").set_value("Confronti").run()
            app.radio(key="comparison_mode").set_value(mode).run()

        self.assertFalse(app.exception)
        self.assertIn("Studio corrente: SWDA — Portafoglio singolo (100%)", [item.value for item in app.caption])
        self.assertEqual({item.label for item in app.metric}, {
            "Rendimento ETF", "Rendimento portafoglio", "Rendimento annualizzato ETF",
            "Rendimento annualizzato portafoglio", "Volatilità annualizzata ETF",
            "Volatilità annualizzata portafoglio", "Drawdown massimo ETF", "Drawdown massimo portafoglio",
            "Mesi vinti ETF", "Mesi vinti portafoglio", "Mesi pari", "Correlazione",
        })
        self.assertIn("Storico breve", " ".join(item.value for item in app.warning))

    def test_comparison_cache_does_not_render_another_target(self) -> None:
        output = self.comparison_report()
        with patch.dict(os.environ, {"ETF_DASHBOARD_ROOT": str(self.root)}), patch(
            "perpetual_engine.dashboard_service.current_data_status", return_value=self.available_status(),
        ):
            app = AppTest.from_file(self.app_path, default_timeout=10).run()
            app.session_state["comparison_cache"] = self.comparison_cache(output)
            app.sidebar.radio(key="section").set_value("Confronti").run()
            app.selectbox(key="comparison_target").set_value("IWMO").run()

        self.assertFalse(app.exception)
        self.assertFalse(app.metric)

    def test_comparison_cache_does_not_render_another_mode(self) -> None:
        output = self.comparison_report()
        with patch.dict(os.environ, {"ETF_DASHBOARD_ROOT": str(self.root)}), patch(
            "perpetual_engine.dashboard_service.current_data_status", return_value=self.available_status(),
        ):
            app = AppTest.from_file(self.app_path, default_timeout=10).run()
            app.session_state["comparison_cache"] = self.comparison_cache(output)
            app.sidebar.radio(key="section").set_value("Confronti").run()
            app.radio(key="comparison_mode").set_value("Portafoglio singolo (100%)").run()

        self.assertFalse(app.exception)
        self.assertFalse(app.metric)

    def test_comparison_cache_requires_current_state_and_vintage(self) -> None:
        output = self.comparison_report()
        for field, value in (("state_sha256", "stale-state"), ("vintage_id", "stale-vintage")):
            with self.subTest(field=field), patch.dict(
                os.environ, {"ETF_DASHBOARD_ROOT": str(self.root)},
            ), patch(
                "perpetual_engine.dashboard_service.current_data_status", return_value=self.available_status(),
            ):
                app = AppTest.from_file(self.app_path, default_timeout=10).run()
                cache = self.comparison_cache(output)
                cache[field] = value
                app.session_state["comparison_cache"] = cache
                app.sidebar.radio(key="section").set_value("Confronti").run()

            self.assertFalse(app.exception)
            self.assertFalse(app.metric)

    def test_comparison_cache_is_hidden_when_current_status_is_unavailable_or_malformed(self) -> None:
        output = self.comparison_report()
        unavailable = replace(self.available_status(), available=False, vintage_id=None, reason="Dati non disponibili")
        for status in (unavailable, ValueError("bad status")):
            with self.subTest(status=status), patch.dict(
                os.environ, {"ETF_DASHBOARD_ROOT": str(self.root)},
            ), patch(
                "perpetual_engine.dashboard_service.current_data_status",
                side_effect=status if isinstance(status, Exception) else None,
                return_value=None if isinstance(status, Exception) else status,
            ):
                app = AppTest.from_file(self.app_path, default_timeout=10).run()
                app.session_state["comparison_cache"] = self.comparison_cache(output)
                app.sidebar.radio(key="section").set_value("Confronti").run()

            self.assertFalse(app.exception)
            self.assertFalse(app.metric)

    def test_calculated_comparison_cache_records_current_state_and_vintage(self) -> None:
        from perpetual_engine.dashboard_service import DashboardPaths, load_dashboard_state, state_sha256

        output = self.comparison_report()
        paths = DashboardPaths.from_root(self.root)
        expected_hash = state_sha256(load_dashboard_state(paths.default_config, paths.state))
        with patch.dict(os.environ, {"ETF_DASHBOARD_ROOT": str(self.root)}), patch(
            "perpetual_engine.dashboard_service.current_data_status", return_value=self.available_status(),
        ), patch(
            "perpetual_engine.dashboard_service.publish_dashboard_comparison",
            return_value=SimpleNamespace(output_dir=output),
        ):
            app = AppTest.from_file(self.app_path, default_timeout=10).run()
            app.sidebar.radio(key="section").set_value("Confronti").run()
            app.button(key="calculate_comparison").click().run()

        self.assertEqual(app.session_state["comparison_cache"]["state_sha256"], expected_hash)
        self.assertEqual(app.session_state["comparison_cache"]["vintage_id"], "vintage-1")

    def test_comparison_csv_uses_datetime_months_and_numeric_chart_columns(self) -> None:
        from perpetual_engine import dashboard

        monthly = dashboard._csv_table(self.comparison_report() / "comparison_monthly.csv")

        self.assertIsInstance(monthly.set_index("month").index, dashboard.pd.DatetimeIndex)
        for name in (
            "etf_cumulative_value", "portfolio_cumulative_value", "etf_trailing_volatility_12m",
            "portfolio_trailing_volatility_12m", "etf_drawdown", "portfolio_drawdown",
        ):
            with self.subTest(column=name):
                self.assertTrue(dashboard.pd.api.types.is_numeric_dtype(monthly[name]))

    def test_refresh_shows_current_instrument_and_reruns_after_success(self) -> None:
        from perpetual_engine import dashboard

        indicator = SimpleNamespace(update=Mock())
        fake = SimpleNamespace(
            session_state={}, status=lambda *args, **kwargs: nullcontext(indicator), success=Mock(), rerun=Mock(),
            error=Mock(), expander=lambda *args, **kwargs: nullcontext(), code=Mock(),
        )

        def refresh(_paths, _state, *, progress):
            progress("SWDA.MI")

        with patch.object(dashboard, "st", fake), patch.object(dashboard, "refresh_dashboard_data", side_effect=refresh):
            dashboard._refresh(SimpleNamespace(), SimpleNamespace())

        indicator.update.assert_called_once_with(label="Aggiornamento dati: SWDA.MI")
        fake.rerun.assert_called_once_with()

    def test_status_keeps_refresh_button_after_cached_data_error(self) -> None:
        from perpetual_engine import dashboard

        sidebar = SimpleNamespace(button=Mock(return_value=False))
        fake = SimpleNamespace(sidebar=sidebar, error=Mock(), expander=lambda *args, **kwargs: nullcontext(), code=Mock(), session_state={})
        with patch.object(dashboard, "st", fake), patch.object(dashboard, "current_data_status", side_effect=ValueError("bad cache")):
            dashboard._render_status(SimpleNamespace(), SimpleNamespace())

        self.assertEqual(sidebar.button.call_args.args[0], "Aggiorna dati")

    def test_refresh_disables_portfolio_editor_and_selectors(self) -> None:
        with patch.dict(os.environ, {"ETF_DASHBOARD_ROOT": str(self.root)}):
            app = AppTest.from_file(self.app_path, default_timeout=10).run()
            app.session_state["refreshing"] = True
            app.run()

        self.assertTrue(app.selectbox(key="add_holding").disabled)
        self.assertTrue(app.selectbox(key="remove_holding").disabled)

        from perpetual_engine import dashboard
        from perpetual_engine.dashboard_service import DashboardPaths, load_dashboard_state

        paths = DashboardPaths.from_root(self.root)
        state = load_dashboard_state(paths.default_config, paths.state)
        data_editor = Mock(return_value=dashboard.pd.DataFrame(dashboard._holding_rows(state)))
        class SessionState(dict):
            __getattr__ = dict.__getitem__
            __setattr__ = dict.__setitem__

        fake = SimpleNamespace(
            session_state=SessionState(refreshing=True, portfolio_draft=dashboard._holding_rows(state)),
            header=Mock(),
            data_editor=data_editor,
            selectbox=Mock(side_effect=["", ""]),
            button=Mock(return_value=False),
            rerun=Mock(),
            column_config=SimpleNamespace(NumberColumn=Mock()),
        )
        with patch.object(dashboard, "st", fake):
            dashboard._render_portfolio(paths, state)

        self.assertIs(data_editor.call_args.kwargs["disabled"], True)

    def test_successful_reset_rotates_the_portfolio_editor(self) -> None:
        from perpetual_engine import dashboard
        from perpetual_engine.dashboard_service import DashboardPaths, load_dashboard_state

        paths = DashboardPaths.from_root(self.root)
        state = load_dashboard_state(paths.default_config, paths.state)

        class SessionState(dict):
            __getattr__ = dict.__getitem__
            __setattr__ = dict.__setitem__

        draft = dashboard._holding_rows(state)
        draft[0]["Peso %"] = 100.0
        data_editor = Mock(return_value=dashboard.pd.DataFrame(draft))
        fake = SimpleNamespace(
            session_state=SessionState(
                portfolio_draft=draft,
                portfolio_editor_generation=3,
                confirm_reset_requested=True,
            ),
            header=Mock(),
            data_editor=data_editor,
            selectbox=Mock(side_effect=["", ""]),
            button=Mock(side_effect=lambda _label, **kwargs: kwargs["key"] == "confirm_reset_button"),
            rerun=Mock(),
            column_config=SimpleNamespace(NumberColumn=Mock()),
        )
        with patch.object(dashboard, "st", fake), patch.object(
            dashboard, "reset_dashboard_state", return_value=state,
        ):
            dashboard._render_portfolio(paths, state)

        self.assertEqual(data_editor.call_args.kwargs["key"], "portfolio_editor_3")
        self.assertEqual(fake.session_state["portfolio_editor_generation"], 4)
        self.assertEqual(fake.session_state["portfolio_draft"], dashboard._holding_rows(state))

    def test_default_holdings_show_known_exchanges(self) -> None:
        from perpetual_engine import dashboard
        from perpetual_engine.dashboard_service import DashboardPaths, load_dashboard_state

        paths = DashboardPaths.from_root(self.root)
        rows = dashboard._holding_rows(load_dashboard_state(paths.default_config, paths.state))

        self.assertEqual(
            [row["Mercato"] for row in rows],
            ["Borsa Italiana", "Borsa Italiana", "Borsa Italiana", "Euronext Paris"],
        )

    def test_successful_portfolio_save_clears_comparison_cache_but_failed_save_keeps_it(self) -> None:
        from perpetual_engine import dashboard
        from perpetual_engine.dashboard_service import DashboardPaths, load_dashboard_state

        paths = DashboardPaths.from_root(self.root)
        state = load_dashboard_state(paths.default_config, paths.state)

        class SessionState(dict):
            __getattr__ = dict.__getitem__
            __setattr__ = dict.__setitem__

        def render(save: Mock, cache: dict[str, str]) -> None:
            fake = SimpleNamespace(
                session_state=SessionState(portfolio_draft=dashboard._holding_rows(state), comparison_cache=cache),
                header=Mock(), data_editor=Mock(return_value=dashboard.pd.DataFrame(dashboard._holding_rows(state))),
                selectbox=Mock(side_effect=["", ""]),
                button=Mock(side_effect=lambda _label, **kwargs: kwargs["key"] == "save_portfolio"),
                rerun=Mock(), success=Mock(), error=Mock(), expander=lambda *args, **kwargs: nullcontext(), code=Mock(),
                column_config=SimpleNamespace(NumberColumn=Mock()),
            )
            with patch.object(dashboard, "st", fake), patch.object(dashboard, "save_dashboard_state", save):
                dashboard._render_portfolio(paths, state)
            return fake.session_state

        self.assertNotIn("comparison_cache", render(Mock(), {"output": "old"}))
        preserved = {"output": "old"}
        self.assertEqual(render(Mock(side_effect=ValueError("invalid")), preserved)["comparison_cache"], preserved)

    def test_successful_catalog_confirmation_clears_comparison_cache(self) -> None:
        from perpetual_engine import dashboard
        from perpetual_engine.dashboard_service import DashboardPaths, EtfCandidate, load_dashboard_state

        paths = DashboardPaths.from_root(self.root)
        state = load_dashboard_state(paths.default_config, paths.state)
        candidate = EtfCandidate("Test ETF", "TEST.MI", "IE00B3XXRP09", "Milan", "EUR", "https://example.test/etf")

        class SessionState(dict):
            __getattr__ = dict.__getitem__
            __setattr__ = dict.__setitem__

        cache = {"output": "old"}
        fake = SimpleNamespace(
            session_state=SessionState(etf_candidates=(candidate,), comparison_cache=cache),
            header=Mock(), dataframe=Mock(), form=lambda *args, **kwargs: nullcontext(), text_input=Mock(),
            form_submit_button=Mock(return_value=False), selectbox=Mock(side_effect=[candidate, ""]),
            button=Mock(side_effect=lambda _label, **kwargs: kwargs["key"] == "confirm_etf"),
            rerun=Mock(), success=Mock(), error=Mock(), expander=lambda *args, **kwargs: nullcontext(), code=Mock(),
        )
        with patch.object(dashboard, "st", fake), patch.object(dashboard, "save_dashboard_state"):
            dashboard._render_etf(paths, state)

        self.assertNotIn("comparison_cache", fake.session_state)

    def test_new_etf_search_clears_old_candidates_on_success_or_failure(self) -> None:
        from perpetual_engine import dashboard
        from perpetual_engine.dashboard_service import DashboardPaths, EtfCandidate, load_dashboard_state

        paths = DashboardPaths.from_root(self.root)
        state = load_dashboard_state(paths.default_config, paths.state)
        old = EtfCandidate("Old", "OLD.MI", "IE0000000002", "Milan", "EUR", "https://example.test/old")
        new = EtfCandidate("New", "NEW.MI", "IE000J80JTL1", "Milan", "EUR", "https://example.test/new")

        class SessionState(dict):
            __getattr__ = dict.__getitem__
            __setattr__ = dict.__setitem__

        for result in ((new,), ValueError("search failed")):
            with self.subTest(result=result):
                session = SessionState(etf_query="NEW", etf_candidates=(old,))

                def search(_query):
                    self.assertEqual(session.etf_candidates, ())
                    if isinstance(result, Exception):
                        raise result
                    return result

                fake = SimpleNamespace(
                    session_state=session,
                    header=Mock(), dataframe=Mock(), form=lambda *args, **kwargs: nullcontext(),
                    text_input=Mock(), form_submit_button=Mock(return_value=True),
                    selectbox=Mock(side_effect=[None, ""]), button=Mock(return_value=False),
                    error=Mock(), expander=lambda *args, **kwargs: nullcontext(), code=Mock(),
                )
                with patch.object(dashboard, "st", fake), patch.object(dashboard, "search_etfs", side_effect=search):
                    dashboard._render_etf(paths, state)

                self.assertEqual(session.etf_candidates, () if isinstance(result, Exception) else result)
