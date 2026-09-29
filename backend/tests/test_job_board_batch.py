from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.audit_job_board_candidates import audit_candidates
from app.discover_job_board_jobs import due_companies, record_job_board_scan
from app.job_boards import UNKNOWN_EMPLOYER
from app.matching import CandidateProfileSpec
from app.review_job_board_queue import _to_spec, load_queue, rank_candidate


def test_due_companies_use_verified_brand() -> None:
    company_id = uuid4()
    with patch("app.discover_job_board_jobs.Session") as session_class:
        session = session_class.return_value.__enter__.return_value
        session.execute.return_value.all.return_value = [
            (company_id, "ACME TEKNOLOJİ A.Ş.", "Acme")
        ]
        selected = due_companies(object(), limit=5)

    assert selected == [
        (company_id, "ACME TEKNOLOJİ A.Ş.", "ACME TEKNOLOJİ")
    ]


def test_successful_scan_is_cached() -> None:
    profile = SimpleNamespace(
        job_boards_consecutive_failures=3,
        updated_at=None,
    )
    with patch("app.discover_job_board_jobs.Session") as session_class:
        session = session_class.return_value.__enter__.return_value
        session.scalar.return_value = profile
        record_job_board_scan(
            object(),
            company_id=uuid4(),
            candidate_count=2,
        )

    assert profile.job_boards_last_outcome == "candidates_found"
    assert profile.job_boards_candidate_count == 2
    assert profile.job_boards_consecutive_failures == 0
    delay = profile.job_boards_next_check_at - profile.job_boards_last_checked_at
    assert timedelta(hours=23) < delay <= timedelta(days=1)
    session.commit.assert_called_once()


def test_failed_scan_uses_bounded_backoff() -> None:
    profile = SimpleNamespace(
        job_boards_consecutive_failures=5,
        updated_at=None,
    )
    with patch("app.discover_job_board_jobs.Session") as session_class:
        session = session_class.return_value.__enter__.return_value
        session.scalar.return_value = profile
        record_job_board_scan(
            object(),
            company_id=uuid4(),
            error_code="timeout",
        )

    assert profile.job_boards_last_outcome == "error"
    assert profile.job_boards_consecutive_failures == 6
    assert profile.job_boards_last_error_code == "timeout"
    delay = profile.job_boards_next_check_at - profile.job_boards_last_checked_at
    assert timedelta(hours=23) < delay <= timedelta(hours=24)


def test_review_queue_ranks_without_mutation() -> None:
    profile = CandidateProfileSpec(
        label="test",
        target_roles=["AI Engineer"],
        skills=["Python", "RAG", "LLM", "FastAPI", "PostgreSQL"],
        preferred_locations=["İstanbul"],
        excluded_keywords=["firmware"],
    )
    candidate = rank_candidate(
        profile,
        candidate_id=uuid4(),
        provider="linkedin",
        company_name="ACME",
        title="AI Engineer",
        listing_url="https://www.linkedin.com/jobs/view/123456",
        snippet="Python RAG LLM FastAPI PostgreSQL",
        location="İstanbul",
        activity_state="active",
        activity_code="linkedin_active_marker",
    )

    assert candidate.recommendation == "strong_apply"
    assert candidate.score >= 75
    assert candidate.provider == "linkedin"


def test_review_queue_preserves_tertiary_roles() -> None:
    stored = SimpleNamespace(
        label="test",
        target_roles=["AI Engineer"],
        secondary_roles=["Backend Engineer"],
        tertiary_roles=["Mobile Developer"],
        skills=["Flutter"],
        preferred_locations=[],
        preferred_remote_locations=[],
        excluded_locations=[],
        allowed_work_modes=[],
        location_filter_mode="prefer",
        max_listing_age_days=30,
        excluded_keywords=[],
        max_years_experience=3,
        remote_allowed=True,
    )

    assert _to_spec(stored).tertiary_roles == ["Mobile Developer"]


def test_review_queue_marks_unknown_employer() -> None:
    profile = CandidateProfileSpec(
        label="test",
        target_roles=["AI Engineer"],
        skills=["Python", "RAG", "LLM", "FastAPI", "PostgreSQL"],
    )
    item = rank_candidate(
        profile,
        candidate_id=uuid4(),
        provider="linkedin",
        company_name=UNKNOWN_EMPLOYER,
        title="AI Engineer",
        listing_url="https://www.linkedin.com/jobs/view/123456",
        snippet="Python RAG LLM FastAPI PostgreSQL",
        location=None,
    )

    assert "company_name_unknown" in item.risk_flags
    assert "candidate_evidence_incomplete" in item.risk_flags
    assert item.recommendation == "review"


