from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.discover_profile_jobs import (
    ProfileJobCandidate,
    discover_profile_candidates,
    persist_profile_candidates,
    reconcile_profile_candidates,
    record_profile_job_search,
)
from app.job_boards import JobBoardListing, JobBoardSearch
from app.matching import CandidateProfileSpec


def profile() -> CandidateProfileSpec:
    return CandidateProfileSpec(
        label="test",
        target_roles=["AI Engineer"],
        secondary_roles=["Backend Engineer"],
        tertiary_roles=["Mobile Developer"],
        skills=["Python", "RAG", "Flutter", "Dart"],
        preferred_locations=["İstanbul"],
        preferred_remote_locations=["Türkiye", "Turkey"],
        allowed_work_modes=["remote", "hybrid", "onsite"],
        location_filter_mode="require",
    )


def test_profile_discovery_filters_non_role_results_and_deduplicates() -> None:
    ai = JobBoardListing(
        provider="linkedin",
        external_id="123456",
        listing_url="https://www.linkedin.com/jobs/view/123456",
        title="AI Engineer - New Employer - LinkedIn",
        snippet="Python RAG · 2 gün önce",
        search_position=1,
        company_name_raw="New Employer",
        location="İstanbul, Türkiye",
        work_mode="hybrid",
    )
    unrelated = JobBoardListing(
        provider="linkedin",
        external_id="999999",
        listing_url="https://www.linkedin.com/jobs/view/999999",
        title="Accountant - Other Employer - LinkedIn",
        snippet="Python reporting",
        search_position=2,
        company_name_raw="Other Employer",
        location="İstanbul, Türkiye",
    )
    connector = SimpleNamespace(
        search_query=lambda query, max_results: JobBoardSearch(
            query=query,
            raw_result_count=2,
            filtered_result_count=0,
            listings=(ai, unrelated),
        )
    )

    result = discover_profile_candidates(
        connector,
        profile(),
        max_queries=2,
        max_results=10,
        minimum_score=20,
    )

    assert result.raw_result_count == 4
    assert result.filtered_result_count == 2
    assert len(result.candidates) == 1
    assert result.candidates[0].listing.external_id == "123456"
    assert result.candidates[0].score >= 55
    assert len(result.query_stats) == 2
    assert result.query_stats[0].source_group == "job_boards"
    assert result.query_stats[0].provider_counts == {"linkedin": 2}
    assert result.query_stats[0].accepted_count == 1


def test_profile_discovery_excludes_closed_and_unlocated_results() -> None:
    listings = (
        JobBoardListing(
            provider="lever",
            external_id="closed-job",
            listing_url="https://jobs.lever.co/acme/closed-job",
            title="AI Engineer",
            snippet="Python",
            search_position=1,
            location="İstanbul",
            work_mode="hybrid",
            activity_state="closed",
            activity_code="lever_public_api_missing",
        ),
        JobBoardListing(
            provider="linkedin",
            external_id="unknown-location",
            listing_url="https://www.linkedin.com/jobs/view/1234567",
            title="AI Engineer",
            snippet="Python",
            search_position=2,
        ),
        JobBoardListing(
            provider="lever",
            external_id="foreign-remote",
            listing_url="https://jobs.lever.co/acme/foreign-remote",
            title="AI Engineer",
            snippet="Python",
            search_position=3,
            location="Texas, United States",
            work_mode="remote",
            activity_state="active",
            activity_code="lever_public_api_present",
        ),
    )
    connector = SimpleNamespace(
        search_query=lambda query, max_results: JobBoardSearch(
            query=query,
            raw_result_count=3,
            filtered_result_count=0,
            listings=listings,
        )
    )

    result = discover_profile_candidates(
        connector,
        profile(),
        max_queries=1,
        max_results=10,
        minimum_score=20,
    )

    assert result.candidates == ()
    assert result.exclusion_counts == {
        "closed": 1,
        "location_or_policy": 1,
        "location_unknown": 1,
    }
    assert result.query_stats[0].accepted_count == 0
    assert result.query_stats[0].activity_code_counts == {
        "lever_public_api_missing": 1,
        "lever_public_api_present": 1,
        "not_checked": 1,
    }


