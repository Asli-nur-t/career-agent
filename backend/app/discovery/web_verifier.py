import http.client
import ipaddress
import re
import socket
import ssl
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from app.company_names import canonical_company_name
from app.discovery.safety import normalize_public_url, safe_text
from app.discovery.schemas import SearchResult


ALLOWED_CONTENT_TYPES = {
    "application/xhtml+xml",
    "text/html",
    "text/plain",
}

REDIRECT_STATUSES = {301, 302, 303, 307, 308}

EVIDENCE_PATH_KEYWORDS = (
    "contact",
    "iletisim",
    "about",
    "hakkimizda",
    "hakkimizda",
    "legal",
    "terms",
    "privacy",
)


class WebsiteVerificationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Website verification failed.")
        self.code = code


@dataclass(frozen=True, slots=True)
class VerificationResult:
    verified: bool
    matched_url: str | None
    code: str


@dataclass(frozen=True, slots=True)
class _FetchResponse:
    status: int
    location: str | None
    content_type: str
    charset: str
    body: bytes


class _VisibleTextParser(HTMLParser):
    _IGNORED_TAGS = {"script", "style", "noscript", "svg"}

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
        if tag.lower() in self._IGNORED_TAGS:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if (
            tag.lower() in self._IGNORED_TAGS
            and self._ignored_depth > 0
        ):
            self._ignored_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0:
            self._parts.append(data)

    def text(self) -> str:
        return " ".join(self._parts)


class _AnchorParser(HTMLParser):
    def __init__(self, *, max_anchors: int = 200) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self._max_anchors = max_anchors

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        if tag.lower() != "a" or len(self.links) >= self._max_anchors:
            return
        for name, value in attrs:
            if name.lower() == "href" and value and len(value) <= 2048:
                self.links.append(value)
                break


def _comparison_text(value: str) -> str:
    value = value.replace("ı", "i").replace("İ", "I")
    value = unicodedata.normalize("NFKD", value).casefold()

    characters = [
        character
        if character.isalnum()
        else " "
        for character in value
        if not unicodedata.combining(character)
    ]

    return " ".join("".join(characters).split())


def _legal_comparison_text(value: str) -> str:
    normalized = _comparison_text(value)

    normalized = re.sub(
        r"\b(?:ltd|limited)\s+(?:sti|sirketi)\b",
        "limited sirketi",
        normalized,
    )
    normalized = re.sub(
        r"\b(?:a\s*s|anonim\s+sirketi)\b",
        "anonim sirketi",
        normalized,
    )

    return normalized


def _site_key(url: str) -> str:
    hostname = (urlsplit(url).hostname or "").lower()

    if hostname.startswith("www."):
        hostname = hostname[4:]

    return hostname


def _is_same_site(first_url: str, second_url: str) -> bool:
    return _site_key(first_url) == _site_key(second_url)


def _path_priority(url: str) -> tuple[int, str]:
    path = urlsplit(url).path.lower()

    for index, keyword in enumerate(EVIDENCE_PATH_KEYWORDS):
        if keyword in path:
            return index, path

    return len(EVIDENCE_PATH_KEYWORDS), path


