import ipaddress
import re
import unicodedata
from collections.abc import Iterable
from urllib.parse import urlsplit, urlunsplit

from app.discovery.schemas import SearchResult


LEGAL_SUFFIXES = (
    re.compile(r"\s+LİMİTED ŞİRKETİ$", re.IGNORECASE),
    re.compile(r"\s+ANONİM ŞİRKETİ$", re.IGNORECASE),
    re.compile(r"\s+LTD\.?\s*ŞTİ\.?$", re.IGNORECASE),
    re.compile(r"\s+A\.?\s*Ş\.?$", re.IGNORECASE),
)

LINKEDIN_COMPANY_HOST = re.compile(
    r"(?:(?:www|[a-z]{2})\.)?linkedin\.com\Z"
)
LINKEDIN_COMPANY_SLUG = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9-]{0,99}\Z"
)
DENIED_OFFICIAL_WEBSITE_DOMAINS = {
    "apps.apple.com",
    "crunchbase.com",
    "entertech.com.tr",
    "facebook.com",
    "find.com.tr",
    "indeed.com",
    "instagram.com",
    "kamubis.com",
    "kariyer.net",
    "linkedin.com",
    "mukellef.info",
    "play.google.com",
    "rocketreach.co",
    "sayfa.istanbul",
    "twitter.com",
    "x.com",
    "youtube.com",
}


def safe_text(value: object, max_length: int) -> str:
    text = " ".join(str(value or "").split())
    text = "".join(
        character
        for character in text
        if not unicodedata.category(character).startswith("C")
    )
    return text[:max_length]


def clean_company_name(value: object) -> str:
    name = safe_text(value, 500)

    for suffix in LEGAL_SUFFIXES:
        name = suffix.sub("", name)

    name = name.strip()

    if not 1 <= len(name) <= 200:
        raise ValueError("Company name is not suitable for search.")

    return name


def build_company_queries(value: object) -> tuple[str, ...]:
    name = clean_company_name(value)

    return (
        name,
        f'"{name}" teknoloji',
        f'"{name}" şirket',
    )


def normalize_public_url(value: object) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 2048:
        raise ValueError("URL length is invalid.")

    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError("URL contains control characters.")

    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as error:
        raise ValueError("URL cannot be parsed.") from error

    if (
        parsed.scheme.lower() not in {"https", "http"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 80, 443}
    ):
        raise ValueError("URL is not an allowed public web URL.")

    hostname = hostname.rstrip(".").lower()

    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("IP-literal URLs are not allowed.")

    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError("URL hostname is invalid.") from error

    if (
        "." not in ascii_hostname
        or ascii_hostname == "localhost"
        or ascii_hostname.endswith((".localhost", ".local", ".internal"))
    ):
        raise ValueError("URL hostname is not publicly routable.")

    netloc = ascii_hostname
    if port is not None and not (
        parsed.scheme.lower() == "https" and port == 443
    ) and not (
        parsed.scheme.lower() == "http" and port == 80
    ):
        netloc = f"{ascii_hostname}:{port}"

    return urlunsplit(
        (
            parsed.scheme.lower(),
            netloc,
            parsed.path.rstrip("/") or "/",
            parsed.query,
            "",
        )
    )


def _hostname_belongs_to(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith(f".{domain}")


def is_denied_official_website(value: object) -> bool:
    try:
        normalized = normalize_public_url(value)
    except ValueError:
        return True
    hostname = (urlsplit(normalized).hostname or "").lower()
    return any(
        _hostname_belongs_to(hostname, domain)
        for domain in DENIED_OFFICIAL_WEBSITE_DOMAINS
    )


def normalize_linkedin_company_url(value: object) -> str:
    """Validate and canonicalize a LinkedIn company profile URL."""
    normalized = normalize_public_url(value)
    parsed = urlsplit(normalized)
    hostname = (parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme != "https"
        or LINKEDIN_COMPANY_HOST.fullmatch(hostname) is None
    ):
        raise ValueError("LinkedIn company URL is invalid.")

    segments = parsed.path.strip("/").split("/")
    if len(segments) < 2 or segments[0].lower() != "company":
        raise ValueError("LinkedIn company URL is invalid.")
    slug = segments[1]
    if LINKEDIN_COMPANY_SLUG.fullmatch(slug) is None:
        raise ValueError("LinkedIn company URL is invalid.")

    return f"https://www.linkedin.com/company/{slug}/"


def allowed_candidate_urls(
    results: Iterable[SearchResult],
) -> set[str]:
    allowed: set[str] = set()

    for result in results:
        url = normalize_public_url(result.url)
        parsed = urlsplit(url)
        root = urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))

        allowed.add(url)
        allowed.add(normalize_public_url(root))

    return allowed
