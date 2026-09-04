from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from math import sqrt
from typing import Iterable, Sequence

from perpetual_engine.data_sources import ir3tib_available_at, treasury_available_at
from perpetual_engine.io import canonical_json
from perpetual_engine.point_in_time import ObservationRow, asof_select, validate_rows


WORLD_PROXY_ID = "PUBLIC_DEVELOPED_WORLD_TR_PROXY_EUR"
ITALY_CPI_PROXY = "ITALY_CPI_PROXY"
HICP_ITALY_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/prc_hicp_midx?geo=IT&coicop=CP00&unit=I15"
HICP_LINKED_SOURCE_URL = "derived://italy-cpi-proxy/hicp-linked"
ZERO = Decimal("0")
ONE = Decimal("1")


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _next_month(value: date) -> date:
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1)


def _month_end(value: date) -> date:
    return _next_month(_month_start(value)) - timedelta(days=1)


def _decision_month(decision_at: datetime) -> tuple[date, date]:
    """The decision is the final instant of the outcome month immediately before allocation."""
    current = _month_start(decision_at.date())
    return current, _month_end(current)


def _finite_decimal(value: Decimal | None, name: str, *, allow_none: bool = True) -> None:
    if value is None and allow_none:
        return
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")


@dataclass(frozen=True)
class SignalSnapshot:
    """Immutable, auditable inputs for one end-of-month allocation decision."""

    decision_at: datetime
    erp: Decimal
    erp_percentile: Decimal | None = None
    erp_history_count: int = 0
    treasury_10y: Decimal | None = None
    tips: Decimal | None = None
    tips_percentile: Decimal | None = None
    tips_history_count: int = 0
    drawdown: Decimal | None = None
    momentum_1m: Decimal | None = None
    momentum_3m: Decimal | None = None
    volatility_12m: Decimal | None = None
    market_history_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_at", _utc(self.decision_at, "decision_at"))
        for name in (
            "erp",
            "erp_percentile",
            "treasury_10y",
            "tips",
            "tips_percentile",
            "drawdown",
            "momentum_1m",
            "momentum_3m",
            "volatility_12m",
        ):
            _finite_decimal(getattr(self, name), name, allow_none=name != "erp")
        for name in ("erp_history_count", "tips_history_count", "market_history_count"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True)
class AllocationState:
    """Immutable allocation ledger row; target weights are recorded only when traded."""

    state: str
    raw_beta: Decimal
    state_proposal: Decimal
    tips_cap: Decimal | None
    band_decision: str
    accepted_final_beta: Decimal
    actual_beta: Decimal
    trade_decision: str
    core_weight: Decimal
    overlay_weight: Decimal | None
    defensive_weight: Decimal | None
    overlay_trade: Decimal
    defensive_trade: Decimal
    flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.flags, str):
            raise ValueError("flags must be an iterable of strings")
        try:
            flags = tuple(self.flags)
        except TypeError as error:
            raise ValueError("flags must be an iterable of strings") from error
        if any(not isinstance(flag, str) or not flag for flag in flags):
            raise ValueError("flags must contain non-empty strings")
        object.__setattr__(self, "flags", flags)


def _latest_by_observation_date(rows: Iterable[ObservationRow], decision_at: datetime, series_id: str | None = None) -> tuple[ObservationRow, ...]:
    decision_at = _utc(decision_at, "decision_at")
    selected: dict[date, ObservationRow] = {}
    for item in validate_rows(rows):
        if series_id is not None and item.series_id != series_id:
            raise ValueError(f"expected {series_id} rows")
        if item.available_at > decision_at:
            continue
        previous = selected.get(item.observation_date)
        if previous is None or (item.available_at, item.retrieved_at, item.source_hash) > (previous.available_at, previous.retrieved_at, previous.source_hash):
            selected[item.observation_date] = item
    return tuple(selected[key] for key in sorted(selected))


