"""Conservative metadata extraction from untrusted job search text."""

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


_RELATIVE_DATE = re.compile(
    r"(?<![a-z0-9])(?P<count>[0-9]{1,3})\s*\+?\s*"
    r"(?P<unit>dakika|saat|gun|hafta|ay|yil|minute|minutes|hour|hours|"
    r"day|days|week|weeks|month|months|year|years)\s*"
    r"(?:once|ago)(?![a-z0-9])"
)
_CLOSED_MARKERS = (
    "artik basvuru kabul etmiyor",
    "basvuru kabul etmiyor",
    "no longer accepting applications",
    "job has expired",
    "job is no longer available",
    "position has been filled",
)
_ACTIVE_MARKERS = (
    "actively recruiting",
    "actively hiring",
    "currently accepting applications",
    "basvurular devam ediyor",
    "aktif olarak ise alim",
)
_LOCATION_NOISE = re.compile(
    r"(?<![a-z0-9])(?:remote|uzaktan|hybrid|hibrit|on\s*site|onsite|"
    r"full\s*time|part\s*time|tam\s*zamanli|yari\s*zamanli|contract|"
    r"internship|temporary|ref|salary|hourly)(?![a-z0-9])"
)


@dataclass(frozen=True)
class ExtractedJobMetadata:
    location: str | None
    work_mode: str
    employment_type: str
    published_at: datetime | None
    published_precision: str | None
    activity_state: str
    activity_code: str
    activity_checked_at: datetime | None


def normalize_metadata_text(value: str | None) -> str:
    if not value:
        return ""
    value = value.replace("ı", "i").replace("İ", "I")
    value = unicodedata.normalize("NFKD", value).casefold()
    return " ".join(
        "".join(
            character if character.isalnum() else " "
            for character in value
            if not unicodedata.combining(character)
        ).split()
    )


def _published_at(
    text: str,
    observed_at: datetime,
) -> tuple[datetime | None, str | None]:
    if re.search(r"(?<![a-z0-9])(?:bugun|today)(?![a-z0-9])", text):
        return observed_at, "day"
    if re.search(r"(?<![a-z0-9])(?:dun|yesterday)(?![a-z0-9])", text):
        return observed_at - timedelta(days=1), "day"
    match = _RELATIVE_DATE.search(text)
    if match is None:
        return None, None
    count = int(match.group("count"))
    unit = match.group("unit")
    if unit in {"dakika", "minute", "minutes"}:
        delta = timedelta(minutes=count)
        precision = "minute"
    elif unit in {"saat", "hour", "hours"}:
        delta = timedelta(hours=count)
        precision = "hour"
    elif unit in {"gun", "day", "days"}:
        delta = timedelta(days=count)
        precision = "day"
    elif unit in {"hafta", "week", "weeks"}:
        delta = timedelta(weeks=count)
        precision = "week"
    elif unit in {"ay", "month", "months"}:
        delta = timedelta(days=30 * count)
        precision = "month"
    else:
        delta = timedelta(days=365 * count)
        precision = "year"
    return observed_at - delta, precision


def _clean_location_candidate(value: str) -> str | None:
    candidate = re.sub(
        r"\s*(?:-|\|)\s*(?:LinkedIn(?:\s+Jobs)?|Indeed|Glassdoor|"
        r"Kariyer\.net)\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip(" .,|-…()")
    candidate = re.sub(
        r"^(?:remote|uzaktan)\s*[,/-]\s*",
        "",
        candidate,
        flags=re.IGNORECASE,
    ).strip(" .,|-…()")
    normalized = normalize_metadata_text(candidate)
    if (
        not candidate
        or len(candidate) > 200
        or "http" in normalized
        or any(symbol in candidate for symbol in ("$", "€", "£", "¥"))
        or "|" in candidate
        or _LOCATION_NOISE.search(normalized)
        or normalized in {"linkedin", "indeed", "glassdoor", "kariyer net"}
    ):
        return None
    return candidate


def _location_from_title(title: str) -> str | None:
    based_in = re.search(
        r"\bbased\s+in\s+(.+?)(?:\)|\s+at\s+|\s+[|—–]\s+|$)",
        title,
        flags=re.IGNORECASE,
    )
    if based_in:
        candidate = _clean_location_candidate(based_in.group(1))
        if candidate:
            return candidate

    hiring_in = re.search(
        r"\bhiring\b.+?\s+in\s+(.+?)(?:\s*\|\s*LinkedIn.*)?$",
        title,
        flags=re.IGNORECASE,
    )
    if hiring_in:
        candidate = _clean_location_candidate(hiring_in.group(1))
        if candidate:
            return candidate

    parts = re.split(r"\s+[—–]\s+", title)
    if len(parts) < 2:
        return None
    return _clean_location_candidate(parts[-1])


def _location_from_snippet(snippet: str) -> str | None:
    parts = [part.strip() for part in re.split(r"[·•]", snippet)]
    for index, part in enumerate(parts):
        if _RELATIVE_DATE.search(normalize_metadata_text(part)) and index:
            for candidate_part in reversed(parts[:index]):
                candidate = _clean_location_candidate(candidate_part)
                if candidate:
                    return candidate
    return None


def _work_mode(text: str) -> str:
    if re.search(r"(?<![a-z0-9])(?:hybrid|hibrit)(?![a-z0-9])", text):
        return "hybrid"
    if re.search(r"(?<![a-z0-9])(?:remote|uzaktan)(?![a-z0-9])", text):
        return "remote"
    if re.search(
        r"(?<![a-z0-9])(?:on\s*site|onsite|is yerinde|ofisten)(?![a-z0-9])",
        text,
    ):
        return "onsite"
    return "unknown"


def _employment_type(text: str) -> str:
    patterns = (
        ("full_time", r"(?:full time|tam zamanli)"),
        ("part_time", r"(?:part time|yari zamanli)"),
        ("contract", r"(?:contract|sozlesmeli)"),
        ("internship", r"(?:internship|intern|stajyer|staj)"),
        ("temporary", r"(?:temporary|gecici)"),
    )
    for value, pattern in patterns:
        if re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", text):
            return value
    return "unknown"


def extract_job_metadata(
    title: str,
    snippet: str | None,
    *,
    observed_at: datetime | None = None,
) -> ExtractedJobMetadata:
    observed = observed_at or datetime.now(timezone.utc)
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    combined = normalize_metadata_text(" ".join((title, snippet or "")))
    published_at, precision = _published_at(combined, observed)
    location = _location_from_title(title) or _location_from_snippet(
        snippet or ""
    )
    if any(marker in combined for marker in _CLOSED_MARKERS):
        activity_state = "closed"
        activity_code = "search_text_closed_marker"
        checked_at = observed
    elif any(marker in combined for marker in _ACTIVE_MARKERS):
        activity_state = "active"
        activity_code = "search_text_active_marker"
        checked_at = observed
    else:
        activity_state = "unknown"
        activity_code = "not_checked"
        checked_at = None
    return ExtractedJobMetadata(
        location=location,
        work_mode=_work_mode(combined),
        employment_type=_employment_type(combined),
        published_at=published_at,
        published_precision=precision,
        activity_state=activity_state,
        activity_code=activity_code,
        activity_checked_at=checked_at,
    )
