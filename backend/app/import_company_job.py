"""Stage a manually selected LinkedIn job in the existing review queue."""

import argparse
import json
from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.discover_job_board_jobs import persist_job_board_candidates
from app.discovery.safety import safe_text
from app.discovery.schemas import SearchResult
from app.job_boards import JobBoardListing, normalize_job_board_result
from app.list_company_job_pages import linkedin_company_jobs_url
from app.models import Company, CompanyWebProfile, JobBoardCandidate


MANUAL_ACTIVE_CODE = "manual_linkedin_company_page_confirmation"
WORK_MODES = ("remote", "hybrid", "onsite", "unknown")
EMPLOYMENT_TYPES = (
    "full_time",
    "part_time",
    "contract",
    "internship",
    "temporary",
    "unknown",
)


def prepare_manual_listing(
    *,
    listing_url: str,
    title: str,
    snippet: str | None,
    location: str | None,
    work_mode: str,
    employment_type: str,
    confirmed_active: bool,
) -> JobBoardListing:
    if not confirmed_active:
        raise ValueError("active_confirmation_required")
    listing = normalize_job_board_result(
        SearchResult(
            title=title,
            url=listing_url,
            snippet=snippet or "",
            position=1,
        )
    )
    if listing.provider != "linkedin":
        raise ValueError("linkedin_job_url_required")

    clean_location = safe_text(location, 500) or None
    return replace(
        listing,
        location=clean_location or listing.location,
        work_mode=(
            work_mode if work_mode != "unknown" else listing.work_mode
        ),
        employment_type=(
            employment_type
            if employment_type != "unknown"
            else listing.employment_type
        ),
        activity_state="active",
        activity_code=MANUAL_ACTIVE_CODE,
        activity_url=listing.listing_url,
        activity_checked_at=datetime.now(timezone.utc),
    )


def import_company_job(
    database: Engine,
    *,
    company_id: UUID,
    listing_url: str,
    title: str,
    snippet: str | None = None,
    location: str | None = None,
    work_mode: str = "unknown",
    employment_type: str = "unknown",
    confirmed_active: bool = False,
) -> dict[str, object]:
    listing = prepare_manual_listing(
        listing_url=listing_url,
        title=title,
        snippet=snippet,
        location=location,
        work_mode=work_mode,
        employment_type=employment_type,
        confirmed_active=confirmed_active,
    )

    with Session(database) as session:
        row = session.execute(
            select(
                Company.name,
                CompanyWebProfile.official_linkedin_url,
            )
            .join(
                CompanyWebProfile,
                CompanyWebProfile.company_id == Company.id,
            )
            .where(
                Company.id == company_id,
                CompanyWebProfile.status == "verified",
                CompanyWebProfile.official_linkedin_url.is_not(None),
            )
        ).one_or_none()
    if row is None:
        raise ValueError("verified_company_not_found")
    company_name, company_linkedin_url = row
    company_jobs_url = linkedin_company_jobs_url(company_linkedin_url)
    if company_jobs_url is None:
        raise ValueError("verified_linkedin_company_url_invalid")

    counts = persist_job_board_candidates(
        database,
        company_id=company_id,
        company_name=company_name,
        query=f"manual_company_jobs_page:{company_jobs_url}",
        listings=(listing,),
    )
    with Session(database) as session:
        candidate = session.execute(
            select(
                JobBoardCandidate.id,
                JobBoardCandidate.status,
            ).where(
                JobBoardCandidate.provider == listing.provider,
                JobBoardCandidate.external_id == listing.external_id,
            )
        ).one()

    return {
        "status": "staged" if candidate.status == "needs_review" else "suppressed",
        "candidate_id": str(candidate.id),
        "candidate_status": candidate.status,
        "provider": listing.provider,
        "listing_url": listing.listing_url,
        "company_id": str(company_id),
        "company_name": company_name,
        "linkedin_jobs_url": company_jobs_url,
        **counts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "LinkedIn şirket ilan sekmesinde elle doğrulanan bir ilanı "
            "inceleme kuyruğuna ekler."
        )
    )
    parser.add_argument("--company-id", type=UUID, required=True)
    parser.add_argument("--listing-url", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--snippet")
    parser.add_argument("--location")
    parser.add_argument("--work-mode", choices=WORK_MODES, default="unknown")
    parser.add_argument(
        "--employment-type",
        choices=EMPLOYMENT_TYPES,
        default="unknown",
    )
    parser.add_argument(
        "--confirmed-active",
        action="store_true",
        help="İlanın şirket ilan sekmesinde açık olduğunu elle doğrula.",
    )
    args = parser.parse_args()
    from app.database import engine

    try:
        result = import_company_job(
            engine,
            company_id=args.company_id,
            listing_url=args.listing_url,
            title=args.title,
            snippet=args.snippet,
            location=args.location,
            work_mode=args.work_mode,
            employment_type=args.employment_type,
            confirmed_active=args.confirmed_active,
        )
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

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
