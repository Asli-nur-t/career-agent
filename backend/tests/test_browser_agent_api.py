import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

os.environ.setdefault("APP_DB_PASSWORD", "test-only-password")

from app.browser_job_agent import (
    BrowserAgentError,
    BrowserCollectedJob,
    BrowserCollection,
    BrowserSourceDiagnostic,
)
from app.main import app
from app.operator_api import (
    BrowserCleanupResponse,
    BrowserCollectedItem,
    BrowserCollectedPage,
    BrowserResultDismissResponse,
    BrowserStaleCleanupResponse,
    ImportManualJobResponse,
    _candidate_search_roles,
    cleanup_browser_collected_jobs,
    cleanup_stale_unassessed_browser_jobs,
)


TOKEN = "a" * 64


def _client() -> TestClient:
    return TestClient(app, base_url="http://localhost")


def _headers() -> dict[str, str]:
    return {"X-Operator-Token": TOKEN}


def _payload() -> dict[str, object]:
    return {
        "provider": "linkedin",
        "role": "AI Engineer",
        "location": "İstanbul, Türkiye",
        "work_modes": ["remote", "hybrid"],
        "max_results": 20,
        "profile": "aslinur-default",
        "confirmed_browser_launch": True,
    }


def test_browser_agent_collects_and_imports_bounded_results(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    listing = BrowserCollectedJob(
        "https://www.linkedin.com/jobs/view/4471899349",
        "AI Engineer",
        "Acme",
        "İstanbul, Türkiye",
        work_mode="hybrid",
        description_text=(
            "Python ve FastAPI bilen, junior seviyede yapay zeka mühendisi. " * 3
        ),
    )
    imported = ImportManualJobResponse(
        candidate_id=uuid4(),
        provider="linkedin",
        listing_url=listing.listing_url,
        status="needs_review",
        created=True,
        changed=True,
    )
    collected = BrowserCollection(
        jobs=(listing,),
        diagnostics=(
            BrowserSourceDiagnostic("linkedin", "LinkedIn", "collected", 1),
        ),
    )
    with (
        patch("app.operator_api.collect_browser_jobs", return_value=collected) as collect,
        patch(
            "app.operator_api.import_manual_job_candidate",
            return_value=imported,
        ) as import_job,
        patch("app.operator_api.assess_browser_candidates") as assess,
        patch("app.operator_api.Session") as session_factory,
    ):
        session_factory.return_value.__enter__.return_value.scalar.return_value = 1
        response = _client().post(
            "/operator/browser-agent/collect",
            headers=_headers(),
            json=_payload(),
        )

    assert response.status_code == 200
    assert response.json()["created_count"] == 1
    assert response.json()["assessment_queued_count"] == 1
    assert collect.call_args.kwargs["max_results_per_provider"] == 20
    assert collect.call_args.kwargs["work_modes"] == ["remote", "hybrid"]
    assert import_job.call_args.kwargs["origin"] == "browser_agent"
    assert import_job.call_args.kwargs["search_role"] == "AI Engineer"
    assert import_job.call_args.args[1].work_mode == "hybrid"
    assert response.json()["diagnostics"][0]["outcome"] == "collected"
    assess.assert_called_once()


def test_browser_agent_requires_explicit_launch_confirmation(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    payload = _payload()
    payload["confirmed_browser_launch"] = False
    response = _client().post(
        "/operator/browser-agent/collect",
        headers=_headers(),
        json=payload,
    )
    assert response.status_code == 422


def test_browser_agent_human_handoff_is_explicit(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    with (
        patch(
            "app.operator_api.collect_browser_jobs",
            side_effect=BrowserAgentError("browser_human_action_required"),
        ),
        patch("app.operator_api.Session") as session_factory,
    ):
        session_factory.return_value.__enter__.return_value.scalar.return_value = 1
        response = _client().post(
            "/operator/browser-agent/collect",
            headers=_headers(),
            json=_payload(),
        )
    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == (
        "browser_human_action_required"
    )


def test_browser_agent_accepts_multiple_allowlisted_sources(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    payload = _payload()
    payload.pop("provider")
    payload.pop("max_results")
    payload["providers"] = ["kariyer", "techcareer", "remotive"]
    payload["max_results_per_provider"] = 10
    collected = BrowserCollection(
        jobs=(),
        diagnostics=(
            BrowserSourceDiagnostic(
                "kariyer", "Kariyer.net", "no_results", 0, "browser_source_no_results"
            ),
            BrowserSourceDiagnostic(
                "techcareer", "Techcareer.net", "login_required", 0,
                "browser_source_login_required",
            ),
            BrowserSourceDiagnostic(
                "remotive", "Remotive", "failed", 0, "browser_source_unavailable"
            ),
        ),
    )
    with (
        patch("app.operator_api.collect_browser_jobs", return_value=collected) as collect,
        patch("app.operator_api.Session") as session_factory,
    ):
        session_factory.return_value.__enter__.return_value.scalar.return_value = 1
        response = _client().post(
            "/operator/browser-agent/collect",
            headers=_headers(),
            json=payload,
        )

    assert response.status_code == 200
    assert response.json()["provider"] == "multi"
    assert response.json()["collected_count"] == 0
    assert len(response.json()["diagnostics"]) == 3
    assert collect.call_args.kwargs["providers"] == [
        "kariyer", "techcareer", "remotive"
    ]


def test_browser_results_are_authenticated_and_persistent(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    item = BrowserCollectedItem(
        candidate_id=uuid4(),
        provider="linkedin",
        title="AI Engineer",
        company_name="Acme",
        listing_url="https://www.linkedin.com/jobs/view/4471899349",
        location="İstanbul, Türkiye",
        work_mode="hybrid",
        status="needs_review",
        collected_at=datetime.now(timezone.utc),
        search_roles=["AI Engineer", "Machine Learning Engineer"],
    )
    page = BrowserCollectedPage(total=1, limit=100, offset=0, items=[item])
    with patch("app.operator_api.load_browser_collected_jobs", return_value=page):
        unauthorized = _client().get(
            "/operator/browser-agent/results?profile=aslinur-default"
        )
        response = _client().get(
            "/operator/browser-agent/results?profile=aslinur-default&limit=100&offset=0",
            headers=_headers(),
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["title"] == "AI Engineer"
    assert response.json()["items"][0]["search_roles"] == [
        "AI Engineer",
        "Machine Learning Engineer",
    ]


def test_browser_search_roles_are_unique_and_ignore_other_evidence() -> None:
    candidate = SimpleNamespace(
        evidence=[
            {"kind": "browser_agent_import", "search_role": "AI Engineer"},
            {"kind": "manual_operator_import", "search_role": "Data Engineer"},
            {"kind": "browser_agent_import", "search_role": "ai engineer"},
            {"kind": "browser_agent_import", "search_role": "Business Analyst"},
        ]
    )

    assert _candidate_search_roles(candidate) == ["AI Engineer", "Business Analyst"]


def test_browser_result_can_be_hidden_and_restored_per_profile(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    candidate_id = uuid4()
    with patch("app.operator_api.set_browser_candidate_dismissal") as update:
        update.side_effect = [
            BrowserResultDismissResponse(candidate_id=candidate_id, dismissed=True),
            BrowserResultDismissResponse(candidate_id=candidate_id, dismissed=False),
        ]
        unauthorized = _client().post(
            f"/operator/browser-agent/results/{candidate_id}/dismiss",
            json={"profile": "aslinur-default"},
        )
        hidden = _client().post(
            f"/operator/browser-agent/results/{candidate_id}/dismiss",
            headers=_headers(),
            json={"profile": "aslinur-default"},
        )
        restored = _client().post(
            f"/operator/browser-agent/results/{candidate_id}/restore",
            headers=_headers(),
            json={"profile": "aslinur-default"},
        )

    assert unauthorized.status_code == 401
    assert hidden.status_code == 200
    assert hidden.json() == {
        "candidate_id": str(candidate_id),
        "dismissed": True,
    }
    assert restored.status_code == 200
    assert restored.json()["dismissed"] is False
    assert update.call_args_list[0].kwargs["profile_label"] == "aslinur-default"
    assert update.call_args_list[0].kwargs["dismissed"] is True
    assert update.call_args_list[1].kwargs["dismissed"] is False


def test_browser_cleanup_is_authenticated_and_non_destructive(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    result = BrowserCleanupResponse(repaired_count=2, quarantined_count=1)
    with patch("app.operator_api.cleanup_browser_collected_jobs", return_value=result):
        unauthorized = _client().post("/operator/browser-agent/results/cleanup")
        response = _client().post(
            "/operator/browser-agent/results/cleanup",
            headers=_headers(),
        )

    assert unauthorized.status_code == 401
    assert response.status_code == 200
    assert response.json() == {"repaired_count": 2, "quarantined_count": 1}


def test_browser_cleanup_repairs_text_and_quarantines_fixture_without_delete() -> None:
    repaired = SimpleNamespace(
        external_id="f7bad0e75f67ef55",
        provider="indeed",
        listing_url="https://tr.indeed.com/viewjob?jk=f7bad0e75f67ef55",
        title="AI Engineer ile ilgili tüm ayrıntılar",
        company_name_raw="AI Engineer",
        location="Kolayca başvur",
        status="needs_review",
        activity_state="active",
        activity_code="browser_agent_listing_confirmation",
        updated_at=None,
        evidence=[],
    )
    fixture = SimpleNamespace(
        external_id="123456789abcdef0",
        provider="indeed",
        listing_url="https://tr.indeed.com/viewjob?jk=123456789abcdef0",
        title="Machine Learning Engineer",
        company_name_raw="İşveren adı doğrulanmadı",
        location=None,
        status="needs_review",
        activity_state="active",
        activity_code="browser_agent_listing_confirmation",
        updated_at=None,
        evidence=[],
    )
    navigation_page = SimpleNamespace(
        external_id="valid-looking-sha256",
        provider="toptalent",
        listing_url="https://toptalent.co/insan-kaynaklari-platformu",
        title="İK Platformu",
        company_name_raw="İşveren adı doğrulanmadı",
        location=None,
        status="needs_review",
        activity_state="active",
        activity_code="browser_agent_listing_confirmation",
        updated_at=None,
        evidence=[],
    )
    session = MagicMock()
    session.__enter__.return_value = session
    session.scalars.return_value.all.return_value = [
        repaired,
        fixture,
        navigation_page,
    ]

    with patch("app.operator_api.Session", return_value=session):
        result = cleanup_browser_collected_jobs(MagicMock())

    assert result.repaired_count == 1
    assert result.quarantined_count == 2
    assert (repaired.title, repaired.company_name_raw, repaired.location) == (
        "AI Engineer",
        "İşveren adı doğrulanmadı",
        None,
    )
    assert fixture.status == "filtered_out"
    assert fixture.activity_code == "browser_agent_invalid_listing"
    assert navigation_page.status == "filtered_out"
    assert navigation_page.evidence[-1]["reason"] == "invalid_listing_url"
    session.delete.assert_not_called()
    session.commit.assert_called_once()


def test_stale_cleanup_requires_preview_then_explicit_confirmation(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    preview = BrowserStaleCleanupResponse(
        matched_count=4,
        quarantined_count=0,
        applied=False,
    )
    applied = BrowserStaleCleanupResponse(
        matched_count=4,
        quarantined_count=4,
        applied=True,
    )
    with patch(
        "app.operator_api.cleanup_stale_unassessed_browser_jobs",
        side_effect=[preview, applied],
    ) as cleanup:
        preview_response = _client().post(
            "/operator/browser-agent/results/stale-cleanup",
            headers=_headers(),
            json={"profile": "aslinur-default", "apply": False},
        )
        apply_response = _client().post(
            "/operator/browser-agent/results/stale-cleanup",
            headers=_headers(),
            json={
                "profile": "aslinur-default",
                "apply": True,
                "confirmed_cleanup": True,
            },
        )

    assert preview_response.status_code == 200
    assert preview_response.json()["matched_count"] == 4
    assert apply_response.status_code == 200
    assert apply_response.json()["quarantined_count"] == 4
    assert cleanup.call_args_list[0].kwargs["apply"] is False
    assert cleanup.call_args_list[1].kwargs["confirmed_cleanup"] is True


def test_stale_cleanup_quarantines_instead_of_deleting() -> None:
    candidate = SimpleNamespace(
        status="needs_review",
        activity_state="unknown",
        activity_code="browser_agent_listing_confirmation",
        updated_at=None,
        evidence=[],
    )
    session = MagicMock()
    session.__enter__.return_value = session
    session.scalar.return_value = SimpleNamespace(id=uuid4())
    session.scalars.return_value.__iter__.return_value = iter([candidate])

    with patch("app.operator_api.Session", return_value=session):
        result = cleanup_stale_unassessed_browser_jobs(
            MagicMock(),
            profile_label="aslinur-default",
            older_than_days=0,
            limit=500,
            apply=True,
            confirmed_cleanup=True,
        )

    assert result.matched_count == 1
    assert result.quarantined_count == 1
    assert candidate.status == "filtered_out"
    assert candidate.evidence[-1]["reason"] == (
        "stale_unassessed_insufficient_description"
    )
    session.delete.assert_not_called()
    session.commit.assert_called_once()


def test_stale_cleanup_refuses_unconfirmed_apply() -> None:
    with patch("app.operator_api.Session") as session_factory:
        try:
            cleanup_stale_unassessed_browser_jobs(
                MagicMock(),
                profile_label="aslinur-default",
                older_than_days=0,
                limit=500,
                apply=True,
                confirmed_cleanup=False,
            )
        except ValueError as error:
            assert str(error) == "cleanup_confirmation_required"
        else:
            raise AssertionError("unconfirmed cleanup unexpectedly succeeded")
    session_factory.assert_not_called()
