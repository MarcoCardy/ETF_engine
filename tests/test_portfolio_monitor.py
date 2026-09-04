from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import statistics
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from perpetual_engine.io import canonical_json, sha256_file


COMPONENTS = (
    {"id": "SWDA", "ticker": "SWDA.MI", "isin": "IE00B4L5Y983", "quote_currency": "EUR", "weight": 0.60},
    {"id": "IWMO", "ticker": "IWMO.MI", "isin": "IE00BP3QZ825", "quote_currency": "EUR", "weight": 0.15},
    {"id": "IWQU", "ticker": "IWQU.MI", "isin": "IE00BP3QZ601", "quote_currency": "EUR", "weight": 0.15},
    {"id": "DBMFE", "ticker": "DBMFE.PA", "isin": "LU2951555403", "quote_currency": "EUR", "weight": 0.10},
)
TARGET_MAPPING = {"WORLD": "SWDA", "MOMENTUM": "IWMO", "QUALITY": "IWQU", "TREND": "DBMFE"}
PROXY_CAVEAT = "Chronos long historical targets are factor proxies; this report monitors exact investable ETF returns."


def month_end(value: date) -> date:
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1) - timedelta(days=1)


def next_month(value: date) -> date:
    return month_end(date(value.year + (value.month == 12), value.month % 12 + 1, 1))


def months(count: int, first: date = date(2025, 1, 31)) -> tuple[date, ...]:
    output = [first]
    while len(output) < count:
        output.append(next_month(output[-1]))
    return tuple(output)


def prices_from_returns(dates: tuple[date, ...], returns: tuple[float, ...], start: float = 100.0) -> dict[date, float]:
    output = {dates[0]: start}
    value = start
    for observed, change in zip(dates[1:], returns):
        value *= 1.0 + change
        output[observed] = value
    return output


def daily_bytes(rows: list[tuple[date, float]]) -> bytes:
    return ("date,adjusted_close\n" + "".join(f"{day.isoformat()},{value:.17g}\n" for day, value in rows)).encode()


def config_payload() -> dict[str, object]:
    return {
        "schema_version": "PORTFOLIO_CONFIG_V1",
        "portfolio_id": "P_WORLD_FACTOR_TREND",
        "base_currency": "EUR",
        "rebalance": "MONTHLY_TARGET_WEIGHT",
        "starting_value": 100,
        "data_root": "data/portfolio_p_v1",
        "max_staleness_calendar_days": 7,
        "short_live_history_returns": 36,
        "listing_currency_rule": "ALL_COMPONENTS_EUR_NO_FX",
        "prelaunch_rule": "NO_DBMFE_BACKFILL_OR_SUBSTITUTION",
        "investable_target_mapping": TARGET_MAPPING,
        "proxy_caveat": PROXY_CAVEAT,
        "components": [{**item} for item in COMPONENTS],
    }


