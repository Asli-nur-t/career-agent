"""Normalize public search results from supported job boards.

This module does not crawl job-board pages or bypass authentication. It only
accepts URLs returned by the configured search provider and keeps all results
in a review queue.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from app.discovery.safety import (
    clean_company_name,
    normalize_public_url,
    safe_text,
)
from app.discovery.schemas import SearchResult


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


@dataclass(frozen=True)
class JobBoardListing:
    provider: str
    external_id: str
    listing_url: str
    title: str
    snippet: str | None
    search_position: int


@dataclass(frozen=True)
class JobBoardSearch:
    query: str
    raw_result_count: int
    listings: tuple[JobBoardListing, ...]


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
    def __init__(self, search_client: SearchClient) -> None:
        self._search_client = search_client

    def search(
        self,
        company_name: object,
        *,
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
        for result in results:
            try:
                listing = normalize_job_board_result(result)
            except ValueError:
                continue
            key = (listing.provider, listing.external_id)
            if key in seen:
                continue
            seen.add(key)
            listings.append(listing)
        return JobBoardSearch(
            query=query,
            raw_result_count=len(results),
            listings=tuple(listings),
        )