def _midrank(current: Decimal, previous: Sequence[Decimal]) -> Decimal | None:
    if not previous:
        return None
    less = sum(value < current for value in previous)
    equal = sum(value == current for value in previous)
    return (Decimal(less) + Decimal("0.5") * Decimal(equal)) / Decimal(len(previous))


def _prior_month_mean(rows: Iterable[ObservationRow], decision_at: datetime, series_id: str) -> Decimal | None:
    prior_start, prior_end = _decision_month(_utc(decision_at, "decision_at"))
    values = [item.value for item in _latest_by_observation_date(rows, decision_at, series_id) if prior_start <= item.observation_date <= prior_end]
    return sum(values, ZERO) / Decimal(len(values)) if values else None


def _monthly_means(rows: Iterable[ObservationRow], decision_at: datetime, series_id: str) -> dict[date, Decimal]:
    grouped: dict[date, list[Decimal]] = {}
    for item in _latest_by_observation_date(rows, decision_at, series_id):
        grouped.setdefault(_month_end(item.observation_date), []).append(item.value)
    return {month: sum(values, ZERO) / Decimal(len(values)) for month, values in grouped.items()}


def _market_rows(rows: Iterable[ObservationRow], decision_at: datetime) -> dict[date, Decimal]:
    _, prior_end = _decision_month(_utc(decision_at, "decision_at"))
    selected = _latest_by_observation_date(rows, decision_at, WORLD_PROXY_ID)
    result: dict[date, Decimal] = {}
    for item in selected:
        month = _month_end(item.observation_date)
        if month <= prior_end:
            result[month] = item.value
    return result


def _last_calendar_values(values: dict[date, Decimal], final_month: date, count: int) -> list[Decimal] | None:
    months: list[date] = []
    current = final_month
    for _ in range(count):
        if current not in values:
            return None
        months.append(current)
        current = _month_end(_month_start(current) - timedelta(days=1))
    return list(reversed([values[month] for month in months]))


def compute_signals(
    *,
    decision_at: datetime,
    erp_rows: Iterable[ObservationRow],
    treasury_rows: Iterable[ObservationRow],
    tips_rows: Iterable[ObservationRow],
    market_rows: Iterable[ObservationRow],
) -> SignalSnapshot:
    """Compute only values whose normalized availability is no later than decision_at."""
    decision_at = _utc(decision_at, "decision_at")
    erp_all = _latest_by_observation_date(erp_rows, decision_at)
    if any(item.series_id != "ERP" for item in erp_all):
        raise ValueError("expected ERP rows")
    current_erp = asof_select(erp_all, decision_at)
    if current_erp is None:
        raise ValueError("an admissible ERP observation is required")
    erp_history = [item.value for item in erp_all if item.observation_date < current_erp.observation_date]

    treasury = _prior_month_mean(treasury_rows, decision_at, "DGS10")
    tips_means = _monthly_means(tips_rows, decision_at, "DFII10")
    _, prior_end = _decision_month(decision_at)
    tips = tips_means.get(prior_end)
    tips_history = [value for month, value in sorted(tips_means.items()) if month < prior_end]
    tips_percentile = _midrank(tips, tips_history) if tips is not None and len(tips_history) >= 36 else None

    market = _market_rows(market_rows, decision_at)
    momentum_1m = market.get(prior_end)
    last_three = _last_calendar_values(market, prior_end, 3)
    momentum_3m = None if last_three is None else (ONE + last_three[0]) * (ONE + last_three[1]) * (ONE + last_three[2]) - ONE
    last_twelve = _last_calendar_values(market, prior_end, 12)
    volatility = None
    if last_twelve is not None:
        mean = sum(last_twelve, ZERO) / Decimal(12)
        variance = sum((value - mean) ** 2 for value in last_twelve) / Decimal(11)
        volatility = variance.sqrt() * Decimal(str(sqrt(12)))
    drawdown = None
    if momentum_1m is not None:
        level = peak = ONE
        for month in sorted(market):
            level *= ONE + market[month]
            peak = max(peak, level)
        drawdown = level / peak - ONE

    return SignalSnapshot(
        decision_at=decision_at,
        erp=current_erp.value,
        erp_percentile=_midrank(current_erp.value, erp_history),
        erp_history_count=len(erp_history),
        treasury_10y=treasury,
        tips=tips,
        tips_percentile=tips_percentile,
        tips_history_count=len(tips_history),
        drawdown=drawdown,
        momentum_1m=momentum_1m,
        momentum_3m=momentum_3m,
        volatility_12m=volatility,
        market_history_count=len(market),
    )


