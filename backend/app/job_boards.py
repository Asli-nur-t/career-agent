"""Normalize public search results from supported job boards.

This module does not crawl job-board pages or bypass authentication. It only
accepts URLs returned by the configured search provider and keeps all results
in a review queue.
"""

import hashlib
import json
import re
import threading
import unicodedata
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal, Protocol
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from app.career_sources import classify_career_source
from app.discovery.safety import (
    clean_company_name,
    normalize_public_url,
    safe_text,
)
from app.discovery.schemas import SearchResult
from app.discovery.web_verifier import PageEvidence, WebsiteVerificationError
from app.job_metadata import extract_job_metadata


MAX_BOARD_RESULTS = 10
UNKNOWN_EMPLOYER = "İşveren adı doğrulanmadı"
PAGE_VERIFIED_ACTIVE_CODES = (
    "linkedin_active_marker",
    "linkedin_valid_through_current",
    "kariyer_active_marker",
    "kariyer_valid_through_current",
    "indeed_active_marker",
    "indeed_valid_through_current",
    "glassdoor_active_marker",
    "glassdoor_valid_through_current",
    "greenhouse_public_api_present",
    "lever_public_api_present",
    "ashby_public_api_present",
)
OFFICIAL_ATS_PROVIDERS = ("greenhouse", "lever", "ashby")
SUPPORTED_JOB_PROVIDERS = (
    "linkedin",
    "kariyer",
    "indeed",
    "glassdoor",
    *OFFICIAL_ATS_PROVIDERS,
)
_NUMERIC_ID = re.compile(r"[0-9]{4,20}\Z")
_OPAQUE_ID = re.compile(r"[A-Za-z0-9_-]{5,100}\Z")
_BOARD_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")


class SearchClient(Protocol):
    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
    ) -> list[SearchResult]: ...


class PublicPageReader(Protocol):
    def read_page_text(self, url: str) -> tuple[str, str]: ...

    def read_page_evidence(self, url: str) -> PageEvidence: ...


class NormalizedATSJob(Protocol):
    external_id: str
    job_url: str
    apply_url: str | None
    title: str
    location: str | None
    department: str | None
    employment_type: str | None
    description_text: str | None
    is_remote: bool | None
    published_at: datetime | None


class PublicATSReader(Protocol):
    def fetch(
        self,
        source_url: str,
        ats_type: str,
    ) -> list[NormalizedATSJob]: ...


@dataclass(frozen=True)
class JobBoardListing:
    provider: str
    external_id: str
    listing_url: str
    title: str
    snippet: str | None
    search_position: int
    company_name_raw: str | None = None
    location: str | None = None
    work_mode: Literal["remote", "hybrid", "onsite", "unknown"] = "unknown"
    employment_type: Literal[
        "full_time",
        "part_time",
        "contract",
        "internship",
        "temporary",
        "unknown",
    ] = "unknown"
    published_at: datetime | None = None
    published_precision: str | None = None
    activity_state: Literal["active", "closed", "unknown"] = "unknown"
    activity_code: str = "not_checked"
    activity_url: str | None = None
    activity_checked_at: datetime | None = None


@dataclass(frozen=True)
class JobBoardActivity:
    state: Literal["active", "closed", "unknown"]
    code: str
    checked_url: str | None
    checked_at: datetime | None = None
    location: str | None = None
    work_mode: Literal["remote", "hybrid", "onsite", "unknown"] = "unknown"
    employment_type: Literal[
        "full_time",
        "part_time",
        "contract",
        "internship",
        "temporary",
        "unknown",
    ] = "unknown"
    published_at: datetime | None = None


@dataclass(frozen=True)
class JobBoardSearch:
    query: str
    raw_result_count: int
    filtered_result_count: int
    listings: tuple[JobBoardListing, ...]


