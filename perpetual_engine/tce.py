from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable, Sequence

from perpetual_engine.io import canonical_json


_HASH = re.compile(r"[0-9a-f]{64}")
_SCORE_FIELDS = (
    "policy",
    "capex_orders",
    "earnings_revisions",
    "relative_strength",
    "valuation",
    "crowding",
    "macro",
    "portfolio_fit",
    "confidence",
)
_WEIGHTS = (
    ("policy", Decimal("0.15")),
    ("capex_orders", Decimal("0.15")),
    ("earnings_revisions", Decimal("0.20")),
    ("relative_strength", Decimal("0.15")),
    ("valuation", Decimal("0.15")),
    ("crowding", Decimal("0.10")),
    ("macro", Decimal("0.05")),
    ("portfolio_fit", Decimal("0.05")),
)
EVENT_TYPES = frozenset(
    {
        "LAW_OR_BUDGET_ENACTED",
        "CONTRACT_OR_ORDER_AWARDED",
        "GUIDANCE_OR_EARNINGS_REVISION",
        "REGULATORY_APPROVAL_OR_CANCELLATION",
        "PROGRAM_CANCELLATION",
        "CREDIT_OR_DEFAULT_EVENT",
    }
)
_AVAILABILITY_STATUSES = frozenset({"LIVE_RECEIVED", "HISTORICAL_TIMESTAMP_RECONSTRUCTED"})


def _utc(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _required_text(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _hash(value: str, name: str) -> str:
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError(f"{name} must be a lowercase SHA-256")
    return value


def _digest(payload: object) -> str:
    return hashlib.sha256(canonical_json(payload)).hexdigest()


@dataclass(frozen=True)
class TceSnapshot:
    candidate_id: str
    variant: str
    decision_at: datetime
    policy: Decimal | None
    capex_orders: Decimal | None
    earnings_revisions: Decimal | None
    relative_strength: Decimal | None
    valuation: Decimal | None
    crowding: Decimal | None
    macro: Decimal | None
    portfolio_fit: Decimal | None
    confidence: Decimal | None
    evidence_hashes: tuple[str, ...]
    config_hash: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _required_text(self.candidate_id, "candidate_id"))
        if self.variant not in {"FIXED_UNIVERSE", "THEMATIC"}:
            raise ValueError("variant must be FIXED_UNIVERSE or THEMATIC")
        object.__setattr__(self, "decision_at", _utc(self.decision_at, "decision_at"))
        for name in _SCORE_FIELDS:
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, Decimal) or not value.is_finite() or not Decimal("0") <= value <= Decimal("100")
            ):
                raise ValueError(f"{name} must be a finite Decimal within [0, 100] or None")
        if isinstance(self.evidence_hashes, str):
            raise ValueError("evidence_hashes must be an iterable of hashes")
        hashes = tuple(sorted(_hash(value, "evidence_hash") for value in self.evidence_hashes))
        if not hashes:
            raise ValueError("at least one evidence hash is required")
        object.__setattr__(self, "evidence_hashes", hashes)
        object.__setattr__(self, "config_hash", _hash(self.config_hash, "config_hash"))


@dataclass(frozen=True)
class TceScore:
    candidate_id: str
    raw_tce: Decimal
    operational_tce: Decimal
    confidence: Decimal
    vetoes: tuple[str, ...]
    eligible: bool
    decision_hash: str


