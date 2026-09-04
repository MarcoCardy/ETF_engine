from __future__ import annotations

import hashlib
import io
import json
import tempfile
import zipfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from perpetual_engine.data_sources import (
    SourceArtifact,
    parse_eurostat_hicp_json,
    parse_fred_csv,
    parse_wdi_market_cap_json,
)
from perpetual_engine.market_proxy import parse_french_archive


NOW = datetime(2026, 8, 22, 10, 30, tzinfo=timezone.utc)
INTERNATIONAL_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_International_Indices.zip"
US_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/F-F_Research_Data_Factors_CSV.zip"
DEVELOPED_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/Developed_3_Factors_CSV.zip"
WDI_URL = "https://api.worldbank.org/v2/country/USA;WLD/indicator/CM.MKT.LCAP.CD?format=json&per_page=20000"
HICP_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_midx?geo=IT&coicop=CP00&unit=I15"
MOMENTUM_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/Developed_6_Portfolios_ME_Prior_12_2_CSV.zip"
QUALITY_URL = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/Developed_6_Portfolios_ME_OP_CSV.zip"
AQR_URL = "https://www.aqr.com/-/media/AQR/Documents/Insights/Data-Sets/Time-Series-Momentum-Factors-Monthly.xlsx"


def archive(member: str, text: str) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zipped:
        zipped.writestr(member, text)
    return output.getvalue()


def artifact(root: Path, filename: str, content: bytes, url: str) -> SourceArtifact:
    path = root / filename
    path.write_bytes(content)
    return SourceArtifact(url, NOW, hashlib.sha256(content).hexdigest(), path, len(content), "1")


