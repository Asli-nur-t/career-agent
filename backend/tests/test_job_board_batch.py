from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from app.audit_job_board_candidates import audit_candidates
from app.discover_job_board_jobs import due_companies, record_job_board_scan
from app.matching import CandidateProfileSpec
from app.review_job_board_queue import rank_candidate


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
    )

    assert candidate.recommendation == "strong_apply"
    assert candidate.score >= 75
    assert candidate.provider == "linkedin"


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
