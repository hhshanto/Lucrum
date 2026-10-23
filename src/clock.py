"""Time handling.

Implements the timezone half of ``period-v1``.

One rule, applied without exception: **instants are stored in UTC, and
conversion to a local calendar date happens at the edges only.** A naive
datetime never reaches the database, and a local date is never stored as if it
were an instant.

The distinction that matters here is between an *instant* and a *calendar
date*. ``order_placed_at_utc`` is an instant: a point in time, the same moment
everywhere. ``order_date`` is a calendar date in the store's timezone: a label
the business uses to decide which month a sale belongs to. Conflating them is
how an order placed at 9pm on 31 March lands in April.

Converting UTC to a local date is always unambiguous, so month assignment has
no DST edge case. The reverse direction does, which is one reason nothing here
converts a local date back to an instant.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = ["utc_now", "ensure_utc", "to_store_date", "store_timezone", "InvalidTimezone"]


class InvalidTimezone(ValueError):
    """A store's timezone is missing or not a recognised IANA identifier.

    US-003 AC-02: missing configuration is identified, never guessed. There is
    no fallback to UTC or to the server's local zone, because a silently wrong
    timezone produces months that are quietly off by a day at the boundary.
    """


def utc_now() -> datetime:
    """The current instant, timezone-aware, in UTC."""
    return datetime.now(timezone.utc)


def ensure_utc(value: datetime) -> datetime:
    """Normalise an instant to UTC, refusing naive datetimes.

    A naive datetime is an instant whose timezone someone forgot. Accepting it
    means guessing, and the guess is wrong near a month boundary, which is
    exactly where it matters most.
    """
    if value.tzinfo is None:
        raise ValueError(
            "naive datetime rejected: an instant must carry its timezone. "
            "Use clock.utc_now() or attach tzinfo at the edge where the value "
            "entered the system."
        )
    return value.astimezone(timezone.utc)


def store_timezone(name: str | None) -> ZoneInfo:
    """Resolve a store's IANA timezone."""
    if not name:
        raise InvalidTimezone(
            "store has no configured timezone. period-v1 requires one before "
            "any date can be resolved; it is never defaulted."
        )
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise InvalidTimezone(f"not a recognised IANA timezone: {name!r}") from exc


def to_store_date(instant: datetime, timezone_name: str | None) -> date:
    """Convert an instant to a calendar date in the store's timezone.

    This produces ``orders.order_date``: the transaction date, which drives
    order-month reporting. It does **not** decide the posting period -- that is
    a separate decision made by the period service, per BR-13.
    """
    return ensure_utc(instant).astimezone(store_timezone(timezone_name)).date()
