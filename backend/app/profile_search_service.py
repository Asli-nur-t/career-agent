"""Reusable profile job discovery service for CLI and operator workflows."""

import os

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.ats import PublicATSClient
from app.discover_profile_jobs import (
    discover_profile_candidates,
    load_profile_search_state,
    persist_profile_candidates,
    reconcile_profile_candidates,
    record_profile_job_search,
)
from app.discovery.serper import SerperClient, SerperError
from app.discovery.web_verifier import SafeWebsiteVerifier
from app.job_boards import (
    OFFICIAL_ATS_PROVIDERS,
    JobBoardActivityVerifier,
    JobBoardSearchConnector,
)


class ProfileSearchError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def run_profile_job_search(
    database: Engine,
    *,
    profile_label: str,
    max_queries: int = 6,
    max_results: int = 10,
    minimum_score: int = 20,
    delay_seconds: float = 1.0,
) -> dict[str, object]:
    """Run one bounded search and return a UI-safe aggregate result."""
    state = load_profile_search_state(database, profile_label=profile_label)
    serper_key = os.environ.get("SERPER_API_KEY", "").strip()
    if not serper_key:
        raise ProfileSearchError("serper_not_configured")

    profile_hash = state.spec.config_hash()
    try:
        search_client = SerperClient(serper_key)
        ats_client = PublicATSClient(
            timeout_seconds=12.0,
            max_response_bytes=2_000_000,
        )
        with search_client, ats_client:
            activity_verifier = JobBoardActivityVerifier(
                SafeWebsiteVerifier(
                    timeout_seconds=8.0,
                    max_response_bytes=750_000,
                    max_redirects=2,
                    max_pages=1,
                ),
                ats_reader=ats_client,
            )
            discovery = discover_profile_candidates(
                JobBoardSearchConnector(
                    search_client,
                    activity_verifier=activity_verifier,
                    activity_providers=OFFICIAL_ATS_PROVIDERS,
                    expand_official_ats=True,
                ),
                state.spec,
                max_queries=max_queries,
                max_results=max_results,
                minimum_score=minimum_score,
                delay_seconds=delay_seconds,
            )
        persisted = persist_profile_candidates(
            database,
            profile_id=state.profile_id,
            profile_hash=profile_hash,
            candidates=discovery.candidates,
        )
        actionable_count = (
            persisted["new_candidates"] + persisted["refreshed_candidates"]
        )
        reconciled_count = reconcile_profile_candidates(
            database,
            profile_id=state.profile_id,
            profile_hash=profile_hash,
            profile=state.spec,
        )
        record_profile_job_search(
            database,
            profile_id=state.profile_id,
            profile_hash=profile_hash,
            candidate_count=actionable_count,
        )
    except SerperError as error:
        try:
            record_profile_job_search(
                database,
                profile_id=state.profile_id,
                profile_hash=profile_hash,
                error_code=error.code,
            )
        except (SQLAlchemyError, ValueError):
            pass
        raise ProfileSearchError(error.code) from None

    return {
        "query_count": len(discovery.queries),
        "raw_result_count": discovery.raw_result_count,
        "excluded_result_count": discovery.filtered_result_count,
        "matched_candidate_count": len(discovery.candidates),
        "candidate_count": actionable_count,
        "new_candidates": persisted["new_candidates"],
        "refreshed_candidates": persisted["refreshed_candidates"],
        "reconciled_candidate_count": reconciled_count,
        "exclusion_counts": discovery.exclusion_counts or {},
    }
