from __future__ import annotations

import hashlib
import gzip
import io
import json
import tempfile
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from openpyxl import Workbook

from perpetual_engine.backtest import LEVERAGED_FEE_MONTH, STRATEGIES
from perpetual_engine.backtest_report import (
    OUTPUT_FILES,
    _recommendation_status,
    compute_vintage_id,
    refresh_data,
    run_backtest_report,
)
from perpetual_engine.cli import _parser, main
from perpetual_engine.io import canonical_json
from tests.test_damodaran import ANNUAL_1977_2007_XLS_GZIP_B64
import base64


UTC = timezone.utc
RETRIEVED_AT = datetime(2026, 8, 22, 10, 30, tzinfo=UTC)


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(value))


def _observation_csv() -> bytes:
    return (
        "series_id,observation_date,period_end,available_at,value,unit,source_url,retrieved_at,source_hash,vintage_status,quality_flags\n"
        "TEST,1978-12-31,1978-12-31,1979-01-01T00:00:00+00:00,1,ratio,https://example.test/one.csv,2026-08-22T10:30:00+00:00,"
        + "a" * 64
        + ",CURRENT_VINTAGE_RESEARCH,\n"
    ).encode()


def _source_config(root: Path, *, count: int = 1) -> Path:
    config = root / "refresh.json"
    sources = [
        {
            "source_id": f"SOURCE_{index}",
            "url": f"https://example.test/{index}.csv",
            "parser_kind": "observation_csv",
            "parser_version": "1",
            "status": "required",
            "normalized_path": f"source_{index}.csv",
        }
        for index in range(count)
    ]
    _write_json(
        config,
        {
            "schema_version": "DATA_SOURCES_V1",
            "version": "1.0",
            "output_data_root": "frozen",
            "build_backtest_bundle": False,
            "sources": sources,
        },
    )
    return config


def _archive(member: str, text: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zipped:
        zipped.writestr(member, text)
    return output.getvalue()


def _monthly_erp_xlsx() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Historical ERP"
    sheet.append(("Start of month", "ERP (T12m)"))
    current = date(2008, 9, 1)
    while current <= date(2026, 8, 1):
        sheet.append((current, 4.5))
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def _annual_erp_xls() -> bytes:
    return gzip.decompress(base64.b64decode(ANNUAL_1977_2007_XLS_GZIP_B64))


def _fred(series_id: str, rows: list[tuple[str, str]]) -> bytes:
    return ("observation_date," + series_id + "\n" + "\n".join(f"{day},{value}" for day, value in rows) + "\n").encode()


def _month_starts(start: date, end: date) -> list[date]:
    result: list[date] = []
    current = start
    while current <= end:
        result.append(current)
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
    return result


def _month_end_for_test(value: date) -> date:
    following = date(value.year + (value.month == 12), value.month % 12 + 1, 1)
    return following - timedelta(days=1)


def _weekly_rows(start: date, end: date) -> list[tuple[str, str]]:
    days: list[date] = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=7)
    if days[-1] != end:
        days.append(end)
    return [(day.isoformat(), "8") for day in days]


