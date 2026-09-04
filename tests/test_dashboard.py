from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
