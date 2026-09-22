import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

os.environ.setdefault("APP_DB_PASSWORD", "test-only-password")

from app.main import app
from app.operator_api import (
    OperatorSummary,
    _ranked_item,
    load_operator_job_detail,
    load_operator_summary,
)
from app.review_job_board_queue import RankedCandidate


TOKEN = "a" * 64


def _headers() -> dict[str, str]:
    return {"X-Operator-Token": TOKEN}


def _client() -> TestClient:
    return TestClient(app, base_url="http://localhost")


def test_operator_api_fails_closed_without_configured_token(
    monkeypatch,
) -> None:
    monkeypatch.delenv("OPERATOR_API_TOKEN", raising=False)
    response = _client().get("/operator/summary")

    assert response.status_code == 503
    assert response.json()["detail"]["error_code"] == (
        "operator_auth_not_configured"
    )


def test_operator_api_rejects_wrong_token(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    response = _client().get(
        "/operator/summary",
        headers={"X-Operator-Token": "b" * 64},
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "ApiKey"


def test_operator_summary_is_authenticated(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    summary = OperatorSummary(
        companies=727,
        verified_companies=14,
        active_sources=0,
        active_postings=0,
        pending_candidates=5,
        verified_active_candidates=0,
        new_matches=0,
        profiles=1,
    )
    with patch("app.operator_api.load_operator_summary", return_value=summary):
        response = _client().get(
            "/operator/summary",
            headers=_headers(),
        )

    assert response.status_code == 200
    assert response.json()["companies"] == 727
    assert response.json()["verified_companies"] == 14
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_summary_loader_uses_bounded_aggregate_queries() -> None:
    session = MagicMock()
    session.__enter__.return_value = session
    session.scalar.side_effect = [727, 14, 2, 3, 5, 1, 4, 1]

    with patch("app.operator_api.Session", return_value=session):
        summary = load_operator_summary(MagicMock())

    assert summary.companies == 727
    assert summary.verified_companies == 14
    assert summary.verified_active_candidates == 1
    assert session.scalar.call_count == 8


def test_candidate_detail_sanitizes_evidence() -> None:
    candidate_id = uuid4()
    now = datetime.now(timezone.utc)
    candidate = SimpleNamespace(
        id=candidate_id,
        company_id=None,
        provider="linkedin",
        title="AI Engineer",
        listing_url="https://www.linkedin.com/jobs/view/123456",
        snippet="Python\x00 RAG",
        location="İstanbul",
        work_mode="hybrid",
        employment_type="full_time",
        published_at=now,
        activity_state="active",
        activity_code="linkedin_active_marker",
        activity_checked_at=now,
        status="needs_review",
        evidence=[
            {
                "kind": "search_result",
                "query": "AI\x00 Engineer",
                "nested": {"ignored": True},
            }
        ],
        first_seen_at=now,
        last_seen_at=now,
    )
    session = MagicMock()
    session.__enter__.return_value = session
    session.execute.return_value = SimpleNamespace(
        one_or_none=lambda: (candidate, "İşveren adı doğrulanmadı")
    )

    with patch("app.operator_api.Session", return_value=session):
        detail = load_operator_job_detail(MagicMock(), candidate_id)

    assert detail is not None
    assert detail.company_identity_required is True
    assert detail.snippet == "Python RAG"
    assert detail.evidence == [
        {"kind": "search_result", "query": "AI Engineer"}
    ]


def test_queue_drops_tampered_listing_url() -> None:
    item = RankedCandidate(
        candidate_id=uuid4(),
        provider="linkedin",
        company_name="Acme",
        title="AI Engineer",
        listing_url="javascript:alert(1)",
        location=None,
        work_mode="unknown",
        employment_type="unknown",
        published_at=None,
        activity_state="unknown",
        activity_code="not_checked",
        score=40,
        recommendation="review",
        matched_terms=[],
        risk_flags=[],
    )

    assert _ranked_item(item) is None


def test_rejection_requires_explicit_confirmation(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    response = _client().post(
        f"/operator/jobs/{uuid4()}/reject",
        headers=_headers(),
        json={"confirmed_rejection": False},
    )

    assert response.status_code == 422


def test_approval_delegates_to_transactional_review(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    candidate_id = uuid4()
    result = {
        "candidate_id": str(candidate_id),
        "provider": "linkedin",
        "title": "AI Engineer",
        "listing_url": "https://www.linkedin.com/jobs/view/123456",
        "status": "approved",
        "posting_id": str(uuid4()),
        "company_id": str(uuid4()),
        "company_name": "Acme",
        "company_created": False,
        "changed": True,
    }
    with patch(
        "app.operator_api.review_job_board_candidate",
        return_value=result,
    ) as review:
        response = _client().post(
            f"/operator/jobs/{candidate_id}/approve",
            headers=_headers(),
            json={"confirmed_active": True},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert review.call_args.kwargs["confirmed_active"] is True
