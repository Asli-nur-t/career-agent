"""List pending job-board candidates ranked for a candidate profile."""

import argparse
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.job_boards import (
    PAGE_VERIFIED_ACTIVE_CODES,
    SUPPORTED_JOB_PROVIDERS,
    UNKNOWN_EMPLOYER,
)
from app.job_metadata import extract_job_metadata
from app.matching import CandidateProfileSpec, JobMatchInput, score_job
from app.models import (
    CandidateProfile,
    Company,
    JobBoardCandidate,
    JobCandidateAssessment,
)


@dataclass(frozen=True)
class RankedCandidate:
    candidate_id: UUID
    provider: str
    company_name: str
    title: str
    listing_url: str
    location: str | None
    work_mode: str
    employment_type: str
    published_at: datetime | None
    activity_state: str
    activity_code: str
    score: int
    recommendation: str
    matched_terms: list[str]
    risk_flags: list[str]
    operator_viewed_at: datetime | None = None


def _bounded_int(value: str, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Değer tam sayı olmalıdır.") from error
    if not minimum <= parsed <= maximum:
        raise argparse.ArgumentTypeError(
            f"Değer {minimum}-{maximum} aralığında olmalıdır."
        )
    return parsed


def _to_spec(profile: CandidateProfile) -> CandidateProfileSpec:
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


def rank_candidate(
    profile: CandidateProfileSpec,
    *,
    candidate_id: UUID,
    provider: str,
    company_name: str,
    title: str,
    listing_url: str,
    snippet: str | None,
    location: str | None,
    work_mode: str = "unknown",
    employment_type: str = "unknown",
    published_at: datetime | None = None,
    activity_state: str = "unknown",
    activity_code: str = "not_checked",
    operator_viewed_at: datetime | None = None,
) -> RankedCandidate:
    extracted = extract_job_metadata(title, snippet)
    effective_location = extracted.location or location
    effective_work_mode = (
        extracted.work_mode
        if extracted.work_mode != "unknown"
        else work_mode
    )
    effective_employment_type = (
        extracted.employment_type
        if extracted.employment_type != "unknown"
        else employment_type
    )
    effective_published_at = published_at or extracted.published_at
    effective_activity_state = activity_state
    effective_activity_code = activity_code
    if activity_state == "unknown" and extracted.activity_state == "closed":
        effective_activity_state = "closed"
        effective_activity_code = extracted.activity_code
    result = score_job(
        profile,
        JobMatchInput(
            title=title,
            description_text=snippet,
            location=effective_location,
            employment_type=effective_employment_type,
            work_mode=effective_work_mode,
            published_at=effective_published_at,
        ),
    )
    risk_flags = list(result.risk_flags)
    if company_name == UNKNOWN_EMPLOYER:
        risk_flags.append("company_name_unknown")
    recommendation = result.recommendation
    score = result.score
    if effective_activity_state == "closed":
        score = 0
        recommendation = "skip"
        risk_flags.append("listing_closed")
    elif (
        effective_activity_state != "active"
        or effective_activity_code not in PAGE_VERIFIED_ACTIVE_CODES
    ):
        if recommendation in {"strong_apply", "apply"}:
            recommendation = "review"
        risk_flags.append("activity_unverified")
    if result.recommendation in {"strong_apply", "apply"} and (
        "skill_not_matched" in risk_flags
        or "company_name_unknown" in risk_flags
    ):
        recommendation = "review"
        risk_flags.append("candidate_evidence_incomplete")
    return RankedCandidate(
        candidate_id=candidate_id,
        provider=provider,
        company_name=company_name,
        title=title,
        listing_url=listing_url,
        location=effective_location,
        work_mode=effective_work_mode,
        employment_type=effective_employment_type,
        published_at=effective_published_at,
        activity_state=effective_activity_state,
        activity_code=effective_activity_code,
        operator_viewed_at=operator_viewed_at,
        score=score,
        recommendation=recommendation,
        matched_terms=result.matched_terms,
        risk_flags=risk_flags,
    )


def load_queue(
    database: Engine,
    *,
    profile_label: str,
    limit: int,
    minimum_score: int,
    provider: str | None = None,
    include_unverified: bool = False,
) -> list[RankedCandidate]:
    with Session(database) as session:
        stored_profile = session.scalar(
            select(CandidateProfile).where(
                CandidateProfile.label == profile_label
            )
        )
        if stored_profile is None:
            raise ValueError("profile_not_found")
        profile = _to_spec(stored_profile)
        if profile.config_hash() != stored_profile.config_hash:
            raise ValueError("profile_hash_mismatch")

        statement = (
            select(
                JobBoardCandidate,
                func.coalesce(
                    Company.name,
                    JobBoardCandidate.company_name_raw,
                ),
                JobCandidateAssessment,
            )
            .outerjoin(Company, Company.id == JobBoardCandidate.company_id)
            .outerjoin(
                JobCandidateAssessment,
                (
                    JobCandidateAssessment.candidate_id == JobBoardCandidate.id
                )
                & (JobCandidateAssessment.profile_id == stored_profile.id),
            )
            .where(JobBoardCandidate.status == "needs_review")
        )
        if not include_unverified:
            statement = statement.where(
                JobBoardCandidate.activity_state == "active",
                JobBoardCandidate.activity_code.in_(
                    (*PAGE_VERIFIED_ACTIVE_CODES, "manual_operator_listing_confirmation")
                ),
            )
        if provider is not None:
            statement = statement.where(
                JobBoardCandidate.provider == provider
            )
        rows = session.execute(
            statement.order_by(
                JobBoardCandidate.last_seen_at.desc(),
                JobBoardCandidate.id,
            ).limit(limit)
        ).all()

    ranked: list[RankedCandidate] = []
    for candidate, company_name, assessment in rows:
        deterministic = rank_candidate(
            profile,
            candidate_id=candidate.id,
            provider=candidate.provider,
            company_name=company_name,
            title=candidate.title,
            listing_url=candidate.listing_url,
            snippet=candidate.snippet,
            location=candidate.location,
            work_mode=candidate.work_mode,
            employment_type=candidate.employment_type,
            published_at=candidate.published_at,
            activity_state=candidate.activity_state,
            activity_code=candidate.activity_code,
            operator_viewed_at=candidate.operator_viewed_at,
        )
        if assessment is None:
            ranked.append(deterministic)
            continue

        risk_flags = list(dict.fromkeys([
            *deterministic.risk_flags,
            "local_agent_assessment",
        ]))
        score = assessment.score
        recommendation = assessment.recommendation

        # Activity and location policy are hard safety gates. A model score may
        # enrich a valid candidate, but it may never revive a closed or
        # policy-rejected listing.
        if deterministic.activity_state == "closed":
            score = 0
            recommendation = "skip"
        elif deterministic.score == 0 and deterministic.recommendation == "skip":
            score = 0
            recommendation = "skip"
        elif "activity_unverified" in risk_flags and recommendation in {
            "strong_apply",
            "apply",
        }:
            recommendation = "review"

        ranked.append(
            replace(
                deterministic,
                score=score,
                recommendation=recommendation,
                matched_terms=list(assessment.matched_requirements),
                risk_flags=risk_flags,
            )
        )
    return sorted(
        (item for item in ranked if item.score >= minimum_score),
        key=lambda item: (-item.score, item.company_name, item.title),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rank pending job-board candidates without modifying them."
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--limit",
        type=lambda value: _bounded_int(value, minimum=1, maximum=200),
        default=50,
    )
    parser.add_argument(
        "--minimum-score",
        type=lambda value: _bounded_int(value, minimum=0, maximum=100),
        default=0,
    )
    parser.add_argument("--provider", choices=SUPPORTED_JOB_PROVIDERS)
    parser.add_argument(
        "--include-unverified",
        action="store_true",
        help=(
            "Aktifliği doğrulanamayan kayıtları yalnızca denetim amacıyla "
            "göster."
        ),
    )
    args = parser.parse_args()
    from app.database import engine

    try:
        candidates = load_queue(
            engine,
            profile_label=args.profile,
            limit=args.limit,
            minimum_score=args.minimum_score,
            provider=args.provider,
            include_unverified=args.include_unverified,
        )
    except ValueError as error:
        raise SystemExit(f"İnceleme kuyruğu hazırlanamadı: {error}") from None
    except SQLAlchemyError:
        raise SystemExit("İnceleme kuyruğu veritabanından okunamadı.") from None

    print(
        json.dumps(
            {
                "profile": args.profile,
                "activity_filter": (
                    "active_and_unverified"
                    if args.include_unverified
                    else "active_only"
                ),
                "candidate_count": len(candidates),
                "candidates": [
                    {
                        **asdict(candidate),
                        "candidate_id": str(candidate.candidate_id),
                        "published_at": (
                            candidate.published_at.isoformat()
                            if candidate.published_at
                            else None
                        ),
                    }
                    for candidate in candidates
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
