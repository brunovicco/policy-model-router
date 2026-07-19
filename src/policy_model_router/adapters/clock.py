"""System clock adapter implementing the application's ``Clock`` port."""

from datetime import UTC, datetime


class SystemClock:
    """Reads the current time from the system clock, in UTC."""

    def now(self) -> datetime:
        """Return the current UTC, timezone-aware time."""
        return datetime.now(UTC)