class OfficialRefreshParserTests(TestCase):
    def test_data_source_catalog_declares_exact_academic_proxy_contracts(self):
        catalog = json.loads((Path(__file__).parents[1] / "config" / "four_sleeve_sources_v1.json").read_text(encoding="utf-8"))
        sources = {source["source_id"]: source for source in catalog["sources"]}
        expected = {
            "FRENCH_DEVELOPED_MOMENTUM_MONTHLY": (
                MOMENTUM_URL,
                "french_developed_momentum_archive",
                "french_developed_big_hiprior_monthly.csv",
            ),
            "FRENCH_DEVELOPED_QUALITY_MONTHLY": (
                QUALITY_URL,
                "french_developed_quality_archive",
                "french_developed_big_robust_monthly.csv",
            ),
            "AQR_TSMOM_MONTHLY": (
                AQR_URL,
                "french_aqr_tsmom_archive",
                "aqr_tsmom_monthly.csv",
            ),
        }
        for source_id, (url, parser_kind, normalized_path) in expected.items():
            with self.subTest(source_id=source_id):
                source = sources[source_id]
                self.assertEqual(source["url"], url)
                self.assertEqual(source["parser_kind"], parser_kind)
                self.assertEqual(source["normalized_path"], normalized_path)
                self.assertEqual(source["status"], "required")

    def test_french_archives_reuse_exact_task_four_members_fields_and_scaling(self):
        fixtures = (
            (
                "french_international_archive",
                INTERNATIONAL_URL,
                "international.zip",
                archive("Ind_all.Dat", "Value-Weight Dollar Returns\nDate Mkt\n197701 5.00\n"),
                "FRENCH_INTERNATIONAL_MKT_USD",
                Decimal("0.05"),
            ),
            (
                "french_us_factor_archive",
                US_URL,
                "us.zip",
                archive("F-F_Research_Data_Factors.csv", "Date,Mkt-RF,RF\n197701,4.90,0.10\n"),
                "FRENCH_US_MKT_USD",
                Decimal("0.05"),
            ),
            (
                "french_developed_factor_archive",
                DEVELOPED_URL,
                "developed.zip",
                archive("Developed_3_Factors.csv", "Date,Mkt-RF,RF\n199007,7.00,0.20\n"),
                "FRENCH_DEVELOPED_MKT_USD",
                Decimal("0.072"),
            ),
            (
                "french_developed_rf_archive",
                DEVELOPED_URL,
                "developed-rf.zip",
                archive("Developed_3_Factors.csv", "Date,Mkt-RF,RF\n199007,7.00,0.20\n"),
                "FRENCH_DEVELOPED_RF_USD",
                Decimal("0.002"),
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for parser_kind, url, filename, content, series_id, expected in fixtures:
                with self.subTest(parser_kind=parser_kind):
                    rows = parse_french_archive(artifact(root, filename, content, url), parser_kind)
                    self.assertEqual((rows[0].series_id, rows[0].value), (series_id, expected))
                    self.assertEqual(rows[0].source_url, url)
                    self.assertEqual(rows[0].source_hash, hashlib.sha256(content).hexdigest())

    def test_wdi_parser_requires_exact_indicator_aggregates_and_emits_both_caps(self):
        payload = [
            {"page": 1, "pages": 1},
            [
                {"indicator": {"id": "CM.MKT.LCAP.CD"}, "countryiso3code": "USA", "date": "1975", "value": 40},
                {"indicator": {"id": "CM.MKT.LCAP.CD"}, "countryiso3code": "WLD", "date": "1975", "value": 100},
            ],
        ]
        content = json.dumps(payload).encode()
        with tempfile.TemporaryDirectory() as directory:
            rows = parse_wdi_market_cap_json(
                artifact(Path(directory), "wdi.json", content, WDI_URL),
                indicator="CM.MKT.LCAP.CD",
                aggregates=("USA", "WLD"),
            )
        self.assertEqual([row.series_id for row in rows], ["WDI_CM.MKT.LCAP.CD_USA", "WDI_CM.MKT.LCAP.CD_WLD"])
        self.assertEqual([row.value for row in rows], [Decimal("40"), Decimal("100")])
        self.assertTrue(all(row.source_hash == hashlib.sha256(content).hexdigest() for row in rows))

        payload[1][1]["indicator"]["id"] = "WRONG"
        bad = json.dumps(payload).encode()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "indicator"):
                parse_wdi_market_cap_json(
                    artifact(Path(directory), "bad.json", bad, WDI_URL),
                    indicator="CM.MKT.LCAP.CD",
                    aggregates=("USA", "WLD"),
                )

    def test_fred_parser_uses_explicit_series_unit_scale_and_availability_rule(self):
        content = b"observation_date,DGS10\n1978-12-28,8.50\n"
        url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10"
        with tempfile.TemporaryDirectory() as directory:
            rows = parse_fred_csv(
                artifact(Path(directory), "dgs10.csv", content, url),
                series_id="DGS10",
                unit="annual_decimal",
                source_scale="percent",
                frequency="daily",
                availability="treasury_next_business_day",
            )
        self.assertEqual(rows[0].value, Decimal("0.085"))
        self.assertEqual(rows[0].available_at, datetime(1978, 12, 29, 23, 59, 59, tzinfo=timezone.utc))
        self.assertEqual(rows[0].source_url, url)

        with tempfile.TemporaryDirectory() as directory:
            bad = artifact(Path(directory), "wrong.csv", b"observation_date,WRONG\n1978-12-28,8.50\n", url)
            with self.assertRaisesRegex(ValueError, "DGS10"):
                parse_fred_csv(
                    bad,
                    series_id="DGS10",
                    unit="annual_decimal",
                    source_scale="percent",
                    frequency="daily",
                    availability="treasury_next_business_day",
                )

    def test_eurostat_parser_requires_exact_dimensions_and_emits_month_end_hicp(self):
        payload = {
            "id": ["freq", "unit", "coicop", "geo", "time"],
            "size": [1, 1, 1, 1, 2],
            "dimension": {
                "freq": {"category": {"index": {"M": 0}}},
                "unit": {"category": {"index": {"I15": 0}}},
                "coicop": {"category": {"index": {"CP00": 0}}},
                "geo": {"category": {"index": {"IT": 0}}},
                "time": {"category": {"index": {"2023-11": 0, "2023-12": 1}}},
            },
            "value": {"0": 120.0, "1": 121.5},
        }
        content = json.dumps(payload).encode()
        with tempfile.TemporaryDirectory() as directory:
            rows = parse_eurostat_hicp_json(artifact(Path(directory), "hicp.json", content, HICP_URL))
        self.assertEqual([row.observation_date for row in rows], [date(2023, 11, 30), date(2023, 12, 31)])
        self.assertEqual([row.value for row in rows], [Decimal("120.0"), Decimal("121.5")])
        self.assertTrue(all(row.series_id == "HICP" and row.source_url == HICP_URL for row in rows))

        payload["dimension"]["geo"]["category"]["index"] = {"FR": 0}
        bad = json.dumps(payload).encode()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "geo"):
                parse_eurostat_hicp_json(artifact(Path(directory), "bad.json", bad, HICP_URL))
