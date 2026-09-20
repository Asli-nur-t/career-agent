"""List pending job-board candidates ranked for a candidate profile."""

import argparse
import json
from dataclasses import asdict, dataclass
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.matching import CandidateProfileSpec, JobMatchInput, score_job
from app.models import CandidateProfile, Company, JobBoardCandidate


SUPPORTED_PROVIDERS = ("linkedin", "kariyer", "indeed", "glassdoor")


@dataclass(frozen=True)
class RankedCandidate:
    candidate_id: UUID
    provider: str
    company_name: str
    title: str
    listing_url: str
    score: int
    recommendation: str
    matched_terms: list[str]
    risk_flags: list[str]


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
        skills=profile.skills,
        preferred_locations=profile.preferred_locations,
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
) -> RankedCandidate:
    result = score_job(
        profile,
        JobMatchInput(
            title=title,
            description_text=snippet,
            location=location,
        ),
    )
    return RankedCandidate(
        candidate_id=candidate_id,
        provider=provider,
        company_name=company_name,
        title=title,
        listing_url=listing_url,
        score=result.score,
        recommendation=result.recommendation,
        matched_terms=result.matched_terms,
        risk_flags=result.risk_flags,
    )


def load_queue(
    database: Engine,
    *,
    profile_label: str,
    limit: int,
    minimum_score: int,
    provider: str | None = None,
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
            select(JobBoardCandidate, Company.name)
            .join(Company, Company.id == JobBoardCandidate.company_id)
            .where(JobBoardCandidate.status == "needs_review")
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

    ranked = [
        rank_candidate(
            profile,
            candidate_id=candidate.id,
            provider=candidate.provider,
            company_name=company_name,
            title=candidate.title,
            listing_url=candidate.listing_url,
            snippet=candidate.snippet,
            location=candidate.location,
        )
        for candidate, company_name in rows
    ]
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
    parser.add_argument("--provider", choices=SUPPORTED_PROVIDERS)
    args = parser.parse_args()
    from app.database import engine

    try:
        candidates = load_queue(
            engine,
            profile_label=args.profile,
            limit=args.limit,
            minimum_score=args.minimum_score,
            provider=args.provider,
        )
    except ValueError as error:
        raise SystemExit(f"İnceleme kuyruğu hazırlanamadı: {error}") from None
    except SQLAlchemyError:
        raise SystemExit("İnceleme kuyruğu veritabanından okunamadı.") from None

    print(json.dumps({
        "profile": args.profile,
        "candidate_count": len(candidates),
        "candidates": [
            {
                **asdict(candidate),
                "candidate_id": str(candidate.candidate_id),
            }
            for candidate in candidates
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