def _official_shaped_payloads(
    config: dict[str, object],
    *,
    outcome_end: date = date(2026, 7, 1),
    include_out_of_regime: bool = True,
    boundary_gap: bool = False,
    internal_gap: bool = False,
    signal_gap: str | None = None,
    signal_end: date | None = None,
) -> dict[str, bytes]:
    months: list[str] = []
    current = date(1977, 1, 1)
    while current <= date(1990, 6, 1):
        months.append(f"{current.year}{current.month:02d}")
        current = date(current.year + (current.month == 12), current.month % 12 + 1, 1)
    developed_end = max(outcome_end, date(1990, 7, 1))
    developed_months = _month_starts(date(1990, 7, 1), developed_end)
    synthetic_months = _month_starts(date(1976, 12, 1), date(1998, 12, 1))
    dex_end = max(outcome_end, date(1999, 1, 1))
    dex_months = _month_starts(date(1999, 1, 1), dex_end)
    funding_end = date(outcome_end.year, outcome_end.month, 1)
    dff_rows = _weekly_rows(date(1979, 1, 1), min(date(1985, 12, 31), _month_end_for_test(funding_end)))
    libor_end = max(date(1986, 1, 1), min(date(2021, 8, 31), _month_end_for_test(funding_end)))
    libor_rows = _weekly_rows(date(1986, 1, 1), libor_end)
    sofr_end = max(date(2021, 9, 1), _month_end_for_test(funding_end))
    sofr_rows = _weekly_rows(date(2021, 9, 1), sofr_end)
    if boundary_gap:
        libor_rows = [row for row in libor_rows if row[0] != "1986-01-01"]
    if internal_gap:
        del dff_rows[9]
    if include_out_of_regime:
        dff_rows.append(("1990-01-01", "99"))
        sofr_rows.append(("2021-08-31", "99"))
    defensive_end = (funding_end - timedelta(days=1)).replace(day=1) - timedelta(days=1)
    defensive_months = _month_starts(date(1978, 11, 1), defensive_end.replace(day=1))
    final_signal_day = signal_end or (funding_end - timedelta(days=1))
    dgs_rows = _weekly_rows(date(1978, 12, 1), final_signal_day)
    tips_rows = (
        [("2003-01-02", "8")]
        if final_signal_day < date(2003, 1, 2)
        else _weekly_rows(date(2003, 1, 2), final_signal_day)
    )
    if signal_gap:
        target = date(2010, 5, 1)
        if signal_gap == "DGS10":
            dgs_rows = [row for row in dgs_rows if date.fromisoformat(row[0]).replace(day=1) != target]
        elif signal_gap == "DFII10":
            tips_rows = [row for row in tips_rows if date.fromisoformat(row[0]).replace(day=1) != target]
        else:
            raise ValueError("unknown signal_gap fixture")
    urls = {source["source_id"]: source["url"] for source in config["sources"]}  # type: ignore[index]
    wdi_rows = [
        {"indicator": {"id": "CM.MKT.LCAP.CD"}, "countryiso3code": aggregate, "date": str(year), "value": value}
        for year in range(1975, 1989)
        for aggregate, value in (("USA", 40), ("WLD", 100))
    ]
    hicp = {
        "id": ["freq", "unit", "coicop", "geo", "time"],
        "size": [1, 1, 1, 1, 2],
        "dimension": {
            "freq": {"category": {"index": {"M": 0}}},
            "unit": {"category": {"index": {"I15": 0}}},
            "coicop": {"category": {"index": {"CP00": 0}}},
            "geo": {"category": {"index": {"IT": 0}}},
            "time": {"category": {"index": {"2023-11": 0, "2023-12": 1}}},
        },
        "value": {"0": 120, "1": 121},
    }
    return {
        urls["DAMODARAN_ERP_ANNUAL"]: _annual_erp_xls(),
        urls["DAMODARAN_ERP_MONTHLY"]: _monthly_erp_xlsx(),
        urls["FRENCH_INTERNATIONAL_MONTHLY"]: _archive(
            "Ind_all.Dat", "Value-Weight Dollar Returns\nDate Mkt\n" + "\n".join(f"{month} 1.00" for month in months)
        ),
        urls["FRENCH_US_MONTHLY"]: _archive(
            "F-F_Research_Data_Factors.csv", "Date,Mkt-RF,RF\n" + "\n".join(f"{month},0.90,0.10" for month in months)
        ),
        urls["FRENCH_DEVELOPED_MONTHLY"]: _archive(
            "Developed_3_Factors.csv",
            "Date,Mkt-RF,RF\n" + "\n".join(f"{month.year}{month.month:02d},0.90,0.10" for month in developed_months),
        ),
        urls["WDI_MARKET_CAP"]: json.dumps([{"page": 1, "pages": 1}, wdi_rows]).encode(),
        urls["FRED_SYNTHETIC_EUR_FX"]: _fred(
            "CCUSSP01DEM650N",
            [(month.isoformat(), "2.0") for month in synthetic_months],
        ),
        urls["FRED_DEXUSEU"]: _fred(
            "DEXUSEU", [(month.isoformat(), "1.105") for month in dex_months]
        ),
        urls["FRED_DGS10"]: _fred("DGS10", dgs_rows),
        urls["FRED_DFII10"]: _fred("DFII10", tips_rows),
        urls["FRED_DFF"]: _fred("DFF", dff_rows),
        urls["FRED_USD1MTD156N"]: _fred("USD1MTD156N", libor_rows),
        urls["FRED_SOFR"]: _fred("SOFR", sofr_rows),
        urls["FRED_IR3TIB01ITM156N"]: _fred(
            "IR3TIB01ITM156N", [(month.isoformat(), "8") for month in defensive_months]
        ),
        urls["FRED_ITALY_CPI"]: _fred("ITACPALTT01IXNBM", [("2023-11-01", "120")]),
        urls["EUROSTAT_ITALY_HICP"]: json.dumps(hicp).encode(),
    }