def score_snapshot(snapshot: TceSnapshot) -> TceScore:
    missing = [name for name in _SCORE_FIELDS if getattr(snapshot, name) is None]
    if missing:
        raise ValueError(f"DATA_INCOMPLETE: missing {', '.join(missing)}")
    values = {name: getattr(snapshot, name) for name in _SCORE_FIELDS}
    raw = sum((values[name] * weight for name, weight in _WEIGHTS), Decimal("0"))
    vetoes: list[str] = []
    caps: list[Decimal] = []
    if values["earnings_revisions"] < Decimal("40") and values["relative_strength"] < Decimal("40"):
        vetoes.append("NARRATIVE_VETO")
        caps.append(Decimal("64"))
    if values["valuation"] < Decimal("30") and values["crowding"] < Decimal("30"):
        vetoes.append("BUBBLE_VETO")
        caps.append(Decimal("69"))
    operational = min((raw, *caps)) if caps else raw
    confidence = values["confidence"]
    eligible = operational >= Decimal("70") and confidence >= Decimal("60") and not vetoes
    payload = {
        "candidate_id": snapshot.candidate_id,
        "variant": snapshot.variant,
        "decision_at": snapshot.decision_at.isoformat(),
        "scores": {name: format(values[name], "f") for name in _SCORE_FIELDS},
        "evidence_hashes": snapshot.evidence_hashes,
        "config_hash": snapshot.config_hash,
        "raw_tce": format(raw, "f"),
        "operational_tce": format(operational, "f"),
        "vetoes": vetoes,
        "eligible": eligible,
    }
    return TceScore(snapshot.candidate_id, raw, operational, confidence, tuple(vetoes), eligible, _digest(payload))


@dataclass(frozen=True)
class TceState:
    active_candidate_id: str | None = None
    eligible_streaks: tuple[tuple[str, int], ...] = ()
    last_trade_at: datetime | None = None
    last_monthly_decision_at: datetime | None = None
    exit_streak: int = 0

    def __post_init__(self) -> None:
        if self.active_candidate_id is not None:
            object.__setattr__(self, "active_candidate_id", _required_text(self.active_candidate_id, "active_candidate_id"))
        if isinstance(self.eligible_streaks, str):
            raise ValueError("eligible_streaks must be candidate/count pairs")
        streaks = tuple(sorted(self.eligible_streaks))
        if len({candidate for candidate, _ in streaks}) != len(streaks):
            raise ValueError("eligible_streaks contains duplicate candidates")
        for candidate, count in streaks:
            _required_text(candidate, "eligible candidate")
            if not isinstance(count, int) or isinstance(count, bool) or count < 1:
                raise ValueError("eligible streak counts must be positive integers")
        object.__setattr__(self, "eligible_streaks", streaks)
        if self.last_trade_at is not None:
            object.__setattr__(self, "last_trade_at", _utc(self.last_trade_at, "last_trade_at"))
        if self.last_monthly_decision_at is not None:
            object.__setattr__(
                self,
                "last_monthly_decision_at",
                _utc(self.last_monthly_decision_at, "last_monthly_decision_at"),
            )
        if not isinstance(self.exit_streak, int) or isinstance(self.exit_streak, bool) or self.exit_streak < 0:
            raise ValueError("exit_streak must be a non-negative integer")


@dataclass(frozen=True)
class TceDecision:
    action: str
    state: TceState
    leader_candidate_id: str | None
    decision_hash: str
    execution_date: date | None = None


def _state_payload(state: TceState) -> dict[str, object]:
    return {
        "active_candidate_id": state.active_candidate_id,
        "eligible_streaks": state.eligible_streaks,
        "last_trade_at": None if state.last_trade_at is None else state.last_trade_at.isoformat(),
        "last_monthly_decision_at": (
            None if state.last_monthly_decision_at is None else state.last_monthly_decision_at.isoformat()
        ),
        "exit_streak": state.exit_streak,
    }


def _score_universe(snapshots: Iterable[TceSnapshot], decision_at: datetime | None = None) -> tuple[TceScore, ...]:
    snapshots = tuple(snapshots)
    if not snapshots:
        raise ValueError("at least one TCE snapshot is required")
    candidates = [snapshot.candidate_id for snapshot in snapshots]
    if len(set(candidates)) != len(candidates):
        raise ValueError("duplicate TCE candidates")
    if len({snapshot.variant for snapshot in snapshots}) != 1:
        raise ValueError("TCE snapshots must use the same variant")
    if decision_at is not None and any(snapshot.decision_at > decision_at for snapshot in snapshots):
        raise ValueError("TCE snapshot is not available at decision_at")
    return tuple(sorted((score_snapshot(snapshot) for snapshot in snapshots), key=lambda item: item.candidate_id))


