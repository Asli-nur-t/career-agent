"""Discover review-only job candidates from public search results."""

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from dotenv import load_dotenv
from sqlalchemy import Engine, and_, case, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.discovery.serper import SerperClient, SerperError
from app.discovery.web_verifier import SafeWebsiteVerifier
from app.job_boards import (
    JobBoardActivityVerifier,
    JobBoardListing,
    JobBoardSearchConnector,
    build_job_board_query,
)
from app.models import Company, CompanyWebProfile, JobBoardCandidate


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def _max_results(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "Sonuç limiti tam sayı olmalıdır."
        ) from error
    if not 1 <= parsed <= 10:
        raise argparse.ArgumentTypeError(
            "Sonuç limiti 1-10 aralığında olmalıdır."
        )
    return parsed


def company_for_search(
    database: Engine,
    company_id: UUID,
) -> tuple[UUID, str, str]:
    with Session(database) as session:
        row = session.execute(
            select(
                Company.id,
                Company.name,
                CompanyWebProfile.brand_name,
                CompanyWebProfile.status,
            )
            .outerjoin(
                CompanyWebProfile,
                CompanyWebProfile.company_id == Company.id,
            )
            .where(Company.id == company_id)
        ).one_or_none()
    if row is None:
        raise ValueError("company_not_found")
    search_name = (
        row.brand_name
        if row.status == "verified" and row.brand_name
        else row.name
    )
    return row.id, row.name, search_name


def persist_job_board_candidates(
    database: Engine,
    *,
    company_id: UUID,
    company_name: str,
    query: str,
    listings: tuple[JobBoardListing, ...],
) -> dict[str, int]:
    if not listings:
        return {
            "new_candidates": 0,
            "refreshed_candidates": 0,
            "auto_rejected_candidates": 0,
        }

    unique_listings = {
        (item.provider, item.external_id): item
        for item in listings
    }
    listings = tuple(unique_listings.values())
    now = datetime.now(timezone.utc)
    keys = list(unique_listings)
    with Session(database) as session:
        current_name = session.scalar(
            select(Company.name).where(Company.id == company_id)
        )
        if current_name != company_name:
            raise ValueError("company_state_changed")

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
        existing = set(existing_statuses)

        values = []
        for item in listings:
            evidence = [
                {
                    "kind": "search_result",
                    "query": query,
                    "url": item.listing_url,
                    "title": item.title,
                    "position": str(item.search_position),
                }
            ]
            if item.activity_code != "not_checked":
                evidence.append(
                    {
                        "kind": "activity_check",
                        "state": item.activity_state,
                        "code": item.activity_code,
                        "url": item.activity_url or item.listing_url,
                    }
                )
            values.append({
                "id": uuid4(),
                "company_id": company_id,
                "provider": item.provider,
                "external_id": item.external_id,
                "listing_url": item.listing_url,
                "title": item.title,
                "company_name_raw": company_name,
                "location": None,
                "snippet": item.snippet,
                "status": (
                    "rejected"
                    if item.activity_state == "closed"
                    else "needs_review"
                ),
                "evidence": evidence,
                "last_seen_at": now,
                "updated_at": now,
            })
        statement = insert(JobBoardCandidate).values(values)
        excluded = statement.excluded
        newly_closed = and_(
            JobBoardCandidate.status == "needs_review",
            excluded.status == "rejected",
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_job_board_candidates_provider_external_id",
            set_={
                "listing_url": case(
                    (
                        JobBoardCandidate.status == "needs_review",
                        excluded.listing_url,
                    ),
                    else_=JobBoardCandidate.listing_url,
                ),
                "title": case(
                    (
                        JobBoardCandidate.status == "needs_review",
                        excluded.title,
                    ),
                    else_=JobBoardCandidate.title,
                ),
                "snippet": case(
                    (
                        JobBoardCandidate.status == "needs_review",
                        excluded.snippet,
                    ),
                    else_=JobBoardCandidate.snippet,
                ),
                "status": case(
                    (newly_closed, "rejected"),
                    else_=JobBoardCandidate.status,
                ),
                "evidence": case(
                    (newly_closed, excluded.evidence),
                    else_=JobBoardCandidate.evidence,
                ),
                "last_seen_at": now,
                "updated_at": now,
            },
        )
        session.execute(statement)
        session.commit()

    refreshed = sum(1 for key in keys if key in existing)
    return {
        "new_candidates": len(keys) - refreshed,
        "refreshed_candidates": refreshed,
        "auto_rejected_candidates": sum(
            item.activity_state == "closed"
            and existing_statuses.get(
                (item.provider, item.external_id)
            ) in {None, "needs_review"}
            for item in listings
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Discover review-only listings from supported job boards."
        )
    )
    parser.add_argument("--company-id", type=UUID, required=True)
    parser.add_argument("--max-results", type=_max_results, default=10)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from app.database import engine

    try:
        company_id, company_name, search_name = company_for_search(
            engine,
            args.company_id,
        )
        query = build_job_board_query(search_name)
    except (SQLAlchemyError, ValueError):
        raise SystemExit("Şirket arama için hazırlanamadı.") from None

    if args.dry_run:
        print(
            json.dumps(
                {
                    "mode": "dry_run",
                    "company_id": str(company_id),
                    "company_name": company_name,
                    "search_name": search_name,
                    "query": query,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    serper_key = os.environ.get("SERPER_API_KEY", "").strip()
    if not serper_key:
        raise SystemExit("SERPER_API_KEY yapılandırılmamış.")

    try:
        with SerperClient(serper_key) as search_client:
            page_reader = SafeWebsiteVerifier(
                timeout_seconds=8.0,
                max_response_bytes=750_000,
                max_redirects=2,
                max_pages=1,
            )
            activity_verifier = JobBoardActivityVerifier(page_reader)
            discovery = JobBoardSearchConnector(
                search_client,
                activity_verifier=activity_verifier,
            ).search(
                search_name,
                max_results=args.max_results,
            )
    except SerperError as error:
        print(
            json.dumps(
                {"status": "error", "error_code": error.code},
                ensure_ascii=False,
            )
        )
        raise SystemExit(1) from None
    except ValueError:
        raise SystemExit("Arama sağlayıcısı yapılandırması geçersiz.") from None

    try:
        persisted = persist_job_board_candidates(
            engine,
            company_id=company_id,
            company_name=company_name,
            query=discovery.query,
            listings=discovery.listings,
        )
    except (SQLAlchemyError, ValueError):
        print(
            json.dumps(
                {"status": "error", "error_code": "persistence_error"},
                ensure_ascii=False,
            )
        )
        raise SystemExit(1) from None

    print(
        json.dumps(
            {
                "status": "completed",
                "company_id": str(company_id),
                "raw_result_count": discovery.raw_result_count,
                "candidate_count": len(discovery.listings),
                **persisted,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