_JOB_BOARD_CLOSED_MARKERS = (
    "this job posting is no longer accepting applications",
    "bu is ilani artik basvuru kabul etmiyor",
    "bu ilan artik basvuru kabul etmiyor",
    "artik basvuru kabul etmiyor",
    "no longer accepting applications",
    "job is no longer available",
    "job has expired",
    "position has been filled",
    "this job is no longer available",
)

_JOB_BOARD_ACTIVE_MARKERS = (
    "actively recruiting",
    "actively hiring",
    "currently accepting applications",
    "easy apply",
    "apply now",
    "hemen basvur",
    "kolay basvuru",
    "basvurular devam ediyor",
)

_GENERIC_COMPANY_WORDS = {
    "anonim",
    "as",
    "company",
    "danismanlik",
    "hizmetleri",
    "limited",
    "ltd",
    "muhendislik",
    "sanayi",
    "sirketi",
    "sti",
    "teknoloji",
    "technologies",
    "technology",
    "ticaret",
    "yazilim",
}


def _structured_datetime(value: object, *, end_of_day: bool) -> datetime | None:
    if not isinstance(value, str) or not 4 <= len(value) <= 64:
        return None
    candidate = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
            parsed_date = date.fromisoformat(candidate)
            return datetime.combine(
                parsed_date,
                time.max if end_of_day else time.min,
                tzinfo=timezone.utc,
            )
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _job_posting_objects(scripts: tuple[str, ...]) -> tuple[dict, ...]:
    postings: list[dict] = []
    remaining_nodes = 200

    def visit(value: object, depth: int) -> None:
        nonlocal remaining_nodes
        if remaining_nodes <= 0 or depth > 6:
            return
        remaining_nodes -= 1
        if isinstance(value, dict):
            raw_types = value.get("@type")
            types = raw_types if isinstance(raw_types, list) else [raw_types]
            if any(
                isinstance(item, str) and item.casefold() == "jobposting"
                for item in types
            ):
                postings.append(value)
                return
            for child in value.values():
                visit(child, depth + 1)
        elif isinstance(value, list):
            for child in value:
                visit(child, depth + 1)

    for script in scripts:
        if len(script) > 65_536:
            continue
        try:
            payload = json.loads(script)
        except (TypeError, ValueError, RecursionError):
            continue
        visit(payload, 0)
        if remaining_nodes <= 0:
            break
    return tuple(postings[:20])


def _structured_activity(
    scripts: tuple[str, ...],
    *,
    checked_at: datetime,
) -> Literal["active", "closed", "unknown"]:
    """Classify only unambiguous, plausible JobPosting expiry evidence."""
    outcomes: list[Literal["active", "closed"]] = []
    for posting in _job_posting_objects(scripts):
        valid_through = _structured_datetime(
            posting.get("validThrough"),
            end_of_day=True,
        )
        if valid_through is None:
            continue
        date_posted = _structured_datetime(
            posting.get("datePosted"),
            end_of_day=False,
        )
        if (
            valid_through > checked_at + timedelta(days=730)
            or (
                date_posted is not None
                and (
                    date_posted > checked_at + timedelta(days=1)
                    or date_posted > valid_through
                )
            )
        ):
            continue
        outcomes.append(
            "active" if valid_through >= checked_at else "closed"
        )
    if outcomes and all(item == outcomes[0] for item in outcomes):
        return outcomes[0]
    return "unknown"


def _marker_text(value: str) -> str:
    value = value.replace("ı", "i").replace("İ", "I")
    value = unicodedata.normalize("NFKD", value).casefold()
    return " ".join(
        "".join(
            character if character.isalnum() else " "
            for character in value
            if not unicodedata.combining(character)
        ).split()
    )


def choose_job_board_identity(
    company_name: object,
    brand_name: object,
) -> str:
    company = clean_company_name(company_name)
    brand = safe_text(brand_name, 300)
    brand_text = _marker_text(brand)
    tokens = brand_text.split()
    meaningful = [
        token
        for token in tokens
        if token not in _GENERIC_COMPANY_WORDS
    ]
    if len(tokens) >= 2 and meaningful:
        return brand
    return company