def test_review_queue_reextracts_location_and_rejects_foreign_remote() -> None:
    profile = CandidateProfileSpec(
        label="strict",
        target_roles=["AI Engineer"],
        skills=["Python"],
        preferred_locations=["İstanbul"],
        preferred_remote_locations=["Türkiye", "Turkey"],
        allowed_work_modes=["remote"],
        location_filter_mode="require",
    )
    item = rank_candidate(
        profile,
        candidate_id=uuid4(),
        provider="linkedin",
        company_name="Acme",
        title="Acme hiring AI Engineer - Remote in Azerbaijan | LinkedIn",
        listing_url="https://www.linkedin.com/jobs/view/123456",
        snippet="Python · Easy Apply",
        location=None,
        activity_state="active",
        activity_code="linkedin_active_marker",
    )

    assert item.location == "Azerbaijan"
    assert item.score == 0
    assert item.recommendation == "skip"
    assert "remote_location_not_eligible" in item.risk_flags


def test_unverified_activity_cannot_be_recommended_for_application() -> None:
    profile = CandidateProfileSpec(
        label="test",
        target_roles=["AI Engineer"],
        skills=["Python"],
    )
    item = rank_candidate(
        profile,
        candidate_id=uuid4(),
        provider="linkedin",
        company_name="Acme",
        title="AI Engineer",
        listing_url="https://www.linkedin.com/jobs/view/123456",
        snippet="Python",
        location=None,
    )

    assert item.recommendation == "review"
    assert "activity_unverified" in item.risk_flags


def test_search_snippet_active_marker_is_not_page_verification() -> None:
    profile = CandidateProfileSpec(
        label="test",
        target_roles=["AI Engineer"],
        skills=["Python"],
    )
    item = rank_candidate(
        profile,
        candidate_id=uuid4(),
        provider="linkedin",
        company_name="Acme",
        title="AI Engineer",
        listing_url="https://www.linkedin.com/jobs/view/123456",
        snippet="Python · Actively recruiting",
        location=None,
        activity_state="active",
        activity_code="search_text_active_marker",
    )

    assert item.recommendation == "review"
    assert "activity_unverified" in item.risk_flags


def test_current_structured_expiry_is_page_verification() -> None:
    profile = CandidateProfileSpec(
        label="test",
        target_roles=["AI Engineer"],
        skills=["Python"],
    )
    item = rank_candidate(
        profile,
        candidate_id=uuid4(),
        provider="linkedin",
        company_name="Acme",
        title="AI Engineer",
        listing_url="https://www.linkedin.com/jobs/view/123456",
        snippet="Python",
        location=None,
        activity_state="active",
        activity_code="linkedin_valid_through_current",
    )

    assert "activity_unverified" not in item.risk_flags


def _stored_profile_for_queue() -> tuple[SimpleNamespace, CandidateProfileSpec]:
    spec = CandidateProfileSpec(
        label="test",
        target_roles=["AI Engineer"],
        skills=["Python"],
        allowed_work_modes=["remote", "hybrid", "onsite"],
    )
    stored = SimpleNamespace(
        id=uuid4(),
        label=spec.label,
        target_roles=spec.target_roles,
        secondary_roles=spec.secondary_roles,
        tertiary_roles=spec.tertiary_roles,
        skills=spec.skills,
        preferred_locations=spec.preferred_locations,
        preferred_remote_locations=spec.preferred_remote_locations,
        excluded_locations=spec.excluded_locations,
        allowed_work_modes=spec.allowed_work_modes,
        location_filter_mode=spec.location_filter_mode,
        max_listing_age_days=spec.max_listing_age_days,
        excluded_keywords=spec.excluded_keywords,
        max_years_experience=spec.max_years_experience,
        remote_allowed=spec.remote_allowed,
        config_hash=spec.config_hash(),
    )
    return stored, spec


