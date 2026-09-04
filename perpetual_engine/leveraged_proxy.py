from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping, Sequence


ZERO = Decimal("0")
ONE = Decimal("1")
_TER = Decimal("0.006")
_MONTHLY_RESIDUAL = Decimal("0.011")
_SERIES_BY_DAY = (
    (date.min, date(1985, 12, 31), "DFF"),
    (date(1986, 1, 1), date(2021, 8, 31), "USD1MTD156N"),
    (date(2021, 9, 1), date.max, "SOFR"),
)


class SeriesKind(StrEnum):
    DAILY_BENCHMARK_PREFEE_USD = "DAILY_BENCHMARK_PREFEE_USD"
    OFFICIAL_LEVERAGED_DAILY = "OFFICIAL_LEVERAGED_DAILY"
    LWLD_INVESTABLE_FEE_ADJUSTED_EUR = "LWLD_INVESTABLE_FEE_ADJUSTED_EUR"
    LWLD_OFFICIAL_NAV_EUR = "LWLD_OFFICIAL_NAV_EUR"


def _decimal(value: Decimal | int | float | str, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{field} must be finite") from error
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


def _day(value: date, field: str) -> date:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError(f"{field} must be a date")
    return value


@dataclass(frozen=True)
class FundingRate:
    observation_date: date
    series_id: str
    rate_pct: Decimal

    def __post_init__(self) -> None:
        _day(self.observation_date, "observation_date")
        if not isinstance(self.series_id, str) or not self.series_id:
            raise ValueError("series_id is required")
        object.__setattr__(self, "rate_pct", _decimal(self.rate_pct, "rate_pct"))


@dataclass(frozen=True, init=False)
class ReturnSeries:
    """Immutable, labelled validation returns that cannot cross fee/currency layers."""

    series_kind: SeriesKind
    _rows: tuple[tuple[date, Decimal], ...]

    def __init__(self, series_kind: SeriesKind, returns: Mapping[date, Decimal | int | float | str]) -> None:
        if not isinstance(series_kind, SeriesKind):
            raise ValueError("series_kind must be a SeriesKind")
        rows = tuple(sorted(((_day(day, "return date"), _decimal(value, "return")) for day, value in returns.items()), key=lambda row: row[0]))
        if not rows or any(value < -ONE for _, value in rows):
            raise ValueError("returns must be non-empty and no less than -100%")
        object.__setattr__(self, "series_kind", series_kind)
        object.__setattr__(self, "_rows", rows)

    @property
    def returns(self) -> Mapping[date, Decimal]:
        return MappingProxyType(dict(self._rows))


@dataclass(frozen=True)
class SummaryHorizons:
    three_year: Decimal | None
    five_year: Decimal | None
    ten_year: Decimal | None

    def __post_init__(self) -> None:
        for field in ("three_year", "five_year", "ten_year"):
            value = getattr(self, field)
            if value is not None:
                object.__setattr__(self, field, _decimal(value, field))


@dataclass(frozen=True, init=False)
class OfficialSummary:
    """Immutable annual and 3/5/10-year official-summary evidence."""

    _annual_rows: tuple[tuple[int, Decimal], ...]
    horizons: SummaryHorizons

    def __init__(self, annual_returns: Mapping[int, Decimal | int | float | str], horizons: SummaryHorizons) -> None:
        if not isinstance(horizons, SummaryHorizons):
            raise ValueError("horizons must be SummaryHorizons")
        if not isinstance(annual_returns, Mapping):
            raise ValueError("annual_returns must be a mapping")
        raw_rows = tuple(annual_returns.items())
        if len({year for year, _ in raw_rows}) != len(raw_rows):
            raise ValueError("annual return years must be unique")
        if any(isinstance(year, bool) or not isinstance(year, int) or not 1900 <= year <= 2100 for year, _ in raw_rows):
            raise ValueError("annual return year must be a plausible non-boolean integer")
        if any(not isinstance(value, Decimal) or not value.is_finite() or value < -ONE for _, value in raw_rows):
            raise ValueError("annual return must be a finite Decimal no less than -100%")
        rows = tuple(sorted(raw_rows, key=lambda row: row[0]))
        object.__setattr__(self, "_annual_rows", rows)
        object.__setattr__(self, "horizons", horizons)

    @property
    def annual_returns(self) -> Mapping[int, Decimal]:
        return MappingProxyType(dict(self._annual_rows))


@dataclass(frozen=True, eq=False)
class LeveragedReturn:
    """An immutable return ledger whose numeric value is ``total_return``."""

    underlying: Decimal
    funding: Decimal
    residual_drag: Decimal
    fx: Decimal
    etf_fee: Decimal
    total_return: Decimal
    return_usd: Decimal
    flags: tuple[str, ...] = ()
    wiped_out: bool = False

    def __float__(self) -> float:
        return float(self.total_return)

    def __add__(self, other: object) -> Decimal:
        return self.total_return + _decimal(other, "return operand")  # type: ignore[arg-type]

    def __radd__(self, other: object) -> Decimal:
        return _decimal(other, "return operand") + self.total_return  # type: ignore[arg-type]

    def __sub__(self, other: object) -> Decimal:
        return self.total_return - _decimal(other, "return operand")  # type: ignore[arg-type]

    def __rsub__(self, other: object) -> Decimal:
        return _decimal(other, "return operand") - self.total_return  # type: ignore[arg-type]

    def __lt__(self, other: object) -> bool:
        return self.total_return < _decimal(other, "return operand")  # type: ignore[arg-type]

    def __le__(self, other: object) -> bool:
        return self.total_return <= _decimal(other, "return operand")  # type: ignore[arg-type]

    def __eq__(self, other: object) -> bool:
        if isinstance(other, LeveragedReturn):
            return self.__dict__ == other.__dict__
        try:
            return self.total_return == _decimal(other, "return operand")  # type: ignore[arg-type]
        except ValueError:
            return False


@dataclass(frozen=True)
class LeveragedValidation:
    mode: str
    n: int
    correlation: float
    beta: float
    tracking_error: float
    cagr_gap: float
    status: str


def _required_series(day: date) -> str:
    for start, end, series_id in _SERIES_BY_DAY:
        if start <= day <= end:
            return series_id
    raise ValueError("funding day is outside the supported range")


def _funding_rows(rows: Sequence[FundingRate]) -> tuple[FundingRate, ...]:
    normalized = tuple(rows)
    if len({row.observation_date for row in normalized}) != len(normalized):
        raise ValueError("funding observations must have unique dates")
    if any(row.series_id != _required_series(row.observation_date) for row in normalized):
        raise ValueError("funding source does not match its observation-date regime")
    return tuple(sorted(normalized, key=lambda row: row.observation_date))


def funding_rate_for_day(day: date, rows: Sequence[FundingRate]) -> FundingRate:
    """Return the allowed latest rate, carrying at most seven calendar days."""
    day = _day(day, "day")
    expected = _required_series(day)
    candidates = [row for row in _funding_rows(rows) if row.observation_date <= day and row.series_id == expected]
    if not candidates:
        raise ValueError(f"missing {expected} funding rate for {day.isoformat()}")
    selected = candidates[-1]
    if (day - selected.observation_date).days > 7:
        raise ValueError("funding carry exceeds seven calendar days")
    return selected


def apply_fx_once(return_usd: Decimal | int | float | str, fx_factor: Decimal | int | float | str) -> Decimal:
    return_usd = _decimal(return_usd, "return_usd")
    fx_factor = _decimal(fx_factor, "fx_factor")
    if fx_factor <= ZERO:
        raise ValueError("fx_factor must be positive")
    return (ONE + return_usd) * fx_factor - ONE


def _calendar_days(days: int) -> int:
    if isinstance(days, bool) or not isinstance(days, int) or days < 1:
        raise ValueError("calendar_days must be a positive integer")
    return days


def _fee(days: int) -> Decimal:
    days = _calendar_days(days)
    return (ONE - _TER) ** (Decimal(days) / Decimal("365")) - ONE


def leveraged_daily_return(
    underlying_return: Decimal | int | float | str,
    funding_rate_pct: Decimal | int | float | str,
    calendar_days: int,
    fx_factor: Decimal | int | float | str = ONE,
    *,
    investable: bool = False,
) -> LeveragedReturn:
    """Daily-reset 2x USD proxy with optional post-FX LWLD fee layer."""
    calendar_days = _calendar_days(calendar_days)
    underlying = Decimal("2") * _decimal(underlying_return, "underlying_return")
    funding = -_decimal(funding_rate_pct, "funding_rate_pct") / Decimal("100") * Decimal(calendar_days) / Decimal("360")
    return_usd = underlying + funding
    before_fee = apply_fx_once(return_usd, fx_factor)
    fx = before_fee - return_usd
    etf_fee = _fee(calendar_days) if investable else ZERO
    return LeveragedReturn(
        underlying=underlying,
        funding=funding,
        residual_drag=ZERO,
        fx=fx,
        etf_fee=etf_fee,
        total_return=(ONE + before_fee) * (ONE + etf_fee) - ONE,
        return_usd=return_usd,
    )


def _days_in_month(month: date) -> tuple[date, ...]:
    month = _day(month, "month").replace(day=1)
    following = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    return tuple(month + timedelta(days=index) for index in range((following - month).days))


def leveraged_monthly_return(
    underlying_return: Decimal | int | float | str,
    funding_rows: Sequence[FundingRate],
    month: date,
    fx_factor: Decimal | int | float | str = ONE,
    *,
    residual_drag: Decimal | int | float | str = _MONTHLY_RESIDUAL,
    investable: bool = False,
    wiped_out: bool = False,
    reset: bool = False,
) -> LeveragedReturn:
    """Long-history monthly approximation; residual drag exists only here."""
    residual = _decimal(residual_drag, "residual_drag")
    if residual not in {Decimal("0.006"), _MONTHLY_RESIDUAL, Decimal("0.015")}:
        raise ValueError("residual_drag must be 0.006, 0.011, or 0.015")
    reset_flags = ("CAPITAL_RESET",) if reset else ()
    if reset:
        wiped_out = False
    underlying = Decimal("2") * _decimal(underlying_return, "underlying_return")
    funding = -sum(
        (funding_rate_for_day(day, funding_rows).rate_pct / Decimal("100") / Decimal("360") for day in _days_in_month(month)),
        ZERO,
    )
    gross = underlying + funding
    if wiped_out or gross <= -ONE:
        return LeveragedReturn(
            underlying,
            funding,
            ZERO,
            ZERO,
            ZERO,
            -ONE,
            -ONE,
            (*reset_flags, "LEVERAGED_SLEEVE_WIPEOUT"),
            True,
        )
    residual_effect = (ONE - residual) ** (ONE / Decimal("12")) - ONE
    return_usd = (ONE + gross) * (ONE + residual_effect) - ONE
    before_fee = apply_fx_once(return_usd, fx_factor)
    fx = before_fee - return_usd
    etf_fee = (ONE - _TER) ** (ONE / Decimal("12")) - ONE if investable else ZERO
    return LeveragedReturn(
        underlying=underlying,
        funding=funding,
        residual_drag=residual_effect,
        fx=fx,
        etf_fee=etf_fee,
        total_return=(ONE + before_fee) * (ONE + etf_fee) - ONE,
        return_usd=return_usd,
        flags=reset_flags,
    )


def _series_returns(value: ReturnSeries, expected_kind: SeriesKind) -> dict[date, float]:
    if not isinstance(value, ReturnSeries):
        raise ValueError("validation inputs must be ReturnSeries")
    if value.series_kind is not expected_kind:
        raise ValueError(f"series_kind must be {expected_kind}")
    return {day: float(result) for day, result in value.returns.items()}


def _empty(mode: str, status: str) -> LeveragedValidation:
    return LeveragedValidation(mode, 0, math.nan, math.nan, math.nan, math.nan, status)


def _metrics(proxy: Sequence[float], official: Sequence[float], periods_per_year: int) -> tuple[float, float, float, float]:
    mean_proxy, mean_official = sum(proxy) / len(proxy), sum(official) / len(official)
    variance_proxy = sum((value - mean_proxy) ** 2 for value in proxy) / (len(proxy) - 1)
    variance_official = sum((value - mean_official) ** 2 for value in official) / (len(official) - 1)
    if variance_proxy == 0 or variance_official == 0:
        raise ValueError("validation sample has zero variance")
    covariance = sum((left - mean_proxy) * (right - mean_official) for left, right in zip(proxy, official)) / (len(proxy) - 1)
    differences = [left - right for left, right in zip(proxy, official)]
    tracking_error = math.sqrt(sum((value - sum(differences) / len(differences)) ** 2 for value in differences) / (len(differences) - 1)) * math.sqrt(periods_per_year)
    cagr_proxy = math.prod(1 + value for value in proxy) ** (periods_per_year / len(proxy)) - 1
    cagr_official = math.prod(1 + value for value in official) ** (periods_per_year / len(official)) - 1
    return covariance / math.sqrt(variance_proxy * variance_official), covariance / variance_official, tracking_error, abs(cagr_proxy - cagr_official)


def _contiguous_months(days: Sequence[date]) -> bool:
    for previous, current in zip(days, days[1:]):
        following = date(previous.year + (previous.month == 12), previous.month % 12 + 1, 1)
        next_following = date(following.year + (following.month == 12), following.month % 12 + 1, 1)
        if current != next_following - timedelta(days=1):
            return False
    return True


def _valid_daily_observation_calendar(days: Sequence[date]) -> bool:
    try:
        dates = tuple(days)
    except TypeError:
        return False
    if (
        len(dates) < 252
        or any(not isinstance(day, date) or isinstance(day, datetime) for day in dates)
        or tuple(sorted(dates)) != dates
        or len(set(dates)) != len(dates)
        or dates[0] < date(2014, 2, 1)
    ):
        return False
    return all(0 < (current - previous).days <= 7 for previous, current in zip(dates, dates[1:]))


def _summary_validation(proxy: OfficialSummary, official: OfficialSummary) -> LeveragedValidation:
    if not isinstance(proxy, OfficialSummary) or not isinstance(official, OfficialSummary):
        raise ValueError("official_summary inputs must be OfficialSummary")
    proxy_annual, official_annual = proxy.annual_returns, official.annual_returns
    years = sorted(set(proxy_annual) & set(official_annual))
    if len(years) < 5 or any(abs(proxy_annual[year] - official_annual[year]) > Decimal("0.03") for year in years):
        return _empty("official_summary", "FAIL_OFFICIAL_SUMMARY_VALIDATION")
    for field in ("three_year", "five_year", "ten_year"):
        proxy_value = getattr(proxy.horizons, field)
        official_value = getattr(official.horizons, field)
        if field in {"three_year", "five_year"} and (proxy_value is None or official_value is None):
            return _empty("official_summary", "FAIL_OFFICIAL_SUMMARY_VALIDATION")
        if (proxy_value is None) != (official_value is None):
            return _empty("official_summary", "FAIL_OFFICIAL_SUMMARY_VALIDATION")
        if proxy_value is not None and abs(proxy_value - official_value) > Decimal("0.02"):
            return _empty("official_summary", "FAIL_OFFICIAL_SUMMARY_VALIDATION")
    return LeveragedValidation("official_summary", len(years), math.nan, math.nan, math.nan, math.nan, "PASS_PARTIAL_OFFICIAL_SUMMARY")


def validate_leveraged_proxy(
    proxy_returns: ReturnSeries | OfficialSummary,
    official_returns: ReturnSeries | OfficialSummary | None,
    *,
    mode: str,
    expected_dates: Sequence[date] | None = None,
) -> LeveragedValidation:
    """Validate one evidence layer only; callers keep benchmark and product results separate."""
    normalized_mode = {"daily": "licensed_daily", "official": "official_summary", "product": "lwld"}.get(mode, mode)
    if normalized_mode == "official_summary":
        if official_returns is None:
            return _empty(normalized_mode, "FAIL_OFFICIAL_SUMMARY_VALIDATION")
        return _summary_validation(proxy_returns, official_returns)  # type: ignore[arg-type]

    if normalized_mode not in {"licensed_daily", "lwld"}:
        raise ValueError("mode must be licensed_daily, official_summary, or lwld")
    if official_returns is None:
        if normalized_mode == "lwld":
            _series_returns(proxy_returns, SeriesKind.LWLD_INVESTABLE_FEE_ADJUSTED_EUR)  # type: ignore[arg-type]
        else:
            _series_returns(proxy_returns, SeriesKind.DAILY_BENCHMARK_PREFEE_USD)  # type: ignore[arg-type]
        return _empty(normalized_mode, "FAIL_MISSING_OFFICIAL_LWLD_NAV" if normalized_mode == "lwld" else "FAIL_INSUFFICIENT_DAILY_SAMPLE")
    if normalized_mode == "licensed_daily":
        proxy = _series_returns(proxy_returns, SeriesKind.DAILY_BENCHMARK_PREFEE_USD)  # type: ignore[arg-type]
        official = _series_returns(official_returns, SeriesKind.OFFICIAL_LEVERAGED_DAILY)  # type: ignore[arg-type]
    else:
        proxy = _series_returns(proxy_returns, SeriesKind.LWLD_INVESTABLE_FEE_ADJUSTED_EUR)  # type: ignore[arg-type]
        official = _series_returns(official_returns, SeriesKind.LWLD_OFFICIAL_NAV_EUR)  # type: ignore[arg-type]
    common = sorted(set(proxy) & set(official))
    if normalized_mode == "licensed_daily":
        if (
            expected_dates is None
            or tuple(common) != tuple(expected_dates)
            or set(proxy) != set(official)
            or not _valid_daily_observation_calendar(expected_dates)
        ):
            return _empty(normalized_mode, "FAIL_INSUFFICIENT_DAILY_SAMPLE")
        periods_per_year, fail_status = 252, "FAIL_DAILY_VALIDATION"
    else:
        if (
            len(common) < 12
            or common[0] < date(2025, 1, 1)
            or not _contiguous_months(common)
            or set(proxy) != set(official)
        ):
            return _empty(normalized_mode, "FAIL_LWLD_VALIDATION")
        periods_per_year, fail_status = 12, "FAIL_LWLD_VALIDATION"
    values_proxy, values_official = [proxy[day] for day in common], [official[day] for day in common]
    try:
        correlation, beta, tracking_error, cagr_gap = _metrics(values_proxy, values_official, periods_per_year)
    except ValueError:
        return _empty(normalized_mode, fail_status)
    if normalized_mode == "licensed_daily":
        passed = correlation >= 0.995 and 0.98 <= beta <= 1.02 and tracking_error <= 0.02
        status = "PASS_FULL_DAILY" if passed else fail_status
    else:
        passed = correlation >= 0.95 and 0.85 <= beta <= 1.15 and tracking_error <= 0.08 and cagr_gap <= 0.05
        status = "PASS_LWLD_VALIDATION" if passed else fail_status
    return LeveragedValidation(normalized_mode, len(common), correlation, beta, tracking_error, cagr_gap, status)
