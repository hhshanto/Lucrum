"""Time and timezone conventions (period-v1)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from src.clock import (
    InvalidTimezone,
    ensure_utc,
    store_timezone,
    to_store_date,
    utc_now,
)


class TestInstantsCarryTimezone:
    def test_naive_datetime_is_rejected(self):
        with pytest.raises(ValueError, match="naive"):
            ensure_utc(datetime(2026, 7, 3, 12, 0, 0))

    def test_utc_now_is_aware(self):
        assert utc_now().tzinfo is not None

    def test_non_utc_is_normalised(self):
        eastern = timezone(timedelta(hours=-4))
        value = datetime(2026, 7, 3, 20, 0, 0, tzinfo=eastern)
        assert ensure_utc(value) == datetime(2026, 7, 4, 0, 0, 0, tzinfo=timezone.utc)


class TestTimezoneIsNeverGuessed:
    """US-003 AC-02: missing configuration is identified, not defaulted."""

    def test_missing_timezone_raises(self):
        with pytest.raises(InvalidTimezone):
            store_timezone(None)

    def test_empty_timezone_raises(self):
        with pytest.raises(InvalidTimezone):
            store_timezone("")

    def test_unknown_timezone_raises(self):
        with pytest.raises(InvalidTimezone):
            store_timezone("Mars/Olympus_Mons")

    def test_does_not_fall_back_to_utc(self):
        # A silent UTC fallback produces months that are off by a day at the
        # boundary, which is both wrong and very hard to notice.
        with pytest.raises(InvalidTimezone):
            to_store_date(utc_now(), None)


class TestOrderDateFollowsStoreTimezone:
    """The instant/calendar-date distinction that decides month membership."""

    def test_late_evening_local_stays_in_its_own_day(self):
        # 2026-07-31 21:00 in New York is 2026-08-01 01:00 UTC. The sale
        # happened in July, and order-month reporting must say July.
        instant = datetime(2026, 8, 1, 1, 0, 0, tzinfo=timezone.utc)
        assert to_store_date(instant, "America/New_York") == date(2026, 7, 31)

    def test_same_instant_differs_by_store_timezone(self):
        instant = datetime(2026, 8, 1, 1, 0, 0, tzinfo=timezone.utc)
        assert to_store_date(instant, "America/New_York") == date(2026, 7, 31)
        assert to_store_date(instant, "Europe/Berlin") == date(2026, 8, 1)

    def test_dst_transition_is_unambiguous(self):
        # Converting UTC to a local date is always well defined, which is why
        # month assignment has no DST edge case.
        before = datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)
        after = datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)
        assert to_store_date(before, "America/New_York") == date(2026, 11, 1)
        assert to_store_date(after, "America/New_York") == date(2026, 11, 1)
