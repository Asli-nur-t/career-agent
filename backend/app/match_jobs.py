"""Score active job postings against a persisted candidate profile."""

import argparse
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Engine, and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.matching import (
    MATCHER_VERSION,
    CandidateProfileSpec,
    JobMatchInput,
    score_job,
)
from app.models import CandidateProfile, JobMatch, JobPosting


MAX_JOBS = 500


@dataclass(frozen=True)
class PendingJob:
    id: UUID
    title: str
    description_text: str | None
    location: str | None
    department: str | None
    employment_type: str | None
    is_remote: bool | None
    work_mode: str
    published_at: datetime | None
    content_hash: str


def _limit(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Limit sayı olmalıdır.") from error
    if not 1 <= parsed <= MAX_JOBS:
        raise argparse.ArgumentTypeError("Limit 1-500 aralığında olmalıdır.")
    return parsed


def _to_spec(profile: CandidateProfile) -> CandidateProfileSpec:
    return CandidateProfileSpec(
        label=profile.label,
        target_roles=profile.target_roles,
        secondary_roles=profile.secondary_roles,
        skills=profile.skills,
        preferred_locations=profile.preferred_locations,
        excluded_locations=profile.excluded_locations,
        allowed_work_modes=profile.allowed_work_modes,
        location_filter_mode=profile.location_filter_mode,
        max_listing_age_days=profile.max_listing_age_days,
        excluded_keywords=profile.excluded_keywords,
        max_years_experience=profile.max_years_experience,
        remote_allowed=profile.remote_allowed,
    )


def select_pending_jobs(
    database: Engine,
    *,
    profile_label: str,
    limit: int,
    refresh: bool = False,
) -> tuple[CandidateProfileSpec, UUID, list[PendingJob]]:
    with Session(database) as session:
        profile = session.scalar(
            select(CandidateProfile).where(
                CandidateProfile.label == profile_label
            )
        )
        if profile is None:
            raise ValueError("profile_not_found")
        spec = _to_spec(profile)
        if spec.config_hash() != profile.config_hash:
            raise ValueError("profile_hash_mismatch")

        statement = (
            select(JobPosting)
            .outerjoin(
                JobMatch,
                and_(
                    JobMatch.job_posting_id == JobPosting.id,
                    JobMatch.profile_id == profile.id,
                ),
            )
            .where(JobPosting.status == "active")
        )
        if not refresh:
            statement = statement.where(
                or_(
                    JobMatch.id.is_(None),
                    JobMatch.job_content_hash != JobPosting.content_hash,
                    JobMatch.profile_hash != profile.config_hash,
                    JobMatch.matcher_version != MATCHER_VERSION,
                )
            )
        postings = session.scalars(
            statement.order_by(
                JobPosting.published_at.desc().nullslast(),
                JobPosting.first_seen_at.desc(),
                JobPosting.id,
            ).limit(limit)
        ).all()
        jobs = [
            PendingJob(
                id=posting.id,
                title=posting.title,
                description_text=posting.description_text,
                location=posting.location,
                department=posting.department,
                employment_type=posting.employment_type,
                is_remote=posting.is_remote,
                work_mode=posting.work_mode,
                published_at=posting.published_at,
                content_hash=posting.content_hash,
            )
            for posting in postings
        ]
        return spec, profile.id, jobs


def persist_matches(
    database: Engine,
    *,
    profile_id: UUID,
    profile_hash: str,
    jobs: list[PendingJob],
) -> dict[str, int]:
    counts = {
        "strong_apply": 0,
        "apply": 0,
        "review": 0,
        "skip": 0,
    }
    with Session(database) as session:
        profile = session.get(CandidateProfile, profile_id)
        if profile is None or profile.config_hash != profile_hash:
            raise ValueError("profile_state_changed")
        spec = _to_spec(profile)
        for job in jobs:
            posting = session.get(JobPosting, job.id)
            if (
                posting is None
                or posting.status != "active"
                or posting.content_hash != job.content_hash
            ):
                raise ValueError("job_state_changed")
            result = score_job(
                spec,
                JobMatchInput(
                    title=job.title,
                    description_text=job.description_text,
                    location=job.location,
                    department=job.department,
                    employment_type=job.employment_type,
                    is_remote=job.is_remote,
                    work_mode=job.work_mode,
                    published_at=job.published_at,
                ),
            )
            statement = insert(JobMatch).values(
                profile_id=profile_id,
                job_posting_id=job.id,
                score=result.score,
                recommendation=result.recommendation,
                review_status="new",
                matched_terms=result.matched_terms,
                risk_flags=result.risk_flags,
                reason=result.reason,
                matcher_version=MATCHER_VERSION,
                job_content_hash=job.content_hash,
                profile_hash=profile_hash,
            )
            statement = statement.on_conflict_do_update(
                constraint="uq_job_matches_profile_posting",
                set_={
                    "score": statement.excluded.score,
                    "recommendation": statement.excluded.recommendation,
                    "matched_terms": statement.excluded.matched_terms,
                    "risk_flags": statement.excluded.risk_flags,
                    "reason": statement.excluded.reason,
                    "matcher_version": statement.excluded.matcher_version,
                    "job_content_hash": statement.excluded.job_content_hash,
                    "profile_hash": statement.excluded.profile_hash,
                    "updated_at": func.now(),
                },
            )
            session.execute(statement)
            counts[result.recommendation] += 1
        session.commit()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score active jobs with deterministic matching rules."
    )
    parser.add_argument("--profile", required=True)
    parser.add_argument("--limit", type=_limit, default=100)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    from app.database import engine

    try:
        spec, profile_id, jobs = select_pending_jobs(
            engine,
            profile_label=args.profile,
            limit=args.limit,
            refresh=args.refresh,
        )
        if args.dry_run:
            print(json.dumps({
                "mode": "dry_run",
                "profile": spec.label,
                "selected_count": len(jobs),
                "jobs": [
                    {"job_posting_id": str(job.id), "title": job.title}
                    for job in jobs
                ],
            }, ensure_ascii=False, indent=2))
            return
        counts = persist_matches(
            engine,
            profile_id=profile_id,
            profile_hash=spec.config_hash(),
            jobs=jobs,
        )
    except ValueError as error:
        raise SystemExit(f"Eşleştirme durduruldu: {error}") from None
    except SQLAlchemyError:
        raise SystemExit("Eşleştirme veritabanı işlemi başarısız.") from None

    print(json.dumps({
        "status": "completed",
        "profile": spec.label,
        "matched_count": len(jobs),
        "recommendations": counts,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