def _decision(action: str, state: TceState, leader: str | None, scores: Sequence[TceScore], prior: TceState, execution_date: date | None = None) -> TceDecision:
    payload = {
        "action": action,
        "prior": _state_payload(prior),
        "state": _state_payload(state),
        "leader_candidate_id": leader,
        "score_hashes": tuple(score.decision_hash for score in scores),
        "execution_date": None if execution_date is None else execution_date.isoformat(),
    }
    return TceDecision(action, state, leader, _digest(payload), execution_date)


def monthly_decision(prior: TceState, snapshots: Iterable[TceSnapshot]) -> TceDecision:
    snapshots = tuple(snapshots)
    if not snapshots:
        raise ValueError("at least one TCE snapshot is required")
    decision_at = snapshots[0].decision_at
    if any(snapshot.decision_at != decision_at for snapshot in snapshots):
        raise ValueError("monthly snapshots must share decision_at")
    variant = snapshots[0].variant
    if prior.last_monthly_decision_at is not None:
        previous = prior.last_monthly_decision_at
        expected_year = previous.year + (previous.month == 12)
        expected_month = previous.month % 12 + 1
        if (decision_at.year, decision_at.month) != (expected_year, expected_month):
            raise ValueError("monthly decision must be in the following calendar month")
    scores = _score_universe(snapshots)
    prior_streaks = dict(prior.eligible_streaks)
    streaks = tuple(
        (score.candidate_id, prior_streaks.get(score.candidate_id, 0) + 1)
        for score in scores
        if score.eligible
    )
    eligible = sorted((score for score in scores if score.eligible), key=lambda item: (-item.operational_tce, item.candidate_id))
    leader = eligible[0].candidate_id if eligible else None
    if variant == "FIXED_UNIVERSE":
        if leader is None:
            action = "CASH"
            state = TceState(None, (), decision_at if prior.active_candidate_id is not None else prior.last_trade_at, decision_at)
        elif leader == prior.active_candidate_id:
            action = "HOLD"
            state = TceState(leader, streaks, prior.last_trade_at, decision_at)
        else:
            action = "ENTER" if prior.active_candidate_id is None else "ROTATE"
            state = TceState(leader, streaks, decision_at, decision_at)
        return _decision(action, state, leader, scores, prior)

    active_score = next((score for score in scores if score.candidate_id == prior.active_candidate_id), None)
    if prior.active_candidate_id is not None and active_score is None:
        raise ValueError("active thematic candidate is missing from monthly snapshots")
    exit_streak = prior.exit_streak + 1 if active_score is not None and active_score.operational_tce < Decimal("55") else 0
    if exit_streak >= 2:
        state = TceState(None, streaks, decision_at, decision_at)
        return _decision("CASH", state, leader, scores, prior)
    if leader == prior.active_candidate_id:
        action = "HOLD"
        state = TceState(prior.active_candidate_id, streaks, prior.last_trade_at, decision_at, exit_streak)
    elif leader is not None and dict(streaks)[leader] >= 2:
        action = "ENTER" if prior.active_candidate_id is None else "ROTATE"
        state = TceState(leader, streaks, decision_at, decision_at)
    else:
        action = "WATCH" if prior.active_candidate_id is None else "HOLD"
        state = TceState(prior.active_candidate_id, streaks, prior.last_trade_at, decision_at, exit_streak)
    return _decision(action, state, leader, scores, prior)


