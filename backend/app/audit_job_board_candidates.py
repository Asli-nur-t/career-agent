"""Quarantine pending candidates without verifiable company identity."""

import argparse
import json
from datetime import datetime, timezone

from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.job_boards import JobBoardListing, listing_matches_company
from app.models import (
    Company,
    CompanyWebProfile,
    JobBoardCandidate,
    JobPosting,
)


def _limit(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Limit tam sayı olmalıdır.") from error
    if not 1 <= parsed <= 500:
        raise argparse.ArgumentTypeError("Limit 1-500 aralığında olmalıdır.")
    return parsed


def audit_candidates(
    database: Engine,
    *,
    limit: int,
    apply: bool,
) -> dict[str, object]:
    now = datetime.now(timezone.utc)
    with Session(database) as session:
        statement = (
            select(
                JobBoardCandidate,
                Company.name,
                CompanyWebProfile.brand_name,
            )
            .join(Company, Company.id == JobBoardCandidate.company_id)
            .outerjoin(
                CompanyWebProfile,
                CompanyWebProfile.company_id == Company.id,
            )
            .outerjoin(
                JobPosting,
                JobPosting.job_board_candidate_id == JobBoardCandidate.id,
            )
            .where(
                JobBoardCandidate.status == "needs_review",
                JobPosting.id.is_(None),
            )
            .order_by(JobBoardCandidate.last_seen_at.desc())
            .limit(limit)
        )
        if apply:
            statement = statement.with_for_update(of=JobBoardCandidate)
        rows = session.execute(statement).all()

        mismatches: list[dict[str, str]] = []
        for candidate, company_name, brand_name in rows:
            listing = JobBoardListing(
                provider=candidate.provider,
                external_id=candidate.external_id,
                listing_url=candidate.listing_url,
                title=candidate.title,
                snippet=candidate.snippet,
                search_position=1,
            )
            identities = tuple(
                value
                for value in (company_name, brand_name)
                if isinstance(value, str) and value.strip()
            )
            if listing_matches_company(listing, identities):
                continue
            mismatches.append({
                "candidate_id": str(candidate.id),
                "provider": candidate.provider,
                "company_name": company_name,
                "title": candidate.title,
                "listing_url": candidate.listing_url,
            })
            if apply:
                evidence = (
                    list(candidate.evidence)
                    if isinstance(candidate.evidence, list)
                    else []
                )
                evidence.append({
                    "kind": "entity_check",
                    "state": "mismatch",
                    "code": "company_identity_not_found",
                    "checked_at": now.isoformat(),
                })
                candidate.evidence = evidence
                candidate.status = "filtered_out"
                candidate.approved_at = None
                candidate.updated_at = now
        if apply:
            session.commit()

    return {
        "mode": "apply" if apply else "dry_run",
        "checked_count": len(rows),
        "filtered_count": len(mismatches),
        "sample": mismatches[:20],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Quarantine pending job-board entity mismatches."
    )
    parser.add_argument("--limit", type=_limit, default=500)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from app.database import engine

    try:
        result = audit_candidates(
            engine,
            limit=args.limit,
            apply=args.apply,
        )
    except SQLAlchemyError:
        raise SystemExit("Aday kimliği denetlenemedi.") from None
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
