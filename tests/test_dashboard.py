from __future__ import annotations

import csv
import os
import shutil
import tempfile
import unittest
from contextlib import nullcontext
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
            ["Portafoglio", "ETF", "Confronti", "Previsioni Chronos"],
        )

    def test_portfolio_section_has_save_and_reset_controls(self) -> None:
        app = self.run_app("Portafoglio")

        self.assertFalse(app.exception)
        self.assertEqual(app.button(key="save_portfolio").label, "Salva portafoglio")
        self.assertEqual(app.button(key="reset_portfolio").label, "Ripristina portafoglio predefinito")

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

    def test_chronos_section_is_inactive_without_model(self) -> None:
        app = self.run_app("Previsioni Chronos")

        self.assertFalse(app.exception)
        self.assertIn("Non ancora attivo", app.info[0].value)

    def test_comparison_report_is_scoped_to_selection_and_shows_full_summary(self) -> None:
        output = self.comparison_report()
        mode = "Portafoglio singolo (100%)"
        with patch.dict(os.environ, {"ETF_DASHBOARD_ROOT": str(self.root)}):
            app = AppTest.from_file(self.app_path, default_timeout=10).run()
            app.session_state["comparison_cache"] = {"target": "SWDA", "mode": mode, "output": str(output)}
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
        with patch.dict(os.environ, {"ETF_DASHBOARD_ROOT": str(self.root)}):
            app = AppTest.from_file(self.app_path, default_timeout=10).run()
            app.session_state["comparison_cache"] = {
                "target": "SWDA", "mode": "Confronta con il portafoglio principale", "output": str(output),
            }
            app.sidebar.radio(key="section").set_value("Confronti").run()
            app.selectbox(key="comparison_target").set_value("IWMO").run()

        self.assertFalse(app.exception)
        self.assertFalse(app.metric)

    def test_comparison_cache_does_not_render_another_mode(self) -> None:
        output = self.comparison_report()
        with patch.dict(os.environ, {"ETF_DASHBOARD_ROOT": str(self.root)}):
            app = AppTest.from_file(self.app_path, default_timeout=10).run()
            app.session_state["comparison_cache"] = {
                "target": "SWDA", "mode": "Confronta con il portafoglio principale", "output": str(output),
            }
            app.sidebar.radio(key="section").set_value("Confronti").run()
            app.radio(key="comparison_mode").set_value("Portafoglio singolo (100%)").run()

        self.assertFalse(app.exception)
        self.assertFalse(app.metric)

    def test_refresh_reruns_after_a_successful_refresh(self) -> None:
        from perpetual_engine import dashboard

        fake = SimpleNamespace(session_state={}, status=lambda *args, **kwargs: nullcontext(), success=Mock(), rerun=Mock())
        with patch.object(dashboard, "st", fake), patch.object(dashboard, "refresh_dashboard_data"):
            dashboard._refresh(SimpleNamespace(), SimpleNamespace())

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
