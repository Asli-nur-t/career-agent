from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.profile_search_service import (
    ProfileSearchError,
    _source_diagnostics,
    load_search_candidate_snapshots,
    run_profile_job_search,
)
from app.matching import CandidateProfileSpec


def test_profile_search_fails_closed_without_serper_key(monkeypatch) -> None:
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    state = SimpleNamespace(
        spec=SimpleNamespace(
            target_roles=["AI Engineer"],
            secondary_roles=[],
            tertiary_roles=[],
        ),
        profile_id="profile-id",
        due=True,
    )

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


def test_source_diagnostics_distinguish_empty_and_filtered_sources() -> None:
    stats = (
        SimpleNamespace(
            source="linkedin",
            query_variant="scoped",
            raw_result_count=0,
            filtered_result_count=0,
            accepted_count=0,
            exclusion_counts={},
        ),
        SimpleNamespace(
            source="linkedin",
            query_variant="date_relaxed",
            raw_result_count=0,
            filtered_result_count=0,
            accepted_count=0,
            exclusion_counts={},
        ),
        SimpleNamespace(
            source="indeed",
            query_variant="scoped",
            raw_result_count=3,
            filtered_result_count=1,
            accepted_count=0,
            exclusion_counts={"location_or_policy": 2},
        ),
    )

    result = _source_diagnostics(stats, ("linkedin", "indeed"))

    assert result[0] == {
        "source": "linkedin",
        "query_count": 2,
        "fallback_query_count": 1,
        "raw_result_count": 0,
        "normalized_result_count": 0,
        "accepted_count": 0,
        "exclusion_counts": {},
        "outcome": "no_results",
    }
    assert result[1]["outcome"] == "filtered"
    assert result[1]["normalized_result_count"] == 2
    assert result[1]["exclusion_counts"] == {"location_or_policy": 2}


def test_profile_search_audits_only_its_unverified_candidates(monkeypatch) -> None:
    monkeypatch.setenv("SERPER_API_KEY", "test-serper-key")
    profile_id = uuid4()
    spec = SimpleNamespace(
        config_hash=lambda: "a" * 64,
        target_roles=["AI Engineer"],
        secondary_roles=[],
        tertiary_roles=[],
    )
    state = SimpleNamespace(spec=spec, profile_id=profile_id, due=True)
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


def test_profile_search_uses_requested_roles_without_mutating_profile(
    monkeypatch,
) -> None:
    monkeypatch.setenv("SERPER_API_KEY", "test-serper-key")
    profile_id = uuid4()
    original = CandidateProfileSpec(
        label="aslinur-default",
        target_roles=["AI Engineer"],
        skills=["Python"],
    )
    state = SimpleNamespace(spec=original, profile_id=profile_id, due=True)
    discovery = SimpleNamespace(
        queries=("query",),
        raw_result_count=0,
        filtered_result_count=0,
        candidates=(),
        exclusion_counts={},
    )
    persisted = {
        "new_candidates": 0,
        "refreshed_candidates": 0,
        "suppressed_candidates": 0,
    }
    audit = {
        "checked_count": 0,
        "changed_count": 0,
        "activity_counts": {"active": 0, "closed": 0, "unknown": 0},
    }

    with (
        patch(
            "app.profile_search_service.load_profile_search_state",
            return_value=state,
        ),
        patch("app.profile_search_service.SerperClient", return_value=MagicMock()),
        patch("app.profile_search_service.PublicATSClient", return_value=MagicMock()),
        patch("app.profile_search_service.JobBoardActivityVerifier"),
        patch("app.profile_search_service.JobBoardSearchConnector"),
        patch(
            "app.profile_search_service.discover_profile_candidates",
            return_value=discovery,
        ) as discover,
        patch(
            "app.profile_search_service.persist_profile_candidates",
            return_value=persisted,
        ),
        patch(
            "app.profile_search_service.audit_job_board_activity",
            return_value=audit,
        ),
        patch(
            "app.profile_search_service.reconcile_profile_candidates",
            return_value=0,
        ),
        patch("app.profile_search_service.record_profile_job_search"),
    ):
        result = run_profile_job_search(
            MagicMock(),
            profile_label="aslinur-default",
            requested_roles=["Platform Engineer", "Backend Engineer"],
            requested_locations=["Türkiye"],
            requested_work_modes=["remote", "hybrid"],
            requested_sources=["linkedin", "ats"],
            max_listing_age_days=14,
        )

    effective = discover.call_args.args[1]
    assert effective.target_roles == ["Platform Engineer", "Backend Engineer"]
    assert discover.call_args.kwargs["search_role_groups"] == (
        ("Platform Engineer", "Backend Engineer"),
    )
    assert discover.call_args.kwargs["search_sources"] == (
        "linkedin", "ats"
    )
    assert effective.preferred_locations == ["Türkiye"]
    assert effective.preferred_remote_locations == ["Türkiye"]
    assert effective.allowed_work_modes == ["remote", "hybrid"]
    assert effective.remote_allowed is True
    assert effective.max_listing_age_days == 14
    assert original.target_roles == ["AI Engineer"]
    assert result["requested_roles"] == ["Platform Engineer", "Backend Engineer"]
    assert result["search_mode"] == "quick"
    assert result["requested_locations"] == ["Türkiye"]
    assert result["requested_work_modes"] == ["remote", "hybrid"]
    assert result["requested_sources"] == ["linkedin", "ats"]
    assert result["max_listing_age_days"] == 14
    assert result["query_limit"] == 10


def test_deep_search_splits_roles_into_bounded_groups() -> None:
    from app.profile_search_service import _search_role_groups

    assert _search_role_groups(
        [
            "AI Engineer",
            "ML Engineer",
            "NLP Engineer",
            "Backend Engineer",
            "Python Developer",
            "Research Engineer",
            "Data Scientist",
        ],
        "deep",
    ) == (
        ("AI Engineer", "ML Engineer", "NLP Engineer"),
        ("Backend Engineer", "Python Developer", "Research Engineer"),
        ("Data Scientist",),
    )
