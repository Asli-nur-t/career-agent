"""List and manually review discovered company web profiles."""

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.discovery.safety import normalize_public_url
from app.list_company_job_pages import linkedin_company_jobs_url
from app.models import Company, CompanyWebProfile


@dataclass(frozen=True)
class ReviewableCompanyProfile:
    company_id: UUID
    company_name: str
    brand_name: str | None
    status: str
    confidence: str
    official_website_url: str | None
    careers_url: str | None
    official_linkedin_url: str | None
    evidence: list[dict[str, str]]


def _bounded_limit(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Limit tam sayı olmalıdır.") from error
    if not 1 <= parsed <= 100:
        raise argparse.ArgumentTypeError("Limit 1-100 aralığında olmalıdır.")
    return parsed


def _validated_urls(
    profile: CompanyWebProfile,
) -> tuple[str, str | None, str | None]:
    try:
        website_url = normalize_public_url(profile.official_website_url)
        careers_url = (
            normalize_public_url(profile.careers_url)
            if profile.careers_url
            else None
        )
    except ValueError as error:
        raise ValueError("profile_url_invalid") from error

    linkedin_url: str | None = None
    if profile.official_linkedin_url:
        jobs_url = linkedin_company_jobs_url(profile.official_linkedin_url)
        if jobs_url is None:
            raise ValueError("profile_linkedin_url_invalid")
        linkedin_url = jobs_url.removesuffix("jobs/")
    return website_url, careers_url, linkedin_url


def load_review_queue(
    database: Engine,
    *,
    limit: int,
) -> list[ReviewableCompanyProfile]:
    with Session(database) as session:
        rows = session.execute(
            select(Company, CompanyWebProfile)
            .join(
                CompanyWebProfile,
                CompanyWebProfile.company_id == Company.id,
            )
            .where(
                CompanyWebProfile.status.in_(
                    ("candidate_found", "needs_review")
                )
            )
            .order_by(Company.name, Company.id)
            .limit(limit)
        ).all()

    return [
        ReviewableCompanyProfile(
            company_id=company.id,
            company_name=company.name,
            brand_name=profile.brand_name,
            status=profile.status,
            confidence=profile.confidence,
            official_website_url=profile.official_website_url,
            careers_url=profile.careers_url,
            official_linkedin_url=profile.official_linkedin_url,
            evidence=(
                profile.evidence
                if isinstance(profile.evidence, list)
                else []
            ),
        )
        for company, profile in rows
    ]


def review_company_profile(
    database: Engine,
    *,
    company_id: UUID,
    approve: bool,
    confirmed_identity: bool = False,
) -> dict[str, object]:
    if approve and not confirmed_identity:
        raise ValueError("identity_confirmation_required")
    if not approve and confirmed_identity:
        raise ValueError("confirmation_not_allowed_for_rejection")

    with Session(database) as session:
        row = session.execute(
            select(Company, CompanyWebProfile)
            .join(
                CompanyWebProfile,
                CompanyWebProfile.company_id == Company.id,
            )
            .where(Company.id == company_id)
            .with_for_update()
        ).one_or_none()
        if row is None:
            raise ValueError("company_profile_not_found")
        company, profile = row

        if not isinstance(profile.evidence, list) or not all(
            isinstance(item, dict) for item in profile.evidence
        ):
            raise ValueError("profile_evidence_invalid")
        if profile.status == "verified":
            if approve:
                return {
                    "company_id": str(company.id),
                    "company_name": company.name,
                    "status": profile.status,
                    "changed": False,
                }
            raise ValueError("verified_profile_protected")
        if profile.status not in {"candidate_found", "needs_review"}:
            raise ValueError("profile_not_reviewable")

        now = datetime.now(timezone.utc)
        decision = "approved" if approve else "rejected"
        if approve:
            website_url, careers_url, linkedin_url = _validated_urls(profile)
            profile.official_website_url = website_url
            profile.careers_url = careers_url
            profile.official_linkedin_url = linkedin_url
            profile.status = "verified"
            profile.last_verified_at = now
        else:
            profile.status = "needs_review"
            profile.last_verified_at = None
        profile.evidence = [
            *profile.evidence,
            {
                "kind": "manual_company_profile_review",
                "decision": decision,
                "reviewed_at": now.isoformat(),
            },
        ]
        profile.updated_at = now
        session.commit()

        return {
            "company_id": str(company.id),
            "company_name": company.name,
            "status": profile.status,
            "changed": True,
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List or manually review discovered company profiles."
    )
    parser.add_argument("--company-id", type=UUID)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--approve", action="store_true")
    action.add_argument("--reject", action="store_true")
    parser.add_argument("--confirmed-identity", action="store_true")
    parser.add_argument("--limit", type=_bounded_limit, default=25)
    args = parser.parse_args()
    from app.database import engine

    if (args.approve or args.reject) and args.company_id is None:
        parser.error("--approve veya --reject için --company-id gereklidir.")
    if args.company_id is not None and not (args.approve or args.reject):
        parser.error("--company-id ile --approve veya --reject gereklidir.")

    try:
        if args.approve or args.reject:
            payload = review_company_profile(
                engine,
                company_id=args.company_id,
                approve=args.approve,
                confirmed_identity=args.confirmed_identity,
            )
        else:
            profiles = load_review_queue(engine, limit=args.limit)
            payload = {
                "count": len(profiles),
                "profiles": [
                    {
                        **asdict(profile),
                        "company_id": str(profile.company_id),
                    }
                    for profile in profiles
                ],
            }
    except ValueError as error:
        print(
            json.dumps(
                {"status": "error", "error_code": str(error)},
                ensure_ascii=False,
            )
        )
        raise SystemExit(1) from None
    except SQLAlchemyError:
        print(
            json.dumps(
                {"status": "error", "error_code": "persistence_error"},
                ensure_ascii=False,
            )
        )
        raise SystemExit(1) from None

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
