"""Reusable profile job discovery service for CLI and operator workflows."""

import os
from collections.abc import Sequence

from sqlalchemy import Engine, select, tuple_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ats import PublicATSClient
from app.audit_job_board_activity import audit_job_board_activity
from app.discover_profile_jobs import (
    ProfileJobCandidate,
    discover_profile_candidates,
    load_profile_search_state,
    persist_profile_candidates,
    reconcile_profile_candidates,
    record_profile_job_search,
)
from app.discovery.serper import SerperClient, SerperError
from app.discovery.safety import safe_text
from app.discovery.web_verifier import SafeWebsiteVerifier
from app.job_boards import (
    OFFICIAL_ATS_PROVIDERS,
    UNKNOWN_EMPLOYER,
    JobBoardActivityVerifier,
    JobBoardSearchConnector,
)
from app.models import JobBoardCandidate


class ProfileSearchError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


SEARCH_MODE_QUERY_LIMITS = {"quick": 10, "deep": 20}
DEEP_ROLE_GROUP_SIZE = 3


def _validated_search_roles(values: Sequence[str]) -> list[str]:
    if isinstance(values, (str, bytes)) or not 1 <= len(values) <= 10:
        raise ValueError("search_roles_invalid")
    roles: list[str] = []
    seen: set[str] = set()
    for value in values:
        role = safe_text(value, 100)
        marker = role.casefold()
        if role and marker and marker not in seen:
            seen.add(marker)
            roles.append(role)
    if not roles:
        raise ValueError("search_roles_invalid")
    return roles


def _validated_search_mode(value: object) -> str:
    mode = safe_text(value, 20).casefold()
    if mode not in SEARCH_MODE_QUERY_LIMITS:
        raise ValueError("search_mode_invalid")
    return mode


def _search_role_groups(
    roles: Sequence[str],
    search_mode: str,
) -> tuple[tuple[str, ...], ...]:
    cleaned = _validated_search_roles(roles)
    if search_mode == "quick":
        return (tuple(cleaned),)
    return tuple(
        tuple(cleaned[position:position + DEEP_ROLE_GROUP_SIZE])
        for position in range(0, len(cleaned), DEEP_ROLE_GROUP_SIZE)
    )


def _disposition(candidate: JobBoardCandidate) -> str:
    if candidate.status == "approved":
        return "already_approved"
    if candidate.status == "filtered_out":
        evidence = candidate.evidence if isinstance(candidate.evidence, list) else []
        for item in reversed(evidence):
            if isinstance(item, dict) and item.get("kind") == "profile_filter":
                reason = item.get("reason")
                if isinstance(reason, str) and reason:
                    return safe_text(reason, 80)
        return "profile_filtered"
    if candidate.status == "rejected":
        return (
            "closed"
            if candidate.activity_state == "closed"
            else "previously_rejected"
        )
    if candidate.activity_state == "active":
        return "active_review"
    return "activity_unknown"


def load_search_candidate_snapshots(
    database: Engine,
    candidates: Sequence[ProfileJobCandidate],
) -> list[dict[str, object]]:
    ranked = sorted(
        candidates,
        key=lambda item: item.score,
        reverse=True,
    )[:100]
    keyed = {
        (
            item.listing.provider,
            item.listing.external_id,
        ): item
        for item in ranked
    }
    if not keyed:
        return []
    with Session(database) as session:
        stored = session.scalars(
            select(JobBoardCandidate).where(
                tuple_(
                    JobBoardCandidate.provider,
                    JobBoardCandidate.external_id,
                ).in_(list(keyed))
            )
        ).all()
    rows = {
        (candidate.provider, candidate.external_id): candidate
        for candidate in stored
    }
    snapshots: list[dict[str, object]] = []
    for key, match in keyed.items():
        candidate = rows.get(key)
        if candidate is None:
            continue
        snapshots.append({
            "candidate_id": str(candidate.id),
            "provider": safe_text(candidate.provider, 30),
            "title": safe_text(candidate.title, 500),
            "company_name": safe_text(
                candidate.company_name_raw or UNKNOWN_EMPLOYER,
                500,
            ),
            "listing_url": candidate.listing_url,
            "location": (
                safe_text(candidate.location, 500)
                if candidate.location
                else None
            ),
            "score": match.score,
            "recommendation": safe_text(match.recommendation, 30),
            "status": safe_text(candidate.status, 30),
            "activity_state": safe_text(candidate.activity_state, 30),
            "activity_code": safe_text(candidate.activity_code, 80),
            "disposition": _disposition(candidate),
            "operator_viewed_at": (
                candidate.operator_viewed_at.isoformat()
                if getattr(candidate, "operator_viewed_at", None)
                else None
            ),
        })
    return snapshots


def run_profile_job_search(
    database: Engine,
    *,
    profile_label: str,
    max_queries: int = 20,
    max_results: int = 10,
    minimum_score: int = 20,
    delay_seconds: float = 1.0,
    force: bool = False,
    requested_roles: Sequence[str] | None = None,
    search_mode: str = "quick",
) -> dict[str, object]:
    """Run one bounded search and return a UI-safe aggregate result."""
    state = load_profile_search_state(database, profile_label=profile_label)
    if not state.due and not force:
        raise ProfileSearchError("cached")
    mode = _validated_search_mode(search_mode)
    query_limit = min(max_queries, SEARCH_MODE_QUERY_LIMITS[mode])
    effective_profile = state.spec
    effective_roles: list[str] = []
    if requested_roles is not None:
        effective_roles = _validated_search_roles(requested_roles)
        effective_profile = state.spec.model_copy(
            update={
                "target_roles": effective_roles,
                "secondary_roles": [],
                "tertiary_roles": [],
            }
        )
    else:
        effective_roles = _validated_search_roles(
            (
                list(getattr(state.spec, "target_roles", []))
                + list(getattr(state.spec, "secondary_roles", []))
                + list(getattr(state.spec, "tertiary_roles", []))
            )[:10]
        )
    search_role_groups = _search_role_groups(effective_roles, mode)
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
                effective_profile,
                max_queries=query_limit,
                max_results=max_results,
                minimum_score=minimum_score,
                delay_seconds=delay_seconds,
                search_role_groups=search_role_groups,
            )
            persisted = persist_profile_candidates(
                database,
                profile_id=state.profile_id,
                profile_hash=profile_hash,
                candidates=discovery.candidates,
            )
            activity_audit = audit_job_board_activity(
                database,
                verifier=activity_verifier,
                limit=20,
                workers=4,
                apply=True,
                profile_id=state.profile_id,
                only_unverified=True,
            )
        actionable_count = (
            persisted["new_candidates"] + persisted["refreshed_candidates"]
        )
        reconciled_count = reconcile_profile_candidates(
            database,
            profile_id=state.profile_id,
            profile_hash=profile_hash,
            profile=effective_profile,
        )
        matched_candidates = load_search_candidate_snapshots(
            database,
            discovery.candidates,
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
        "suppressed_candidates": persisted["suppressed_candidates"],
        "reconciled_candidate_count": reconciled_count,
        "exclusion_counts": discovery.exclusion_counts or {},
        "activity_checked_count": activity_audit["checked_count"],
        "activity_changed_count": activity_audit["changed_count"],
        "activity_counts": activity_audit["activity_counts"],
        "matched_candidates": matched_candidates,
        "requested_roles": effective_roles,
        "search_mode": mode,
        "query_limit": query_limit,
        "result_limit_per_query": max_results,
    }
