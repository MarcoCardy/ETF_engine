from datetime import date, timedelta
from decimal import Decimal
from unittest import TestCase

from perpetual_engine.leveraged_proxy import (
    OfficialSummary,
    FundingRate,
    ReturnSeries,
    SeriesKind,
    SummaryHorizons,
    apply_fx_once,
    funding_rate_for_day,
    leveraged_daily_return,
    leveraged_monthly_return,
    validate_leveraged_proxy,
)


def rates_for_month(month: date, series_id: str = "DFF", rate: str = "0") -> tuple[FundingRate, ...]:
    next_month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    return tuple(
        FundingRate(day, series_id, Decimal(rate))
        for day in (month + timedelta(days=index) for index in range((next_month - month).days))
    )


def month_end(year: int, month: int) -> date:
    return date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)


def series(kind: SeriesKind, values: dict[date, Decimal]) -> ReturnSeries:
    return ReturnSeries(kind, values)


def business_days(start: date, count: int) -> tuple[date, ...]:
    days: list[date] = []
    current = start
    while len(days) < count:
        if current.weekday() < 5:
            days.append(current)
        current += timedelta(days=1)
    return tuple(days)


class LeveragedProxyTests(TestCase):
    def test_flat_market_loses_positive_funding_and_keeps_a_separate_ledger(self):
        result = leveraged_daily_return(Decimal("0"), Decimal("5"), 1)

        self.assertLess(result, Decimal("0"))
        self.assertEqual(result.underlying, Decimal("0"))
        self.assertEqual(result.funding, Decimal("-0.0001388888888888888888888888889"))
        self.assertEqual(result.residual_drag, Decimal("0"))
        self.assertEqual(result.etf_fee, Decimal("0"))

    def test_daily_reset_is_path_dependent(self):
        wealth = (Decimal("1") + leveraged_daily_return(Decimal("0.10"), Decimal("0"), 1)) * (
            Decimal("1") + leveraged_daily_return(Decimal("-0.0909090909"), Decimal("0"), 1)
        )

        self.assertNotEqual(wealth, Decimal("1"))
        self.assertAlmostEqual(float(wealth), 0.98181818184, places=10)

    def test_funding_uses_actual_days_carry_limit_and_exact_source_boundaries(self):
        friday = funding_rate_for_day(
            date(1985, 12, 27),
            (FundingRate(date(1985, 12, 27), "DFF", Decimal("5")),),
        )
        self.assertEqual(friday.series_id, "DFF")
        self.assertEqual(leveraged_daily_return(Decimal("0"), friday.rate_pct, 3).funding, Decimal("-0.0004166666666666666666666666667"))
        self.assertEqual(
            funding_rate_for_day(date(1985, 12, 31), (FundingRate(date(1985, 12, 24), "DFF", Decimal("5")),)).rate_pct,
            Decimal("5"),
        )
        with self.assertRaisesRegex(ValueError, "seven"):
            funding_rate_for_day(date(1985, 12, 31), (FundingRate(date(1985, 12, 23), "DFF", Decimal("5")),))

        boundary_rates = (
            FundingRate(date(1985, 12, 31), "DFF", Decimal("4")),
            FundingRate(date(1986, 1, 1), "USD1MTD156N", Decimal("5")),
            FundingRate(date(2021, 8, 31), "USD1MTD156N", Decimal("1")),
            FundingRate(date(2021, 9, 1), "SOFR", Decimal("0.1")),
        )
        self.assertEqual(funding_rate_for_day(date(1985, 12, 31), boundary_rates).series_id, "DFF")
        self.assertEqual(funding_rate_for_day(date(1986, 1, 1), boundary_rates).series_id, "USD1MTD156N")
        self.assertEqual(funding_rate_for_day(date(2021, 8, 31), boundary_rates).series_id, "USD1MTD156N")
        self.assertEqual(funding_rate_for_day(date(2021, 9, 1), boundary_rates).series_id, "SOFR")
        with self.assertRaisesRegex(ValueError, "SOFR"):
            funding_rate_for_day(date(2021, 9, 1), boundary_rates[:-1])
        with self.assertRaisesRegex(ValueError, "does not match"):
            funding_rate_for_day(date(2021, 9, 1), (FundingRate(date(2021, 8, 31), "SOFR", Decimal("0.1")),))

    def test_fx_is_applied_once_after_usd_leverage(self):
        self.assertEqual(apply_fx_once(Decimal("0.10"), Decimal("0.90")), Decimal("-0.01"))
        self.assertEqual(leveraged_daily_return(Decimal("0.05"), Decimal("0"), 1, Decimal("0.90")).fx, Decimal("-0.11"))

    def test_monthly_residual_and_ter_are_separate_and_daily_has_no_residual(self):
        month = date(1985, 1, 1)
        benchmark = leveraged_monthly_return(Decimal("0.10"), rates_for_month(month), month, Decimal("1"))
        investable = leveraged_monthly_return(Decimal("0.10"), rates_for_month(month), month, Decimal("1"), investable=True)
        february = leveraged_monthly_return(Decimal("0.10"), rates_for_month(date(1985, 2, 1)), date(1985, 2, 1), Decimal("1"), investable=True)
        daily = leveraged_daily_return(Decimal("0"), Decimal("0"), 1, investable=True)

        self.assertLess(benchmark.residual_drag, Decimal("0"))
        self.assertEqual(benchmark.etf_fee, Decimal("0"))
        self.assertEqual(investable.etf_fee, Decimal("0.994") ** (Decimal("1") / Decimal("12")) - Decimal("1"))
        self.assertEqual(february.etf_fee, investable.etf_fee)
        self.assertEqual(daily.residual_drag, Decimal("0"))
        self.assertNotEqual(daily.etf_fee, Decimal("-0.011"))

    def test_monthly_wipeout_clamps_and_persists_until_explicit_reset(self):
        month = date(1985, 1, 1)
        wiped = leveraged_monthly_return(Decimal("-0.60"), rates_for_month(month), month)
        still_wiped = leveraged_monthly_return(Decimal("0.50"), rates_for_month(month), month, wiped_out=wiped.wiped_out)
        reset = leveraged_monthly_return(Decimal("0.50"), rates_for_month(month), month, wiped_out=wiped.wiped_out, reset=True)

        self.assertEqual(wiped.total_return, Decimal("-1"))
        self.assertIn("LEVERAGED_SLEEVE_WIPEOUT", wiped.flags)
        self.assertEqual(still_wiped.total_return, Decimal("-1"))
        self.assertEqual(reset.wiped_out, False)
        self.assertGreater(reset.total_return, Decimal("0"))

    def test_monthly_reset_is_audited_even_when_the_new_month_wipes_out_again(self):
        month = date(1985, 1, 1)
        recovered = leveraged_monthly_return(
            Decimal("0.10"),
            rates_for_month(month),
            month,
            wiped_out=True,
            reset=True,
        )
        wiped_again = leveraged_monthly_return(
            Decimal("-0.60"),
            rates_for_month(month),
            month,
            wiped_out=True,
            reset=True,
        )

        self.assertEqual(recovered.flags, ("CAPITAL_RESET",))
        self.assertEqual(wiped_again.flags, ("CAPITAL_RESET", "LEVERAGED_SLEEVE_WIPEOUT"))
        self.assertFalse(recovered.wiped_out)
        self.assertTrue(wiped_again.wiped_out)

    def test_daily_ter_uses_actual_calendar_days_once(self):
        benchmark = leveraged_daily_return(Decimal("0"), Decimal("0"), 3)
        investable = leveraged_daily_return(Decimal("0"), Decimal("0"), 3, investable=True)

        self.assertEqual(benchmark.etf_fee, Decimal("0"))
        self.assertEqual(investable.etf_fee, Decimal("0.994") ** (Decimal("3") / Decimal("365")) - Decimal("1"))
        self.assertEqual(investable.total_return, investable.etf_fee)

    def test_daily_calendar_days_reject_non_positive_boolean_and_non_integer_before_any_calculation(self):
        for calendar_days in (0, -1, True, False, Decimal("1"), 1.0, "1"):
            for investable in (False, True):
                with self.subTest(calendar_days=calendar_days, investable=investable):
                    with self.assertRaisesRegex(ValueError, "calendar_days"):
                        leveraged_daily_return(Decimal("0"), Decimal("0"), calendar_days, investable=investable)  # type: ignore[arg-type]

    def test_validation_modes_keep_full_daily_official_summary_and_lwld_independent(self):
        daily_dates = tuple(date(2014, 2, 1) + timedelta(days=index) for index in range(252))
        daily = {day: Decimal("0.001") + Decimal(index % 5) / Decimal("10000") for index, day in enumerate(daily_dates)}
        full = validate_leveraged_proxy(
            series(SeriesKind.DAILY_BENCHMARK_PREFEE_USD, daily),
            series(SeriesKind.OFFICIAL_LEVERAGED_DAILY, daily),
            mode="licensed_daily",
            expected_dates=daily_dates,
        )
        insufficient = validate_leveraged_proxy(
            series(SeriesKind.DAILY_BENCHMARK_PREFEE_USD, daily),
            series(SeriesKind.OFFICIAL_LEVERAGED_DAILY, daily),
            mode="licensed_daily",
        )
        unmatched_proxy = dict(daily)
        unmatched_proxy[date(2015, 1, 1)] = Decimal("0.001")
        incomplete = validate_leveraged_proxy(
            series(SeriesKind.DAILY_BENCHMARK_PREFEE_USD, unmatched_proxy),
            series(SeriesKind.OFFICIAL_LEVERAGED_DAILY, daily),
            mode="licensed_daily",
            expected_dates=daily_dates,
        )
        short = validate_leveraged_proxy(
            series(SeriesKind.DAILY_BENCHMARK_PREFEE_USD, dict(list(daily.items())[:-1])),
            series(SeriesKind.OFFICIAL_LEVERAGED_DAILY, dict(list(daily.items())[:-1])),
            mode="licensed_daily",
            expected_dates=daily_dates[:-1],
        )
        gap_source_dates = tuple(date(2014, 2, 1) + timedelta(days=index) for index in range(260))
        gap_source = {day: Decimal("0.001") + Decimal(index % 5) / Decimal("10000") for index, day in enumerate(gap_source_dates)}
        gap_dates = gap_source_dates[:100] + gap_source_dates[108:]
        gap = validate_leveraged_proxy(
            series(SeriesKind.DAILY_BENCHMARK_PREFEE_USD, {day: gap_source[day] for day in gap_dates}),
            series(SeriesKind.OFFICIAL_LEVERAGED_DAILY, {day: gap_source[day] for day in gap_dates}),
            mode="licensed_daily",
            expected_dates=gap_dates,
        )
        pre_start_dates = tuple(date(2014, 1, 1) + timedelta(days=index) for index in range(252))
        pre_start = validate_leveraged_proxy(
            series(SeriesKind.DAILY_BENCHMARK_PREFEE_USD, {day: daily[daily_dates[index]] for index, day in enumerate(pre_start_dates)}),
            series(SeriesKind.OFFICIAL_LEVERAGED_DAILY, {day: daily[daily_dates[index]] for index, day in enumerate(pre_start_dates)}),
            mode="licensed_daily",
            expected_dates=pre_start_dates,
        )

        self.assertEqual(full.status, "PASS_FULL_DAILY")
        self.assertEqual(full.n, 252)
        self.assertNotEqual(insufficient.status, "PASS_FULL_DAILY")
        self.assertNotEqual(incomplete.status, "PASS_FULL_DAILY")
        self.assertNotEqual(short.status, "PASS_FULL_DAILY")
        self.assertNotEqual(gap.status, "PASS_FULL_DAILY")
        self.assertNotEqual(pre_start.status, "PASS_FULL_DAILY")

        annual = {2020 + index: Decimal("0.10") + Decimal(index) / Decimal("100") for index in range(5)}
        summaries = validate_leveraged_proxy(
            OfficialSummary(annual, SummaryHorizons(Decimal("0.10"), Decimal("0.12"), None)),
            OfficialSummary({year: value + Decimal("0.03") for year, value in annual.items()}, SummaryHorizons(Decimal("0.12"), Decimal("0.14"), None)),
            mode="official_summary",
        )
        missing_horizons = validate_leveraged_proxy(
            OfficialSummary(annual, SummaryHorizons(None, None, None)),
            OfficialSummary(dict(annual), SummaryHorizons(None, None, None)),
            mode="official_summary",
        )
        bad_horizon = validate_leveraged_proxy(
            OfficialSummary(annual, SummaryHorizons(Decimal("0.10"), Decimal("0.12"), None)),
            OfficialSummary(dict(annual), SummaryHorizons(Decimal("0.1201"), Decimal("0.14"), None)),
            mode="official_summary",
        )
        self.assertEqual(summaries.status, "PASS_PARTIAL_OFFICIAL_SUMMARY")
        self.assertEqual(summaries.n, 5)
        self.assertNotEqual(missing_horizons.status, "PASS_PARTIAL_OFFICIAL_SUMMARY")
        self.assertNotEqual(bad_horizon.status, "PASS_PARTIAL_OFFICIAL_SUMMARY")

        months = tuple(month_end(2025 + index // 12, index % 12 + 1) for index in range(12))
        lwld = {day: Decimal("0.01") + Decimal(index % 3) / Decimal("1000") for index, day in enumerate(months)}
        product = validate_leveraged_proxy(
            series(SeriesKind.LWLD_INVESTABLE_FEE_ADJUSTED_EUR, lwld),
            series(SeriesKind.LWLD_OFFICIAL_NAV_EUR, lwld),
            mode="lwld",
        )
        missing = validate_leveraged_proxy(series(SeriesKind.LWLD_INVESTABLE_FEE_ADJUSTED_EUR, lwld), None, mode="lwld")
        failed = validate_leveraged_proxy(
            series(SeriesKind.LWLD_INVESTABLE_FEE_ADJUSTED_EUR, lwld),
            series(SeriesKind.LWLD_OFFICIAL_NAV_EUR, {day: -value for day, value in lwld.items()}),
            mode="lwld",
        )
        pre_inception = {date(2014, 2, 28): Decimal("0.01"), **lwld}
        old_data = validate_leveraged_proxy(
            series(SeriesKind.LWLD_INVESTABLE_FEE_ADJUSTED_EUR, pre_inception),
            series(SeriesKind.LWLD_OFFICIAL_NAV_EUR, pre_inception),
            mode="lwld",
        )
        self.assertEqual(product.status, "PASS_LWLD_VALIDATION")
        self.assertEqual(product.n, 12)
        self.assertEqual(missing.status, "FAIL_MISSING_OFFICIAL_LWLD_NAV")
        self.assertEqual(failed.status, "FAIL_LWLD_VALIDATION")
        self.assertEqual(old_data.status, "FAIL_LWLD_VALIDATION")

    def test_validation_rejects_fee_layer_or_currency_kind_mismatches(self):
        days = tuple(date(2014, 2, 1) + timedelta(days=index) for index in range(252))
        daily = {day: Decimal("0.001") + Decimal(index % 3) / Decimal("10000") for index, day in enumerate(days)}
        with self.assertRaisesRegex(ValueError, "series_kind"):
            validate_leveraged_proxy(
                series(SeriesKind.LWLD_INVESTABLE_FEE_ADJUSTED_EUR, daily),
                series(SeriesKind.OFFICIAL_LEVERAGED_DAILY, daily),
                mode="licensed_daily",
                expected_dates=days,
            )
        months = {month_end(2025 + index // 12, index % 12 + 1): Decimal("0.01") + Decimal(index % 2) / Decimal("1000") for index in range(12)}
        with self.assertRaisesRegex(ValueError, "series_kind"):
            validate_leveraged_proxy(
                series(SeriesKind.DAILY_BENCHMARK_PREFEE_USD, months),
                series(SeriesKind.LWLD_OFFICIAL_NAV_EUR, months),
                mode="lwld",
            )

    def test_full_daily_uses_the_expected_trading_calendar_and_rejects_a_missing_expected_day(self):
        expected_dates = business_days(date(2014, 2, 3), 252)
        values = {day: Decimal("0.001") + Decimal(index % 4) / Decimal("10000") for index, day in enumerate(expected_dates)}
        passed = validate_leveraged_proxy(
            series(SeriesKind.DAILY_BENCHMARK_PREFEE_USD, values),
            series(SeriesKind.OFFICIAL_LEVERAGED_DAILY, values),
            mode="licensed_daily",
            expected_dates=expected_dates,
        )
        missing_proxy = dict(values)
        del missing_proxy[expected_dates[101]]
        missing = validate_leveraged_proxy(
            series(SeriesKind.DAILY_BENCHMARK_PREFEE_USD, missing_proxy),
            series(SeriesKind.OFFICIAL_LEVERAGED_DAILY, values),
            mode="licensed_daily",
            expected_dates=expected_dates,
        )

        self.assertEqual(passed.status, "PASS_FULL_DAILY")
        self.assertEqual(passed.n, 252)
        self.assertNotEqual(missing.status, "PASS_FULL_DAILY")

    def test_official_summary_rejects_ambiguous_year_keys_and_non_decimal_values_before_normalizing(self):
        horizons = SummaryHorizons(Decimal("0.10"), Decimal("0.11"), None)
        collisions = (
            {2020: Decimal("0.10"), "2020": Decimal("0.11")},
            {"2020": Decimal("0.11"), 2020: Decimal("0.10")},
        )
        for annual_returns in collisions:
            with self.subTest(annual_returns=annual_returns), self.assertRaisesRegex(ValueError, "year"):
                OfficialSummary(annual_returns, horizons)  # type: ignore[arg-type]
        for annual_returns in (
            {True: Decimal("0.10")},
            {2020.0: Decimal("0.10")},
            {1800: Decimal("0.10")},
            {2200: Decimal("0.10")},
            {2020: "0.10"},
            {2020: 0.10},
            {2020: Decimal("NaN")},
        ):
            with self.subTest(annual_returns=annual_returns), self.assertRaises(ValueError):
                OfficialSummary(annual_returns, horizons)  # type: ignore[arg-type]