@dataclass(frozen=True)
class EventRecord:
    candidate_id: str
    event_type: str
    published_at: datetime
    available_at: datetime
    recorded_at: datetime
    source_url: str
    source_hash: str
    mapper_version: str
    availability_status: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate_id", _required_text(self.candidate_id, "candidate_id"))
        if self.event_type not in EVENT_TYPES:
            raise ValueError("event_type is not whitelisted")
        published_at = _utc(self.published_at, "published_at")
        available_at = _utc(self.available_at, "available_at")
        recorded_at = _utc(self.recorded_at, "recorded_at")
        if available_at < published_at:
            raise ValueError("available_at cannot precede published_at")
        if recorded_at < available_at:
            raise ValueError("recorded_at cannot precede available_at")
        if self.availability_status not in _AVAILABILITY_STATUSES:
            raise ValueError("invalid availability_status")
        if self.availability_status == "LIVE_RECEIVED" and available_at != recorded_at:
            raise ValueError("live events become available when recorded")
        object.__setattr__(self, "published_at", published_at)
        object.__setattr__(self, "available_at", available_at)
        object.__setattr__(self, "recorded_at", recorded_at)
        object.__setattr__(self, "source_url", _required_text(self.source_url, "source_url"))
        object.__setattr__(self, "source_hash", _hash(self.source_hash, "source_hash"))
        object.__setattr__(self, "mapper_version", _required_text(self.mapper_version, "mapper_version"))

    @property
    def event_id(self) -> str:
        return _digest(
            {
                "candidate_id": self.candidate_id,
                "event_type": self.event_type,
                "published_at": self.published_at.isoformat(),
                "available_at": self.available_at.isoformat(),
                "recorded_at": self.recorded_at.isoformat(),
                "source_url": self.source_url,
                "source_hash": self.source_hash,
                "mapper_version": self.mapper_version,
                "availability_status": self.availability_status,
            }
        )


def _execution_date(available_at: datetime, sessions: Iterable[date]) -> date:
    future = sorted({session for session in sessions if session > available_at.date()})
    if len(future) < 2:
        raise ValueError("two future trading sessions are required for one-session execution lag")
    return future[1]


def intramonth_decision(
    prior: TceState,
    event: EventRecord,
    snapshots: Iterable[TceSnapshot],
    *,
    decision_at: datetime,
    trading_sessions: Iterable[date],
) -> TceDecision:
    decision_at = _utc(decision_at, "decision_at")
    if event.available_at > decision_at:
        raise ValueError("event is not available at decision_at")
    scores = _score_universe(snapshots, decision_at)
    by_candidate = {score.candidate_id: score for score in scores}
    active = prior.active_candidate_id
    if active is None:
        return _decision("HOLD", prior, None, scores, prior)
    if active not in by_candidate:
        raise ValueError("active candidate is missing from the intramonth snapshots")

    incumbent = by_candidate[active]
    break_event = event.candidate_id == active and event.event_type in {
        "PROGRAM_CANCELLATION",
        "CREDIT_OR_DEFAULT_EVENT",
    }
    if break_event or incumbent.operational_tce < Decimal("65"):
        execution = _execution_date(event.available_at, trading_sessions)
        state = TceState(None, prior.eligible_streaks, decision_at, prior.last_monthly_decision_at)
        return _decision("CASH", state, None, scores, prior, execution)

    if prior.last_trade_at is not None and decision_at - prior.last_trade_at < timedelta(days=30):
        return _decision("HOLD", prior, active, scores, prior)

    challenger = by_candidate.get(event.candidate_id)
    confirmed = dict(prior.eligible_streaks).get(event.candidate_id, 0) >= 2
    if (
        challenger is not None
        and event.candidate_id != active
        and confirmed
        and challenger.eligible
        and challenger.operational_tce >= incumbent.operational_tce + Decimal("5")
    ):
        execution = _execution_date(event.available_at, trading_sessions)
        state = TceState(
            event.candidate_id,
            prior.eligible_streaks,
            decision_at,
            prior.last_monthly_decision_at,
        )
        return _decision("ROTATE", state, event.candidate_id, scores, prior, execution)
    return _decision("HOLD", prior, active, scores, prior)
