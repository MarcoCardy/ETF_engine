import base64
import gzip
import tempfile
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from openpyxl import Workbook

from perpetual_engine.data_sources import (
    SourceArtifact,
    damodaran_erp_asof,
    parse_damodaran_annual,
    parse_damodaran_monthly,
    select_damodaran_erp,
)


ANNUAL_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/histimpl.xls"
MONTHLY_URL = "https://pages.stern.nyu.edu/~adamodar/pc/implprem/ERPbymonth.xlsx"
ANNUAL_XLS_GZIP_B64 = (
    "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"
)

ANNUAL_1977_2007_XLS_GZIP_B64 = (
    "H4sIAAAAAAACCu3YT0gUURwH8N+b/b+u+8/d1V11XaRMTQILIgLdMlwpSMygPxLVmgtJprJ4qUuWeQyCTkUXwUsXq0t/qKBuBYFRhyAItKBDQVBUROhO7313ltbXO7hQkjG/ZX7z9s3nzTydN29n5tlsYG7qZmyepGgnC+V0F9mL6hhfXIUvfuLbdV0UC2snX3QzVlW4nPxE2m10r/ypQ5xDcb7nSaMb1kc8E73hyyEape6R4UxiBaMDfUgz0Yc2nhld5TVeiqJXQeRjyBXI1yHvI29DzQXkNm7nWB/NJrubtxij+IBWj21eEvu9jTavUNNKYXosRvGZiyxvbbQ9O5ge+jc3xK0emiZ+3royw5lsemiOQvwETtNXPUH0pXClPkyY9Stbz4jXf19a71DUX9KsROOkH8UAn+QD8oklfxHuPZ7JjLUu0BoMS7FweTCTzgaJdp4cHRrMDCQ6e3sSjakdqc4mt5iYcSH7l1zI5RjgHp4HyIdyAPvy86l64dqn57v7e5JHUDOOyTs/xa8VvSKdzooWvLEXWxLIwjYjr0c+h71WoxxDDvHBydcNPWGjkJqAOY+tDfw4mxAvkuuKyo28PPlhz5345NtkEy/PdM2fDs28TE5RPf/JGeDtxWeCWlgLu3JZxN1kYc2M6eA1cvS3qcGp+Y2+68bvmI8WyY1iADn/Tfx3mOGZ5B/wYzD4d3GRyxzCC6kppAb5XpIWhbRAfpSkVSGtkJ8laVNIG+Q3SdoV0g75Q5IOhXRA5iTpVEgnpFa3VLoU0gVpl6RbId2QbkmWKWQZpFeSHoX0QAYlWa6Q5ZARSXoV0gsZk6RPIX2QcUn6FdIPWS/JgEIGIBskGVTIIGSzJCsUsgJygyRDChmC3CjJsEKGITdLMqKQEcitkqxUyErIdklWKWQVZIckowoZhUxJMqaQMchdkqxWyGrIbknWKGQNZK8kaxWyFnKfJOMKGYfsk2SdQtZBHi6S7VqQbomJjd9e/Qo3mWGGGWaYsSqDGTf1lvwjBm4exW2hw3ivs8iXnPma5L+NXhrhnzH+eNdJw3ydpVMljZ8I2VhhX2yZbQrvC0Xs50fP0gnqRz9OlDx++cMWK/57lt3Q/+cuoVKPnyuln3/5+D8BLnd62gAWAAA="
)


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def artifact(path: Path, source_url: str, retrieved_at: datetime) -> SourceArtifact:
    return SourceArtifact(source_url, retrieved_at, "a" * 64, path, path.stat().st_size, "test")


def write_annual_xls(path: Path) -> None:
    path.write_bytes(gzip.decompress(base64.b64decode(ANNUAL_XLS_GZIP_B64)))


def write_monthly_xlsx(
    path: Path,
    *,
    sheet_name: str = "Historical ERP",
    headers: tuple[str, str] = ("Start of month", "ERP (T12m)"),
    rows: tuple[tuple[object, object], ...] = ((datetime(2008, 9, 1), 4.5), (datetime(2008, 10, 1), 4.6)),
    percent_format: bool = False,
) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    if percent_format:
        for row_index in range(2, len(rows) + 2):
            sheet.cell(row_index, 2).number_format = "0.0%"
    workbook.save(path)


