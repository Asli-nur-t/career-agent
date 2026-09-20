"""Normalize public search results from supported job boards.

This module does not crawl job-board pages or bypass authentication. It only
accepts URLs returned by the configured search provider and keeps all results
in a review queue.
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Literal, Protocol
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from app.discovery.safety import (
    clean_company_name,
    normalize_public_url,
    safe_text,
)
from app.discovery.schemas import SearchResult
from app.discovery.web_verifier import WebsiteVerificationError


MAX_BOARD_RESULTS = 10
_NUMERIC_ID = re.compile(r"[0-9]{4,20}\Z")
_OPAQUE_ID = re.compile(r"[A-Za-z0-9_-]{5,100}\Z")


class SearchClient(Protocol):
    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
    ) -> list[SearchResult]: ...


class PublicPageReader(Protocol):
    def read_page_text(self, url: str) -> tuple[str, str]: ...


@dataclass(frozen=True)
class JobBoardListing:
    provider: str
    external_id: str
    listing_url: str
    title: str
    snippet: str | None
    search_position: int
    activity_state: Literal["closed", "unknown"] = "unknown"
    activity_code: str = "not_checked"
    activity_url: str | None = None


@dataclass(frozen=True)
class JobBoardActivity:
    state: Literal["closed", "unknown"]
    code: str
    checked_url: str | None


@dataclass(frozen=True)
class JobBoardSearch:
    query: str
    raw_result_count: int
    filtered_result_count: int
    listings: tuple[JobBoardListing, ...]


_KARIYER_CLOSED_MARKERS = (
    "this job posting is no longer accepting applications",
    "bu is ilani artik basvuru kabul etmiyor",
    "bu ilan artik basvuru kabul etmiyor",
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
    """Detect explicit closure signals without inferring an active job."""

    def __init__(self, page_reader: PublicPageReader) -> None:
        self._page_reader = page_reader

    def check(self, listing: JobBoardListing) -> JobBoardActivity:
        if listing.provider != "kariyer":
            return JobBoardActivity(
                state="unknown",
                code="provider_not_checked",
                checked_url=None,
            )

        try:
            final_url, page_text = self._page_reader.read_page_text(
                listing.listing_url
            )
        except WebsiteVerificationError:
            return JobBoardActivity(
                state="unknown",
                code="fetch_failed",
                checked_url=None,
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
            )

        if (
            final_listing.provider != listing.provider
            or final_listing.external_id != listing.external_id
        ):
            return JobBoardActivity(
                state="unknown",
                code="redirect_mismatch",
                checked_url=None,
            )

        normalized_text = _marker_text(page_text)
        if any(
            marker in normalized_text
            for marker in _KARIYER_CLOSED_MARKERS
        ):
            return JobBoardActivity(
                state="closed",
                code="kariyer_closed_marker",
                checked_url=final_listing.listing_url,
            )

        return JobBoardActivity(
            state="unknown",
            code="no_closed_marker",
            checked_url=final_listing.listing_url,
        )


def _hostname_belongs_to(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith(f".{domain}")


def _provider(hostname: str) -> str | None:
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

    if provider == "linkedin":
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
    return JobBoardListing(
        provider=provider,
        external_id=external_id,
        listing_url=normalize_public_url(listing_url),
        title=title,
        snippet=snippet,
        search_position=result.position,
    )


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


class JobBoardSearchConnector:
    def __init__(
        self,
        search_client: SearchClient,
        *,
        activity_verifier: JobBoardActivityVerifier | None = None,
    ) -> None:
        self._search_client = search_client
        self._activity_verifier = activity_verifier

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
            if self._activity_verifier is not None:
                activity = self._activity_verifier.check(listing)
                listing = replace(
                    listing,
                    activity_state=activity.state,
                    activity_code=activity.code,
                    activity_url=activity.checked_url,
                )
            listings.append(listing)
        return JobBoardSearch(
            query=query,
            raw_result_count=len(results),
            filtered_result_count=filtered,
            listings=tuple(listings),
        )