def test_zero_result_query_retries_once_without_date_filter() -> None:
    calls: list[str] = []
    listing = JobBoardListing(
        provider="linkedin",
        external_id="fallback-123",
        listing_url="https://www.linkedin.com/jobs/view/12345678",
        title="AI Engineer - Acme - LinkedIn",
        snippet="Python RAG",
        search_position=1,
        company_name_raw="Acme",
        location="İstanbul, Türkiye",
        work_mode="hybrid",
    )

    def search_query(query: str, max_results: int) -> JobBoardSearch:
        calls.append(query)
        results = () if " after:" in query else (listing,)
        return JobBoardSearch(
            query=query,
            raw_result_count=len(results),
            filtered_result_count=0,
            listings=results,
        )

    result = discover_profile_candidates(
        SimpleNamespace(search_query=search_query),
        profile(),
        max_queries=3,
        max_results=10,
        minimum_score=20,
        search_role_groups=(("AI Engineer",),),
        search_sources=("linkedin",),
    )

    assert len(calls) == 2
    assert " after:" in calls[0]
    assert " after:" not in calls[1]
    assert result.raw_result_count == 1
    assert result.query_stats[0].source == "linkedin"
    assert result.query_stats[0].query_variant == "scoped"
    assert result.query_stats[1].query_variant == "date_relaxed"
    assert len(result.candidates) == 1


def test_fallback_queries_never_exceed_total_query_budget() -> None:
    calls: list[str] = []

    def search_query(query: str, max_results: int) -> JobBoardSearch:
        calls.append(query)
        return JobBoardSearch(
            query=query,
            raw_result_count=0,
            filtered_result_count=0,
            listings=(),
        )

    result = discover_profile_candidates(
        SimpleNamespace(search_query=search_query),
        profile(),
        max_queries=5,
        max_results=10,
        minimum_score=20,
        search_role_groups=(("AI Engineer",),),
        search_sources=("linkedin", "kariyer", "indeed"),
    )

    assert len(calls) == 5
    assert len(result.queries) == 5
    assert sum(
        item.query_variant != "scoped" for item in result.query_stats
    ) == 2


def test_first_fallback_round_is_fair_across_all_sources() -> None:
    connector = SimpleNamespace(
        search_query=lambda query, max_results: JobBoardSearch(
            query=query,
            raw_result_count=0,
            filtered_result_count=0,
            listings=(),
        )
    )

    result = discover_profile_candidates(
        connector,
        profile(),
        max_queries=10,
        max_results=10,
        minimum_score=20,
        search_role_groups=(("AI Engineer",),),
        search_sources=(
            "linkedin", "kariyer", "indeed", "glassdoor", "ats",
        ),
    )

    assert len(result.query_stats) == 10
    assert {
        source: sum(item.source == source for item in result.query_stats)
        for source in ("linkedin", "kariyer", "indeed", "glassdoor", "ats")
    } == {
        "linkedin": 2,
        "kariyer": 2,
        "indeed": 2,
        "glassdoor": 2,
        "ats": 2,
    }
    assert any(
        item.source == "ats" and item.query_variant == "broad"
        for item in result.query_stats
    )


def test_tertiary_mobile_role_remains_review_priority() -> None:
    mobile = JobBoardListing(
        provider="linkedin",
        external_id="654321",
        listing_url="https://www.linkedin.com/jobs/view/654321",
        title="Flutter Mobile Developer - New Employer - LinkedIn",
        snippet="Dart · İstanbul · hibrit · 1 gün önce",
        search_position=1,
        company_name_raw="New Employer",
        location="İstanbul, Türkiye",
        work_mode="hybrid",
    )
    connector = SimpleNamespace(
        search_query=lambda query, max_results: JobBoardSearch(
            query=query,
            raw_result_count=1,
            filtered_result_count=0,
            listings=(mobile,),
        )
    )

    result = discover_profile_candidates(
        connector,
        profile(),
        max_queries=3,
        max_results=10,
        minimum_score=20,
    )

    candidate = result.candidates[0]
    assert candidate.recommendation == "review"
    assert "tertiary_role:Mobile Developer" in candidate.matched_terms


def test_successful_profile_search_is_cached() -> None:
    stored = SimpleNamespace(
        config_hash="a" * 64,
        job_search_consecutive_failures=2,
        updated_at=None,
    )
    with patch("app.discover_profile_jobs.Session") as session_class:
        session = session_class.return_value.__enter__.return_value
        session.scalar.return_value = stored
        record_profile_job_search(
            object(),
            profile_id=uuid4(),
            profile_hash="a" * 64,
            candidate_count=4,
        )

    assert stored.job_search_last_outcome == "candidates_found"
    assert stored.job_search_candidate_count == 4
    assert stored.job_search_consecutive_failures == 0
    delay = stored.job_search_next_check_at - stored.job_search_last_checked_at
    assert timedelta(hours=11) < delay <= timedelta(hours=12)
    session.commit.assert_called_once()


