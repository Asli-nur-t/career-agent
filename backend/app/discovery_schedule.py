"""Pure scheduling rules for retrying company discovery."""

from datetime import datetime, timedelta, timezone


RETRYABLE_OUTCOMES = (
    "evaluation_error",
    "rejected",
    "search_error",
)
LONG_COOLDOWN_ERRORS = {
    "authentication_error",
    "model_not_found",
}
IMMEDIATE_PROVIDER_STOP_ERRORS = {
    "authentication_error",
    "model_not_found",
    "rate_limited",
}
TRANSIENT_PROVIDER_ERRORS = {
    "connection_error",
    "service_unavailable",
    "timeout",
}


def should_open_provider_circuit(
    error_code: str | None,
    consecutive_provider_failures: int,
) -> bool:
    if error_code in IMMEDIATE_PROVIDER_STOP_ERRORS:
        return True
    return (
        error_code in TRANSIENT_PROVIDER_ERRORS
        and consecutive_provider_failures >= 2
    )


def retry_due(
    attempts: list[tuple[str, str | None, datetime]],
    now: datetime,
) -> bool:
    if not attempts:
        return True
    latest_outcome, latest_error_code, latest_at = max(
        attempts,
        key=lambda item: item[2],
    )
    if latest_at.tzinfo is None:
        latest_at = latest_at.replace(tzinfo=timezone.utc)
    if (
        latest_outcome == "rejected"
        or latest_error_code in LONG_COOLDOWN_ERRORS
    ):
        delay = timedelta(days=7)
    elif latest_error_code == "rate_limited":
        delay = timedelta(hours=6)
    else:
        delay = timedelta(hours=min(2 ** (len(attempts) - 1), 24))
    return now >= latest_at + delay
