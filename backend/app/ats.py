"""Bounded clients for public ATS job-board APIs."""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit

import httpx

from app.career_sources import classify_career_source
from app.discovery.safety import normalize_public_url, safe_text


MAX_RESPONSE_BYTES = 5_000_000
MAX_JOBS = 1_000
BOARD_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,99}\Z")


class ATSFetchError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("ATS fetch failed.")
        self.code = code


class _TextParser(HTMLParser):
    _IGNORED = {"script", "style", "noscript", "svg"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._parts: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        if tag.lower() in self._IGNORED:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._IGNORED and self._ignored_depth:
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self._parts.append(data)

    def text(self) -> str:
        return " ".join(self._parts)


@dataclass(frozen=True)
class NormalizedJob:
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
    content_hash: str


def _optional_text(value: object, max_length: int) -> str | None:
    cleaned = safe_text(value, max_length)
    return cleaned or None


def _html_text(value: object) -> str | None:
    raw = str(value or "")[:500_000]
    parser = _TextParser()
    try:
        parser.feed(raw)
        parser.close()
    except Exception as error:
        raise ATSFetchError("invalid_job_html") from error
    return _optional_text(parser.text(), 100_000)


def _public_https_url(value: object) -> str | None:
    try:
        normalized = normalize_public_url(value)
    except ValueError:
        return None
    if urlsplit(normalized).scheme != "https":
        return None
    return normalized


def _iso_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) > 100:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _millisecond_datetime(value: object) -> datetime | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return datetime.fromtimestamp(value / 1_000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _content_hash(fields: dict[str, object]) -> str:
    encoded = json.dumps(
        fields,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _job(
    *,
    external_id: object,
    job_url: object,
    apply_url: object,
    title: object,
    location: object = None,
    department: object = None,
    employment_type: object = None,
    description_text: str | None = None,
    is_remote: bool | None = None,
    published_at: datetime | None = None,
) -> NormalizedJob | None:
    clean_id = safe_text(external_id, 500)
    clean_title = safe_text(title, 500)
    clean_job_url = _public_https_url(job_url)
    clean_apply_url = _public_https_url(apply_url)
    if not clean_id or not clean_title or not clean_job_url:
        return None
    clean_location = _optional_text(location, 500)
    clean_department = _optional_text(department, 300)
    clean_employment_type = _optional_text(employment_type, 100)
    fields: dict[str, object] = {
        "external_id": clean_id,
        "job_url": clean_job_url,
        "apply_url": clean_apply_url,
        "title": clean_title,
        "location": clean_location,
        "department": clean_department,
        "employment_type": clean_employment_type,
        "description_text": description_text,
        "is_remote": is_remote,
        "published_at": published_at.isoformat() if published_at else None,
    }
    return NormalizedJob(
        external_id=clean_id,
        job_url=clean_job_url,
        apply_url=clean_apply_url,
        title=clean_title,
        location=clean_location,
        department=clean_department,
        employment_type=clean_employment_type,
        description_text=description_text,
        is_remote=is_remote,
        published_at=published_at,
        content_hash=_content_hash(fields),
    )


def _board_slug(source_url: str, ats_type: str) -> str:
    try:
        candidate = classify_career_source(source_url)
    except ValueError as error:
        raise ATSFetchError("invalid_source_url") from error
    if (
        candidate is None
        or candidate.source_type != "ats"
        or candidate.ats_type != ats_type
    ):
        raise ATSFetchError("source_type_mismatch")
    slug = urlsplit(candidate.source_url).path.strip("/").split("/", 1)[0]
    if not BOARD_SLUG.fullmatch(slug):
        raise ATSFetchError("invalid_board_slug")
    return slug


class PublicATSClient:
    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        if not 1 <= timeout_seconds <= 30:
            raise ValueError("ATS timeout is invalid.")
        if not 100_000 <= max_response_bytes <= MAX_RESPONSE_BYTES:
            raise ValueError("ATS response limit is invalid.")
        self._max_response_bytes = max_response_bytes
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "User-Agent": "CareerAgent/0.1 public-ats-reader",
            },
        )

    def __enter__(self) -> "PublicATSClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def fetch(self, source_url: str, ats_type: str) -> list[NormalizedJob]:
        slug = _board_slug(source_url, ats_type)
        escaped = quote(slug, safe="")
        if ats_type == "greenhouse":
            endpoint = (
                "https://boards-api.greenhouse.io/v1/boards/"
                f"{escaped}/jobs?content=true"
            )
        elif ats_type == "lever":
            api_host = (
                "api.eu.lever.co"
                if urlsplit(source_url).hostname == "jobs.eu.lever.co"
                else "api.lever.co"
            )
            endpoint = f"https://{api_host}/v0/postings/{escaped}?mode=json"
        elif ats_type == "ashby":
            endpoint = (
                "https://api.ashbyhq.com/posting-api/job-board/"
                f"{escaped}"
            )
        else:
            raise ATSFetchError("unsupported_ats")
        payload = self._get_json(endpoint)
        if ats_type == "greenhouse":
            return self._greenhouse(payload)
        if ats_type == "lever":
            return self._lever(payload)
        return self._ashby(payload)

    def _get_json(self, endpoint: str) -> object:
        try:
            with self._client.stream("GET", endpoint) as response:
                if response.status_code == 429:
                    raise ATSFetchError("rate_limited")
                if not 200 <= response.status_code < 300:
                    raise ATSFetchError("http_error")
                media_type = response.headers.get("Content-Type", "").split(
                    ";", 1
                )[0].strip().lower()
                if media_type not in {"application/json", "text/json"}:
                    raise ATSFetchError("invalid_content_type")
                declared = response.headers.get("Content-Length")
                if declared:
                    try:
                        length = int(declared)
                    except ValueError as error:
                        raise ATSFetchError("invalid_content_length") from error
                    if length < 0 or length > self._max_response_bytes:
                        raise ATSFetchError("response_too_large")
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > self._max_response_bytes:
                        raise ATSFetchError("response_too_large")
                    chunks.append(chunk)
        except ATSFetchError:
            raise
        except httpx.TimeoutException as error:
            raise ATSFetchError("timeout") from error
        except httpx.RequestError as error:
            raise ATSFetchError("connection_error") from error
        try:
            return json.loads(b"".join(chunks))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ATSFetchError("invalid_json") from error

    @staticmethod
    def _greenhouse(payload: object) -> list[NormalizedJob]:
        if not isinstance(payload, dict) or not isinstance(
            payload.get("jobs"), list
        ):
            raise ATSFetchError("invalid_response")
        if len(payload["jobs"]) > MAX_JOBS:
            raise ATSFetchError("too_many_jobs")
        jobs: list[NormalizedJob] = []
        for item in payload["jobs"][:MAX_JOBS]:
            if not isinstance(item, dict):
                continue
            location = item.get("location")
            departments = item.get("departments")
            job = _job(
                external_id=item.get("id"),
                job_url=item.get("absolute_url"),
                apply_url=item.get("absolute_url"),
                title=item.get("title"),
                location=(
                    location.get("name")
                    if isinstance(location, dict)
                    else None
                ),
                department=(
                    departments[0].get("name")
                    if isinstance(departments, list)
                    and departments
                    and isinstance(departments[0], dict)
                    else None
                ),
                description_text=_html_text(item.get("content")),
                published_at=_iso_datetime(item.get("first_published")),
            )
            if job:
                jobs.append(job)
        return jobs

    @staticmethod
    def _lever(payload: object) -> list[NormalizedJob]:
        if not isinstance(payload, list):
            raise ATSFetchError("invalid_response")
        if len(payload) > MAX_JOBS:
            raise ATSFetchError("too_many_jobs")
        jobs: list[NormalizedJob] = []
        for item in payload[:MAX_JOBS]:
            if not isinstance(item, dict):
                continue
            categories = item.get("categories")
            categories = categories if isinstance(categories, dict) else {}
            workplace = _optional_text(item.get("workplaceType"), 100)
            job = _job(
                external_id=item.get("id"),
                job_url=item.get("hostedUrl"),
                apply_url=item.get("applyUrl"),
                title=item.get("text"),
                location=categories.get("location"),
                department=categories.get("team"),
                employment_type=categories.get("commitment"),
                description_text=(
                    _optional_text(item.get("descriptionPlain"), 100_000)
                    or _html_text(item.get("description"))
                ),
                is_remote=(
                    workplace.casefold() == "remote" if workplace else None
                ),
                published_at=_millisecond_datetime(item.get("createdAt")),
            )
            if job:
                jobs.append(job)
        return jobs

    @staticmethod
    def _ashby(payload: object) -> list[NormalizedJob]:
        if not isinstance(payload, dict) or not isinstance(
            payload.get("jobs"), list
        ):
            raise ATSFetchError("invalid_response")
        if len(payload["jobs"]) > MAX_JOBS:
            raise ATSFetchError("too_many_jobs")
        jobs: list[NormalizedJob] = []
        for item in payload["jobs"][:MAX_JOBS]:
            if not isinstance(item, dict):
                continue
            remote = item.get("isRemote")
            job = _job(
                external_id=item.get("id") or item.get("jobUrl"),
                job_url=item.get("jobUrl"),
                apply_url=item.get("applyUrl"),
                title=item.get("title"),
                location=item.get("location"),
                department=item.get("department"),
                employment_type=item.get("employmentType"),
                description_text=(
                    _optional_text(item.get("descriptionPlain"), 100_000)
                    or _html_text(item.get("descriptionHtml"))
                ),
                is_remote=remote if isinstance(remote, bool) else None,
                published_at=_iso_datetime(item.get("publishedAt")),
            )
            if job:
                jobs.append(job)
        return jobs
