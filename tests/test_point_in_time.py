from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from unittest import TestCase

from perpetual_engine.point_in_time import (
    ObservationRow,
    asof_select,
    load_observation_csv,
    validate_rows,
    write_observation_csv,
)


def utc(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def observation(**changes: object) -> ObservationRow:
    fields: dict[str, object] = {
        "series_id": "ERP",
        "observation_date": date(2007, 12, 31),
        "period_end": date(2007, 12, 31),
        "available_at": utc("2008-02-01T23:59:59+00:00"),
        "value": Decimal("0.05"),
        "unit": "ratio",
        "source_url": "https://pages.stern.nyu.edu/source.xls",
        "retrieved_at": utc("2026-08-22T00:00:00+00:00"),
        "source_hash": "a" * 64,
    }
    fields.update(changes)
    return ObservationRow(**fields)  # type: ignore[arg-type]


class PointInTimeTests(TestCase):
    def test_asof_never_selects_future_release(self):
        row = observation()
        self.assertIsNone(asof_select((row,), utc("2008-02-01T12:00:00+00:00")))
        self.assertEqual(asof_select((row,), utc("2008-03-01T00:00:00+00:00")), row)

    def test_asof_uses_release_then_observation_retrieval_and_hash_tiebreakers(self):
        earlier = observation(source_hash="a" * 64)
        later_release = observation(
            observation_date=date(2008, 1, 31),
            period_end=date(2008, 1, 31),
            available_at=utc("2008-02-02T00:00:00+00:00"),
            source_hash="b" * 64,
        )
        later_observation = observation(
            observation_date=date(2008, 2, 1),
            period_end=date(2008, 2, 1),
            source_hash="c" * 64,
        )
        later_retrieval = observation(
            retrieved_at=utc("2026-08-22T00:00:01+00:00"), source_hash="d" * 64
        )
        later_hash = observation(source_hash="e" * 64)
        self.assertEqual(
            asof_select(
                (earlier, later_release, later_observation, later_retrieval, later_hash),
                utc("2008-03-01T00:00:00+00:00"),
            ),
            later_release,
        )

    def test_asof_tiebreaks_on_observation_date(self):
        earlier = observation(source_hash="a" * 64)
        later = observation(observation_date=date(2008, 1, 1), period_end=date(2008, 1, 1), source_hash="b" * 64)
        self.assertEqual(asof_select((earlier, later), utc("2008-03-01T00:00:00+00:00")), later)

    def test_asof_tiebreaks_on_retrieval_time(self):
        earlier = observation(source_hash="a" * 64)
        later = observation(retrieved_at=utc("2026-08-22T00:00:01+00:00"), source_hash="b" * 64)
        self.assertEqual(asof_select((earlier, later), utc("2008-03-01T00:00:00+00:00")), later)

    def test_asof_tiebreaks_on_source_hash(self):
        lower = observation(source_hash="a" * 64)
        higher = observation(source_hash="b" * 64)
        self.assertEqual(asof_select((lower, higher), utc("2008-03-01T00:00:00+00:00")), higher)

    def test_validation_normalizes_timestamps_to_utc(self):
        row = observation(
            available_at=datetime(2008, 2, 2, 0, tzinfo=timezone(timedelta(hours=1))),
            retrieved_at=datetime(2026, 8, 22, 2, tzinfo=timezone(timedelta(hours=2))),
        )
        validated = validate_rows((row,))
        self.assertEqual(validated[0].available_at, utc("2008-02-01T23:00:00+00:00"))
        self.assertEqual(validated[0].retrieved_at, utc("2026-08-22T00:00:00+00:00"))
        self.assertEqual(validated[0].available_at.tzinfo, timezone.utc)

    def test_validation_rejects_invalid_rows(self):
        invalid_rows = {
            "naive timestamp": observation(available_at=datetime(2008, 2, 1, 23, 59, 59)),
            "non-finite value": observation(value=Decimal("NaN")),
            "empty series": observation(series_id=""),
            "empty unit": observation(unit=""),
            "empty provenance": observation(source_url=""),
            "invalid hash": observation(source_hash="A" * 64),
            "period before observation": observation(period_end=date(2007, 12, 30)),
            "retrieved before available": observation(retrieved_at=utc("2008-02-01T23:59:58+00:00")),
        }
        for reason, row in invalid_rows.items():
            with self.subTest(reason=reason):
                with self.assertRaises(ValueError):
                    validate_rows((row,))

    def test_validation_rejects_duplicate_series_observation_and_vintage(self):
        row = observation()
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_rows((row, row))

    def test_csv_round_trip_is_byte_stable(self):
        rows = (
            observation(quality_flags=("late", "checked")),
            observation(
                series_id="CPI",
                source_hash="b" * 64,
                value=Decimal("123.4500"),
                vintage_status="OFFICIAL_VINTAGE",
            ),
        )
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as directory:
            first_path = Path(directory) / "first.csv"
            second_path = Path(directory) / "second.csv"
            write_observation_csv(rows, first_path)
            write_observation_csv(load_observation_csv(first_path), second_path)
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
            self.assertIn(b"checked|late", first_path.read_bytes())

    def test_csv_output_is_stable_when_rows_arrive_in_a_different_order(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        rows = (observation(series_id="CPI", source_hash="b" * 64), observation())
        with TemporaryDirectory() as directory:
            first_path = Path(directory) / "first.csv"
            second_path = Path(directory) / "second.csv"
            write_observation_csv(rows, first_path)
            write_observation_csv(reversed(rows), second_path)
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())

    def test_quality_flags_are_immutable_tuples_and_csv_stays_deterministic(self):
        source_flags = ["late", "checked"]
        row = observation(quality_flags=source_flags)
        source_flags.append("mutated")
        self.assertEqual(row.quality_flags, ("late", "checked"))
        with self.assertRaises(AttributeError):
            row.quality_flags.append("mutated")  # type: ignore[attr-defined]
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as directory:
            first_path = Path(directory) / "first.csv"
            second_path = Path(directory) / "second.csv"
            write_observation_csv((row,), first_path)
            write_observation_csv((observation(quality_flags=("checked", "late")),), second_path)
            self.assertEqual(first_path.read_bytes(), second_path.read_bytes())
