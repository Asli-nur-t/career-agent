"""Validate operator-supplied public job listing URLs without fetching them."""

import hashlib
import re
from urllib.parse import urlsplit, urlunsplit

from app.discovery.safety import normalize_public_url, safe_text
from app.discovery.schemas import SearchResult
from app.job_boards import (
    JobBoardListing,
    SUPPORTED_JOB_PROVIDERS,
    normalize_job_board_result,
)


MANUAL_JOB_PROVIDERS = (
    *SUPPORTED_JOB_PROVIDERS,
    "techcareer",
    "yenibiris",
    "secretcv",
    "toptalent",
    "weworkremotely",
    "remoteok",
    "remotive",
    "jobicy",
)

_DOMAIN_PROVIDERS = {
    "techcareer.net": "techcareer",
    "yenibiris.com": "yenibiris",
    "secretcv.com": "secretcv",
    "toptalent.co": "toptalent",
    "weworkremotely.com": "weworkremotely",
    "remoteok.com": "remoteok",
    "remotive.com": "remotive",
    "jobicy.com": "jobicy",
}

PLACEHOLDER_JOB_IDENTIFIERS = frozenset({
    "0000000000000000",
    "123456789abcdef0",
    "abcdef1234567890",
    "ffffffffffffffff",
})


def is_placeholder_job_identifier(value: str) -> bool:
    """Reject obvious fixture identifiers accidentally exposed by a page."""

    cleaned = safe_text(value, 200).casefold().strip()
    if not cleaned:
        return True
    compact = re.sub(r"[^a-z0-9]", "", cleaned)
    if compact in PLACEHOLDER_JOB_IDENTIFIERS:
        return True
    if len(compact) >= 8 and len(set(compact)) == 1:
        return True
    return any(
        marker in cleaned
        for marker in ("placeholder", "dummy-job", "example-job", "sample-job")
    )


def _provider(hostname: str) -> str | None:
    for domain, provider in _DOMAIN_PROVIDERS.items():
        if hostname == domain or hostname.endswith(f".{domain}"):
            return provider
    return None


def _canonical_path(provider: str, path: str) -> str | None:
    cleaned = path.rstrip("/")
    patterns = {
        "techcareer": r"/jobs/detail/[A-Za-z0-9._~-]+-\d{1,20}",
        "yenibiris": r"/is-ilani/[A-Za-z0-9._~-]+/\d{1,20}",
        "secretcv": r"/(?!is-ilanlari/)[A-Za-z0-9._~/-]+-is-ilanlari-\d{1,20}",
        # Real Toptalent listings end in a numeric listing identifier. Keeping
        # that identifier mandatory rejects navigation, employer and product pages.
        "toptalent": r"/[A-Za-z0-9._~-]{3,180}-\d{4,20}",
        "weworkremotely": r"/remote-jobs/[A-Za-z0-9._~%-]{3,300}",
        "remoteok": r"/remote-jobs/[A-Za-z0-9._~%-]{3,300}",
        "remotive": r"/remote-jobs/[A-Za-z0-9._~%-]{3,300}/[A-Za-z0-9._~%-]{3,300}",
        "jobicy": r"/jobs(?:-offers)?/[A-Za-z0-9._~%-]{3,300}",
    }
    pattern = patterns.get(provider)
    if pattern is None or re.fullmatch(pattern, cleaned) is None:
        return None
    return cleaned


def normalize_manual_job_result(result: SearchResult) -> JobBoardListing:
    """Return a canonical allowlisted listing; never performs network I/O."""
    url = normalize_public_url(result.url)
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").casefold()
    provider = _provider(hostname)
    if provider is not None:
        path = _canonical_path(provider, parsed.path)
        if path is None:
            raise ValueError("Unsupported manual job listing URL.")

        listing_url = urlunsplit(("https", hostname, path, "", ""))
        external_id = hashlib.sha256(
            f"{provider}\0{listing_url}".encode("utf-8")
        ).hexdigest()
        if is_placeholder_job_identifier(external_id):
            raise ValueError("Placeholder job listing identifier.")
        title = safe_text(result.title, 500)
        if not title:
            raise ValueError("Manual job title is invalid.")
        return JobBoardListing(
            provider=provider,
            external_id=external_id,
            listing_url=listing_url,
            title=title,
            snippet=None,
            search_position=1,
        )

    try:
        normalized = normalize_job_board_result(result)
    except ValueError:
        normalized = None
    if normalized is not None:
        if is_placeholder_job_identifier(normalized.external_id):
            raise ValueError("Placeholder job listing identifier.")
        return normalized
    raise ValueError("Unsupported manual job listing URL.")
