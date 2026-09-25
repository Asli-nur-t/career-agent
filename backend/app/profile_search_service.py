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
    PROFILE_JOB_SEARCH_SOURCES,
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
SEARCH_WORK_MODES = {"remote", "hybrid", "onsite"}


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


def _validated_search_locations(values: Sequence[str] | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, (str, bytes)) or len(values) > 3:
        raise ValueError("search_locations_invalid")
    locations: list[str] = []
    seen: set[str] = set()
    for value in values:
        location = safe_text(value, 100)
        marker = location.casefold()
        if location and marker not in seen:
            seen.add(marker)
            locations.append(location)
    return locations


def _validated_work_modes(values: Sequence[str] | None) -> list[str]:
    modes = list(values or ("remote", "hybrid", "onsite"))
    if (
        isinstance(values, (str, bytes))
        or not 1 <= len(modes) <= len(SEARCH_WORK_MODES)
        or len(set(modes)) != len(modes)
        or any(mode not in SEARCH_WORK_MODES for mode in modes)
    ):
        raise ValueError("search_work_modes_invalid")
    return modes


def _validated_search_sources(values: Sequence[str] | None) -> list[str]:
    sources = list(values or PROFILE_JOB_SEARCH_SOURCES)
    if (
        isinstance(values, (str, bytes))
        or not 1 <= len(sources) <= len(PROFILE_JOB_SEARCH_SOURCES)
        or len(set(sources)) != len(sources)
        or any(source not in PROFILE_JOB_SEARCH_SOURCES for source in sources)
    ):
        raise ValueError("search_sources_invalid")
    return sources


def _validated_max_age_days(value: int | None) -> int:
    days = 30 if value is None else value
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 90:
        raise ValueError("search_age_invalid")
    return days


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


def _source_diagnostics(
    query_stats: Sequence[object],
    requested_sources: Sequence[str],
) -> list[dict[str, object]]:
    diagnostics = {
        source: {
            "source": source,
            "query_count": 0,
            "fallback_query_count": 0,
            "raw_result_count": 0,
            "normalized_result_count": 0,
            "accepted_count": 0,
            "exclusion_counts": {},
        }
        for source in requested_sources
    }
    for item in query_stats:
        source = safe_text(getattr(item, "source", ""), 20)
        row = diagnostics.get(source)
        if row is None:
            continue
        raw_count = max(0, int(getattr(item, "raw_result_count", 0)))
        filtered_count = max(
            0, int(getattr(item, "filtered_result_count", 0))
        )
        row["query_count"] += 1
        if getattr(item, "query_variant", "scoped") != "scoped":
            row["fallback_query_count"] += 1
        row["raw_result_count"] += raw_count
        row["normalized_result_count"] += max(
            raw_count - filtered_count, 0
        )
        row["accepted_count"] += max(
            0, int(getattr(item, "accepted_count", 0))
        )
        raw_exclusions = getattr(item, "exclusion_counts", {})
        if isinstance(raw_exclusions, dict):
            exclusions = row["exclusion_counts"]
            for reason, count in raw_exclusions.items():
                cleaned_reason = safe_text(reason, 80)
                if not cleaned_reason:
                    continue
                exclusions[cleaned_reason] = (
                    exclusions.get(cleaned_reason, 0) + max(0, int(count))
                )
    rows: list[dict[str, object]] = []
    for source in requested_sources:
        row = diagnostics[source]
        if row["accepted_count"] > 0:
            row["outcome"] = "matched"
        elif row["raw_result_count"] > 0:
            row["outcome"] = "filtered"
        else:
            row["outcome"] = "no_results"
        rows.append(row)
    return rows


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
    requested_locations: Sequence[str] | None = None,
    requested_work_modes: Sequence[str] | None = None,
    requested_sources: Sequence[str] | None = None,
    max_listing_age_days: int | None = None,
) -> dict[str, object]:
    """Run one bounded search and return a UI-safe aggregate result."""
    state = load_profile_search_state(database, profile_label=profile_label)
    if not state.due and not force:
        raise ProfileSearchError("cached")
    mode = _validated_search_mode(search_mode)
    locations = _validated_search_locations(requested_locations)
    work_modes = _validated_work_modes(
        requested_work_modes
        if requested_work_modes is not None
        else list(getattr(
            state.spec,
            "allowed_work_modes",
            ["remote", "hybrid", "onsite"],
        ))
    )
    sources = _validated_search_sources(requested_sources)
    age_days = _validated_max_age_days(
        max_listing_age_days
        if max_listing_age_days is not None
        else getattr(state.spec, "max_listing_age_days", 30)
    )
    query_limit = min(max_queries, SEARCH_MODE_QUERY_LIMITS[mode])
    effective_profile = state.spec
    effective_roles: list[str] = []
    if requested_roles is not None:
        effective_roles = _validated_search_roles(requested_roles)
        effective_profile = state.spec.model_copy(update={
            "target_roles": effective_roles,
            "secondary_roles": [],
            "tertiary_roles": [],
        })
    else:
        effective_roles = _validated_search_roles(
            (
                list(getattr(state.spec, "target_roles", []))
                + list(getattr(state.spec, "secondary_roles", []))
                + list(getattr(state.spec, "tertiary_roles", []))
            )[:10]
        )
    profile_updates: dict[str, object] = {}
    if requested_work_modes is not None:
        profile_updates.update({
            "allowed_work_modes": work_modes,
            "remote_allowed": "remote" in work_modes,
        })
    if max_listing_age_days is not None:
        profile_updates["max_listing_age_days"] = age_days
    if locations:
        profile_updates.update({
            "preferred_locations": locations,
            "preferred_remote_locations": locations,
        })
    if profile_updates:
        effective_profile = effective_profile.model_copy(update=profile_updates)
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
                search_sources=tuple(sources),
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
        "requested_locations": locations,
        "requested_work_modes": work_modes,
        "requested_sources": sources,
        "source_diagnostics": _source_diagnostics(
            getattr(discovery, "query_stats", ()),
            sources,
        ),
        "max_listing_age_days": age_days,
        "query_limit": query_limit,
        "result_limit_per_query": max_results,
    }
