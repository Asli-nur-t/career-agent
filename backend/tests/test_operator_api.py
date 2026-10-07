import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

os.environ.setdefault("APP_DB_PASSWORD", "test-only-password")

from app.main import app
from app.company_discovery_runs import (
    CompanyDiscoveryRunError,
    CompanyDiscoveryRunSnapshot,
)
from app.company_profile_search_service import CompanyProfileSearchError
from app.operator_api import (
    OperatorSummary,
    _ranked_item,
    load_operator_companies,
    load_operator_company_detail,
    load_operator_job_detail,
    load_operator_summary,
    mark_operator_job_viewed,
)
from app.operator_search_runs import SearchAlreadyRunning, SearchCooldownActive
from app.review_job_board_queue import RankedCandidate


TOKEN = "a" * 64


def _headers() -> dict[str, str]:
    return {"X-Operator-Token": TOKEN}


def _client() -> TestClient:
    return TestClient(app, base_url="http://localhost")


def _company_run_snapshot(
    *,
    run_id=None,
    status="queued",
) -> CompanyDiscoveryRunSnapshot:
    now = datetime.now(timezone.utc)
    return CompanyDiscoveryRunSnapshot(
        run_id=run_id or uuid4(),
        status=status,
        scope="unprofiled",
        query_budget=2200,
        query_count=0,
        total_count=10,
        queued_count=10 if status in {"queued", "paused"} else 0,
        running_count=0,
        succeeded_count=0,
        failed_count=0,
        profile_counts={},
        error_counts={},
        error_code=None,
        created_at=now,
        started_at=None,
        finished_at=None,
    )


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