class SafeWebsiteVerifier:
    def __init__(
        self,
        *,
        timeout_seconds: float = 10.0,
        max_response_bytes: int = 1_000_000,
        max_redirects: int = 3,
        max_pages: int = 4,
    ) -> None:
        if (
            not 1.0 <= timeout_seconds <= 30.0
            or not 1_024 <= max_response_bytes <= 2_000_000
            or not 0 <= max_redirects <= 5
            or not 1 <= max_pages <= 5
        ):
            raise ValueError("Verifier configuration is invalid.")

        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._max_redirects = max_redirects
        self._max_pages = max_pages
        self._ssl_context = ssl.create_default_context()
        self._ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2

    def verify(
        self,
        *,
        company_name: str,
        official_website: str,
        search_results: list[SearchResult],
    ) -> VerificationResult:
        company_key = _legal_comparison_text(
            canonical_company_name(safe_text(company_name, 500))
        )
        official_website = normalize_public_url(official_website)

        if not company_key:
            raise ValueError("Company name is invalid.")

        if urlsplit(official_website).scheme != "https":
            return VerificationResult(
                verified=False,
                matched_url=None,
                code="https_required",
            )

        candidates: set[str] = {official_website}

        for result in search_results:
            try:
                result_url = normalize_public_url(result.url)
            except ValueError:
                continue

            if (
                urlsplit(result_url).scheme == "https"
                and _is_same_site(official_website, result_url)
            ):
                candidates.add(result_url)

        ordered_candidates = sorted(candidates, key=_path_priority)
        fetched_page = False

        for candidate_url in ordered_candidates[:self._max_pages]:
            try:
                final_url, page_text = self._fetch_text(candidate_url)
            except WebsiteVerificationError:
                continue

            fetched_page = True

            if company_key in _legal_comparison_text(page_text):
                return VerificationResult(
                    verified=True,
                    matched_url=final_url,
                    code="verified",
                )

        return VerificationResult(
            verified=False,
            matched_url=None,
            code=(
                "no_exact_name_match"
                if fetched_page
                else "fetch_failed"
            ),
        )

    def find_page_links(
        self,
        official_website: str,
        *,
        max_links: int = 200,
    ) -> tuple[str, tuple[str, ...]]:
        """Extract untrusted links from a verified company's landing page.

        The caller must confirm the company profile is verified before use.
        """
        if not 1 <= max_links <= 200:
            raise ValueError("Link limit is invalid.")
        final_url, media_type, body = self._fetch_document(official_website)
        if media_type not in {"text/html", "application/xhtml+xml"}:
            return final_url, ()

        parser = _AnchorParser()
        try:
            parser.feed(body)
            parser.close()
        except Exception as error:
            raise WebsiteVerificationError("invalid_html") from error

        links: list[str] = []
        for href in parser.links:
            try:
                link = urljoin(final_url, href)
            except ValueError:
                continue
            if len(link) <= 2048 and link not in links:
                links.append(link)
            if len(links) >= max_links:
                break
        return final_url, tuple(links)

    def read_page_text(self, url: str) -> tuple[str, str]:
        """Read visible text through the verifier's SSRF-safe fetch path."""
        return self._fetch_text(url)

    def _fetch_text(self, initial_url: str) -> tuple[str, str]:
        current_url, media_type, decoded = self._fetch_document(initial_url)
        if media_type == "text/plain":
            return current_url, safe_text(decoded, 1_000_000)

        parser = _VisibleTextParser()
        try:
            parser.feed(decoded)
            parser.close()
        except Exception as error:
            raise WebsiteVerificationError("invalid_html") from error

        return current_url, safe_text(parser.text(), 1_000_000)

    def _fetch_document(self, initial_url: str) -> tuple[str, str, str]:
        initial_url = normalize_public_url(initial_url)
        original_site = _site_key(initial_url)
        current_url = initial_url

        for redirect_count in range(self._max_redirects + 1):
            response = self._request_once(current_url)

            if response.status in REDIRECT_STATUSES:
                if (
                    redirect_count >= self._max_redirects
                    or not response.location
                ):
                    raise WebsiteVerificationError(
                        "redirect_rejected"
                    )

                try:
                    next_url = normalize_public_url(
                        urljoin(current_url, response.location)
                    )
                except ValueError as error:
                    raise WebsiteVerificationError(
                        "redirect_rejected"
                    ) from error

                if (
                    urlsplit(next_url).scheme != "https"
                    or _site_key(next_url) != original_site
                ):
                    raise WebsiteVerificationError(
                        "cross_site_redirect"
                    )

                current_url = next_url
                continue

            if response.status != 200:
                raise WebsiteVerificationError("http_status")

            media_type = response.content_type.split(";", 1)[0].strip()

            if media_type not in ALLOWED_CONTENT_TYPES:
                raise WebsiteVerificationError(
                    "unsupported_content_type"
                )

            try:
                decoded = response.body.decode(
                    response.charset or "utf-8",
                    errors="replace",
                )
            except LookupError:
                decoded = response.body.decode(
                    "utf-8",
                    errors="replace",
                )

            return current_url, media_type, decoded

        raise WebsiteVerificationError("redirect_rejected")

    def _request_once(self, url: str) -> _FetchResponse:
        url = normalize_public_url(url)
        parsed = urlsplit(url)
        hostname = parsed.hostname

        if (
            parsed.scheme != "https"
            or not hostname
            or parsed.port not in {None, 443}
        ):
            raise WebsiteVerificationError("https_required")

        target = urlunsplit(
            ("", "", parsed.path or "/", parsed.query, "")
        )

        try:
            target.encode("ascii")
        except UnicodeEncodeError as error:
            raise WebsiteVerificationError(
                "non_ascii_request_target"
            ) from error

        addresses = self._resolve_public_addresses(hostname)
        secure_socket = self._connect_tls(hostname, addresses)

        connection = http.client.HTTPSConnection(
            hostname,
            port=443,
            timeout=self._timeout_seconds,
            context=self._ssl_context,
        )
        connection.sock = secure_socket

        try:
            connection.request(
                "GET",
                target,
                headers={
                    "Accept": "text/html,application/xhtml+xml,text/plain",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                    "User-Agent": "CareerAgent/0.1 source-verifier",
                },
            )
            response = connection.getresponse()

            content_length = response.getheader("Content-Length")
            if content_length:
                try:
                    declared_length = int(content_length)
                except ValueError as error:
                    raise WebsiteVerificationError(
                        "invalid_content_length"
                    ) from error

                if (
                    declared_length < 0
                    or declared_length > self._max_response_bytes
                ):
                    raise WebsiteVerificationError(
                        "response_too_large"
                    )

            body = response.read(self._max_response_bytes + 1)

            if len(body) > self._max_response_bytes:
                raise WebsiteVerificationError(
                    "response_too_large"
                )

            return _FetchResponse(
                status=response.status,
                location=response.getheader("Location"),
                content_type=(
                    response.getheader("Content-Type") or ""
                ).lower(),
                charset=response.headers.get_content_charset() or "utf-8",
                body=body,
            )

        except WebsiteVerificationError:
            raise
        except (
            http.client.HTTPException,
            OSError,
            ssl.SSLError,
        ) as error:
            raise WebsiteVerificationError(
                "request_failed"
            ) from error
        finally:
            connection.close()

    @staticmethod
    def _resolve_public_addresses(
        hostname: str,
    ) -> tuple[str, ...]:
        try:
            records = socket.getaddrinfo(
                hostname,
                443,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
        except socket.gaierror as error:
            raise WebsiteVerificationError(
                "dns_failed"
            ) from error

        addresses: list[str] = []

        for record in records:
            address = record[4][0].split("%", 1)[0]

            try:
                parsed_address = ipaddress.ip_address(address)
            except ValueError as error:
                raise WebsiteVerificationError(
                    "invalid_dns_address"
                ) from error

            if not parsed_address.is_global:
                raise WebsiteVerificationError(
                    "private_address_blocked"
                )

            normalized = str(parsed_address)
            if normalized not in addresses:
                addresses.append(normalized)

        if not addresses:
            raise WebsiteVerificationError("dns_failed")

        return tuple(addresses)

    def _connect_tls(
        self,
        hostname: str,
        addresses: tuple[str, ...],
    ) -> ssl.SSLSocket:
        for address in addresses[:6]:
            raw_socket: socket.socket | None = None

            try:
                raw_socket = socket.create_connection(
                    (address, 443),
                    timeout=self._timeout_seconds,
                )
                return self._ssl_context.wrap_socket(
                    raw_socket,
                    server_hostname=hostname,
                )
            except (OSError, ssl.SSLError):
                if raw_socket is not None:
                    raw_socket.close()

        raise WebsiteVerificationError("connection_failed")