def _bundle() -> dict[str, object]:
    with localcontext() as context:
        context.prec = 50
        fee = format(LEVERAGED_FEE_MONTH, "f")
    return {
        "schema_version": "BACKTEST_BUNDLE_V1",
        "expected_through": "1979-01-31",
        "actual_end": "1979-01-31",
        "signals": [
            {
                "month": "1979-01-31",
                "decision_at": "1978-12-31T23:59:59+00:00",
                "erp": "0.04",
                "erp_percentile": "0.5",
                "erp_history_count": 36,
                "treasury_10y": "0.08",
                "tips": None,
                "tips_percentile": None,
                "tips_history_count": 0,
                "drawdown": None,
                "momentum_1m": None,
                "momentum_3m": None,
                "volatility_12m": None,
                "market_history_count": 0,
            }
        ],
        "world_returns": [{"month": "1979-01-31", "value": "0.01"}],
        "defensive_returns": [{"month": "1979-01-31", "value": "0.001"}],
        "leveraged_returns": [
            {
                "month": "1979-01-31",
                "underlying": "0",
                "funding": "0",
                "residual_drag": "0",
                "fx": "0",
                "etf_fee": fee,
                "total_return": fee,
                "return_usd": "0",
                "flags": [],
                "wiped_out": False,
            }
        ],
        "world_validation": None,
        "leveraged_validation": {
            "licensed_daily": {"status": "FAIL_INSUFFICIENT_DAILY_SAMPLE", "n": 0},
            "official_summary": {"status": "FAIL_OFFICIAL_SUMMARY_VALIDATION", "n": 0},
            "lwld": {"status": "FAIL_MISSING_OFFICIAL_LWLD_NAV", "n": 0},
        },
    }


def _backtest_fixture(root: Path) -> tuple[Path, Path, Path]:
    frozen = root / "frozen"
    raw_path = frozen / "raw" / "source.bin"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_bytes(b"raw fixture")
    bundle_path = frozen / "derived" / "bundle.json"
    _write_json(bundle_path, _bundle())
    observations_path = frozen / "derived" / "observations.csv"
    observations_path.write_bytes(_observation_csv())
    config_hash = "b" * 64
    sources = [
        {
            "source_id": "SOURCE",
            "url": "https://example.test/source.bin",
            "raw_hash": _sha(raw_path.read_bytes()),
            "parser_version": "1",
        }
    ]
    artifacts = [
        {"kind": "raw", "path": "raw/source.bin", "sha256": _sha(raw_path.read_bytes())},
        {
            "kind": "derived",
            "role": "normalized_observations",
            "path": "derived/observations.csv",
            "sha256": _sha(observations_path.read_bytes()),
            "schema_version": "OBSERVATION_ROW_CSV_V1",
        },
        {
            "kind": "derived",
            "role": "backtest_bundle",
            "path": "derived/bundle.json",
            "sha256": _sha(bundle_path.read_bytes()),
            "schema_version": "BACKTEST_BUNDLE_V1",
        },
    ]
    manifest = {
        "schema_version": "BACKTEST_INPUT_MANIFEST_V1",
        "config_hash": config_hash,
        "vintage_id": compute_vintage_id(config_hash, sources, artifacts),
        "retrieved_at": "2026-08-22T10:30:00+00:00",
        "run_as_of": "2026-08-22T10:30:00+00:00",
        "expected_through": "1979-01-31",
        "actual_end": "1979-01-31",
        "bundle_path": "derived/bundle.json",
        "artifacts": artifacts,
        "sources": sources,
        "validation_statuses": {
            "world": "FAIL_MISSING_OFFICIAL_NAV",
            "leveraged": "FAIL_MISSING_OFFICIAL_LWLD_NAV",
        },
    }
    manifest_path = frozen / "current_manifest.json"
    _write_json(manifest_path, manifest)
    config = root / "backtest.json"
    _write_json(
        config,
        {
            "schema_version": "BACKTEST_CONFIG_V1",
            "version": "1.0",
            "input_manifest": "frozen/current_manifest.json",
            "initial_capital_eur": "800000",
            "commission_per_executed_sleeve_order_eur": "19",
            "spread_slippage_bps": "10",
            "world_annual_fee": "0.002",
            "leveraged_annual_fee": "0.006",
            "strategies": list(STRATEGIES),
        },
    )
    return config, manifest_path, bundle_path