def test_company_list_is_authenticated_and_bounded(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    page = {
        "total": 1,
        "limit": 25,
        "offset": 0,
        "items": [],
    }
    with patch("app.operator_api.load_operator_companies", return_value=page) as load:
        response = _client().get(
            "/operator/companies?q=AI%20Agency&profile_status=verified",
            headers=_headers(),
        )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert load.call_args.kwargs == {
        "limit": 25,
        "offset": 0,
        "query": "AI Agency",
        "profile_status": "verified",
    }


def test_company_list_rejects_unknown_profile_status(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    response = _client().get(
        "/operator/companies?profile_status=deleted",
        headers=_headers(),
    )

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == (
        "company_profile_status_invalid"
    )


def test_company_loader_sanitizes_text_and_external_urls() -> None:
    company_id = uuid4()
    now = datetime.now(timezone.utc)
    company = SimpleNamespace(
        id=company_id,
        name="Acme\x00 AI",
        sector="Yazılım\x00",
        needs_review=False,
    )
    profile = SimpleNamespace(
        status="verified",
        brand_name="Acme\x00",
        confidence="high",
        official_website_url="https://acme.example/",
        careers_url="javascript:alert(1)",
        official_linkedin_url="https://tr.linkedin.com/company/acme-ai/about/",
        last_verified_at=now,
        updated_at=now,
    )
    session = MagicMock()
    session.__enter__.return_value = session
    session.scalar.return_value = 1
    session.execute.side_effect = [
        SimpleNamespace(all=lambda: [(company, profile)]),
        SimpleNamespace(all=lambda: [(company_id, "İTÜ\x00 ARI Teknokent")]),
    ]

    with patch("app.operator_api.Session", return_value=session):
        page = load_operator_companies(
            MagicMock(),
            limit=25,
            offset=0,
            query="100%_safe",
            profile_status="verified",
        )

    assert page.total == 1
    assert page.items[0].name == "Acme AI"
    assert page.items[0].teknoparks == ["İTÜ ARI Teknokent"]
    assert page.items[0].official_website_url == "https://acme.example/"
    assert page.items[0].careers_url is None
    assert page.items[0].official_linkedin_url == (
        "https://www.linkedin.com/company/acme-ai/"
    )


def test_company_detail_sanitizes_evidence_and_urls() -> None:
    company_id = uuid4()
    now = datetime.now(timezone.utc)
    company = SimpleNamespace(
        id=company_id,
        name="Acme\x00 AI",
        sector="Yazılım",
        needs_review=False,
    )
    profile = SimpleNamespace(
        status="candidate_found",
        brand_name="Acme AI",
        confidence="high",
        official_website_url="https://acme.example/",
        careers_url="javascript:alert(1)",
        official_linkedin_url="https://tr.linkedin.com/company/acme-ai/about/",
        evidence=[
            {"text": "Acme\x00 evidence", "nested": {"ignored": True}}
        ],
        search_provider="serper",
        evaluator_model="gemini-test",
        last_searched_at=now,
        last_verified_at=None,
        career_sources_last_checked_at=now,
        career_sources_next_check_at=now,
        career_sources_last_outcome="no_results",
        career_sources_last_error_code=None,
        career_sources_candidate_count=0,
        job_boards_last_checked_at=now,
        job_boards_next_check_at=now,
        job_boards_last_outcome="candidates_found",
        job_boards_last_error_code=None,
        job_boards_candidate_count=2,
        updated_at=now,
    )
    session = MagicMock()
    session.__enter__.return_value = session
    session.execute.return_value = SimpleNamespace(
        one_or_none=lambda: (company, profile)
    )
    session.scalars.return_value = SimpleNamespace(
        all=lambda: ["İTÜ\x00 ARI Teknokent"]
    )

    with patch("app.operator_api.Session", return_value=session):
        detail = load_operator_company_detail(MagicMock(), company_id)

    assert detail is not None
    assert detail.name == "Acme AI"
    assert detail.teknoparks == ["İTÜ ARI Teknokent"]
    assert detail.careers_url is None
    assert detail.official_linkedin_url == (
        "https://www.linkedin.com/company/acme-ai/"
    )
    assert detail.evidence == [{"text": "Acme evidence"}]
    assert detail.reviewable is True


def test_company_approval_requires_explicit_confirmation(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    response = _client().post(
        f"/operator/companies/{uuid4()}/approve",
        headers=_headers(),
        json={"confirmed_identity": False},
    )

    assert response.status_code == 422


def test_company_rejection_delegates_allowlisted_reason(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    company_id = uuid4()
    result = {
        "company_id": str(company_id),
        "company_name": "Acme",
        "status": "not_found",
        "changed": True,
    }
    with patch(
        "app.operator_api.review_company_profile",
        return_value=result,
    ) as review:
        response = _client().post(
            f"/operator/companies/{company_id}/reject",
            headers=_headers(),
            json={
                "confirmed_rejection": True,
                "reason": "wrong_company",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "not_found"
    assert review.call_args.kwargs["rejection_reason"] == "wrong_company"


def test_company_discovery_requires_explicit_confirmation(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    response = _client().post(
        f"/operator/companies/{uuid4()}/discover",
        headers=_headers(),
        json={"confirmed_external_search": False},
    )

    assert response.status_code == 422


def test_company_discovery_is_bounded_to_path_company(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    company_id = uuid4()
    result = SimpleNamespace(
        company_id=company_id,
        company_name="Acme",
        status="succeeded",
        profile_status="candidate_found",
        confidence="high",
        result_count=3,
        attempt_count=1,
        profile_updated=True,
        verification_code="legal_name_not_found",
        error_code=None,
    )
    with patch(
        "app.operator_api.discover_company_profile",
        return_value=result,
    ) as discover:
        response = _client().post(
            f"/operator/companies/{company_id}/discover",
            headers=_headers(),
            json={"confirmed_external_search": True},
        )

    assert response.status_code == 200
    assert response.json()["profile_status"] == "candidate_found"
    assert discover.call_args.kwargs["company_id"] == company_id


def test_company_discovery_cooldown_maps_to_rate_limit(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    with patch(
        "app.operator_api.discover_company_profile",
        side_effect=CompanyProfileSearchError("company_search_cooldown"),
    ):
        response = _client().post(
            f"/operator/companies/{uuid4()}/discover",
            headers=_headers(),
            json={"confirmed_external_search": True},
        )

    assert response.status_code == 429
    assert response.json()["detail"]["error_code"] == (
        "company_search_cooldown"
    )


def test_bulk_company_discovery_requires_explicit_confirmation(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)

    response = _client().post(
        "/operator/company-discovery-runs",
        headers=_headers(),
        json={"confirmed_external_search": False, "query_budget": 2200},
    )

    assert response.status_code == 422


def test_operator_can_queue_budgeted_company_discovery(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    run_id = uuid4()
    snapshot = _company_run_snapshot(run_id=run_id)
    with (
        patch(
            "app.operator_api.queue_company_discovery_run",
            return_value=run_id,
        ) as queue,
        patch(
            "app.operator_api.load_company_discovery_run",
            return_value=snapshot,
        ),
        patch("app.operator_api.execute_company_discovery_run") as execute,
    ):
        response = _client().post(
            "/operator/company-discovery-runs",
            headers=_headers(),
            json={
                "confirmed_external_search": True,
                "query_budget": 2200,
            },
        )

    assert response.status_code == 202
    assert response.json()["run_id"] == str(run_id)
    assert response.json()["query_budget"] == 2200
    queue.assert_called_once()
    assert queue.call_args.kwargs == {"query_budget": 2200}
    execute.assert_called_once()


def test_operator_rejects_parallel_company_discovery(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    with patch(
        "app.operator_api.queue_company_discovery_run",
        side_effect=CompanyDiscoveryRunError("company_discovery_run_active"),
    ):
        response = _client().post(
            "/operator/company-discovery-runs",
            headers=_headers(),
            json={"confirmed_external_search": True, "query_budget": 2200},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == (
        "company_discovery_run_active"
    )


def test_company_discovery_pause_uses_requested_run(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    run_id = uuid4()
    snapshot = _company_run_snapshot(run_id=run_id, status="pause_requested")
    with (
        patch("app.operator_api.pause_company_discovery_run") as pause,
        patch(
            "app.operator_api.load_company_discovery_run",
            return_value=snapshot,
        ) as load,
    ):
        response = _client().post(
            f"/operator/company-discovery-runs/{run_id}/pause",
            headers=_headers(),
            json={"confirmed": True},
        )

    assert response.status_code == 200
    pause.assert_called_once()
    assert pause.call_args.args[1] == run_id
    assert load.call_args.args[1] == run_id


def test_company_discovery_resume_schedules_requested_run(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    run_id = uuid4()
    snapshot = _company_run_snapshot(run_id=run_id, status="paused")
    with (
        patch("app.operator_api.resume_company_discovery_run") as resume,
        patch(
            "app.operator_api.load_company_discovery_run",
            return_value=snapshot,
        ),
        patch("app.operator_api.execute_company_discovery_run") as execute,
    ):
        response = _client().post(
            f"/operator/company-discovery-runs/{run_id}/resume",
            headers=_headers(),
            json={"confirmed": True},
        )

    assert response.status_code == 202
    resume.assert_called_once()
    assert resume.call_args.args[1] == run_id
    execute.assert_called_once()


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


def test_operator_can_queue_bounded_profile_search(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    run_id = uuid4()
    now = datetime.now(timezone.utc)
    run = SimpleNamespace(
        id=run_id,
        status="queued",
        result={},
        error_code=None,
        created_at=now,
        started_at=None,
        finished_at=None,
    )
    with (
        patch(
            "app.operator_api.queue_profile_search",
            return_value=run_id,
        ) as queue,
        patch("app.operator_api.load_latest_profile_search", return_value=run),
        patch("app.operator_api.execute_profile_search_run") as execute,
        patch(
            "app.operator_api.assess_profile_search_run_candidates"
        ) as assess,
    ):
        response = _client().post(
            "/operator/search-runs",
            headers=_headers(),
            json={
                "profile": "aslinur-default",
                "roles": ["AI Engineer", "Backend Engineer"],
                "confirmed_external_search": True,
            },
        )

    assert response.status_code == 202
    assert response.json()["run_id"] == str(run_id)
    assert response.json()["result"]["matched_candidates"] == []
    assert queue.call_args.kwargs["requested_roles"] == [
        "AI Engineer",
        "Backend Engineer",
    ]
    assert queue.call_args.kwargs["search_mode"] == "quick"
    assert queue.call_args.kwargs["requested_locations"] == []
    assert queue.call_args.kwargs["requested_work_modes"] == [
        "remote", "hybrid", "onsite"
    ]
    assert queue.call_args.kwargs["requested_sources"] == [
        "linkedin", "kariyer", "indeed", "glassdoor", "ats",
        "turkey_tech", "remote_feeds",
    ]
    assert queue.call_args.kwargs["max_listing_age_days"] == 30
    assert queue.call_args.kwargs["force"] is False
    assert execute.call_args.args[1] == run_id
    execute.assert_called_once()
    assert assess.call_args.args[1] == run_id
    assess.assert_called_once()


def test_operator_can_force_deep_profile_search(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    run_id = uuid4()
    now = datetime.now(timezone.utc)
    run = SimpleNamespace(
        id=run_id,
        status="queued",
        result={"search_mode": "deep"},
        error_code=None,
        created_at=now,
        started_at=None,
        finished_at=None,
    )
    with (
        patch("app.operator_api.queue_profile_search", return_value=run_id) as queue,
        patch("app.operator_api.load_latest_profile_search", return_value=run),
        patch("app.operator_api.execute_profile_search_run") as execute,
        patch(
            "app.operator_api.assess_profile_search_run_candidates"
        ) as assess,
    ):
        response = _client().post(
            "/operator/search-runs",
            headers=_headers(),
            json={
                "profile": "aslinur-default",
                "roles": ["AI Engineer"],
                "search_mode": "deep",
                "locations": ["Türkiye"],
                "work_modes": ["remote"],
                "sources": ["linkedin", "ats"],
                "max_age_days": 14,
                "force": True,
                "confirmed_external_search": True,
            },
        )

    assert response.status_code == 202
    assert queue.call_args.kwargs["search_mode"] == "deep"
    assert queue.call_args.kwargs["requested_locations"] == ["Türkiye"]
    assert queue.call_args.kwargs["requested_work_modes"] == ["remote"]
    assert queue.call_args.kwargs["requested_sources"] == ["linkedin", "ats"]
    assert queue.call_args.kwargs["max_listing_age_days"] == 14
    assert queue.call_args.kwargs["force"] is True
    assert execute.call_args.kwargs["force"] is True
    assert assess.call_args.args[1] == run_id
    assess.assert_called_once()


def test_operator_returns_retry_after_for_same_search_cooldown(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    with patch(
        "app.operator_api.queue_profile_search",
        side_effect=SearchCooldownActive(173),
    ):
        response = _client().post(
            "/operator/search-runs",
            headers=_headers(),
            json={
                "profile": "aslinur-default",
                "roles": ["AI Engineer"],
                "force": True,
                "confirmed_external_search": True,
            },
        )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "173"
    assert response.json()["detail"]["error_code"] == "search_cooldown"


def test_operator_rejects_parallel_profile_search(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    with patch(
        "app.operator_api.queue_profile_search",
        side_effect=SearchAlreadyRunning("search_already_running"),
    ):
        response = _client().post(
            "/operator/search-runs",
            headers=_headers(),
            json={
                "profile": "aslinur-default",
                "roles": ["AI Engineer"],
                "confirmed_external_search": True,
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == "search_already_running"


def test_operator_search_requires_external_request_confirmation(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    response = _client().post(
        "/operator/search-runs",
        headers=_headers(),
        json={"profile": "aslinur-default", "roles": ["AI Engineer"]},
    )

    assert response.status_code == 422


def test_operator_rejects_empty_search_roles(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    response = _client().post(
        "/operator/search-runs",
        headers=_headers(),
        json={
            "profile": "aslinur-default",
            "roles": [],
            "confirmed_external_search": True,
        },
    )

    assert response.status_code == 422


def test_operator_rejects_unallowlisted_search_scope(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    response = _client().post(
        "/operator/search-runs",
        headers=_headers(),
        json={
            "profile": "aslinur-default",
            "roles": ["AI Engineer"],
            "sources": ["unsafe-crawler"],
            "work_modes": ["remote"],
            "max_age_days": 30,
            "confirmed_external_search": True,
        },
    )

    assert response.status_code == 422


def test_operator_marks_job_viewed(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    candidate_id = uuid4()
    viewed_at = datetime.now(timezone.utc)
    with patch(
        "app.operator_api.mark_operator_job_viewed",
        return_value=SimpleNamespace(
            candidate_id=candidate_id,
            operator_viewed_at=viewed_at,
        ),
    ):
        response = _client().post(
            f"/operator/jobs/{candidate_id}/viewed",
            headers=_headers(),
        )

    assert response.status_code == 200
    assert response.json()["candidate_id"] == str(candidate_id)
    returned = datetime.fromisoformat(
        response.json()["operator_viewed_at"].replace("Z", "+00:00")
    )
    assert returned == viewed_at


def test_mark_job_viewed_preserves_first_view_time() -> None:
    candidate_id = uuid4()
    first_viewed_at = datetime.now(timezone.utc)
    candidate = SimpleNamespace(
        id=candidate_id,
        operator_viewed_at=first_viewed_at,
    )
    session = MagicMock()
    session.__enter__.return_value = session
    session.scalar.return_value = candidate

    with patch("app.operator_api.Session", return_value=session):
        result = mark_operator_job_viewed(MagicMock(), candidate_id)

    assert result is not None
    assert result.operator_viewed_at == first_viewed_at
    session.commit.assert_not_called()


def test_latest_search_exposes_only_allowlisted_result_fields(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    now = datetime.now(timezone.utc)
    run = SimpleNamespace(
        id=uuid4(),
        status="succeeded",
        result={"candidate_count": 2, "internal_debug": "do-not-return"},
        error_code=None,
        created_at=now,
        started_at=now,
        finished_at=now,
    )
    with patch(
        "app.operator_api.load_latest_profile_search",
        return_value=run,
    ):
        response = _client().get(
            "/operator/search-runs/latest?profile=aslinur-default",
            headers=_headers(),
        )

    assert response.status_code == 200
    assert response.json()["result"] == {
        "candidate_count": 2,
        "matched_candidates": [],
    }


def test_latest_search_drops_unsafe_matched_candidate_url(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    now = datetime.now(timezone.utc)
    run = SimpleNamespace(
        id=uuid4(),
        status="succeeded",
        result={
            "matched_candidates": [{
                "candidate_id": str(uuid4()),
                "provider": "linkedin",
                "title": "AI Engineer",
                "company_name": "Acme",
                "listing_url": "javascript:alert(1)",
                "score": 70,
                "disposition": "active_review",
            }]
        },
        error_code=None,
        created_at=now,
        started_at=now,
        finished_at=now,
    )
    with patch(
        "app.operator_api.load_latest_profile_search",
        return_value=run,
    ):
        response = _client().get(
            "/operator/search-runs/latest?profile=aslinur-default",
            headers=_headers(),
        )

    assert response.status_code == 200
    assert response.json()["result"]["matched_candidates"] == []