def listing_matches_company(
    listing: JobBoardListing,
    company_names: tuple[str, ...],
) -> bool:
    text = _marker_text(
        " ".join(
            value
            for value in (
                listing.title,
                listing.snippet,
                listing.listing_url,
            )
            if value
        )
    )
    for name in company_names:
        try:
            cleaned = clean_company_name(name)
        except ValueError:
            continue
        identity = _marker_text(cleaned)
        tokens = identity.split()
        if len(tokens) >= 2 and re.search(
            rf"(?<![a-z0-9]){re.escape(identity)}(?![a-z0-9])",
            text,
        ):
            return True
        meaningful = [
            token
            for token in tokens
            if token not in _GENERIC_COMPANY_WORDS and len(token) >= 3
        ]
        if len(meaningful) >= 2 and all(
            re.search(
                rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])",
                text,
            )
            for token in meaningful
        ):
            return True
    return False


class JobBoardActivityVerifier:
    """Detect explicit activity signals through an SSRF-safe page reader."""

    def __init__(
        self,
        page_reader: PublicPageReader,
        *,
        ats_reader: PublicATSReader | None = None,
    ) -> None:
        self._page_reader = page_reader
        self._ats_reader = ats_reader
        self._ats_cache: dict[
            tuple[str, str],
            tuple[tuple[NormalizedATSJob, ...] | None, str | None],
        ] = {}
        self._ats_lock = threading.Lock()

    def check(self, listing: JobBoardListing) -> JobBoardActivity:
        checked_at = datetime.now(timezone.utc)
        if listing.provider in OFFICIAL_ATS_PROVIDERS:
            return self._check_official_ats(listing, checked_at=checked_at)
        if listing.activity_state != "unknown":
            return JobBoardActivity(
                state=listing.activity_state,
                code=listing.activity_code,
                checked_url=listing.activity_url,
                checked_at=listing.activity_checked_at,
            )

        json_ld: tuple[str, ...] = ()
        try:
            evidence_reader = getattr(
                self._page_reader,
                "read_page_evidence",
                None,
            )
            if callable(evidence_reader):
                evidence = evidence_reader(listing.listing_url)
                final_url = evidence.final_url
                page_text = evidence.visible_text
                json_ld = evidence.json_ld
            else:
                final_url, page_text = self._page_reader.read_page_text(
                    listing.listing_url
                )
        except WebsiteVerificationError:
            return JobBoardActivity(
                state="unknown",
                code="fetch_failed",
                checked_url=None,
                checked_at=checked_at,
            )

        try:
            final_listing = normalize_job_board_result(
                SearchResult(
                    title=listing.title,
                    url=final_url,
                    snippet=listing.snippet or "",
                    position=listing.search_position,
                )
            )
        except ValueError:
            return JobBoardActivity(
                state="unknown",
                code="redirect_mismatch",
                checked_url=None,
                checked_at=checked_at,
            )

        if (
            final_listing.provider != listing.provider
            or final_listing.external_id != listing.external_id
        ):
            return JobBoardActivity(
                state="closed",
                code="redirected_to_different_job",
                checked_url=final_listing.listing_url,
                checked_at=checked_at,
            )

        normalized_text = _marker_text(page_text)
        if any(
            marker in normalized_text
            for marker in _JOB_BOARD_CLOSED_MARKERS
        ):
            return JobBoardActivity(
                state="closed",
                code=f"{listing.provider}_closed_marker",
                checked_url=final_listing.listing_url,
                checked_at=checked_at,
            )

        structured_state = _structured_activity(
            json_ld,
            checked_at=checked_at,
        )
        if structured_state != "unknown":
            return JobBoardActivity(
                state=structured_state,
                code=(
                    f"{listing.provider}_valid_through_current"
                    if structured_state == "active"
                    else f"{listing.provider}_valid_through_expired"
                ),
                checked_url=final_listing.listing_url,
                checked_at=checked_at,
            )

        if any(
            marker in normalized_text
            for marker in _JOB_BOARD_ACTIVE_MARKERS
        ):
            return JobBoardActivity(
                state="active",
                code=f"{listing.provider}_active_marker",
                checked_url=final_listing.listing_url,
                checked_at=checked_at,
            )

        return JobBoardActivity(
            state="unknown",
            code="no_closed_marker",
            checked_url=final_listing.listing_url,
            checked_at=checked_at,
        )

    def _check_official_ats(
        self,
        listing: JobBoardListing,
        *,
        checked_at: datetime,
    ) -> JobBoardActivity:
        if self._ats_reader is None:
            return JobBoardActivity(
                state="unknown",
                code="ats_verifier_unavailable",
                checked_url=None,
                checked_at=checked_at,
            )
        try:
            source = classify_career_source(listing.listing_url)
        except ValueError:
            source = None
        if (
            source is None
            or source.source_type != "ats"
            or source.ats_type != listing.provider
        ):
            return JobBoardActivity(
                state="unknown",
                code="invalid_ats_listing",
                checked_url=None,
                checked_at=checked_at,
            )

        key = (source.source_url, listing.provider)
        with self._ats_lock:
            cached = self._ats_cache.get(key)
            if cached is None:
                try:
                    jobs = tuple(
                        self._ats_reader.fetch(
                            source.source_url,
                            listing.provider,
                        )
                    )
                except Exception as error:
                    from app.ats import ATSFetchError

                    if not isinstance(error, ATSFetchError):
                        raise
                    cached = (None, error.code)
                else:
                    cached = (jobs, None)
                self._ats_cache[key] = cached
        jobs, error_code = cached
        if jobs is None:
            return JobBoardActivity(
                state="unknown",
                code=f"ats_{error_code or 'fetch_failed'}",
                checked_url=None,
                checked_at=checked_at,
            )

        matched: NormalizedATSJob | None = None
        for job in jobs:
            try:
                normalized = normalize_job_board_result(
                    SearchResult(
                        title=job.title,
                        url=job.job_url,
                        snippet=job.description_text or "",
                        position=1,
                    )
                )
            except ValueError:
                continue
            if (
                normalized.provider == listing.provider
                and normalized.external_id == listing.external_id
            ):
                matched = job
                break
        if matched is None:
            return JobBoardActivity(
                state="closed",
                code=f"{listing.provider}_public_api_missing",
                checked_url=listing.listing_url,
                checked_at=checked_at,
            )

        metadata = extract_job_metadata(
            matched.title,
            " ".join(
                item
                for item in (matched.location, matched.employment_type)
                if item
            ),
            observed_at=checked_at,
        )
        return JobBoardActivity(
            state="active",
            code=f"{listing.provider}_public_api_present",
            checked_url=matched.job_url,
            checked_at=checked_at,
            location=matched.location,
            work_mode=(
                "remote"
                if matched.is_remote is True
                else metadata.work_mode
            ),
            employment_type=metadata.employment_type,
            published_at=matched.published_at,
        )


