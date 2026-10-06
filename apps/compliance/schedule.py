"""Alert arithmetic. Pure functions with no database access, so the rules are easy to test."""

DEFAULT_ALERT_DAYS = [30, 14, 7, 1, 0]
MAX_ALERT_DAYS = 365

EXPIRED, DUE, VALID = "expired", "due", "valid"


def default_alert_days():
    return list(DEFAULT_ALERT_DAYS)


def normalise_alert_days(value):
    """'30, 14, 7' or [30, 14, 7] -> [30, 14, 7]. Sorted largest first, duplicates removed."""
    if value in (None, ""):
        return []
    if isinstance(value, str):
        parts = [p for p in value.replace(";", ",").replace(" ", ",").split(",") if p.strip()]
    else:
        parts = list(value)
    days = set()
    for part in parts:
        try:
            number = int(str(part).strip())
        except ValueError:
            raise ValueError(f"'{part}' is not a whole number.")
        if not 0 <= number <= MAX_ALERT_DAYS:
            raise ValueError(f"Days must be between 0 and {MAX_ALERT_DAYS}.")
        days.add(number)
    return sorted(days, reverse=True)


def due_bucket(days_left, alert_days, repeat_days):
    """Which alert, if any, is due for a document with `days_left` days to run.

    Before expiry the bucket is the smallest threshold not yet passed (30, 14, 7, 1, 0), so a document
    that skipped several thresholds while the server was down gets ONE alert, the most urgent one.
    After expiry the bucket is -1, -2, ... one step per `repeat_days`, which gives a repeating reminder.
    Returns None when nothing is due. The caller records sent buckets so each is delivered once.
    """
    if not alert_days:
        return None
    if days_left < 0:
        repeat = max(int(repeat_days or 7), 1)
        return -(((-days_left) - 1) // repeat + 1)
    reached = [t for t in alert_days if t >= days_left]
    return min(reached) if reached else None


def state_for(days_left, alert_days):
    """'expired', 'due' (inside the alert window) or 'valid'. Always derived from dates, never stored."""
    if days_left < 0:
        return EXPIRED
    if alert_days and days_left <= max(alert_days):
        return DUE
    return VALID
