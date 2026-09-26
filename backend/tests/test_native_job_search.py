import os
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_DB_PASSWORD", "test-only-password")

from app.main import app
from app.native_job_search import (
    ALLOWED_DESTINATIONS,
    build_native_search_links,
)


TOKEN = "a" * 64


def _client() -> TestClient:
    return TestClient(app, base_url="http://localhost")


def _headers() -> dict[str, str]:
    return {"X-Operator-Token": TOKEN}


def test_all_market_source_groups_expand_to_allowlisted_sites() -> None:
    links, unavailable = build_native_search_links(
        role="Software Engineer",
        location="İstanbul, Türkiye",
        sources=[
            "linkedin",
            "kariyer",
            "indeed",
            "glassdoor",
            "ats",
            "turkey_tech",
            "remote_feeds",
        ],
    )

    assert {link.provider for link in links} == set(ALLOWED_DESTINATIONS)
    assert unavailable == ["ats"]
    for link in links:
        host, path_prefix = ALLOWED_DESTINATIONS[link.provider]
        parsed = urlsplit(link.url)
        assert parsed.scheme == "https"
        assert parsed.hostname == host
        assert parsed.path.startswith(path_prefix)
        assert parsed.username is None
        assert parsed.fragment == ""


def test_search_terms_are_encoded_and_never_become_destinations() -> None:
    hostile = 'AI Engineer https://evil.example/"<script>'
    links, _ = build_native_search_links(
        role=hostile,
        location="İstanbul & Remote",
        sources=["linkedin", "indeed", "turkey_tech"],
    )

    assert all(urlsplit(link.url).hostname != "evil.example" for link in links)
    assert all("<script>" not in link.url for link in links)
    linkedin = next(link for link in links if link.provider == "linkedin")
    query = parse_qs(urlsplit(linkedin.url).query)
    assert query["keywords"] == [hostile]
    assert query["location"] == ["İstanbul & Remote"]


def test_control_characters_fail_closed() -> None:
    with pytest.raises(ValueError, match="role_invalid"):
        build_native_search_links(
            role="Software\x00Engineer",
            location=None,
            sources=["linkedin"],
        )


def test_native_search_endpoint_requires_authentication(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    response = _client().post(
        "/operator/native-search-links",
        json={"role": "Software Engineer", "sources": ["linkedin"]},
    )

    assert response.status_code == 401


def test_native_search_endpoint_returns_expanded_links(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    response = _client().post(
        "/operator/native-search-links",
        headers=_headers(),
        json={
            "role": "Backend Engineer",
            "location": "Türkiye",
            "sources": ["turkey_tech", "remote_feeds", "ats"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["links"]) == 8
    assert payload["unavailable_sources"] == ["ats"]
    assert {item["provider"] for item in payload["links"]} == {
        "techcareer",
        "yenibiris",
        "secretcv",
        "toptalent",
        "weworkremotely",
        "remoteok",
        "remotive",
        "jobicy",
    }


def test_native_search_endpoint_rejects_unknown_source(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    response = _client().post(
        "/operator/native-search-links",
        headers=_headers(),
        json={"role": "Engineer", "sources": ["evil_source"]},
    )

    assert response.status_code == 422