class PortfolioFixture(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.config_path = self.root / "config" / "portfolio_p_v1.json"
        self.write_config()

    def tearDown(self):
        self.directory.cleanup()

    def write_config(self, payload: dict[str, object] | None = None) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_bytes(canonical_json(payload or config_payload()))

    def payloads(self, count: int = 14) -> dict[str, bytes]:
        dates = months(count)
        return_sets = {
            "SWDA.MI": (0.01, 0.02, -0.01, 0.015, 0.005, -0.02, 0.03, 0.01, -0.005, 0.02, 0.012, -0.008, 0.018),
            "IWMO.MI": (0.02, -0.01, 0.03, 0.005, -0.015, 0.025, 0.01, -0.02, 0.015, 0.005, 0.03, -0.01, 0.02),
            "IWQU.MI": (-0.01, 0.015, 0.005, 0.02, -0.005, 0.01, -0.015, 0.025, 0.01, -0.01, 0.02, 0.005, 0.015),
            "DBMFE.PA": (0.03, -0.02, 0.01, 0.025, -0.01, 0.015, 0.005, -0.005, 0.02, 0.01, -0.015, 0.03, 0.005),
        }
        return {
            ticker: daily_bytes(list(prices_from_returns(dates, changes).items()))
            for ticker, changes in return_sets.items()
        }


class PortfolioConfigTests(PortfolioFixture):
    def test_strict_config_freezes_identity_weights_mapping_and_eur_rule(self):
        from perpetual_engine.portfolio_monitor import load_portfolio_config

        config = load_portfolio_config(self.config_path)
        self.assertEqual(config.path, self.config_path.resolve())
        self.assertEqual(config.project_root, self.root.resolve())
        self.assertEqual((config.portfolio_id, config.base_currency, config.rebalance), ("P_WORLD_FACTOR_TREND", "EUR", "MONTHLY_TARGET_WEIGHT"))
        self.assertEqual((config.starting_value, config.max_staleness_days, config.short_history_returns), (100.0, 7, 36))
        self.assertEqual(config.data_root, (self.root / "data" / "portfolio_p_v1").resolve())
        self.assertEqual(config.listing_currency_rule, "ALL_COMPONENTS_EUR_NO_FX")
        self.assertEqual(config.prelaunch_rule, "NO_DBMFE_BACKFILL_OR_SUBSTITUTION")
        self.assertEqual(config.investable_target_mapping, TARGET_MAPPING)
        self.assertEqual(config.proxy_caveat, PROXY_CAVEAT)
        self.assertEqual(
            tuple((item.component_id, item.ticker, item.isin, item.quote_currency, item.weight) for item in config.components),
            tuple((item["id"], item["ticker"], item["isin"], item["quote_currency"], item["weight"]) for item in COMPONENTS),
        )
        self.assertEqual(config.config_hash, sha256_file(self.config_path))

        mutations = []
        wrong_weight = config_payload()
        wrong_weight["components"][0]["weight"] = 0.59  # type: ignore[index]
        mutations.append(wrong_weight)
        wrong_order = config_payload()
        wrong_order["components"][0], wrong_order["components"][1] = wrong_order["components"][1], wrong_order["components"][0]  # type: ignore[index]
        mutations.append(wrong_order)
        wrong_mapping = config_payload()
        wrong_mapping["investable_target_mapping"] = {**TARGET_MAPPING, "TREND": "SWDA"}
        mutations.append(wrong_mapping)
        escaped = config_payload()
        escaped["data_root"] = "../outside"
        mutations.append(escaped)
        unexpected = config_payload()
        unexpected["optimizer"] = True
        mutations.append(unexpected)
        for payload in mutations:
            with self.subTest(payload=payload):
                self.write_config(payload)
                with self.assertRaises(ValueError):
                    load_portfolio_config(self.config_path)


class PortfolioArithmeticTests(PortfolioFixture):
    def test_first_common_month_weights_value_drawdown_and_exact_12m_volatility(self):
        from perpetual_engine.portfolio_monitor import load_portfolio_config, portfolio_rows

        config = load_portfolio_config(self.config_path)
        common = months(14)
        changes = {
            "SWDA": (0.01, 0.02, -0.01, 0.015, 0.005, -0.02, 0.03, 0.01, -0.005, 0.02, 0.012, -0.008, 0.018),
            "IWMO": (0.02, -0.01, 0.03, 0.005, -0.015, 0.025, 0.01, -0.02, 0.015, 0.005, 0.03, -0.01, 0.02),
            "IWQU": (-0.01, 0.015, 0.005, 0.02, -0.005, 0.01, -0.015, 0.025, 0.01, -0.01, 0.02, 0.005, 0.015),
            "DBMFE": (0.03, -0.02, 0.01, 0.025, -0.01, 0.015, 0.005, -0.005, 0.02, 0.01, -0.015, 0.03, 0.005),
        }
        extra = date(2024, 12, 31)
        eur_prices = {name: prices_from_returns(common, values) for name, values in changes.items()}
        for name in ("SWDA", "IWMO", "IWQU"):
            eur_prices[name] = {extra: 99.0, **eur_prices[name]}

        rows = portfolio_rows(config, eur_prices)
        self.assertEqual((len(rows), rows[0].month), (13, date(2025, 2, 28)))
        for actual, expected in zip(rows[0].component_returns, (0.01, 0.02, -0.01, 0.03)):
            self.assertAlmostEqual(actual, expected)
        self.assertAlmostEqual(rows[0].portfolio_return, 0.0105)
        self.assertAlmostEqual(rows[0].cumulative_value, 101.05)
        self.assertEqual(rows[0].trailing_volatility_12m, None)
        self.assertLess(min(row.drawdown for row in rows), 0.0)
        expected_portfolio = [
            0.60 * values[0] + 0.15 * values[1] + 0.15 * values[2] + 0.10 * values[3]
            for values in zip(*(changes[name] for name in ("SWDA", "IWMO", "IWQU", "DBMFE")))
        ]
        self.assertAlmostEqual(rows[11].trailing_volatility_12m, statistics.stdev(expected_portfolio[:12]) * math.sqrt(12))

    def test_rejects_unexpected_stale_nonpositive_and_total_loss_inputs(self):
        from perpetual_engine.portfolio_monitor import load_portfolio_config, portfolio_rows

        config = load_portfolio_config(self.config_path)
        ends = months(2)
        valid = {name: {ends[0]: 100.0, ends[1]: 101.0} for name in ("SWDA", "IWMO", "IWQU", "DBMFE")}
        with self.assertRaisesRegex(ValueError, "identifier"):
            portfolio_rows(config, {**valid, "OTHER": valid["DBMFE"]})
        stale = {name: {end - timedelta(days=8): 100.0 + index for index, end in enumerate(ends)} for name in valid}
        with self.assertRaisesRegex(ValueError, "common return"):
            portfolio_rows(config, stale)
        nonpositive = {name: dict(series) for name, series in valid.items()}
        nonpositive["SWDA"][ends[1]] = 0.0
        with self.assertRaisesRegex(ValueError, "positive"):
            portfolio_rows(config, nonpositive)
        total_loss = {name: dict(series) for name, series in valid.items()}
        total_loss["SWDA"][ends[1]] = 1e-300
        with self.assertRaisesRegex(ValueError, "-100"):
            portfolio_rows(config, total_loss)


class PortfolioFrozenReportTests(PortfolioFixture):
    def test_refresh_report_eur_inputs_metrics_mapping_all_correlations_and_hashes(self):
        from perpetual_engine.portfolio_monitor import load_portfolio_config, refresh_portfolio_prices, write_portfolio_report

        payloads = self.payloads()
        observed_symbols: list[str] = []

        def downloader(ticker: str) -> bytes:
            observed_symbols.append(ticker)
            return payloads[ticker]

        at = datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)
        vintage_id = refresh_portfolio_prices(self.config_path, downloader=downloader, retrieved_at=at)
        self.assertEqual(observed_symbols, ["SWDA.MI", "IWMO.MI", "IWQU.MI", "DBMFE.PA"])
        config = load_portfolio_config(self.config_path)
        vintage = config.data_root / "vintages" / vintage_id
        manifest = json.loads((vintage / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((manifest["vintage_id"], manifest["retrieved_at"]), (vintage_id, at.isoformat()))
        self.assertEqual(tuple(item["ticker"] for item in manifest["sources"]), tuple(observed_symbols))
        for source in manifest["sources"]:
            self.assertEqual(sha256_file(vintage / source["path"]), source["sha256"])

        output = self.root / "outputs" / "portfolio"
        self.assertEqual(write_portfolio_report(self.config_path, output), output.resolve())
        with (output / "portfolio_monthly.csv").open(newline="", encoding="utf-8") as handle:
            monthly = list(csv.DictReader(handle))
        self.assertEqual(len(monthly), 13)
        self.assertAlmostEqual(float(monthly[0]["DBMFE"]), 0.03)
        self.assertEqual(monthly[10]["trailing_volatility_12m"], "")
        self.assertNotEqual(monthly[11]["trailing_volatility_12m"], "")
        with (output / "portfolio_correlations.csv").open(newline="", encoding="utf-8") as handle:
            correlations = list(csv.DictReader(handle))
        pairs = ["SWDA-IWMO", "SWDA-IWQU", "SWDA-DBMFE", "IWMO-IWQU", "IWMO-DBMFE", "IWQU-DBMFE"]
        self.assertEqual(len(correlations), 13 * 6)
        self.assertEqual([row["pair"] for row in correlations[:6]], pairs)
        self.assertTrue(all(row["rolling_correlation_12m"] == "" for row in correlations[:11 * 6]))
        self.assertTrue(all(row["rolling_correlation_12m"] != "" for row in correlations[11 * 6:]))
        with (output / "portfolio_metrics.csv").open(newline="", encoding="utf-8") as handle:
            metrics = next(csv.DictReader(handle))
        self.assertEqual((metrics["first_month"], metrics["last_month"], metrics["count"]), ("2025-02-28", "2026-02-28", "13"))
        self.assertNotEqual(metrics["annualized_volatility"], "")
        report_manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(set(report_manifest), {
            "schema_version", "portfolio_id", "config_sha256", "input_sha256", "vintage",
            "components", "investable_target_mapping", "listing_currency_rule", "prelaunch_rule",
            "proxy_caveat", "first_common_return_month", "SHORT_LIVE_HISTORY", "generated_sha256",
        })
        self.assertTrue(report_manifest["SHORT_LIVE_HISTORY"])
        self.assertEqual(report_manifest["first_common_return_month"], "2025-02-28")
        self.assertEqual(report_manifest["vintage"]["vintage_id"], vintage_id)
        self.assertEqual(report_manifest["investable_target_mapping"], TARGET_MAPPING)
        self.assertEqual(report_manifest["listing_currency_rule"], "ALL_COMPONENTS_EUR_NO_FX")
        self.assertEqual(report_manifest["proxy_caveat"], PROXY_CAVEAT)
        self.assertEqual(set(report_manifest["input_sha256"]), {"SWDA", "IWMO", "IWQU", "DBMFE"})
        for filename, digest in report_manifest["generated_sha256"].items():
            self.assertEqual(sha256_file(output / filename), digest)

    def test_refresh_is_transactional_and_report_validates_frozen_hashes_and_pointer_links(self):
        from perpetual_engine.portfolio_monitor import load_portfolio_config, refresh_portfolio_prices, write_portfolio_report

        at = datetime(2026, 3, 2, tzinfo=timezone.utc)
        payloads = self.payloads()
        first = refresh_portfolio_prices(self.config_path, downloader=payloads.__getitem__, retrieved_at=at)
        config = load_portfolio_config(self.config_path)
        pointer = config.data_root / "current_manifest.json"
        pointer_before = pointer.read_bytes()
        self.assertEqual(refresh_portfolio_prices(self.config_path, downloader=payloads.__getitem__, retrieved_at=at), first)
        later = refresh_portfolio_prices(self.config_path, downloader=payloads.__getitem__, retrieved_at=at + timedelta(seconds=1))
        self.assertNotEqual(later, first)
        pointer_before_failure = pointer.read_bytes()

        def failed(ticker: str) -> bytes:
            if ticker == "IWQU.MI":
                raise ValueError("download failed")
            return payloads[ticker]

        with self.assertRaisesRegex(ValueError, "download failed"):
            refresh_portfolio_prices(self.config_path, downloader=failed, retrieved_at=at + timedelta(seconds=2))
        self.assertEqual(pointer.read_bytes(), pointer_before_failure)
        self.assertNotEqual(pointer_before, pointer_before_failure)
        vintages_before = {item.name for item in (config.data_root / "vintages").iterdir()}
        with patch(
            "perpetual_engine.portfolio_monitor._require_config_unchanged",
            side_effect=(None, ValueError("configuration changed during publication")),
        ), self.assertRaisesRegex(ValueError, "changed during publication"):
            refresh_portfolio_prices(self.config_path, downloader=payloads.__getitem__, retrieved_at=at + timedelta(seconds=3))
        self.assertEqual(pointer.read_bytes(), pointer_before_failure)
        self.assertEqual({item.name for item in (config.data_root / "vintages").iterdir()}, vintages_before)
        good_output = self.root / "good-report"
        write_portfolio_report(self.config_path, good_output)

        current = json.loads(pointer.read_text(encoding="utf-8"))
        vintage = config.data_root / "vintages" / current["vintage_id"]
        source = json.loads((vintage / "manifest.json").read_text(encoding="utf-8"))["sources"][0]
        (vintage / source["path"]).write_bytes(b"date,adjusted_close\n2025-01-31,999\n")
        with self.assertRaisesRegex(ValueError, "hash"):
            write_portfolio_report(self.config_path, self.root / "corrupt-report")

        pointer.replace(self.root / "external-pointer.json")
        try:
            pointer.symlink_to(self.root / "external-pointer.json")
        except OSError as error:
            self.skipTest(f"symlink non disponibile: {error}")
        with self.assertRaisesRegex(ValueError, "escapes|pointer"):
            write_portfolio_report(self.config_path, self.root / "linked-report")

    def test_refresh_rejects_missing_duplicate_and_nonfinite_daily_rows(self):
        from perpetual_engine.portfolio_monitor import refresh_portfolio_prices

        valid = self.payloads()
        cases = (
            (b"date,adjusted_close\n", "no usable|missing"),
            (b"date,adjusted_close\n2025-01-31,1\n2025-01-31,2\n", "duplicate"),
            (b"date,adjusted_close\n2025-01-31,nan\n", "finite"),
        )
        for bad, message in cases:
            with self.subTest(message=message):
                payloads = {**valid, "SWDA.MI": bad}
                with self.assertRaisesRegex(ValueError, message):
                    refresh_portfolio_prices(
                        self.config_path,
                        downloader=payloads.__getitem__,
                        retrieved_at=datetime(2026, 3, 2, tzinfo=timezone.utc),
                    )

    def test_report_reuses_identical_bytes_and_rejects_collisions(self):
        from perpetual_engine.portfolio_monitor import refresh_portfolio_prices, write_portfolio_report

        refresh_portfolio_prices(
            self.config_path,
            downloader=self.payloads().__getitem__,
            retrieved_at=datetime(2026, 3, 2, tzinfo=timezone.utc),
        )
        output = self.root / "report"
        first = write_portfolio_report(self.config_path, output)
        before = {path.name: path.read_bytes() for path in output.iterdir()}
        self.assertEqual(write_portfolio_report(self.config_path, output), first)
        self.assertEqual({path.name: path.read_bytes() for path in output.iterdir()}, before)
        (output / "portfolio_monthly.csv").write_bytes(b"changed\n")
        with self.assertRaisesRegex(ValueError, "collision"):
            write_portfolio_report(self.config_path, output)
        ancestor = self.root / "ancestor"
        ancestor.write_text("file", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "collision"):
            write_portfolio_report(self.config_path, ancestor / "report")


class PortfolioCliTests(unittest.TestCase):
    def test_portfolio_commands_dispatch_lazily_and_preserve_error_boundary(self):
        from perpetual_engine.cli import main

        cases = (
            (["chronos", "portfolio-refresh", "--config", "portfolio.json"], "perpetual_engine.portfolio_monitor.refresh_portfolio_prices", (Path("portfolio.json"),), "vintage-id"),
            (["chronos", "portfolio-report", "--config", "portfolio.json", "--output", "out"], "perpetual_engine.portfolio_monitor.write_portfolio_report", (Path("portfolio.json"), Path("out")), Path("out")),
        )
        for argv, target, expected, returned in cases:
            with self.subTest(command=argv[1]), patch(target, return_value=returned) as selected, patch("sys.stdout", new=io.StringIO()) as stdout, patch("sys.stderr", new=io.StringIO()) as stderr:
                self.assertEqual(main(argv), 0)
                selected.assert_called_once_with(*expected)
                self.assertEqual(stdout.getvalue(), f"{returned}\n")
                self.assertEqual(stderr.getvalue(), "")

        with patch("perpetual_engine.portfolio_monitor.write_portfolio_report", side_effect=ValueError("corrupt prices")), patch("sys.stdout", new=io.StringIO()) as stdout, patch("sys.stderr", new=io.StringIO()) as stderr:
            self.assertEqual(main(["chronos", "portfolio-report", "--config", "bad.json", "--output", "out"]), 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Input error", stderr.getvalue())
        self.assertIn("bad.json", stderr.getvalue())
        self.assertIn("out", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
