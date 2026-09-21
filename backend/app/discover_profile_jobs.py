"""Discover job-board candidates directly from a candidate profile."""

import argparse
import json
import os
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import Engine, and_, case, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ats import PublicATSClient
from app.discovery.serper import SerperClient, SerperError
from app.discovery.web_verifier import SafeWebsiteVerifier
from app.job_boards import (
    OFFICIAL_ATS_PROVIDERS,
    UNKNOWN_EMPLOYER,
    JobBoardActivityVerifier,
    JobBoardListing,
    JobBoardSearchConnector,
    build_profile_job_queries,
)
from app.matching import CandidateProfileSpec, JobMatchInput, score_job
from app.models import CandidateProfile, JobBoardCandidate


@dataclass(frozen=True)
class ProfileSearchState:
    profile_id: UUID
    spec: CandidateProfileSpec
    due: bool
    next_check_at: datetime | None


@dataclass(frozen=True)
class ProfileJobCandidate:
    query: str
    listing: JobBoardListing
    score: int
    recommendation: str
    matched_terms: tuple[str, ...]
    risk_flags: tuple[str, ...]


@dataclass(frozen=True)
class ProfileDiscovery:
    queries: tuple[str, ...]
    raw_result_count: int
    filtered_result_count: int
    candidates: tuple[ProfileJobCandidate, ...]
    query_stats: tuple["ProfileQueryStats", ...] = ()
    exclusion_counts: dict[str, int] | None = None


@dataclass(frozen=True)
class ProfileQueryStats:
    query: str
    source_group: str
    raw_result_count: int
    filtered_result_count: int
    provider_counts: dict[str, int]
    activity_counts: dict[str, int]
    activity_code_counts: dict[str, int]
    accepted_count: int
    exclusion_counts: dict[str, int]


def _bounded_int(
    value: str,
    *,
    minimum: int,
    maximum: int,
    label: str,
) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"{label} tam sayı olmalıdır.") from error
    if not minimum <= parsed <= maximum:
        raise argparse.ArgumentTypeError(
            f"{label} {minimum}-{maximum} aralığında olmalıdır."
        )
    return parsed