def _raw_beta(erp: Decimal) -> Decimal:
    if erp < Decimal("0.03"):
        return Decimal("0.60")
    if erp < Decimal("0.04"):
        return Decimal("0.95")
    return ONE


def _crisis(signals: SignalSnapshot) -> bool:
    return signals.market_history_count >= 12 and all(
        value is not None
        for value in (signals.drawdown, signals.momentum_3m, signals.volatility_12m)
    ) and signals.drawdown <= Decimal("-0.05") and signals.momentum_3m < ZERO and signals.volatility_12m > Decimal("0.24")


def _recovery(signals: SignalSnapshot) -> bool:
    return signals.market_history_count >= 12 and all(
        value is not None
        for value in (signals.drawdown, signals.momentum_1m, signals.momentum_3m, signals.volatility_12m)
    ) and signals.erp >= Decimal("0.04") and signals.momentum_1m > ZERO and signals.momentum_3m > Decimal("0.01") and signals.volatility_12m < Decimal("0.32") and signals.drawdown < ZERO


def _tips_cap(signals: SignalSnapshot) -> Decimal | None:
    if signals.tips_percentile is None or signals.tips_history_count < 36:
        return None
    caps: list[Decimal] = []
    if signals.tips_percentile > Decimal("0.90"):
        caps.append(ONE)
    if signals.tips_percentile > Decimal("0.95") and signals.erp_percentile is not None and signals.erp_percentile < Decimal("0.60"):
        caps.append(Decimal("0.90"))
    return min(caps) if caps else None


def _pretrade_weights(
    prior: AllocationState | None,
    core_weight: Decimal,
    overlay_weight: Decimal | None,
    defensive_weight: Decimal | None,
) -> tuple[Decimal, Decimal]:
    supplied = overlay_weight is not None or defensive_weight is not None
    if supplied and (overlay_weight is None or defensive_weight is None):
        raise ValueError("full pretrade weights require both overlay and defensive values")
    if not supplied:
        if prior is None:
            return ZERO, ZERO
        if core_weight != prior.core_weight:
            raise ValueError("full pretrade weights are required when core_weight_pretrade changed")
        overlay_weight, defensive_weight = prior.overlay_weight, prior.defensive_weight
    _finite_decimal(overlay_weight, "overlay_weight_pretrade", allow_none=False)
    _finite_decimal(defensive_weight, "defensive_weight_pretrade", allow_none=False)
    if overlay_weight < ZERO or defensive_weight < ZERO:
        raise ValueError("pretrade weights must be non-negative")
    if prior is not None or supplied:
        if abs(core_weight + overlay_weight + defensive_weight - ONE) > Decimal("1e-12"):
            raise ValueError("pretrade weights must sum to one within 1e-12")
    return overlay_weight, defensive_weight