def _hostname_belongs_to(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith(f".{domain}")


def _provider(hostname: str) -> str | None:
    ats_hosts = {
        "boards.greenhouse.io": "greenhouse",
        "job-boards.greenhouse.io": "greenhouse",
        "jobs.lever.co": "lever",
        "jobs.eu.lever.co": "lever",
        "jobs.ashbyhq.com": "ashby",
    }
    if hostname in ats_hosts:
        return ats_hosts[hostname]
    if _hostname_belongs_to(hostname, "linkedin.com"):
        return "linkedin"
    if _hostname_belongs_to(hostname, "kariyer.net"):
        return "kariyer"
    if _hostname_belongs_to(hostname, "indeed.com"):
        return "indeed"
    if any(
        _hostname_belongs_to(hostname, domain)
        for domain in ("glassdoor.com", "glassdoor.com.tr")
    ):
        return "glassdoor"
    return None


def _official_ats_path(
    provider: str,
    path: str,
) -> tuple[str, str] | None:
    segments = [segment for segment in path.strip("/").split("/") if segment]
    if provider == "greenhouse":
        if (
            len(segments) != 3
            or segments[1].casefold() != "jobs"
            or not _BOARD_SLUG.fullmatch(segments[0])
            or not _NUMERIC_ID.fullmatch(segments[2])
        ):
            return None
        return segments[0], segments[2]
    if provider in {"lever", "ashby"}:
        if (
            len(segments) not in {2, 3}
            or not _BOARD_SLUG.fullmatch(segments[0])
            or not _OPAQUE_ID.fullmatch(segments[1])
            or (
                len(segments) == 3
                and segments[2].casefold() not in {"apply", "application"}
            )
        ):
            return None
        return segments[0], segments[1]
    return None


def _linkedin_id(path: str) -> str | None:
    prefix = "/jobs/view/"
    if not path.casefold().startswith(prefix):
        return None
    tail = path.rstrip("/").rsplit("/", 1)[-1]
    candidate = tail.rsplit("-", 1)[-1]
    return candidate if _NUMERIC_ID.fullmatch(candidate) else None


def _kariyer_id(path: str) -> str | None:
    if not path.casefold().startswith("/is-ilani/"):
        return None
    tail = path.rstrip("/").rsplit("/", 1)[-1]
    candidate = tail.rsplit("-", 1)[-1]
    return candidate if _NUMERIC_ID.fullmatch(candidate) else None


def _query_id(query: str, field: str) -> str | None:
    values = parse_qs(
        query,
        keep_blank_values=False,
        max_num_fields=20,
    ).get(field, [])
    if len(values) != 1:
        return None
    candidate = safe_text(values[0], 100)
    return candidate if _OPAQUE_ID.fullmatch(candidate) else None


def _glassdoor_id(path: str, query: str, normalized_url: str) -> str | None:
    if "/job-listing/" not in path.casefold():
        return None
    query_id = _query_id(query, "jl")
    if query_id:
        return query_id
    path_match = re.search(r"([0-9]{6,20})(?:\.htm)?/?\Z", path)
    if path_match:
        return path_match.group(1)
    return hashlib.sha256(normalized_url.encode("utf-8")).hexdigest()


def normalize_job_board_result(result: SearchResult) -> JobBoardListing:
    url = normalize_public_url(result.url)
    parsed = urlsplit(url)
    hostname = (parsed.hostname or "").lower()
    provider = _provider(hostname)
    if provider is None:
        raise ValueError("Unsupported job-board hostname.")

    if provider in OFFICIAL_ATS_PROVIDERS:
        parsed_path = _official_ats_path(provider, parsed.path)
        if parsed_path is None:
            raise ValueError("Official ATS job URL is invalid.")
        board_slug, external_id = parsed_path
        if provider == "greenhouse":
            path = f"/{board_slug}/jobs/{external_id}"
        else:
            path = f"/{board_slug}/{external_id}"
        listing_url = urlunsplit(("https", hostname, path, "", ""))
    elif provider == "linkedin":
        external_id = _linkedin_id(parsed.path)
        if external_id is None:
            raise ValueError("LinkedIn job URL is invalid.")
        listing_url = f"https://www.linkedin.com/jobs/view/{external_id}"
    elif provider == "kariyer":
        external_id = _kariyer_id(parsed.path)
        if external_id is None:
            raise ValueError("Kariyer job URL is invalid.")
        listing_url = urlunsplit(
            ("https", hostname, parsed.path.rstrip("/"), "", "")
        )
    elif provider == "indeed":
        if parsed.path.rstrip("/").casefold() != "/viewjob":
            raise ValueError("Indeed job URL is invalid.")
        external_id = _query_id(parsed.query, "jk")
        if external_id is None:
            raise ValueError("Indeed job identifier is invalid.")
        listing_url = urlunsplit(
            (
                "https",
                hostname,
                "/viewjob",
                urlencode({"jk": external_id}),
                "",
            )
        )
    else:
        external_id = _glassdoor_id(parsed.path, parsed.query, url)
        if external_id is None:
            raise ValueError("Glassdoor job URL is invalid.")
        query = urlencode({"jl": external_id}) if len(external_id) < 64 else ""
        listing_url = urlunsplit(
            ("https", hostname, parsed.path.rstrip("/"), query, "")
        )

    title = safe_text(result.title, 500)
    if not title:
        raise ValueError("Job title is invalid.")
    snippet = safe_text(result.snippet, 2_000) or None
    metadata = extract_job_metadata(title, snippet)
    return JobBoardListing(
        provider=provider,
        external_id=external_id,
        listing_url=normalize_public_url(listing_url),
        title=title,
        snippet=snippet,
        search_position=result.position,
        company_name_raw=infer_job_board_company(title, provider),
        location=metadata.location,
        work_mode=metadata.work_mode,
        employment_type=metadata.employment_type,
        published_at=metadata.published_at,
        published_precision=metadata.published_precision,
        activity_state=metadata.activity_state,
        activity_code=metadata.activity_code,
        activity_url=(
            normalize_public_url(listing_url)
            if metadata.activity_state != "unknown"
            else None
        ),
        activity_checked_at=metadata.activity_checked_at,
    )


def infer_job_board_company(title: object, provider: str) -> str | None:
    """Extract a review hint from common search-result title shapes."""

    value = safe_text(title, 500)
    if not value:
        return None
    value = re.sub(
        r"\s*[-|]\s*(?:LinkedIn|Kariyer\.net|Indeed|Glassdoor)\s*$",
        "",
        value,
        flags=re.IGNORECASE,
    ).strip()
    patterns = (
        r"^Job Application for .+? at (?P<company>.+?)$",
        r"^(?P<company>.+?)\s+hiring\s+.+?(?:\s+in\s+.+)?$",
        r"\bna empresa\s+(?P<company>.+?)(?:\s+[—|-]\s+|$)",
        r"\bat\s+(?P<company>.+?)(?:\s+[—|-]\s+|$)",
    )
    def valid_hint(candidate: str) -> str | None:
        company = safe_text(candidate, 300)
        normalized = _marker_text(company)
        if (
            not company
            or any(symbol in company for symbol in ("$", "€", "£", "¥", "|"))
            or re.search(
                r"(?<![a-z0-9])(?:linkedin|indeed|glassdoor|kariyer|"
                r"fully remote|remote work|part time|full time|ref|"
                r"salary|hourly)(?![a-z0-9])",
                normalized,
            )
        ):
            return None
        return company

    for pattern in patterns:
        match = re.search(pattern, value, flags=re.IGNORECASE)
        if match:
            company = valid_hint(match.group("company"))
            if company:
                return company

    segments = [segment.strip() for segment in value.split(" - ")]
    if provider == "linkedin" and len(segments) >= 2:
        company = valid_hint(segments[-1].split(" — ", 1)[0])
        if company:
            return company
    return None


def build_job_board_query(company_name: object) -> str:
    name = clean_company_name(company_name)
    name = safe_text(name.replace('"', " ").replace("\\", " "), 200)
    if not name:
        raise ValueError("Job-board company name is invalid.")
    query = (
        f'"{name}" '
        "(site:linkedin.com/jobs/view OR "
        "site:kariyer.net/is-ilani OR "
        "site:tr.indeed.com/viewjob OR "
        "site:glassdoor.com/job-listing)"
    )
    if len(query) > 500:
        raise ValueError("Job-board query is too long.")
    return query


def build_profile_job_queries(
    role_groups: tuple[tuple[str, ...], ...],
    *,
    preferred_locations: tuple[str, ...] = (),
    preferred_remote_locations: tuple[str, ...] = (),
    remote_allowed: bool = True,
    published_after: date | None = None,
    max_queries: int = 6,
) -> tuple[str, ...]:
    """Build bounded, tier-aware queries without interpolating operators."""

    if not 1 <= max_queries <= 6:
        raise ValueError("Profile job query limit is invalid.")

    def phrase(value: object, maximum: int) -> str:
        cleaned = safe_text(value, maximum)
        cleaned = safe_text(
            cleaned.replace('"', " ").replace("\\", " "),
            maximum,
        )
        return cleaned

    locations = tuple(
        item
        for item in (
            phrase(location, 100)
            for location in preferred_locations[:3]
        )
        if item
    )
    remote_locations = tuple(
        item
        for item in (
            phrase(location, 100)
            for location in preferred_remote_locations[:3]
        )
        if item
    )
    location_groups: list[str] = []
    if locations:
        location_groups.append(
            "(" + " OR ".join(f'"{item}"' for item in locations) + ")"
        )
    if remote_allowed:
        remote_group = "(remote OR uzaktan)"
        if remote_locations:
            remote_group += " (" + " OR ".join(
                f'"{item}"' for item in remote_locations
            ) + ")"
        location_groups.append(f"({remote_group})")
    location_clause = (
        f" ({' OR '.join(location_groups)})" if location_groups else ""
    )
    source_groups = (
        (
            "(site:job-boards.greenhouse.io OR "
            "site:boards.greenhouse.io OR "
            "site:jobs.lever.co OR "
            "site:jobs.eu.lever.co OR "
            "site:jobs.ashbyhq.com)",
            False,
        ),
        (
            "(site:linkedin.com/jobs/view OR "
            "site:kariyer.net/is-ilani OR "
            "site:tr.indeed.com/viewjob OR "
            "site:glassdoor.com/job-listing)",
            True,
        ),
    )
    date_clause = (
        f" after:{published_after.isoformat()}" if published_after else ""
    )

    role_clauses: list[str] = []
    for group in role_groups:
        roles = []
        seen: set[str] = set()
        for role in group:
            cleaned = phrase(role, 100)
            marker = _marker_text(cleaned)
            if cleaned and marker and marker not in seen:
                seen.add(marker)
                roles.append(cleaned)
        if not roles:
            continue

        selected: list[str] = []
        for role in roles:
            candidate = selected + [role]
            role_clause = " OR ".join(f'"{item}"' for item in candidate)
            queries_for_sources = (
                (
                    f"({role_clause}){location_clause} {sites}{date_clause}"
                    if apply_search_filters
                    else f"({role_clause}) {sites}"
                )
                for sites, apply_search_filters in source_groups
            )
            if any(len(query) > 500 for query in queries_for_sources):
                break
            selected = candidate
        if not selected:
            raise ValueError("Profile job role is too long.")
        role_clauses.append(
            " OR ".join(f'"{item}"' for item in selected)
        )

    queries: list[str] = []
    for sites, apply_search_filters in source_groups:
        for role_clause in role_clauses:
            queries.append(
                (
                    f"({role_clause}){location_clause} {sites}{date_clause}"
                    if apply_search_filters
                    else f"({role_clause}) {sites}"
                )
            )
            if len(queries) >= max_queries:
                break
        if len(queries) >= max_queries:
            break
    if not queries:
        raise ValueError("Profile job roles are empty.")
    return tuple(queries)


class JobBoardSearchConnector:
    def __init__(
        self,
        search_client: SearchClient,
        *,
        activity_verifier: JobBoardActivityVerifier | None = None,
        activity_providers: tuple[str, ...] | None = None,
    ) -> None:
        if activity_providers is not None and any(
            provider not in SUPPORTED_JOB_PROVIDERS
            for provider in activity_providers
        ):
            raise ValueError("Activity provider filter is invalid.")
        self._search_client = search_client
        self._activity_verifier = activity_verifier
        self._activity_providers = (
            frozenset(activity_providers)
            if activity_providers is not None
            else None
        )

    def _should_verify_activity(self, provider: str) -> bool:
        return self._activity_verifier is not None and (
            self._activity_providers is None
            or provider in self._activity_providers
        )

    def search(
        self,
        company_name: object,
        *,
        aliases: tuple[str, ...] = (),
        max_results: int = MAX_BOARD_RESULTS,
    ) -> JobBoardSearch:
        if not 1 <= max_results <= MAX_BOARD_RESULTS:
            raise ValueError("Job-board result limit is invalid.")
        query = build_job_board_query(company_name)
        results = self._search_client.search(
            query,
            max_results=max_results,
        )
        listings: list[JobBoardListing] = []
        seen: set[tuple[str, str]] = set()
        filtered = 0
        for result in results:
            try:
                listing = normalize_job_board_result(result)
            except ValueError:
                continue
            key = (listing.provider, listing.external_id)
            if key in seen:
                continue
            seen.add(key)
            identities = (clean_company_name(company_name), *aliases)
            if not listing_matches_company(listing, identities):
                filtered += 1
                continue
            if self._should_verify_activity(listing.provider):
                activity = self._activity_verifier.check(listing)
                listing = replace(
                    listing,
                    location=activity.location or listing.location,
                    work_mode=(
                        activity.work_mode
                        if activity.work_mode != "unknown"
                        else listing.work_mode
                    ),
                    employment_type=(
                        activity.employment_type
                        if activity.employment_type != "unknown"
                        else listing.employment_type
                    ),
                    published_at=(
                        activity.published_at or listing.published_at
                    ),
                    activity_state=activity.state,
                    activity_code=activity.code,
                    activity_url=activity.checked_url,
                    activity_checked_at=activity.checked_at,
                )
            listings.append(listing)
        return JobBoardSearch(
            query=query,
            raw_result_count=len(results),
            filtered_result_count=filtered,
            listings=tuple(listings),
        )

    def search_query(
        self,
        query: str,
        *,
        max_results: int = MAX_BOARD_RESULTS,
    ) -> JobBoardSearch:
        """Normalize a trusted role query without applying company filtering."""

        if not 1 <= max_results <= MAX_BOARD_RESULTS:
            raise ValueError("Job-board result limit is invalid.")
        query = safe_text(query, 500)
        if not query:
            raise ValueError("Job-board query is invalid.")
        results = self._search_client.search(query, max_results=max_results)
        listings: list[JobBoardListing] = []
        seen: set[tuple[str, str]] = set()
        filtered = 0
        for result in results:
            try:
                listing = normalize_job_board_result(result)
            except ValueError:
                filtered += 1
                continue
            key = (listing.provider, listing.external_id)
            if key in seen:
                filtered += 1
                continue
            seen.add(key)
            if self._should_verify_activity(listing.provider):
                activity = self._activity_verifier.check(listing)
                listing = replace(
                    listing,
                    location=activity.location or listing.location,
                    work_mode=(
                        activity.work_mode
                        if activity.work_mode != "unknown"
                        else listing.work_mode
                    ),
                    employment_type=(
                        activity.employment_type
                        if activity.employment_type != "unknown"
                        else listing.employment_type
                    ),
                    published_at=(
                        activity.published_at or listing.published_at
                    ),
                    activity_state=activity.state,
                    activity_code=activity.code,
                    activity_url=activity.checked_url,
                    activity_checked_at=activity.checked_at,
                )
            listings.append(listing)
        return JobBoardSearch(
            query=query,
            raw_result_count=len(results),
            filtered_result_count=filtered,
            listings=tuple(listings),
        )
