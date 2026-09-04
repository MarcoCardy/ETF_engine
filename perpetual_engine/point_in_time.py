from __future__ import annotations

import re
import csv
from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Iterable


_HASH = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class ObservationRow:
    series_id: str
    observation_date: date
    period_end: date
    available_at: datetime
    value: Decimal
    unit: str
    source_url: str
    retrieved_at: datetime
    source_hash: str
    vintage_status: str = "CURRENT_VINTAGE_RESEARCH"
    quality_flags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.quality_flags, str):
            raise ValueError("quality_flags must be an iterable of strings")
        try:
            quality_flags = tuple(self.quality_flags)
        except TypeError as error:
            raise ValueError("quality_flags must be an iterable of strings") from error
        if any(not isinstance(flag, str) or not flag for flag in quality_flags):
            raise ValueError("quality_flags must contain non-empty strings")
        object.__setattr__(self, "quality_flags", quality_flags)


def _utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(timezone.utc)


def validate_rows(rows: Iterable[ObservationRow]) -> tuple[ObservationRow, ...]:
    validated: list[ObservationRow] = []
    identities: set[tuple[str, date, str]] = set()
    for row in rows:
        if not isinstance(row, ObservationRow):
            raise ValueError("rows must be ObservationRow instances")
        if not isinstance(row.observation_date, date) or isinstance(row.observation_date, datetime):
            raise ValueError("observation_date must be a date")
        if not isinstance(row.period_end, date) or isinstance(row.period_end, datetime):
            raise ValueError("period_end must be a date")
        available_at = _utc(row.available_at, "available_at")
        retrieved_at = _utc(row.retrieved_at, "retrieved_at")
        if not isinstance(row.value, Decimal) or not row.value.is_finite():
            raise ValueError("value must be a finite Decimal")
        if not all(isinstance(value, str) and value.strip() for value in (row.series_id, row.unit, row.source_url)):
            raise ValueError("series_id, unit, and source_url are required")
        if not isinstance(row.source_hash, str) or not _HASH.fullmatch(row.source_hash):
            raise ValueError("source_hash must be a 64-character lowercase hexadecimal SHA-256")
        if row.period_end < row.observation_date:
            raise ValueError("period_end cannot precede observation_date")
        if retrieved_at < available_at:
            raise ValueError("retrieved_at cannot precede available_at")
        identity = (row.series_id, row.observation_date, row.source_hash)
        if identity in identities:
            raise ValueError("duplicate series_id, observation_date, and source_hash")
        identities.add(identity)
        validated.append(replace(row, available_at=available_at, retrieved_at=retrieved_at))
    return tuple(validated)


def asof_select(rows: Iterable[ObservationRow], decision_at: datetime) -> ObservationRow | None:
    decision_at = _utc(decision_at, "decision_at")
    eligible = [row for row in validate_rows(rows) if row.available_at <= decision_at]
    if not eligible:
        return None
    return max(eligible, key=lambda row: (row.available_at, row.observation_date, row.retrieved_at, row.source_hash))


_CSV_COLUMNS = tuple(ObservationRow.__dataclass_fields__)


def write_observation_csv(rows: Iterable[ObservationRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        for row in sorted(
            validate_rows(rows),
            key=lambda row: (
                row.series_id,
                row.observation_date,
                row.period_end,
                row.available_at,
                row.retrieved_at,
                row.source_hash,
                row.unit,
                row.source_url,
                row.vintage_status,
                tuple(sorted(row.quality_flags)),
                format(row.value, "f"),
            ),
        ):
            writer.writerow(
                {
                    "series_id": row.series_id,
                    "observation_date": row.observation_date.isoformat(),
                    "period_end": row.period_end.isoformat(),
                    "available_at": row.available_at.isoformat(),
                    "value": format(row.value, "f"),
                    "unit": row.unit,
                    "source_url": row.source_url,
                    "retrieved_at": row.retrieved_at.isoformat(),
                    "source_hash": row.source_hash,
                    "vintage_status": row.vintage_status,
                    "quality_flags": "|".join(sorted(row.quality_flags)),
                }
            )


def load_observation_csv(path: Path) -> tuple[ObservationRow, ...]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != _CSV_COLUMNS:
            raise ValueError("observation CSV columns must match ObservationRow")
        rows = tuple(
            ObservationRow(
                series_id=row["series_id"],
                observation_date=date.fromisoformat(row["observation_date"]),
                period_end=date.fromisoformat(row["period_end"]),
                available_at=datetime.fromisoformat(row["available_at"]),
                value=Decimal(row["value"]),
                unit=row["unit"],
                source_url=row["source_url"],
                retrieved_at=datetime.fromisoformat(row["retrieved_at"]),
                source_hash=row["source_hash"],
                vintage_status=row["vintage_status"],
                quality_flags=tuple(filter(None, row["quality_flags"].split("|"))),
            )
            for row in reader
        )
    return validate_rows(rows)
