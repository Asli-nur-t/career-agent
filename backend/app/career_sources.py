"""Classify career source URLs without fetching pages or writing to the database."""

import re
from ipaddress import ip_address
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit


ATS_HOSTS = {
    "boards.greenhouse.io": "greenhouse",
    "job-boards.greenhouse.io": "greenhouse",
    "jobs.lever.co": "lever",
    "jobs.eu.lever.co": "lever",
    "jobs.ashbyhq.com": "ashby",
}
CAREER_PATHS = {
    "careers",
    "career",
    "jobs",
    "job-openings",
    "open-positions",
    "join-us",
    "work-with-us",
    "kariyer",
    "is-ilanlari",
}
HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
BOARD_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")


@dataclass(frozen=True)
class SourceCandidate:
    source_url: str
    source_type: str
    ats_type: str | None
    access_strategy: str
    status: str = "needs_review"


def _parse_public_https(raw_url: str) -> tuple[str, str]:
    """Syntax checks only. Network/DNS checks must happen before any request."""
    if not isinstance(raw_url, str) or not 1 <= len(raw_url) <= 2048:
        raise ValueError("invalid_url")
    if any(ord(char) <= 32 or ord(char) == 127 or char == "\\" for char in raw_url):
        raise ValueError("invalid_url")
    try:
        parts = urlsplit(raw_url)
        host = parts.hostname
        port = parts.port
    except ValueError as error:
        raise ValueError("invalid_url") from error
    if (
        parts.scheme != "https"
        or not host
        or not parts.netloc
        or "@" in parts.netloc
        or ":" in parts.netloc
        or port is not None
        or host.endswith(".")
        or len(host) > 253
    ):
        raise ValueError("invalid_url")
    labels = host.split(".")
    if len(labels) < 2 or any(not HOST_LABEL.fullmatch(label) for label in labels):
        raise ValueError("invalid_url")
    try:
        ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("invalid_url")
    if host.startswith("localhost.") or all(label.isdigit() for label in labels):
        raise ValueError("invalid_url")
    if labels[-1] in {"local", "localhost", "internal", "test", "invalid", "example", "onion"}:
        raise ValueError("invalid_url")
    try:
        decoded_path = unquote(parts.path, errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("invalid_url") from error
    if any(ord(char) < 32 or ord(char) == 127 or char == "\\" for char in decoded_path):
        raise ValueError("invalid_url")
    if any(segment in {".", ".."} for segment in decoded_path.split("/")):
        raise ValueError("invalid_url")
    return host, parts.path


def classify_career_source(raw_url: str) -> SourceCandidate | None:
    """Recognize documented ATS boards and likely company career pages.

    A result does not prove that the source belongs to a company. Returned
    records require a separate company-link verification before activation.
    """
    host, path = _parse_public_https(raw_url)
    ats_type = ATS_HOSTS.get(host)
    if ats_type is not None:
        slug = path.strip("/").split("/", 1)[0]
        if not BOARD_NAME.fullmatch(slug):
            return None
        return SourceCandidate(
            source_url=f"https://{host}/{slug}",
            source_type="ats",
            ats_type=ats_type,
            access_strategy="public_api",
        )

    path_segments = [segment.lower() for segment in unquote(path).split("/") if segment]
    if path_segments and re.fullmatch(r"[a-z]{2}(?:-[a-z]{2})?", path_segments[0]):
        path_segments.pop(0)
    if not path_segments or path_segments[0] not in CAREER_PATHS:
        return None
    normalized_path = path.rstrip("/")
    return SourceCandidate(
        source_url=f"https://{host}{normalized_path}",
        source_type="career_page",
        ats_type=None,
        access_strategy="manual_import",
    )