def test_profile_search_rate_limit_waits_six_hours() -> None:
    stored = SimpleNamespace(
        config_hash="b" * 64,
        job_search_consecutive_failures=0,
        updated_at=None,
    )
    with patch("app.discover_profile_jobs.Session") as session_class:
        session = session_class.return_value.__enter__.return_value
        session.scalar.return_value = stored
        record_profile_job_search(
            object(),
            profile_id=uuid4(),
            profile_hash="b" * 64,
            error_code="rate_limited",
        )

    assert stored.job_search_last_outcome == "error"
    assert stored.job_search_consecutive_failures == 1
    delay = stored.job_search_next_check_at - stored.job_search_last_checked_at
    assert timedelta(hours=5) < delay <= timedelta(hours=6)


def test_profile_candidates_are_review_only_and_company_optional() -> None:
    profile_id = uuid4()
    profile_hash = "c" * 64
    item = ProfileJobCandidate(
        query='("AI Engineer") site:linkedin.com/jobs/view',
        listing=JobBoardListing(
            provider="linkedin",
            external_id="123456",
            listing_url="https://www.linkedin.com/jobs/view/123456",
            title="AI Engineer - New Employer - LinkedIn",
            snippet="Python RAG",
            search_position=1,
            company_name_raw="New Employer",
            location="İstanbul, Türkiye",
            work_mode="hybrid",
        ),
        score=65,
        recommendation="apply",
        matched_terms=("target_role:AI Engineer", "skill:Python"),
        risk_flags=(),
    )
    existing_result = SimpleNamespace(all=lambda: [])
    with patch("app.discover_profile_jobs.Session") as session_class:
        session = session_class.return_value.__enter__.return_value
        session.get.return_value = SimpleNamespace(config_hash=profile_hash)
        session.execute.side_effect = [existing_result, SimpleNamespace()]
        result = persist_profile_candidates(
            object(),
            profile_id=profile_id,
            profile_hash=profile_hash,
            candidates=(item,),
        )

    statement = session.execute.call_args_list[1].args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert result == {
        "new_candidates": 1,
        "refreshed_candidates": 0,
        "suppressed_candidates": 0,
    }
    assert "ON CONFLICT" in str(compiled)
    assert "needs_review" in compiled.params.values()
    assert "New Employer" in compiled.params.values()
    assert None in compiled.params.values()
    session.commit.assert_called_once()


def test_terminal_profile_candidate_is_counted_as_suppressed() -> None:
    profile_id = uuid4()
    profile_hash = "e" * 64
    item = ProfileJobCandidate(
        query='("Software Engineer") site:linkedin.com/jobs/view',
        listing=JobBoardListing(
            provider="linkedin",
            external_id="4466255577",
            listing_url="https://www.linkedin.com/jobs/view/4466255577",
            title="Junior Software Engineer - Vega Networks",
            snippet="İstanbul",
            search_position=1,
            company_name_raw="Vega Networks",
            location="İstanbul, Türkiye",
        ),
        score=35,
        recommendation="review",
        matched_terms=("secondary_role:Software Engineer",),
        risk_flags=(),
    )
    existing_result = SimpleNamespace(
        all=lambda: [("linkedin", "4466255577", "rejected")]
    )
    with patch("app.discover_profile_jobs.Session") as session_class:
        session = session_class.return_value.__enter__.return_value
        session.get.return_value = SimpleNamespace(config_hash=profile_hash)
        session.execute.side_effect = [existing_result, SimpleNamespace()]
        result = persist_profile_candidates(
            object(),
            profile_id=profile_id,
            profile_hash=profile_hash,
            candidates=(item,),
        )

    assert result == {
        "new_candidates": 0,
        "refreshed_candidates": 0,
        "suppressed_candidates": 1,
    }
    session.commit.assert_called_once()


def test_reconcile_quarantines_previous_foreign_profile_candidate() -> None:
    profile_id = uuid4()
    profile_hash = "d" * 64
    candidate = SimpleNamespace(
        title="AI Engineer",
        snippet="Python",
        location="Texas, United States",
        employment_type="full_time",
        work_mode="remote",
        published_at=None,
        activity_state="unknown",
        evidence=[],
        status="needs_review",
        approved_at=None,
        updated_at=None,
    )
    with patch("app.discover_profile_jobs.Session") as session_class:
        session = session_class.return_value.__enter__.return_value
        session.get.return_value = SimpleNamespace(config_hash=profile_hash)
        session.scalars.return_value.all.return_value = [candidate]

        changed = reconcile_profile_candidates(
            object(),
            profile_id=profile_id,
            profile_hash=profile_hash,
            profile=profile(),
        )

    assert changed == 1
    assert candidate.status == "filtered_out"
    assert candidate.evidence[-1]["reason"] == "location_or_policy"
    session.commit.assert_called_once()