def test_queue_prefers_persistent_agent_assessment() -> None:
    stored, _ = _stored_profile_for_queue()
    candidate = SimpleNamespace(
        id=uuid4(),
        provider="linkedin",
        company_id=None,
        company_name_raw="Acme",
        title="AI Engineer",
        listing_url="https://www.linkedin.com/jobs/view/123456",
        snippet="Python role",
        location="İstanbul",
        work_mode="hybrid",
        employment_type="full_time",
        published_at=None,
        activity_state="active",
        activity_code="linkedin_active_marker",
        operator_viewed_at=None,
        last_seen_at=None,
        status="needs_review",
    )
    assessment = SimpleNamespace(
        score=91,
        recommendation="strong_apply",
        matched_requirements=["Python", "AI Engineer"],
    )
    session = MagicMock()
    session.__enter__.return_value = session
    session.scalar.return_value = stored
    session.execute.return_value.all.return_value = [
        (candidate, "Acme", assessment)
    ]

    with patch("app.review_job_board_queue.Session", return_value=session):
        items = load_queue(
            MagicMock(),
            profile_label="test",
            limit=20,
            minimum_score=0,
        )

    assert items[0].score == 91
    assert items[0].recommendation == "strong_apply"
    assert items[0].matched_terms == ["Python", "AI Engineer"]
    assert "local_agent_assessment" in items[0].risk_flags


def test_closed_listing_overrides_persistent_agent_assessment() -> None:
    stored, _ = _stored_profile_for_queue()
    candidate = SimpleNamespace(
        id=uuid4(),
        provider="linkedin",
        company_id=None,
        company_name_raw="Acme",
        title="AI Engineer",
        listing_url="https://www.linkedin.com/jobs/view/123456",
        snippet="Python role",
        location="İstanbul",
        work_mode="hybrid",
        employment_type="full_time",
        published_at=None,
        activity_state="closed",
        activity_code="linkedin_closed_marker",
        operator_viewed_at=None,
        last_seen_at=None,
        status="needs_review",
    )
    assessment = SimpleNamespace(
        score=98,
        recommendation="strong_apply",
        matched_requirements=["Python"],
    )
    session = MagicMock()
    session.__enter__.return_value = session
    session.scalar.return_value = stored
    session.execute.return_value.all.return_value = [
        (candidate, "Acme", assessment)
    ]

    with patch("app.review_job_board_queue.Session", return_value=session):
        items = load_queue(
            MagicMock(),
            profile_label="test",
            limit=20,
            minimum_score=0,
            include_unverified=True,
        )

    assert items[0].score == 0
    assert items[0].recommendation == "skip"
    assert "listing_closed" in items[0].risk_flags


def test_entity_audit_is_dry_run_by_default() -> None:
    unrelated = SimpleNamespace(
        id=uuid4(),
        provider="linkedin",
        external_id="4417373125",
        listing_url="https://www.linkedin.com/jobs/view/4417373125",
        title="Accounting Manager at Active System",
        snippet="Remote role in Brazil",
        evidence=[],
        status="needs_review",
    )
    related = SimpleNamespace(
        id=uuid4(),
        provider="linkedin",
        external_id="2765864334",
        listing_url="https://www.linkedin.com/jobs/view/2765864334",
        title="Python Developer - ABE Teknoloji",
        snippet="İstanbul, Türkiye",
        evidence=[],
        status="needs_review",
    )
    with patch("app.audit_job_board_candidates.Session") as session_class:
        session = session_class.return_value.__enter__.return_value
        session.execute.return_value.all.return_value = [
            (
                unrelated,
                "4ARC YAZILIM TEKNOLOJİLERİ A.Ş.",
                "4ARC",
            ),
            (
                related,
                "ABE TEKNOLOJİ MÜHENDİSLİK HİZMETLERİ A.Ş.",
                "ABE Teknoloji",
            ),
        ]
        result = audit_candidates(object(), limit=500, apply=False)

    assert result["checked_count"] == 2
    assert result["filtered_count"] == 1
    assert result["sample"][0]["candidate_id"] == str(unrelated.id)
    assert unrelated.status == "needs_review"
    session.commit.assert_not_called()


def test_entity_audit_quarantines_mismatch_when_applied() -> None:
    candidate = SimpleNamespace(
        id=uuid4(),
        provider="linkedin",
        external_id="4417373125",
        listing_url="https://www.linkedin.com/jobs/view/4417373125",
        title="Accounting Manager at Active System",
        snippet="Remote role in Brazil",
        evidence=[{"kind": "search_result"}],
        status="needs_review",
        approved_at=None,
        updated_at=None,
    )
    with patch("app.audit_job_board_candidates.Session") as session_class:
        session = session_class.return_value.__enter__.return_value
        session.execute.return_value.all.return_value = [
            (
                candidate,
                "4ARC YAZILIM TEKNOLOJİLERİ A.Ş.",
                "4ARC",
            )
        ]
        result = audit_candidates(object(), limit=500, apply=True)

    assert result["filtered_count"] == 1
    assert candidate.status == "filtered_out"
    assert candidate.evidence[-1]["code"] == "company_identity_not_found"
    session.commit.assert_called_once()
