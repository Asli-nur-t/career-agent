from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

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

    assert selected == [(company_id, "ACME TEKNOLOJİ A.Ş.", "Acme")]


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
