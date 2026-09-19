"""Pure scheduling rules for retrying company discovery."""

from datetime import datetime, timedelta, timezone


RETRYABLE_OUTCOMES = (
    "evaluation_error",
    "rejected",
    "search_error",
)


def retry_due(
    attempts: list[tuple[str, datetime]],
    now: datetime,
) -> bool:
    if not attempts:
        return True
    latest_outcome, latest_at = max(attempts, key=lambda item: item[1])
    if latest_at.tzinfo is None:
        latest_at = latest_at.replace(tzinfo=timezone.utc)
    if latest_outcome == "rejected":
        delay = timedelta(days=7)
    else:
        delay = timedelta(hours=min(2 ** (len(attempts) - 1), 24))
    return now >= latest_at + delay