def next_allocation(
    prior: AllocationState | None,
    signals: SignalSnapshot,
    *,
    core_weight_pretrade: Decimal = Decimal("0.60"),
    overlay_weight_pretrade: Decimal | None = None,
    defensive_weight_pretrade: Decimal | None = None,
) -> AllocationState:
    """Apply the approved total precedence once and emit a deterministic ledger row."""
    _finite_decimal(core_weight_pretrade, "core_weight_pretrade", allow_none=False)
    if not ZERO <= core_weight_pretrade <= ONE:
        raise ValueError("core_weight_pretrade must be within [0, 1]")
    pretrade_overlay, pretrade_defensive = _pretrade_weights(
        prior, core_weight_pretrade, overlay_weight_pretrade, defensive_weight_pretrade
    )
    raw = _raw_beta(signals.erp)
    if _crisis(signals):
        state, proposal, band = "CRISIS", Decimal("0.80"), "BYPASS_CRISIS"
    elif prior is not None and prior.state in {"CRISIS", "RECOVERY"} and _recovery(signals):
        state, proposal, band = "RECOVERY", min(prior.accepted_final_beta + Decimal("0.10"), Decimal("1.20")), "BYPASS_RECOVERY"
    else:
        state, proposal, band = "NORMAL", raw, ""

    cap = _tips_cap(signals)
    capped = min(proposal, cap) if cap is not None else proposal
    cap_reduced = capped < proposal
    flags: list[str] = ["TIPS_CAP_REDUCTION"] if cap_reduced else []
    if prior is None:
        band = "BYPASS_INCEPTION" if state == "NORMAL" else band
        accepted = capped
    elif state != "NORMAL":
        accepted = capped
    elif cap_reduced:
        band, accepted = "BYPASS_TIPS_CAP", capped
    elif abs(proposal - prior.accepted_final_beta) < Decimal("0.15"):
        band, accepted = "HOLD", prior.accepted_final_beta
    else:
        band, accepted = "EXECUTE", capped
    if accepted > Decimal("1.20"):
        raise ValueError("final requested beta cannot exceed 1.20")

    trade = prior is None or accepted != prior.accepted_final_beta
    if not trade:
        return AllocationState(
            state, raw, proposal, cap, band, accepted, core_weight_pretrade + Decimal("2") * pretrade_overlay, "HOLD", core_weight_pretrade,
            pretrade_overlay, pretrade_defensive, ZERO, ZERO, tuple(flags),
        )

    overlay = max((accepted - core_weight_pretrade) / Decimal("2"), ZERO)
    defensive = ONE - core_weight_pretrade - overlay
    if defensive < ZERO:
        overlay, defensive = ONE - core_weight_pretrade, ZERO
        flags.append("NO_BORROWING_CAP")
    actual = core_weight_pretrade + Decimal("2") * overlay
    if accepted < core_weight_pretrade:
        actual = core_weight_pretrade
        flags.append("CORE_FLOOR_BINDING")
    if core_weight_pretrade + overlay + defensive != ONE:
        raise AssertionError("allocation weights must sum to one")
    return AllocationState(
        state, raw, proposal, cap, band, accepted, actual, "TRADE", core_weight_pretrade,
        overlay, defensive, overlay - pretrade_overlay, defensive - pretrade_defensive, tuple(flags),
    )


def defensive_monthly_return(
    rate_rows: Iterable[ObservationRow],
    allocation_month: date,
    decision_at: datetime,
    *,
    max_staleness_days: int,
) -> Decimal:
    """Price the allocation-month defensive outcome from the latest admissible annual decimal rate."""
    if not isinstance(max_staleness_days, int) or isinstance(max_staleness_days, bool) or max_staleness_days < 0:
        raise ValueError("max_staleness_days must be a non-negative integer")
    decision_at = _utc(decision_at, "decision_at")
    if not isinstance(allocation_month, date) or isinstance(allocation_month, datetime):
        raise ValueError("allocation_month must be a date")
    expected_month = _next_month(_month_start(decision_at.date()))
    if _month_start(allocation_month) != expected_month:
        raise ValueError("allocation_month must be the calendar month immediately following decision_at")
    selected = asof_select(_latest_by_observation_date(rate_rows, decision_at, "IR3TIB01ITM156N"), decision_at)
    if selected is None:
        raise ValueError("no defensive rate is available at decision_at")
    if selected.value <= -ONE:
        raise ValueError("defensive rate must be greater than -100%")
    if (decision_at.date() - selected.observation_date).days > max_staleness_days:
        raise ValueError("defensive rate is stale")
    days = Decimal((_month_end(allocation_month) - _month_start(allocation_month)).days + 1)
    with localcontext() as context:
        context.prec = 40
        return (ONE + selected.value) ** (days / Decimal("365")) - ONE


