from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.company_profile_search_service import (
    CompanyProfileSearchError,
    _assert_search_allowed,
    discover_company_profile,
)
from app.discovery.schemas import CompanyAssessment


def test_verified_company_profile_cannot_be_searched_again() -> None:
    company_id = uuid4()
    session = MagicMock()
    session.__enter__.return_value = session
    session.execute.return_value = SimpleNamespace(
        one_or_none=lambda: (company_id, "verified")
    )

    with patch(
        "app.company_profile_search_service.Session",
        return_value=session,
    ):
        with pytest.raises(
            CompanyProfileSearchError,
            match="Company profile search failed",
        ) as caught:
            _assert_search_allowed(
                MagicMock(),
                company_id,
                now=datetime.now(timezone.utc),
            )

    assert caught.value.code == "verified_profile_protected"
    session.scalar.assert_not_called()


def test_recent_search_attempt_enforces_cooldown() -> None:
    company_id = uuid4()
    now = datetime.now(timezone.utc)
    session = MagicMock()
    session.__enter__.return_value = session
    session.execute.return_value = SimpleNamespace(
        one_or_none=lambda: (company_id, "not_found")
    )
    session.scalar.return_value = now - timedelta(minutes=2)

    with patch(
        "app.company_profile_search_service.Session",
        return_value=session,
    ):
        with pytest.raises(CompanyProfileSearchError) as caught:
            _assert_search_allowed(MagicMock(), company_id, now=now)

    assert caught.value.code == "company_search_cooldown"


def test_search_fails_closed_without_serper_key(monkeypatch) -> None:
    monkeypatch.delenv("SERPER_API_KEY", raising=False)

    with patch(
        "app.company_profile_search_service._assert_search_allowed"
    ):
        with pytest.raises(CompanyProfileSearchError) as caught:
            discover_company_profile(MagicMock(), company_id=uuid4())

    assert caught.value.code == "serper_not_configured"


def test_successful_search_returns_only_bounded_summary(monkeypatch) -> None:
    monkeypatch.setenv("SERPER_API_KEY", "test-serper-key")
    company_id = uuid4()
    assessment = CompanyAssessment(
        company_name="Acme Teknoloji",
        brand_name="Acme",
        official_website_candidate="https://acme.example/",
        careers_url_candidate=None,
        official_linkedin_candidate=None,
        confidence="high",
        status="candidate_found",
        evidence=["Şirket adı eşleşti."],
        reason="Resmî site adayı bulundu.",
    )
    graph = MagicMock()
    graph.__enter__.return_value = graph
    graph.run.return_value = {
        "company_name": "Acme\x00 Teknoloji",
        "search_results": [object(), object()],
        "attempts": [{"outcome": "success"}],
        "assessment": assessment,
        "stored_status": "candidate_found",
        "profile_updated": True,
        "verification_code": "legal_name_not_found",
        "error_code": None,
        "internal_results": "must-not-be-exposed",
    }

    with (
        patch(
            "app.company_profile_search_service._assert_search_allowed"
        ),
        patch(
            "app.company_profile_search_service.build_evaluator",
            return_value=MagicMock(),
        ),
        patch(
            "app.company_profile_search_service.CompanyDiscoveryGraph",
            return_value=graph,
        ),
    ):
        result = discover_company_profile(
            MagicMock(),
            company_id=company_id,
        )

    assert result.status == "succeeded"
    assert result.company_name == "Acme Teknoloji"
    assert result.profile_status == "candidate_found"
    assert result.confidence == "high"
    assert result.result_count == 2
    assert result.attempt_count == 1
    assert result.profile_updated is True


def test_unexpected_provider_failure_returns_generic_code(monkeypatch) -> None:
    monkeypatch.setenv("SERPER_API_KEY", "test-serper-key")
    graph = MagicMock()
    graph.__enter__.return_value = graph
    graph.run.side_effect = RuntimeError("secret provider detail")

    with (
        patch(
            "app.company_profile_search_service._assert_search_allowed"
        ),
        patch(
            "app.company_profile_search_service.build_evaluator",
            return_value=MagicMock(),
        ),
        patch(
            "app.company_profile_search_service.CompanyDiscoveryGraph",
            return_value=graph,
        ),
        patch("app.company_profile_search_service.logger.exception"),
    ):
        with pytest.raises(CompanyProfileSearchError) as caught:
            discover_company_profile(MagicMock(), company_id=uuid4())

    assert caught.value.code == "company_discovery_failed"