class CliContractTests(TestCase):
    def test_parser_adds_refresh_and_backtest_without_breaking_evaluate(self):
        parser = _parser()
        evaluate = parser.parse_args(
            ["evaluate", "--config", "policy.json", "--snapshot", "snapshot.json", "--out-dir", "out"]
        )
        refresh = parser.parse_args(["data", "refresh", "--config", "sources.json"])
        backtest = parser.parse_args(["backtest", "--config", "backtest.json", "--output", "out"])

        self.assertEqual(evaluate.command, "evaluate")
        self.assertEqual((refresh.command, refresh.data_command), ("data", "refresh"))
        self.assertEqual(backtest.command, "backtest")

    def test_offline_cli_succeeds_when_network_is_disabled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, _, _ = _backtest_fixture(root)
            output = root / "output"
            with patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")):
                code = main(["backtest", "--config", str(config), "--output", str(output)])

            self.assertEqual(code, 0)
            self.assertEqual({path.name for path in output.iterdir()}, set(OUTPUT_FILES))

    def test_data_refresh_network_access_is_confined_to_adapter(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self):
                return _observation_csv()

        with tempfile.TemporaryDirectory() as directory:
            config = _source_config(Path(directory))
            with patch("urllib.request.urlopen", return_value=Response()) as urlopen:
                self.assertEqual(refresh_data(config, retrieved_at=RETRIEVED_AT), 0)
            self.assertEqual(urlopen.call_count, 1)