def splice_italy_cpi(oecd_rows: Iterable[ObservationRow], hicp_rows: Iterable[ObservationRow]) -> tuple[ObservationRow, ...]:
    """Splice OECD through November 2023 to linked Eurostat HICP from December."""
    november = date(2023, 11, 30)
    oecd = _latest_by_observation_date(oecd_rows, datetime.max.replace(tzinfo=timezone.utc))
    hicp = _latest_by_observation_date(hicp_rows, datetime.max.replace(tzinfo=timezone.utc))
    if any(item.series_id != "ITACPALTT01IXNBM" for item in oecd):
        raise ValueError("OECD CPI rows must use ITACPALTT01IXNBM")
    if any(item.series_id != "HICP" for item in hicp):
        raise ValueError("HICP rows must use HICP")
    oecd_by_month = _unique_month_rows(oecd, "OECD CPI")
    hicp_by_month = _unique_month_rows(hicp, "HICP")
    if november not in oecd_by_month or november not in hicp_by_month:
        raise ValueError("both November 2023 OECD and HICP link observations are mandatory")
    if hicp_by_month[november].value <= ZERO:
        raise ValueError("November 2023 HICP link value must be positive")
    if any(month > november for month in oecd_by_month):
        raise ValueError("OECD CPI must not continue past November 2023")
    link = oecd_by_month[november].value / hicp_by_month[november].value
    output: list[ObservationRow] = []
    for month, item in sorted(oecd_by_month.items()):
        if month <= november:
            output.append(ObservationRow(ITALY_CPI_PROXY, month, month, item.available_at, item.value, item.unit, item.source_url, item.retrieved_at, item.source_hash, item.vintage_status, tuple((*item.quality_flags, "OECD_CPI"))))
    for month, item in sorted(hicp_by_month.items()):
        if month > november:
            link_row = oecd_by_month[november]
            output.append(
                ObservationRow(
                    ITALY_CPI_PROXY,
                    month,
                    month,
                    max(item.available_at, link_row.available_at),
                    item.value * link,
                    item.unit,
                    HICP_LINKED_SOURCE_URL,
                    max(item.retrieved_at, link_row.retrieved_at),
                    _linked_cpi_hash(item, link_row),
                    item.vintage_status,
                    tuple((*item.quality_flags, "HICP_LINKED", "OECD_NOV_LINK")),
                )
            )
    _require_continuous_months(output)
    return validate_rows(output)


def _unique_month_rows(rows: Iterable[ObservationRow], name: str) -> dict[date, ObservationRow]:
    result: dict[date, ObservationRow] = {}
    for item in rows:
        month = _month_end(item.observation_date)
        if month in result:
            raise ValueError(f"{name} has a duplicate monthly observation")
        result[month] = item
    return result


def _linked_cpi_hash(hicp_row: ObservationRow, oecd_link_row: ObservationRow) -> str:
    return hashlib.sha256(
        canonical_json(
            [
                {
                    "available_at": hicp_row.available_at.isoformat(),
                    "observation_date": hicp_row.observation_date.isoformat(),
                    "retrieved_at": hicp_row.retrieved_at.isoformat(),
                    "source_hash": hicp_row.source_hash,
                    "source_url": hicp_row.source_url,
                },
                {
                    "available_at": oecd_link_row.available_at.isoformat(),
                    "observation_date": oecd_link_row.observation_date.isoformat(),
                    "retrieved_at": oecd_link_row.retrieved_at.isoformat(),
                    "source_hash": oecd_link_row.source_hash,
                    "source_url": oecd_link_row.source_url,
                },
            ]
        )
    ).hexdigest()


def _require_continuous_months(rows: Sequence[ObservationRow]) -> None:
    months = [item.observation_date for item in rows]
    if len(months) != len(set(months)):
        raise ValueError("Italy CPI splice has a duplicate monthly output")
    for previous, current in zip(months, months[1:]):
        if _next_month(_month_start(previous)) != _month_start(current):
            raise ValueError("Italy CPI splice has an internal monthly gap")