def _delay(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Bekleme sayı olmalıdır.") from error
    if not 1 <= parsed <= 60:
        raise argparse.ArgumentTypeError(
            "Bekleme 1-60 saniye aralığında olmalıdır."
        )
    return parsed


def _profile_spec(profile: CandidateProfile) -> CandidateProfileSpec:
    return CandidateProfileSpec(
        label=profile.label,
        target_roles=profile.target_roles,
        secondary_roles=profile.secondary_roles,
        tertiary_roles=profile.tertiary_roles,
        skills=profile.skills,
        preferred_locations=profile.preferred_locations,
        preferred_remote_locations=profile.preferred_remote_locations,
        excluded_locations=profile.excluded_locations,
        allowed_work_modes=profile.allowed_work_modes,
        location_filter_mode=profile.location_filter_mode,
        max_listing_age_days=profile.max_listing_age_days,
        excluded_keywords=profile.excluded_keywords,
        max_years_experience=profile.max_years_experience,
        remote_allowed=profile.remote_allowed,
    )


def load_profile_search_state(
    database: Engine,
    *,
    profile_label: str,
    now: datetime | None = None,
) -> ProfileSearchState:
    reference = now or datetime.now(timezone.utc)
    with Session(database) as session:
        profile = session.scalar(
            select(CandidateProfile).where(
                CandidateProfile.label == profile_label
            )
        )
        if profile is None:
            raise ValueError("profile_not_found")
        spec = _profile_spec(profile)
        profile_hash = spec.config_hash()
        if profile_hash != profile.config_hash:
            raise ValueError("profile_hash_mismatch")
        next_check = profile.job_search_next_check_at
        if next_check is not None and next_check.tzinfo is None:
            next_check = next_check.replace(tzinfo=timezone.utc)
        due = (
            profile.job_search_profile_hash != profile_hash
            or next_check is None
            or next_check <= reference
        )
        return ProfileSearchState(
            profile_id=profile.id,
            spec=spec,
            due=due,
            next_check_at=next_check,
        )


def record_profile_job_search(
    database: Engine,
    *,
    profile_id: UUID,
    profile_hash: str,
    candidate_count: int | None = None,
    error_code: str | None = None,
) -> None:
    if (candidate_count is None) == (error_code is None):
        raise ValueError("scan_result_invalid")
    now = datetime.now(timezone.utc)
    with Session(database) as session:
        profile = session.scalar(
            select(CandidateProfile)
            .where(CandidateProfile.id == profile_id)
            .with_for_update()
        )
        if profile is None or profile.config_hash != profile_hash:
            raise ValueError("profile_state_changed")
        profile.job_search_last_checked_at = now
        profile.job_search_profile_hash = profile_hash
        if error_code is None:
            count = min(max(candidate_count or 0, 0), 1000)
            profile.job_search_last_outcome = (
                "candidates_found" if count else "no_results"
            )
            profile.job_search_last_error_code = None
            profile.job_search_consecutive_failures = 0
            profile.job_search_candidate_count = count
            profile.job_search_next_check_at = now + (
                timedelta(hours=12) if count else timedelta(days=1)
            )
        else:
            failures = min(profile.job_search_consecutive_failures + 1, 1000)
            profile.job_search_last_outcome = "error"
            profile.job_search_last_error_code = error_code[:80]
            profile.job_search_consecutive_failures = failures
            if error_code == "rate_limited":
                wait = timedelta(hours=6)
            elif error_code == "authentication_error":
                wait = timedelta(days=7)
            else:
                wait = timedelta(hours=min(2 ** (failures - 1), 24))
            profile.job_search_next_check_at = now + wait
        profile.updated_at = now
        session.commit()


def discover_profile_candidates(
    connector: JobBoardSearchConnector,
    profile: CandidateProfileSpec,
    *,
    max_queries: int,
    max_results: int,
    minimum_score: int,
    delay_seconds: float = 0,
) -> ProfileDiscovery:
    queries = build_profile_job_queries(
        (
            tuple(profile.target_roles),
            tuple(profile.secondary_roles),
            tuple(profile.tertiary_roles),
        ),
        preferred_locations=tuple(profile.preferred_locations),
        preferred_remote_locations=tuple(
            profile.preferred_remote_locations
        ),
        remote_allowed=profile.remote_allowed,
        published_after=(
            datetime.now(timezone.utc).date()
            - timedelta(days=profile.max_listing_age_days)
        ),
        max_queries=max_queries,
    )
    candidates: dict[tuple[str, str], ProfileJobCandidate] = {}
    raw_count = 0
    filtered_count = 0
    query_stats: list[ProfileQueryStats] = []
    exclusion_counts: Counter[str] = Counter()
    for position, query in enumerate(queries):
        discovery = connector.search_query(query, max_results=max_results)
        raw_count += discovery.raw_result_count
        filtered_count += discovery.filtered_result_count
        query_exclusions: Counter[str] = Counter()
        accepted_count = 0
        for listing in discovery.listings:
            if listing.activity_state == "closed":
                query_exclusions["closed"] += 1
                exclusion_counts["closed"] += 1
                filtered_count += 1
                continue
            result = score_job(
                profile,
                JobMatchInput(
                    title=listing.title,
                    description_text=listing.snippet,
                    location=listing.location,
                    employment_type=listing.employment_type,
                    work_mode=listing.work_mode,
                    published_at=listing.published_at,
                ),
            )
            if (
                profile.location_filter_mode == "require"
                and any(
                    risk in {"location_unknown", "remote_location_unknown"}
                    for risk in result.risk_flags
                )
            ):
                query_exclusions["location_unknown"] += 1
                exclusion_counts["location_unknown"] += 1
                filtered_count += 1
                continue
            role_match = any(
                term.startswith(
                    ("target_role:", "secondary_role:", "tertiary_role:")
                )
                for term in result.matched_terms
            )
            if (
                not role_match
                or result.score < minimum_score
                or result.recommendation == "skip"
            ):
                reason = (
                    "location_or_policy"
                    if result.recommendation == "skip"
                    else "role_or_score"
                )
                query_exclusions[reason] += 1
                exclusion_counts[reason] += 1
                filtered_count += 1
                continue
            accepted_count += 1
            key = (listing.provider, listing.external_id)
            candidate = ProfileJobCandidate(
                query=query,
                listing=listing,
                score=result.score,
                recommendation=result.recommendation,
                matched_terms=tuple(result.matched_terms),
                risk_flags=tuple(result.risk_flags),
            )
            current = candidates.get(key)
            if current is None or candidate.score > current.score:
                candidates[key] = candidate
        query_stats.append(
            ProfileQueryStats(
                query=query,
                source_group=(
                    "official_ats"
                    if any(
                        marker in query
                        for marker in (
                            "jobs.lever.co",
                            "job-boards.greenhouse.io",
                            "jobs.ashbyhq.com",
                        )
                    )
                    else "job_boards"
                ),
                raw_result_count=discovery.raw_result_count,
                filtered_result_count=discovery.filtered_result_count,
                provider_counts=dict(
                    sorted(
                        Counter(
                            listing.provider
                            for listing in discovery.listings
                        ).items()
                    )
                ),
                activity_counts=dict(
                    sorted(
                        Counter(
                            listing.activity_state
                            for listing in discovery.listings
                        ).items()
                    )
                ),
                activity_code_counts=dict(
                    sorted(
                        Counter(
                            listing.activity_code
                            for listing in discovery.listings
                        ).items()
                    )
                ),
                accepted_count=accepted_count,
                exclusion_counts=dict(sorted(query_exclusions.items())),
            )
        )
        if delay_seconds and position + 1 < len(queries):
            time.sleep(delay_seconds)
    return ProfileDiscovery(
        queries=queries,
        raw_result_count=raw_count,
        filtered_result_count=filtered_count,
        candidates=tuple(candidates.values()),
        query_stats=tuple(query_stats),
        exclusion_counts=dict(sorted(exclusion_counts.items())),
    )


def persist_profile_candidates(
    database: Engine,
    *,
    profile_id: UUID,
    profile_hash: str,
    candidates: tuple[ProfileJobCandidate, ...],
) -> dict[str, int]:
    if not candidates:
        return {
            "new_candidates": 0,
            "refreshed_candidates": 0,
            "suppressed_candidates": 0,
        }
    unique = {
        (item.listing.provider, item.listing.external_id): item
        for item in candidates
    }
    keys = list(unique)
    now = datetime.now(timezone.utc)
    with Session(database) as session:
        profile = session.get(CandidateProfile, profile_id)
        if profile is None or profile.config_hash != profile_hash:
            raise ValueError("profile_state_changed")
        existing_statuses = {
            (provider, external_id): status
            for provider, external_id, status in session.execute(
                select(
                    JobBoardCandidate.provider,
                    JobBoardCandidate.external_id,
                    JobBoardCandidate.status,
                ).where(
                    tuple_(
                        JobBoardCandidate.provider,
                        JobBoardCandidate.external_id,
                    ).in_(keys)
                )
            ).all()
        }
        values = []
        for candidate in unique.values():
            item = candidate.listing
            company_name = item.company_name_raw or UNKNOWN_EMPLOYER
            evidence = [
                {
                    "kind": "profile_search_result",
                    "profile_id": str(profile_id),
                    "profile_hash": profile_hash,
                    "query": candidate.query,
                    "url": item.listing_url,
                    "title": item.title,
                    "position": str(item.search_position),
                },
                {
                    "kind": "profile_match",
                    "score": str(candidate.score),
                    "recommendation": candidate.recommendation,
                    "matched_terms": ", ".join(candidate.matched_terms[:10]),
                    "risk_flags": ", ".join(candidate.risk_flags[:10]),
                },
            ]
            values.append(
                {
                    "id": uuid4(),
                    "company_id": None,
                    "provider": item.provider,
                    "external_id": item.external_id,
                    "listing_url": item.listing_url,
                    "title": item.title,
                    "company_name_raw": company_name,
                    "location": item.location,
                    "work_mode": item.work_mode,
                    "employment_type": item.employment_type,
                    "published_at": item.published_at,
                    "activity_state": item.activity_state,
                    "activity_code": item.activity_code,
                    "activity_checked_at": item.activity_checked_at,
                    "snippet": item.snippet,
                    "status": (
                        "rejected"
                        if item.activity_state == "closed"
                        else "needs_review"
                    ),
                    "evidence": evidence,
                    "last_seen_at": now,
                    "updated_at": now,
                }
            )
        statement = insert(JobBoardCandidate).values(values)
        excluded = statement.excluded
        mutable = JobBoardCandidate.status == "needs_review"
        newly_closed = and_(mutable, excluded.status == "rejected")
        statement = statement.on_conflict_do_update(
            constraint="uq_job_board_candidates_provider_external_id",
            set_={
                "listing_url": case(
                    (mutable, excluded.listing_url),
                    else_=JobBoardCandidate.listing_url,
                ),
                "title": case(
                    (mutable, excluded.title),
                    else_=JobBoardCandidate.title,
                ),
                "company_name_raw": case(
                    (
                        and_(
                            mutable,
                            or_(
                                JobBoardCandidate.company_name_raw.is_(None),
                                JobBoardCandidate.company_name_raw
                                == UNKNOWN_EMPLOYER,
                            ),
                        ),
                        excluded.company_name_raw,
                    ),
                    else_=JobBoardCandidate.company_name_raw,
                ),
                "snippet": case(
                    (mutable, excluded.snippet),
                    else_=JobBoardCandidate.snippet,
                ),
                "location": case(
                    (
                        mutable,
                        func.coalesce(
                            excluded.location,
                            JobBoardCandidate.location,
                        ),
                    ),
                    else_=JobBoardCandidate.location,
                ),
                "work_mode": case(
                    (
                        and_(mutable, excluded.work_mode != "unknown"),
                        excluded.work_mode,
                    ),
                    else_=JobBoardCandidate.work_mode,
                ),
                "employment_type": case(
                    (
                        and_(
                            mutable,
                            excluded.employment_type != "unknown",
                        ),
                        excluded.employment_type,
                    ),
                    else_=JobBoardCandidate.employment_type,
                ),
                "published_at": case(
                    (
                        mutable,
                        func.coalesce(
                            JobBoardCandidate.published_at,
                            excluded.published_at,
                        ),
                    ),
                    else_=JobBoardCandidate.published_at,
                ),
                "activity_state": case(
                    (mutable, excluded.activity_state),
                    else_=JobBoardCandidate.activity_state,
                ),
                "activity_code": case(
                    (mutable, excluded.activity_code),
                    else_=JobBoardCandidate.activity_code,
                ),
                "activity_checked_at": case(
                    (mutable, excluded.activity_checked_at),
                    else_=JobBoardCandidate.activity_checked_at,
                ),
                "status": case(
                    (newly_closed, "rejected"),
                    else_=JobBoardCandidate.status,
                ),
                "evidence": case(
                    (mutable, excluded.evidence),
                    else_=JobBoardCandidate.evidence,
                ),
                "last_seen_at": now,
                "updated_at": now,
            },
        )
        session.execute(statement)
        session.commit()
    new_count = sum(key not in existing_statuses for key in keys)
    refreshed = sum(
        existing_statuses.get(key) == "needs_review" for key in keys
    )
    suppressed = len(keys) - new_count - refreshed
    return {
        "new_candidates": new_count,
        "refreshed_candidates": refreshed,
        "suppressed_candidates": suppressed,
    }


def reconcile_profile_candidates(
    database: Engine,
    *,
    profile_id: UUID,
    profile_hash: str,
    profile: CandidateProfileSpec,
    limit: int = 500,
) -> int:
    """Quarantine older profile-search rows that fail current hard gates."""

    if not 1 <= limit <= 500:
        raise ValueError("reconcile_limit_invalid")
    now = datetime.now(timezone.utc)
    with Session(database) as session:
        stored_profile = session.get(CandidateProfile, profile_id)
        if (
            stored_profile is None
            or stored_profile.config_hash != profile_hash
        ):
            raise ValueError("profile_state_changed")
        candidates = session.scalars(
            select(JobBoardCandidate)
            .where(
                JobBoardCandidate.status == "needs_review",
                JobBoardCandidate.evidence.contains([
                    {
                        "kind": "profile_search_result",
                        "profile_id": str(profile_id),
                    }
                ]),
            )
            .order_by(JobBoardCandidate.last_seen_at.desc())
            .limit(limit)
            .with_for_update(of=JobBoardCandidate)
        ).all()
        changed = 0
        for candidate in candidates:
            result = score_job(
                profile,
                JobMatchInput(
                    title=candidate.title,
                    description_text=candidate.snippet,
                    location=candidate.location,
                    employment_type=candidate.employment_type,
                    work_mode=candidate.work_mode,
                    published_at=candidate.published_at,
                ),
            )
            if candidate.activity_state == "closed":
                reason = "closed"
            elif (
                profile.location_filter_mode == "require"
                and any(
                    risk in {"location_unknown", "remote_location_unknown"}
                    for risk in result.risk_flags
                )
            ):
                reason = "location_unknown"
            elif result.recommendation == "skip":
                reason = "location_or_policy"
            else:
                continue
            evidence = (
                list(candidate.evidence)
                if isinstance(candidate.evidence, list)
                else []
            )
            evidence.append({
                "kind": "profile_filter",
                "profile_id": str(profile_id),
                "profile_hash": profile_hash,
                "reason": reason,
                "checked_at": now.isoformat(),
            })
            candidate.evidence = evidence[-50:]
            candidate.status = "filtered_out"
            candidate.approved_at = None
            candidate.updated_at = now
            changed += 1
        session.commit()
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover current jobs from candidate-profile roles."
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--max-queries",
        type=lambda value: _bounded_int(
            value,
            minimum=1,
            maximum=6,
            label="Sorgu limiti",
        ),
        default=6,
    )
    parser.add_argument(
        "--max-results",
        type=lambda value: _bounded_int(
            value,
            minimum=1,
            maximum=10,
            label="Sonuç limiti",
        ),
        default=10,
    )
    parser.add_argument(
        "--minimum-score",
        type=lambda value: _bounded_int(
            value,
            minimum=0,
            maximum=100,
            label="Minimum puan",
        ),
        default=20,
    )
    parser.add_argument("--delay-seconds", type=_delay, default=2.0)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--reconcile-only",
        action="store_true",
        help="Yeni arama yapmadan eski profil adaylarına güncel kapıları uygula.",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.dry_run and args.reconcile_only:
        parser.error("--dry-run ve --reconcile-only birlikte kullanılamaz.")

    from app.database import engine

    try:
        state = load_profile_search_state(
            engine,
            profile_label=args.profile,
        )
        queries = build_profile_job_queries(
            (
                tuple(state.spec.target_roles),
                tuple(state.spec.secondary_roles),
                tuple(state.spec.tertiary_roles),
            ),
            preferred_locations=tuple(state.spec.preferred_locations),
            preferred_remote_locations=tuple(
                state.spec.preferred_remote_locations
            ),
            remote_allowed=state.spec.remote_allowed,
            published_after=(
                datetime.now(timezone.utc).date()
                - timedelta(days=state.spec.max_listing_age_days)
            ),
            max_queries=args.max_queries,
        )
    except (SQLAlchemyError, ValueError) as error:
        code = str(error) if isinstance(error, ValueError) else "database_error"
        print(json.dumps({"status": "error", "error_code": code}))
        raise SystemExit(1) from None

    if args.dry_run:
        print(
            json.dumps(
                {
                    "mode": "dry_run",
                    "profile": state.spec.label,
                    "due": state.due,
                    "next_check_at": (
                        state.next_check_at.isoformat()
                        if state.next_check_at
                        else None
                    ),
                    "queries": list(queries),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if args.reconcile_only:
        try:
            reconciled_count = reconcile_profile_candidates(
                engine,
                profile_id=state.profile_id,
                profile_hash=state.spec.config_hash(),
                profile=state.spec,
            )
        except (SQLAlchemyError, ValueError) as error:
            code = (
                str(error)
                if isinstance(error, ValueError)
                else "database_error"
            )
            print(json.dumps({"status": "error", "error_code": code}))
            raise SystemExit(1) from None
        print(
            json.dumps(
                {
                    "status": "reconciled",
                    "profile": state.spec.label,
                    "reconciled_candidate_count": reconciled_count,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if not state.due and not args.force:
        print(
            json.dumps(
                {
                    "status": "cached",
                    "profile": state.spec.label,
                    "next_check_at": (
                        state.next_check_at.isoformat()
                        if state.next_check_at
                        else None
                    ),
                },
                ensure_ascii=False,
            )
        )
        return

    serper_key = os.environ.get("SERPER_API_KEY", "").strip()
    if not serper_key:
        raise SystemExit("SERPER_API_KEY yapılandırılmamış.")

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
                max_queries=args.max_queries,
                max_results=args.max_results,
                minimum_score=args.minimum_score,
                delay_seconds=args.delay_seconds,
            )
        persisted = persist_profile_candidates(
            engine,
            profile_id=state.profile_id,
            profile_hash=profile_hash,
            candidates=discovery.candidates,
        )
        actionable_candidate_count = (
            persisted["new_candidates"]
            + persisted["refreshed_candidates"]
        )
        reconciled_count = reconcile_profile_candidates(
            engine,
            profile_id=state.profile_id,
            profile_hash=profile_hash,
            profile=state.spec,
        )
        record_profile_job_search(
            engine,
            profile_id=state.profile_id,
            profile_hash=profile_hash,
            candidate_count=actionable_candidate_count,
        )
    except SerperError as error:
        try:
            record_profile_job_search(
                engine,
                profile_id=state.profile_id,
                profile_hash=profile_hash,
                error_code=error.code,
            )
        except (SQLAlchemyError, ValueError):
            pass
        print(json.dumps({"status": "error", "error_code": error.code}))
        raise SystemExit(1) from None
    except (SQLAlchemyError, ValueError) as error:
        code = str(error) if isinstance(error, ValueError) else "database_error"
        print(json.dumps({"status": "error", "error_code": code}))
        raise SystemExit(1) from None

    print(
        json.dumps(
            {
                "status": "completed",
                "profile": state.spec.label,
                "query_count": len(discovery.queries),
                "raw_result_count": discovery.raw_result_count,
                "filtered_result_count": discovery.filtered_result_count,
                "exclusion_counts": discovery.exclusion_counts or {},
                "query_stats": [
                    {
                        "source_group": item.source_group,
                        "query": item.query,
                        "raw_result_count": item.raw_result_count,
                        "filtered_result_count": item.filtered_result_count,
                        "provider_counts": item.provider_counts,
                        "activity_counts": item.activity_counts,
                        "activity_code_counts": item.activity_code_counts,
                        "accepted_count": item.accepted_count,
                        "exclusion_counts": item.exclusion_counts,
                    }
                    for item in discovery.query_stats
                ],
                "matched_candidate_count": len(discovery.candidates),
                "candidate_count": actionable_candidate_count,
                "reconciled_candidate_count": reconciled_count,
                **persisted,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
