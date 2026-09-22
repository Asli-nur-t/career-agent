from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.profile_search_service import (
    ProfileSearchError,
    load_search_candidate_snapshots,
    run_profile_job_search,
)


def test_profile_search_fails_closed_without_serper_key(monkeypatch) -> None:
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    state = SimpleNamespace(spec=SimpleNamespace(), profile_id="profile-id")

    with patch(
        "app.profile_search_service.load_profile_search_state",
        return_value=state,
    ):
        with pytest.raises(ProfileSearchError) as caught:
            run_profile_job_search(
                MagicMock(),
                profile_label="aslinur-default",
            )

    assert caught.value.code == "serper_not_configured"


def test_profile_search_audits_only_its_unverified_candidates(monkeypatch) -> None:
    monkeypatch.setenv("SERPER_API_KEY", "test-serper-key")
    profile_id = uuid4()
    spec = SimpleNamespace(config_hash=lambda: "a" * 64)
    state = SimpleNamespace(spec=spec, profile_id=profile_id)
    discovery = SimpleNamespace(
        queries=("query",),
        raw_result_count=4,
        filtered_result_count=2,
        candidates=(),
        exclusion_counts={"location_or_policy": 2},
    )
    persisted = {
        "new_candidates": 1,
        "refreshed_candidates": 0,
        "suppressed_candidates": 0,
    }
    audit = {
        "checked_count": 1,
        "changed_count": 1,
        "activity_counts": {"active": 1, "closed": 0, "unknown": 0},
    }
    search_client = MagicMock()
    ats_client = MagicMock()
    verifier = MagicMock()

    with (
        patch(
            "app.profile_search_service.load_profile_search_state",
            return_value=state,
        ),
        patch(
            "app.profile_search_service.SerperClient",
            return_value=search_client,
        ),
        patch(
            "app.profile_search_service.PublicATSClient",
            return_value=ats_client,
        ),
        patch(
            "app.profile_search_service.JobBoardActivityVerifier",
            return_value=verifier,
        ),
        patch("app.profile_search_service.JobBoardSearchConnector"),
        patch(
            "app.profile_search_service.discover_profile_candidates",
            return_value=discovery,
        ),
        patch(
            "app.profile_search_service.persist_profile_candidates",
            return_value=persisted,
        ),
        patch(
            "app.profile_search_service.audit_job_board_activity",
            return_value=audit,
        ) as audit_call,
        patch(
            "app.profile_search_service.reconcile_profile_candidates",
            return_value=0,
        ),
        patch("app.profile_search_service.record_profile_job_search"),
    ):
        result = run_profile_job_search(
            MagicMock(),
            profile_label="aslinur-default",
        )

    assert result["activity_counts"]["active"] == 1
    assert result["activity_checked_count"] == 1
    assert audit_call.call_args.kwargs["profile_id"] == profile_id
    assert audit_call.call_args.kwargs["only_unverified"] is True
    assert audit_call.call_args.kwargs["limit"] == 20


def test_search_snapshot_keeps_suppressed_match_reason() -> None:
    candidate_id = uuid4()
    match = SimpleNamespace(
        listing=SimpleNamespace(
            provider="linkedin",
            external_id="123456",
        ),
        score=65,
        recommendation="apply",
    )
    stored = SimpleNamespace(
        id=candidate_id,
        provider="linkedin",
        external_id="123456",
        title="AI Engineer",
        company_name_raw="Acme",
        listing_url="https://www.linkedin.com/jobs/view/123456",
        location="İstanbul, Türkiye",
        status="rejected",
        activity_state="closed",
        activity_code="linkedin_closed_marker",
        evidence=[],
    )
    with patch("app.profile_search_service.Session") as session_class:
        session = session_class.return_value.__enter__.return_value
        session.scalars.return_value.all.return_value = [stored]
        result = load_search_candidate_snapshots(
            MagicMock(),
            [match],
        )

    assert result[0]["candidate_id"] == str(candidate_id)
    assert result[0]["disposition"] == "closed"
    assert result[0]["score"] == 65
