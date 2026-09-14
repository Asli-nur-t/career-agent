import httpx

from app.discovery.safety import (
    normalize_public_url,
    safe_text,
)
from app.discovery.schemas import SearchResult


class SerperError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Serper search failed.")
        self.code = code


class SerperClient:
    _ENDPOINT = "https://google.serper.dev/search"

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 15.0,
    ) -> None:
        api_key = api_key.strip()

        if (
            not 20 <= len(api_key) <= 512
            or not api_key.isascii()
            or any(character.isspace() for character in api_key)
        ):
            raise ValueError("SERPER_API_KEY is invalid.")

        self._api_key = api_key
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            headers={
                "X-API-KEY": api_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    def __enter__(self) -> "SerperClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def search(
        self,
        query: str,
        *,
        max_results: int = 10,
    ) -> list[SearchResult]:
        query = safe_text(query, 500)

        if not query or not 1 <= max_results <= 10:
            raise ValueError("Search parameters are invalid.")

        try:
            response = self._client.post(
                self._ENDPOINT,
                json={
                    "q": query,
                    "gl": "tr",
                    "hl": "tr",
                    "num": max_results,
                },
            )
        except httpx.TimeoutException as error:
            raise SerperError("timeout") from error
        except httpx.RequestError as error:
            raise SerperError("connection_error") from error

        if response.status_code in {401, 403}:
            raise SerperError("authentication_error")
        if response.status_code == 429:
            raise SerperError("rate_limited")
        if not 200 <= response.status_code < 300:
            raise SerperError("http_error")

        try:
            payload = response.json()
        except ValueError as error:
            raise SerperError("invalid_json") from error

        organic = payload.get("organic")
        if organic is None:
            return []
        if not isinstance(organic, list):
            raise SerperError("invalid_response")

        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for item in organic:
            if not isinstance(item, dict):
                continue

            try:
                url = normalize_public_url(item.get("link"))
            except ValueError:
                continue

            if url in seen_urls:
                continue

            title = safe_text(item.get("title"), 300)
            if not title:
                continue

            seen_urls.add(url)
            results.append(
                SearchResult(
                    title=title,
                    url=url,
                    snippet=safe_text(item.get("snippet"), 1000),
                    position=len(results) + 1,
                )
            )

            if len(results) >= max_results:
                break

        return results