class RefreshTransactionTests(TestCase):
    def test_required_source_failure_preserves_published_manifest_and_vintage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = _source_config(root, count=2)
            data_root = root / "frozen"
            old_vintage = data_root / "vintages" / "OLD" / "raw.bin"
            old_vintage.parent.mkdir(parents=True)
            old_vintage.write_bytes(b"old")
            pointer = data_root / "current_manifest.json"
            pointer.write_bytes(b'{"vintage_id":"OLD"}')

            with patch(
                "perpetual_engine.data_sources.fetch_url",
                side_effect=[_observation_csv(), OSError("second source failed")],
            ):
                with self.assertRaisesRegex(OSError, "second source failed"):
                    refresh_data(config, retrieved_at=RETRIEVED_AT)

            self.assertEqual(pointer.read_bytes(), b'{"vintage_id":"OLD"}')
            self.assertEqual(old_vintage.read_bytes(), b"old")
            self.assertEqual({path.name for path in (data_root / "vintages").iterdir()}, {"OLD"})

    def test_pointer_replace_failure_removes_unpointed_new_vintage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = _source_config(root)
            data_root = root / "frozen"
            old_vintage = data_root / "vintages" / "OLD" / "raw.bin"
            old_vintage.parent.mkdir(parents=True)
            old_vintage.write_bytes(b"old")
            pointer = data_root / "current_manifest.json"
            pointer.write_bytes(b'{"vintage_id":"OLD"}')
            real_replace = Path.replace

            def fail_pointer_replace(source: Path, target: Path) -> Path:
                if Path(target) == pointer:
                    raise OSError("pointer replace failed")
                return real_replace(source, target)

            with patch("perpetual_engine.data_sources.fetch_url", return_value=_observation_csv()):
                with patch("pathlib.Path.replace", autospec=True, side_effect=fail_pointer_replace):
                    with self.assertRaisesRegex(OSError, "pointer replace failed"):
                        refresh_data(config, retrieved_at=RETRIEVED_AT)

            self.assertEqual(pointer.read_bytes(), b'{"vintage_id":"OLD"}')
            self.assertEqual(old_vintage.read_bytes(), b"old")
            self.assertEqual({path.name for path in (data_root / "vintages").iterdir()}, {"OLD"})

    def test_official_shaped_refresh_directly_feeds_offline_backtest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "config"
            config_dir.mkdir()
            source_config = json.loads(Path("config/data_sources_v1.json").read_bytes())
            source_path = config_dir / "data_sources_v1.json"
            _write_json(source_path, source_config)
            backtest_path = config_dir / "backtest_v1.json"
            _write_json(backtest_path, json.loads(Path("config/backtest_v1.json").read_bytes()))
            payloads = _official_shaped_payloads(source_config)

            def download(url: str) -> bytes:
                if url not in payloads:
                    raise OSError("validation evidence unavailable")
                return payloads[url]

            with patch("perpetual_engine.data_sources.fetch_url", side_effect=download):
                self.assertEqual(refresh_data(source_path, retrieved_at=RETRIEVED_AT), 0)
            manifest = json.loads((root / "data" / "frozen" / "current_manifest.json").read_bytes())
            self.assertEqual(manifest["schema_version"], "BACKTEST_INPUT_MANIFEST_V1")
            self.assertEqual((manifest["expected_through"], manifest["actual_end"]), ("2026-07-31", "2026-07-31"))
            bundle_path = root / "data" / "frozen" / manifest["bundle_path"]
            bundle = json.loads(bundle_path.read_bytes())
            self.assertEqual((bundle["expected_through"], bundle["actual_end"]), ("2026-07-31", "2026-07-31"))

            output = root / "outputs" / "backtest_v1"
            self.assertEqual(run_backtest_report(backtest_path, output), 0)
            self.assertEqual({path.name for path in output.iterdir()}, set(OUTPUT_FILES))
            curves = (output / "equity_curves.csv").read_text(encoding="utf-8")
            self.assertTrue(all(strategy in curves for strategy in STRATEGIES))

    def test_missing_funding_boundary_fails_instead_of_truncating_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "config"
            config_dir.mkdir()
            source_config = json.loads(Path("config/data_sources_v1.json").read_bytes())
            source_path = config_dir / "data_sources_v1.json"
            _write_json(source_path, source_config)
            payloads = _official_shaped_payloads(
                source_config, include_out_of_regime=False, boundary_gap=True
            )

            with patch(
                "perpetual_engine.data_sources.fetch_url",
                side_effect=lambda url: payloads[url] if url in payloads else (_ for _ in ()).throw(OSError()),
            ):
                with self.assertRaisesRegex(ValueError, "missing USD1MTD156N funding rate"):
                    refresh_data(source_path, retrieved_at=RETRIEVED_AT)

            self.assertFalse((root / "data" / "frozen" / "current_manifest.json").exists())

    def test_internal_funding_gap_fails_instead_of_becoming_bundle_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "config"
            config_dir.mkdir()
            source_config = json.loads(Path("config/data_sources_v1.json").read_bytes())
            source_path = config_dir / "data_sources_v1.json"
            _write_json(source_path, source_config)
            payloads = _official_shaped_payloads(
                source_config, include_out_of_regime=False, internal_gap=True
            )

            with patch(
                "perpetual_engine.data_sources.fetch_url",
                side_effect=lambda url: payloads[url] if url in payloads else (_ for _ in ()).throw(OSError()),
            ):
                with self.assertRaisesRegex(ValueError, "funding carry exceeds seven"):
                    refresh_data(source_path, retrieved_at=RETRIEVED_AT)

            self.assertFalse((root / "data" / "frozen" / "current_manifest.json").exists())

    def test_stale_short_bundle_fails_instead_of_publishing_january_1979(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "config"
            config_dir.mkdir()
            source_config = json.loads(Path("config/data_sources_v1.json").read_bytes())
            source_path = config_dir / "data_sources_v1.json"
            _write_json(source_path, source_config)
            payloads = _official_shaped_payloads(
                source_config,
                outcome_end=date(1979, 1, 1),
                include_out_of_regime=False,
            )

            with patch(
                "perpetual_engine.data_sources.fetch_url",
                side_effect=lambda url: payloads[url] if url in payloads else (_ for _ in ()).throw(OSError()),
            ):
                with self.assertRaisesRegex(ValueError, "bundle end is stale"):
                    refresh_data(source_path, retrieved_at=RETRIEVED_AT)

            self.assertFalse((root / "data" / "frozen" / "current_manifest.json").exists())

    def test_stale_signal_tail_fails_instead_of_publishing_a_longer_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "config"
            config_dir.mkdir()
            source_config = json.loads(Path("config/data_sources_v1.json").read_bytes())
            source_path = config_dir / "data_sources_v1.json"
            _write_json(source_path, source_config)
            payloads = _official_shaped_payloads(
                source_config,
                signal_end=date(2026, 5, 31),
            )

            with patch(
                "perpetual_engine.data_sources.fetch_url",
                side_effect=lambda url: payloads[url] if url in payloads else (_ for _ in ()).throw(OSError()),
            ):
                with self.assertRaisesRegex(ValueError, "bundle end is stale"):
                    refresh_data(source_path, retrieved_at=RETRIEVED_AT)

            self.assertFalse((root / "data" / "frozen" / "current_manifest.json").exists())

    def test_recent_signal_tail_sets_the_explicit_common_bundle_end(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_dir = root / "config"
            config_dir.mkdir()
            source_config = json.loads(Path("config/data_sources_v1.json").read_bytes())
            source_path = config_dir / "data_sources_v1.json"
            _write_json(source_path, source_config)
            payloads = _official_shaped_payloads(source_config, signal_end=date(2026, 5, 31))

            with patch(
                "perpetual_engine.data_sources.fetch_url",
                side_effect=lambda url: payloads[url] if url in payloads else (_ for _ in ()).throw(OSError()),
            ):
                self.assertEqual(
                    refresh_data(source_path, retrieved_at=datetime(2026, 8, 10, tzinfo=UTC)),
                    0,
                )

            manifest = json.loads((root / "data/frozen/current_manifest.json").read_bytes())
            bundle = json.loads((root / "data/frozen" / manifest["bundle_path"]).read_bytes())
            self.assertEqual((manifest["expected_through"], manifest["actual_end"]), ("2026-06-30", "2026-06-30"))
            self.assertEqual((bundle["expected_through"], bundle["actual_end"]), ("2026-06-30", "2026-06-30"))

    def test_internal_signal_gap_fails_instead_of_disabling_the_veto(self):
        for series_id in ("DGS10", "DFII10"):
            with self.subTest(series_id=series_id), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config_dir = root / "config"
                config_dir.mkdir()
                source_config = json.loads(Path("config/data_sources_v1.json").read_bytes())
                source_path = config_dir / "data_sources_v1.json"
                _write_json(source_path, source_config)
                payloads = _official_shaped_payloads(source_config, signal_gap=series_id)

                with patch(
                    "perpetual_engine.data_sources.fetch_url",
                    side_effect=lambda url: payloads[url] if url in payloads else (_ for _ in ()).throw(OSError()),
                ):
                    with self.assertRaisesRegex(ValueError, f"{series_id} coverage has an internal gap"):
                        refresh_data(source_path, retrieved_at=RETRIEVED_AT)

                self.assertFalse((root / "data" / "frozen" / "current_manifest.json").exists())


class OfflineBacktestTests(TestCase):
    def test_recommendation_requires_world_lwld_and_one_leveraged_benchmark_mode(self):
        cases = (
            ("PASS", "PASS_LWLD_VALIDATION", "PASS_FULL_DAILY", "FAIL", "VALIDATED"),
            ("PASS", "PASS_LWLD_VALIDATION", "FAIL", "PASS_PARTIAL_OFFICIAL_SUMMARY", "VALIDATED"),
            ("FAIL", "PASS_LWLD_VALIDATION", "PASS_FULL_DAILY", "PASS_PARTIAL_OFFICIAL_SUMMARY", "UNVALIDATED"),
            ("PASS", "FAIL", "PASS_FULL_DAILY", "PASS_PARTIAL_OFFICIAL_SUMMARY", "UNVALIDATED"),
            ("PASS", "PASS_LWLD_VALIDATION", "FAIL", "FAIL", "UNVALIDATED"),
        )
        for world, lwld, daily, summary, expected in cases:
            with self.subTest(world=world, lwld=lwld, daily=daily, summary=summary):
                self.assertEqual(
                    _recommendation_status(
                        {"status": world},
                        {
                            "lwld": {"status": lwld},
                            "licensed_daily": {"status": daily},
                            "official_summary": {"status": summary},
                        },
                    ),
                    expected,
                )

    def test_corrupt_hash_fails_before_output_directory_and_calculation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, _, bundle = _backtest_fixture(root)
            bundle.write_bytes(bundle.read_bytes() + b"corrupt")
            output = root / "output"
            with patch("perpetual_engine.backtest_report.run_backtest") as calculator:
                with self.assertRaisesRegex(ValueError, "SHA-256"):
                    run_backtest_report(config, output)
            calculator.assert_not_called()
            self.assertFalse(output.exists())

    def test_noncontiguous_bundle_fails_preflight_before_calling_calculator(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, manifest_path, bundle_path = _backtest_fixture(root)
            bundle = json.loads(bundle_path.read_bytes())
            signal = dict(bundle["signals"][0])
            signal.update(month="1979-03-31", decision_at="1979-02-28T23:59:59+00:00")
            bundle["signals"].append(signal)
            for name in ("world_returns", "defensive_returns"):
                bundle[name].append({"month": "1979-03-31", "value": "0"})
            leveraged = dict(bundle["leveraged_returns"][0])
            leveraged["month"] = "1979-03-31"
            bundle["leveraged_returns"].append(leveraged)
            _write_json(bundle_path, bundle)
            manifest = json.loads(manifest_path.read_bytes())
            next(entry for entry in manifest["artifacts"] if entry.get("role") == "backtest_bundle")["sha256"] = _sha(
                bundle_path.read_bytes()
            )
            manifest["vintage_id"] = compute_vintage_id(
                manifest["config_hash"], manifest["sources"], manifest["artifacts"]
            )
            _write_json(manifest_path, manifest)
            output = root / "output"

            with patch("perpetual_engine.backtest_report.run_backtest") as calculator:
                with self.assertRaisesRegex(ValueError, "contiguous"):
                    run_backtest_report(config, output)
            calculator.assert_not_called()
            self.assertFalse(output.exists())

    def test_every_derived_artifact_schema_is_checked_before_calculation(self):
        for schema in (None, "UNKNOWN_SCHEMA"):
            with self.subTest(schema=schema), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config, manifest_path, _ = _backtest_fixture(root)
                manifest = json.loads(manifest_path.read_bytes())
                entry = next(item for item in manifest["artifacts"] if item.get("role") == "normalized_observations")
                if schema is None:
                    entry.pop("schema_version")
                else:
                    entry["schema_version"] = schema
                _write_json(manifest_path, manifest)
                output = root / "output"

                with patch("perpetual_engine.backtest_report.run_backtest") as calculator:
                    with self.assertRaisesRegex(ValueError, "derived artifact schema_version"):
                        run_backtest_report(config, output)
                calculator.assert_not_called()
                self.assertFalse(output.exists())

    def test_stale_vintage_rejects_mutation_even_when_artifact_hash_is_updated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, manifest_path, bundle_path = _backtest_fixture(root)
            bundle = json.loads(bundle_path.read_bytes())
            bundle["world_validation"] = {"status": "PASS", "n": 1}
            _write_json(bundle_path, bundle)
            manifest = json.loads(manifest_path.read_bytes())
            bundle_entry = next(item for item in manifest["artifacts"] if item.get("role") == "backtest_bundle")
            bundle_entry["sha256"] = _sha(bundle_path.read_bytes())
            _write_json(manifest_path, manifest)
            output = root / "output"

            with patch("perpetual_engine.backtest_report.run_backtest") as calculator:
                with self.assertRaisesRegex(ValueError, "vintage_id"):
                    run_backtest_report(config, output)
            calculator.assert_not_called()
            self.assertFalse(output.exists())

    def test_manifest_and_bundle_end_must_reconcile_before_calculation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, manifest_path, bundle_path = _backtest_fixture(root)
            bundle = json.loads(bundle_path.read_bytes())
            bundle["expected_through"] = "1979-02-28"
            _write_json(bundle_path, bundle)
            manifest = json.loads(manifest_path.read_bytes())
            bundle_entry = next(item for item in manifest["artifacts"] if item.get("role") == "backtest_bundle")
            bundle_entry["sha256"] = _sha(bundle_path.read_bytes())
            manifest["vintage_id"] = compute_vintage_id(
                manifest["config_hash"], manifest["sources"], manifest["artifacts"]
            )
            _write_json(manifest_path, manifest)
            output = root / "output"

            with patch("perpetual_engine.backtest_report.run_backtest") as calculator:
                with self.assertRaisesRegex(ValueError, "expected_through|actual_end"):
                    run_backtest_report(config, output)
            calculator.assert_not_called()
            self.assertFalse(output.exists())

    def test_real_backtest_writes_every_artifact_and_named_missing_nav_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, _, _ = _backtest_fixture(root)
            output = root / "output"

            run_backtest_report(config, output)

            self.assertEqual({path.name for path in output.iterdir()}, set(OUTPUT_FILES))
            curves = (output / "equity_curves.csv").read_text(encoding="utf-8")
            self.assertEqual(curves.count("\n"), 1 + len(STRATEGIES))
            world = json.loads((output / "world_validation.json").read_bytes())
            leveraged = json.loads((output / "leveraged_validation.json").read_bytes())
            self.assertEqual(world["status"], "FAIL_MISSING_OFFICIAL_NAV")
            self.assertEqual(leveraged["lwld"]["status"], "FAIL_MISSING_OFFICIAL_LWLD_NAV")

    def test_two_runs_are_byte_identical_and_manifest_hashes_reconcile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, _, _ = _backtest_fixture(root)
            payload = json.loads(config.read_bytes())
            payload["output_files"] = dict(zip((name.rsplit(".", 1)[0] for name in OUTPUT_FILES), OUTPUT_FILES))
            payload["output_files"]["signals"] = "tables/signals.csv"
            _write_json(config, payload)
            first, second = root / "first", root / "second"
            run_backtest_report(config, first)
            run_backtest_report(config, second)

            first_files = {
                path.relative_to(first).as_posix(): path.read_bytes() for path in first.rglob("*") if path.is_file()
            }
            second_files = {
                path.relative_to(second).as_posix(): path.read_bytes() for path in second.rglob("*") if path.is_file()
            }
            self.assertEqual(first_files, second_files)
            manifest = json.loads(first_files["run_manifest.json"])
            self.assertNotIn("run_manifest.json", manifest["output_hashes"])
            self.assertEqual(manifest["run_as_of"], "2026-08-22T10:30:00+00:00")
            self.assertEqual(manifest["recommendation_status"], "UNVALIDATED")
            self.assertNotIn("generated_at", manifest)
            for name, digest in manifest["output_hashes"].items():
                self.assertEqual(digest, _sha(first_files[name]))

    def test_path_traversal_duplicate_artifacts_and_malformed_schema_fail_closed(self):
        mutations = (
            lambda manifest: manifest["artifacts"].append(
                {"kind": "raw", "path": "raw/source.bin", "sha256": "0" * 64}
            ),
            lambda manifest: manifest["artifacts"].append(
                {"kind": "raw", "path": "../escape.bin", "sha256": "0" * 64}
            ),
            lambda manifest: manifest.update(schema_version="UNKNOWN"),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config, manifest_path, _ = _backtest_fixture(root)
                manifest = json.loads(manifest_path.read_bytes())
                mutate(manifest)
                _write_json(manifest_path, manifest)
                output = root / "output"

                self.assertEqual(main(["backtest", "--config", str(config), "--output", str(output)]), 2)
                self.assertFalse(output.exists())

    def test_duplicate_or_traversing_output_paths_fail_before_output_creation(self):
        mutations = (
            lambda outputs: outputs.update(allocations=outputs["signals"]),
            lambda outputs: outputs.update(signals="../signals.csv"),
            lambda outputs: outputs.update(signals="CON.csv"),
            lambda outputs: outputs.update(signals="nested/trailing. "),
            lambda outputs: outputs.update(signals="nested/file:stream.csv"),
            lambda outputs: outputs.update(signals="nested//signals.csv"),
            lambda outputs: outputs.update(signals="nested/./signals.csv"),
            lambda outputs: outputs.update(signals="reports", allocations="reports/allocations.csv"),
            lambda outputs: outputs.update(signals="Reports/SIGNALS.csv", allocations="reports/signals.CSV"),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config, _, _ = _backtest_fixture(root)
                payload = json.loads(config.read_bytes())
                payload["output_files"] = dict(zip(
                    (name.rsplit(".", 1)[0] for name in OUTPUT_FILES),
                    OUTPUT_FILES,
                ))
                mutate(payload["output_files"])
                _write_json(config, payload)
                output = root / "output"

                self.assertEqual(main(["backtest", "--config", str(config), "--output", str(output)]), 2)
                self.assertFalse(output.exists())
