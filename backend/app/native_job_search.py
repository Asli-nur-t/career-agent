"""Build safe, provider-specific search links for manual browser searches.

The client may choose a role, location and source groups, but it can never
provide a destination host or path.  Every returned URL is built from this
server-side allowlist and validated again before it leaves the module.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass
from urllib.parse import quote, urlencode, urlsplit


SOURCE_GROUPS = {
    "linkedin": ("linkedin",),
    "kariyer": ("kariyer",),
    "indeed": ("indeed",),
    "glassdoor": ("glassdoor",),
    "ats": (),
    "turkey_tech": ("techcareer", "yenibiris", "secretcv", "toptalent"),
    "remote_feeds": ("weworkremotely", "remoteok", "remotive", "jobicy"),
}

PROVIDER_LABELS = {
    "linkedin": "LinkedIn",
    "kariyer": "Kariyer.net",
    "indeed": "Indeed",
    "glassdoor": "Glassdoor",
    "techcareer": "Techcareer.net",
    "yenibiris": "Yenibiriş",
    "secretcv": "SecretCV",
    "toptalent": "Toptalent",
    "weworkremotely": "We Work Remotely",
    "remoteok": "Remote OK",
    "remotive": "Remotive",
    "jobicy": "Jobicy",
}

ALLOWED_DESTINATIONS = {
    "linkedin": ("www.linkedin.com", "/jobs/search/"),
    "kariyer": ("www.kariyer.net", "/is-ilanlari/"),
    "indeed": ("tr.indeed.com", "/jobs"),
    "glassdoor": ("www.glassdoor.com", "/Job/jobs.htm"),
    "techcareer": ("www.techcareer.net", "/jobs"),
    "yenibiris": ("www.yenibiris.com", "/is-ilanlari/"),
    "secretcv": ("www.secretcv.com", "/is-ilanlari/"),
    "toptalent": ("toptalent.co", "/is-ilanlari"),
    "weworkremotely": ("weworkremotely.com", "/remote-jobs"),
    "remoteok": ("remoteok.com", "/remote-"),
    "remotive": ("remotive.com", "/remote-jobs"),
    "jobicy": ("jobicy.com", "/jobs"),
}


@dataclass(frozen=True)
class NativeSearchLink:
    provider: str
    label: str
    url: str
    query_prefilled: bool
    location_prefilled: bool
    note: str

    def to_dict(self) -> dict[str, str | bool]:
        return asdict(self)


def _clean_term(value: str | None, *, field: str) -> str:
    if value is None:
        return ""
    normalized = unicodedata.normalize("NFKC", value)
    if any(unicodedata.category(char).startswith("C") for char in normalized):
        raise ValueError(f"{field}_invalid")
    cleaned = " ".join(normalized.split())
    if not cleaned or len(cleaned) > 100:
        raise ValueError(f"{field}_invalid")
    return cleaned


def _slug(value: str) -> str:
    translated = value.casefold().replace("ı", "i")
    ascii_value = unicodedata.normalize("NFKD", translated).encode(
        "ascii", "ignore"
    ).decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    if not slug:
        raise ValueError("role_invalid")
    return slug[:80].rstrip("-")


def _validate_url(provider: str, url: str) -> None:
    expected_host, expected_path = ALLOWED_DESTINATIONS[provider]
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != expected_host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
        or not parsed.path.startswith(expected_path)
        or len(url) > 2048
    ):
        raise ValueError("native_search_url_invalid")


def _build_url(provider: str, role: str, location: str) -> NativeSearchLink:
    role_slug = _slug(role)
    query_prefilled = True
    location_prefilled = False
    note = "Rol hazır; konum filtresini kaynak sitede kontrol et."

    if provider == "linkedin":
        params = {"keywords": role}
        if location:
            params["location"] = location
            location_prefilled = True
        url = "https://www.linkedin.com/jobs/search/?" + urlencode(params)
        note = "Rol ve varsa konum hazır; giriş durumuna göre sonuç değişebilir."
    elif provider == "kariyer":
        path_term = quote("+".join(role_slug.split("-")), safe="")
        url = f"https://www.kariyer.net/is-ilanlari/{path_term}"
    elif provider == "indeed":
        params = {"q": role}
        if location:
            params["l"] = location
            location_prefilled = True
        url = "https://tr.indeed.com/jobs?" + urlencode(params)
        note = "Rol ve varsa konum hazır; ilan tarihini sitede seç."
    elif provider == "glassdoor":
        url = "https://www.glassdoor.com/Job/jobs.htm?" + urlencode(
            {"sc.keyword": role}
        )
        note = "Rol hazır; konum ve tarih filtresini sitede seç."
    elif provider == "techcareer":
        url = "https://www.techcareer.net/jobs?" + urlencode(
            {
                "jobs[isCompleted]": "false",
                "jobs[page]": "1",
                "jobs[search][keyword]": role,
                "jobs[search][select]": "position",
            }
        )
        note = "Rol ve açık ilan filtresi hazır; konumu sitede seç."
    elif provider == "yenibiris":
        url = f"https://www.yenibiris.com/is-ilanlari/{role_slug}"
    elif provider == "secretcv":
        url = f"https://www.secretcv.com/is-ilanlari/{role_slug}-is-ilanlari"
    elif provider == "toptalent":
        url = "https://toptalent.co/is-ilanlari"
        query_prefilled = False
        note = "Kaynak açılır; rol ve konum filtresini sitede tamamla."
    elif provider == "weworkremotely":
        url = "https://weworkremotely.com/remote-jobs"
        query_prefilled = False
        note = "Remote ilan sayfası açılır; rolü kaynak sitede ara."
    elif provider == "remoteok":
        url = f"https://remoteok.com/remote-{quote(role_slug, safe='')}-jobs"
        note = "Remote rol etiketi hazır; bölge uygunluğunu ilanda doğrula."
    elif provider == "remotive":
        url = "https://remotive.com/remote-jobs?" + urlencode({"search": role})
        note = "Remote rol araması hazır; ülke uygunluğunu ilanda doğrula."
    elif provider == "jobicy":
        url = "https://jobicy.com/jobs?" + urlencode({"search": role})
        note = "Remote rol araması hazır; ülke uygunluğunu ilanda doğrula."
    else:  # pragma: no cover - callers only use the static catalog
        raise ValueError("native_search_provider_invalid")

    _validate_url(provider, url)
    return NativeSearchLink(
        provider=provider,
        label=PROVIDER_LABELS[provider],
        url=url,
        query_prefilled=query_prefilled,
        location_prefilled=location_prefilled,
        note=note,
    )


def build_native_search_links(
    *,
    role: str,
    location: str | None,
    sources: list[str],
) -> tuple[list[NativeSearchLink], list[str]]:
    """Return deduplicated allowlisted links and unsupported source groups."""

    cleaned_role = _clean_term(role, field="role")
    cleaned_location = (
        _clean_term(location, field="location") if location else ""
    )
    providers: list[str] = []
    unavailable: list[str] = []
    for source in sources:
        members = SOURCE_GROUPS.get(source)
        if members is None:
            raise ValueError("native_search_source_invalid")
        if not members:
            unavailable.append(source)
        for provider in members:
            if provider not in providers:
                providers.append(provider)

    links = [
        _build_url(provider, cleaned_role, cleaned_location)
        for provider in providers
    ]
    return links, unavailable


def build_provider_search_links(
    *,
    role: str,
    location: str | None,
    providers: list[str],
) -> list[NativeSearchLink]:
    """Build links for an explicit, allowlisted provider selection."""

    cleaned_role = _clean_term(role, field="role")
    cleaned_location = _clean_term(location, field="location") if location else ""
    if not providers or len(providers) > len(PROVIDER_LABELS):
        raise ValueError("native_search_provider_invalid")

    unique: list[str] = []
    for provider in providers:
        if provider not in PROVIDER_LABELS:
            raise ValueError("native_search_provider_invalid")
        if provider not in unique:
            unique.append(provider)
    return [
        _build_url(provider, cleaned_role, cleaned_location)
        for provider in unique
    ]