class DamodaranParserTests(TestCase):
    def test_annual_skips_years_with_no_published_erp(self):
        def cell(value: object) -> SimpleNamespace:
            return SimpleNamespace(value=value, xf_index=None)

        class AnnualSheet:
            def __init__(self):
                self.rows = ((cell("Year"), cell("Implied ERP (FCFE)")), (cell(1960), cell(None))) + tuple(
                    (cell(year), cell(4.5)) for year in range(1961, 2008)
                )
                self.nrows = len(self.rows)

            def row(self, index):
                return self.rows[index]

            def cell_value(self, row_index, column_index):
                return self.rows[row_index][column_index].value

            def cell(self, row_index, column_index):
                return self.rows[row_index][column_index]

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "histimpl.xls"
            write_annual_xls(path)
            with patch("perpetual_engine.data_sources.xlrd.open_workbook", return_value=SimpleNamespace(sheets=lambda: (AnnualSheet(),))):
                rows = parse_damodaran_annual(path, artifact(path, ANNUAL_URL, utc("2008-03-01T00:00:00Z")))

        self.assertEqual(rows[0].observation_date, date(1961, 12, 31))

    def test_annual_2007_first_affects_march_2008(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "histimpl.xls"
            write_annual_xls(path)
            rows = parse_damodaran_annual(path, artifact(path, ANNUAL_URL, utc("2008-03-01T00:00:00Z")))

        row = rows[-1]
        self.assertEqual(row.observation_date, date(2007, 12, 31))
        self.assertEqual(row.value, Decimal("0.045"))
        self.assertEqual(row.available_at, utc("2008-02-01T23:59:59Z"))
        self.assertIsNone(damodaran_erp_asof((row,), (), utc("2008-02-01T12:00:00Z")))
        self.assertEqual(damodaran_erp_asof((row,), (), utc("2008-02-29T23:59:59Z")), row)

    def test_monthly_switch_is_exact_and_date_only_release_cannot_affect_september_open(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ERPbymonth.xlsx"
            write_monthly_xlsx(path)
            rows = parse_damodaran_monthly(path, artifact(path, MONTHLY_URL, utc("2008-11-01T00:00:00Z")))

        self.assertEqual(rows[0].series_id, "DAMODARAN_ERP_T12M")
        self.assertEqual(rows[0].observation_date, date(2008, 9, 1))
        self.assertEqual(rows[0].value, Decimal("0.045"))
        self.assertEqual(rows[0].available_at, utc("2008-09-02T23:59:59Z"))
        self.assertIsNone(damodaran_erp_asof((), rows, utc("2008-08-31T23:59:59Z")))
        self.assertEqual(damodaran_erp_asof((), rows, utc("2008-09-30T23:59:59Z")), rows[0])

    def test_monthly_accepts_damodaran_literal_date_and_percent_cells(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ERPbymonth.xlsx"
            write_monthly_xlsx(path, rows=(("1-Sep-08", "4.50%"), ("1-Oct-08", "4.60%")))
            rows = parse_damodaran_monthly(path, artifact(path, MONTHLY_URL, utc("2008-11-01T00:00:00Z")))

        self.assertEqual(rows[0].observation_date, date(2008, 9, 1))
        self.assertEqual(rows[0].value, Decimal("0.045"))

    def test_monthly_preferred_after_september_switch_and_annual_is_used_before_release(self):
        with tempfile.TemporaryDirectory() as directory:
            annual_path = Path(directory) / "histimpl.xls"
            monthly_path = Path(directory) / "ERPbymonth.xlsx"
            write_annual_xls(annual_path)
            write_monthly_xlsx(monthly_path)
            annual_rows = parse_damodaran_annual(annual_path, artifact(annual_path, ANNUAL_URL, utc("2008-11-01T00:00:00Z")))
            monthly_rows = parse_damodaran_monthly(monthly_path, artifact(monthly_path, MONTHLY_URL, utc("2008-11-01T00:00:00Z")))

        self.assertEqual(damodaran_erp_asof(annual_rows, monthly_rows, utc("2008-08-31T23:59:59Z")), annual_rows[-1])
        self.assertEqual(damodaran_erp_asof(annual_rows, monthly_rows, utc("2008-09-30T23:59:59Z")), monthly_rows[0])

    def test_switch_month_never_falls_back_to_annual_before_monthly_is_available(self):
        with tempfile.TemporaryDirectory() as directory:
            annual_path = Path(directory) / "histimpl.xls"
            monthly_path = Path(directory) / "ERPbymonth.xlsx"
            write_annual_xls(annual_path)
            write_monthly_xlsx(monthly_path)
            annual_rows = parse_damodaran_annual(annual_path, artifact(annual_path, ANNUAL_URL, utc("2008-11-01T00:00:00Z")))
            monthly_rows = parse_damodaran_monthly(monthly_path, artifact(monthly_path, MONTHLY_URL, utc("2008-11-01T00:00:00Z")))

        self.assertIsNone(select_damodaran_erp(annual_rows, monthly_rows, utc("2008-09-01T23:59:59Z")))
        self.assertIsNone(select_damodaran_erp(annual_rows, monthly_rows, utc("2008-09-02T12:00:00Z")))
        self.assertEqual(select_damodaran_erp(annual_rows, monthly_rows, utc("2008-09-02T23:59:59Z")), monthly_rows[0])
        self.assertIsNone(damodaran_erp_asof(annual_rows, monthly_rows, utc("2008-09-01T23:59:59Z")))

    def test_rejects_monthly_workbook_without_exact_september_2008_first_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ERPbymonth.xlsx"
            write_monthly_xlsx(path, rows=((datetime(2008, 10, 1), 4.6),))
            with self.assertRaisesRegex(ValueError, "first observation.*2008-09-01"):
                parse_damodaran_monthly(path, artifact(path, MONTHLY_URL, utc("2008-11-01T00:00:00Z")))

    def test_rejects_internal_annual_year_gap(self):
        def cell(value: object) -> SimpleNamespace:
            return SimpleNamespace(value=value, xf_index=None)

        class AnnualSheet:
            def __init__(self):
                self.rows = (
                    (cell("Year"), cell("Implied ERP (FCFE)")),
                    (cell(2005), cell(4.4)),
                    (cell(2007), cell(4.5)),
                )
                self.nrows = len(self.rows)

            def row(self, index: int):
                return self.rows[index]

            def cell_value(self, row_index: int, column_index: int):
                return self.rows[row_index][column_index].value

            def cell(self, row_index: int, column_index: int):
                return self.rows[row_index][column_index]

        class AnnualBook:
            def sheets(self):
                return (AnnualSheet(),)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "histimpl.xls"
            write_annual_xls(path)
            with patch("perpetual_engine.data_sources.xlrd.open_workbook", return_value=AnnualBook()):
                with self.assertRaisesRegex(ValueError, "annual.*gap"):
                    parse_damodaran_annual(path, artifact(path, ANNUAL_URL, utc("2008-03-01T00:00:00Z")))

    def test_rejects_annual_history_that_does_not_end_in_2007(self):
        def cell(value: object) -> SimpleNamespace:
            return SimpleNamespace(value=value, xf_index=None)

        class AnnualSheet:
            def __init__(self, years: tuple[int, ...]):
                self.rows = ((cell("Year"), cell("Implied ERP (FCFE)")),) + tuple(
                    (cell(year), cell(4.5)) for year in years
                )
                self.nrows = len(self.rows)

            def row(self, index: int):
                return self.rows[index]

            def cell_value(self, row_index: int, column_index: int):
                return self.rows[row_index][column_index].value

            def cell(self, row_index: int, column_index: int):
                return self.rows[row_index][column_index]

        class AnnualBook:
            def __init__(self, years: tuple[int, ...]):
                self.sheet = AnnualSheet(years)

            def sheets(self):
                return (self.sheet,)

        for years in ((1977, 1978), (2005, 2006)):
            with self.subTest(years=years), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "histimpl.xls"
                write_annual_xls(path)
                with patch("perpetual_engine.data_sources.xlrd.open_workbook", return_value=AnnualBook(years)):
                    with self.assertRaisesRegex(ValueError, "must end in 2007"):
                        parse_damodaran_annual(path, artifact(path, ANNUAL_URL, utc("2008-03-01T00:00:00Z")))

    def test_rejects_nonofficial_monthly_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ERPbymonth.xlsx"
            write_monthly_xlsx(path)
            bad_url = "https://pages.stern.nyu.edu/ERPbymonth.xlsx"
            with self.assertRaisesRegex(ValueError, "source URL"):
                parse_damodaran_monthly(path, artifact(path, bad_url, utc("2008-11-01T00:00:00Z")))

    def test_rejects_nonofficial_annual_source(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "histimpl.xls"
            write_annual_xls(path)
            bad_url = "https://pages.stern.nyu.edu/~adamodar/pc/histimpl.xls"
            with self.assertRaisesRegex(ValueError, "source URL"):
                parse_damodaran_annual(path, artifact(path, bad_url, utc("2008-03-01T00:00:00Z")))

    def test_rejects_alternative_erp_column(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ERPbymonth.xlsx"
            write_monthly_xlsx(path, headers=("Start of month", "Sustainable ERP"))
            with self.assertRaisesRegex(ValueError, "ERP \\(T12m\\)"):
                parse_damodaran_monthly(path, artifact(path, MONTHLY_URL, utc("2008-11-01T00:00:00Z")))

    def test_rejects_wrong_monthly_sheet(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ERPbymonth.xlsx"
            write_monthly_xlsx(path, sheet_name="ERP")
            with self.assertRaisesRegex(ValueError, "Historical ERP"):
                parse_damodaran_monthly(path, artifact(path, MONTHLY_URL, utc("2008-11-01T00:00:00Z")))

    def test_rejects_null_or_nonnumeric_monthly_erp(self):
        invalid_values = (None, "not a number")
        for value in invalid_values:
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "ERPbymonth.xlsx"
                write_monthly_xlsx(path, rows=((datetime(2008, 9, 1), value),))
                with self.assertRaisesRegex(ValueError, "ERP \\(T12m\\)"):
                    parse_damodaran_monthly(path, artifact(path, MONTHLY_URL, utc("2008-10-01T00:00:00Z")))

    def test_rejects_duplicate_or_gapped_monthly_dates(self):
        cases = {
            "duplicate": ((datetime(2008, 9, 1), 4.5), (datetime(2008, 9, 1), 4.6)),
            "gap": ((datetime(2008, 9, 1), 4.5), (datetime(2008, 11, 1), 4.6)),
        }
        for reason, rows in cases.items():
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "ERPbymonth.xlsx"
                write_monthly_xlsx(path, rows=rows)
                with self.assertRaisesRegex(ValueError, "duplicate|gap"):
                    parse_damodaran_monthly(path, artifact(path, MONTHLY_URL, utc("2008-12-01T00:00:00Z")))

    def test_rejects_stale_monthly_maximum_date_from_configured_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ERPbymonth.xlsx"
            write_monthly_xlsx(path)
            with self.assertRaisesRegex(ValueError, "maximum observation date is stale"):
                parse_damodaran_monthly(path, artifact(path, MONTHLY_URL, utc("2009-01-01T00:00:00Z")))

    def test_percent_formatted_decimal_erp_is_preserved_but_unformatted_decimal_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            percent_path = Path(directory) / "percent.xlsx"
            plain_path = Path(directory) / "plain.xlsx"
            rows = ((datetime(2008, 9, 1), 0.045),)
            write_monthly_xlsx(percent_path, rows=rows, percent_format=True)
            write_monthly_xlsx(plain_path, rows=rows)
            percent_rows = parse_damodaran_monthly(percent_path, artifact(percent_path, MONTHLY_URL, utc("2008-10-01T00:00:00Z")))
            self.assertEqual(percent_rows[0].value, Decimal("0.045"))
            with self.assertRaisesRegex(ValueError, "decimal scale"):
                parse_damodaran_monthly(plain_path, artifact(plain_path, MONTHLY_URL, utc("2008-10-01T00:00:00Z")))

    def test_rejects_implausible_normalized_erp_median(self):
        for value in (0.005, 0.16):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "ERPbymonth.xlsx"
                write_monthly_xlsx(path, rows=((datetime(2008, 9, 1), value),), percent_format=True)
                with self.assertRaisesRegex(ValueError, "historical median"):
                    parse_damodaran_monthly(path, artifact(path, MONTHLY_URL, utc("2008-10-01T00:00:00Z")))
