"""Discover review-only job candidates from public search results."""

import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from dotenv import load_dotenv
from sqlalchemy import Engine, and_, case, func, or_, select, tuple_
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
    choose_job_board_identity,
)
from app.models import Company, CompanyWebProfile, JobBoardCandidate


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)
MAX_COMPANIES = 20


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


def _limit(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Limit tam sayı olmalıdır.") from error
    if not 1 <= parsed <= MAX_COMPANIES:
        raise argparse.ArgumentTypeError("Limit 1-20 aralığında olmalıdır.")
    return parsed


def _delay(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Bekleme sayı olmalıdır.") from error
    if not 1 <= parsed <= 60:
        raise argparse.ArgumentTypeError("Bekleme 1-60 saniye olmalıdır.")
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
    search_name = choose_job_board_identity(
        row.name,
        row.brand_name if row.status == "verified" else None,
    )
    return row.id, row.name, search_name


def due_companies(
    database: Engine,
    *,
    limit: int,
) -> list[tuple[UUID, str, str]]:
    now = datetime.now(timezone.utc)
    with Session(database) as session:
        rows = session.execute(
            select(
                Company.id,
                Company.name,
                CompanyWebProfile.brand_name,
            )
            .join(
                CompanyWebProfile,
                CompanyWebProfile.company_id == Company.id,
            )
            .where(
                CompanyWebProfile.status == "verified",
                or_(
                    CompanyWebProfile.job_boards_next_check_at.is_(None),
                    CompanyWebProfile.job_boards_next_check_at <= now,
                ),
            )
            .order_by(Company.name, Company.id)
            .limit(limit)
        ).all()
    return [
        (
            company_id,
            name,
            choose_job_board_identity(name, brand_name),
        )
        for company_id, name, brand_name in rows
    ]


def record_job_board_scan(
    database: Engine,
    *,
    company_id: UUID,
    candidate_count: int | None = None,
    error_code: str | None = None,
) -> None:
    if (candidate_count is None) == (error_code is None):
        raise ValueError("scan_result_invalid")
    now = datetime.now(timezone.utc)
    with Session(database) as session:
        profile = session.scalar(
            select(CompanyWebProfile)
            .where(CompanyWebProfile.company_id == company_id)
            .with_for_update()
        )
        if profile is None:
            raise ValueError("profile_not_found")
        profile.job_boards_last_checked_at = now
        if error_code is None:
            count = min(max(candidate_count or 0, 0), 100)
            profile.job_boards_last_outcome = (
                "candidates_found" if count else "no_results"
            )
            profile.job_boards_last_error_code = None
            profile.job_boards_consecutive_failures = 0
            profile.job_boards_candidate_count = count
            profile.job_boards_next_check_at = now + (
                timedelta(days=1) if count else timedelta(days=7)
            )
        else:
            failures = min(
                profile.job_boards_consecutive_failures + 1,
                1000,
            )
            profile.job_boards_last_outcome = "error"
            profile.job_boards_last_error_code = error_code[:80]
            profile.job_boards_consecutive_failures = failures
            if error_code == "rate_limited":
                delay = timedelta(hours=6)
            elif error_code == "authentication_error":
                delay = timedelta(days=7)
            else:
                delay = timedelta(hours=min(2 ** (failures - 1), 24))
            profile.job_boards_next_check_at = now + delay
        profile.updated_at = now
        session.commit()


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
                        "checked_at": (
                            item.activity_checked_at.isoformat()
                            if item.activity_checked_at
                            else ""
                        ),
                    }
                )
            evidence.append(
                {
                    "kind": "listing_metadata",
                    "location": item.location or "",
                    "work_mode": item.work_mode,
                    "employment_type": item.employment_type,
                    "published_at": (
                        item.published_at.isoformat()
                        if item.published_at
                        else ""
                    ),
                    "published_precision": item.published_precision or "",
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
                "location": case(
                    (
                        JobBoardCandidate.status == "needs_review",
                        func.coalesce(
                            excluded.location,
                            JobBoardCandidate.location,
                        ),
                    ),
                    else_=JobBoardCandidate.location,
                ),
                "work_mode": case(
                    (
                        and_(
                            JobBoardCandidate.status == "needs_review",
                            excluded.work_mode != "unknown",
                        ),
                        excluded.work_mode,
                    ),
                    else_=JobBoardCandidate.work_mode,
                ),
                "employment_type": case(
                    (
                        and_(
                            JobBoardCandidate.status == "needs_review",
                            excluded.employment_type != "unknown",
                        ),
                        excluded.employment_type,
                    ),
                    else_=JobBoardCandidate.employment_type,
                ),
                "published_at": case(
                    (
                        JobBoardCandidate.status == "needs_review",
                        case(
                            (
                                JobBoardCandidate.published_at.is_(None),
                                excluded.published_at,
                            ),
                            (
                                excluded.published_at.is_(None),
                                JobBoardCandidate.published_at,
                            ),
                            else_=func.least(
                                JobBoardCandidate.published_at,
                                excluded.published_at,
                            ),
                        ),
                    ),
                    else_=JobBoardCandidate.published_at,
                ),
                "activity_state": case(
                    (
                        JobBoardCandidate.status == "needs_review",
                        excluded.activity_state,
                    ),
                    else_=JobBoardCandidate.activity_state,
                ),
                "activity_code": case(
                    (
                        JobBoardCandidate.status == "needs_review",
                        excluded.activity_code,
                    ),
                    else_=JobBoardCandidate.activity_code,
                ),
                "activity_checked_at": case(
                    (
                        JobBoardCandidate.status == "needs_review",
                        excluded.activity_checked_at,
                    ),
                    else_=JobBoardCandidate.activity_checked_at,
                ),
                "status": case(
                    (newly_closed, "rejected"),
                    else_=JobBoardCandidate.status,
                ),
                "evidence": case(
                    (
                        JobBoardCandidate.status == "needs_review",
                        excluded.evidence,
                    ),
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
    parser.add_argument("--company-id", type=UUID)
    parser.add_argument("--limit", type=_limit, default=5)
    parser.add_argument("--max-results", type=_max_results, default=10)
    parser.add_argument("--delay-seconds", type=_delay, default=3.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from app.database import engine

    try:
        if args.company_id is not None:
            companies = [company_for_search(engine, args.company_id)]
        else:
            companies = due_companies(engine, limit=args.limit)
    except (SQLAlchemyError, ValueError):
        raise SystemExit("Şirket arama için hazırlanamadı.") from None

    if args.dry_run:
        try:
            payload = {
                "mode": "dry_run",
                "selected_count": len(companies),
                "companies": [
                    {
                        "company_id": str(company_id),
                        "company_name": company_name,
                        "search_name": search_name,
                        "query": build_job_board_query(search_name),
                    }
                    for company_id, company_name, search_name in companies
                ],
            }
        except ValueError:
            raise SystemExit("Şirket arama kimliği geçersiz.") from None
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    serper_key = os.environ.get("SERPER_API_KEY", "").strip()
    if not serper_key:
        raise SystemExit("SERPER_API_KEY yapılandırılmamış.")

    successes = 0
    failures = 0
    new_candidates = 0
    transient_failures = 0
    circuit_open = False
    try:
        search_client = SerperClient(serper_key)
    except ValueError:
        raise SystemExit("Arama sağlayıcısı yapılandırması geçersiz.") from None

    with search_client:
        try:
            page_reader = SafeWebsiteVerifier(
                timeout_seconds=8.0,
                max_response_bytes=750_000,
                max_redirects=2,
                max_pages=1,
            )
            activity_verifier = JobBoardActivityVerifier(page_reader)
            connector = JobBoardSearchConnector(
                search_client,
                activity_verifier=activity_verifier,
            )
        except ValueError:
            raise SystemExit("Sayfa doğrulayıcı yapılandırması geçersiz.") from None

        for position, (company_id, company_name, search_name) in enumerate(
            companies,
            start=1,
        ):
            try:
                discovery = connector.search(
                    search_name,
                    aliases=(company_name,),
                    max_results=args.max_results,
                )
                persisted = persist_job_board_candidates(
                    engine,
                    company_id=company_id,
                    company_name=company_name,
                    query=discovery.query,
                    listings=discovery.listings,
                )
                record_job_board_scan(
                    engine,
                    company_id=company_id,
                    candidate_count=len(discovery.listings),
                )
                successes += 1
                new_candidates += persisted["new_candidates"]
                transient_failures = 0
                output: dict[str, object] = {
                    "status": "completed",
                    "company_id": str(company_id),
                    "company_name": company_name,
                    "raw_result_count": discovery.raw_result_count,
                    "filtered_result_count": (
                        discovery.filtered_result_count
                    ),
                    "candidate_count": len(discovery.listings),
                    **persisted,
                }
            except SerperError as error:
                failures += 1
                try:
                    record_job_board_scan(
                        engine,
                        company_id=company_id,
                        error_code=error.code,
                    )
                except (SQLAlchemyError, ValueError):
                    pass
                output = {
                    "status": "error",
                    "company_id": str(company_id),
                    "company_name": company_name,
                    "error_code": error.code,
                }
                if error.code in {"timeout", "connection_error", "http_error"}:
                    transient_failures += 1
                else:
                    transient_failures = 0
                circuit_open = (
                    error.code in {"rate_limited", "authentication_error"}
                    or transient_failures >= 2
                )
            except ValueError:
                failures += 1
                try:
                    record_job_board_scan(
                        engine,
                        company_id=company_id,
                        error_code="validation_error",
                    )
                except (SQLAlchemyError, ValueError):
                    pass
                output = {
                    "status": "error",
                    "company_id": str(company_id),
                    "company_name": company_name,
                    "error_code": "validation_error",
                }
            except SQLAlchemyError:
                failures += 1
                circuit_open = True
                output = {
                    "status": "error",
                    "company_id": str(company_id),
                    "company_name": company_name,
                    "error_code": "database_error",
                }

            print(json.dumps(output, ensure_ascii=False))
            if circuit_open:
                break
            if position < len(companies):
                time.sleep(args.delay_seconds)

    print(json.dumps({
        "status": "completed_with_errors" if failures else "completed",
        "selected_count": len(companies),
        "processed_count": successes + failures,
        "success_count": successes,
        "failure_count": failures,
        "new_candidates": new_candidates,
        "circuit_open": circuit_open,
    }, ensure_ascii=False))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
