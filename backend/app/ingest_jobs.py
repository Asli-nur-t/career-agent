"""Synchronize public ATS postings from approved career sources."""

import argparse
import json
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import Engine, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ats import ATSFetchError, NormalizedJob, PublicATSClient
from app.models import CareerSource, CompanyWebProfile, JobPosting


MAX_SOURCES = 10
SUPPORTED_ATS = ("greenhouse", "lever", "ashby")


@dataclass(frozen=True)
class ActiveSource:
    source_id: UUID
    company_id: UUID
    source_url: str
    ats_type: str


def select_sources(
    database: Engine,
    *,
    limit: int,
    source_id: UUID | None = None,
) -> list[ActiveSource]:
    with Session(database) as session:
        statement = (
            select(
                CareerSource.id,
                CareerSource.company_id,
                CareerSource.source_url,
                CareerSource.ats_type,
            )
            .join(
                CompanyWebProfile,
                CompanyWebProfile.company_id == CareerSource.company_id,
            )
            .where(
                CareerSource.status == "active",
                CareerSource.source_type == "ats",
                CareerSource.access_strategy == "public_api",
                CareerSource.ats_type.in_(SUPPORTED_ATS),
                CompanyWebProfile.status == "verified",
            )
        )
        if source_id is not None:
            statement = statement.where(CareerSource.id == source_id)
        else:
            statement = statement.where(
                or_(
                    CareerSource.next_check_at.is_(None),
                    CareerSource.next_check_at <= datetime.now(timezone.utc),
                )
            )
        rows = session.execute(
            statement.order_by(CareerSource.created_at, CareerSource.id).limit(limit)
        ).all()
    return [ActiveSource(id_, company_id, url, ats) for id_, company_id, url, ats in rows]


def sync_jobs(
    database: Engine,
    source_data: ActiveSource,
    jobs: list[NormalizedJob],
) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    jobs_by_id = {job.external_id: job for job in jobs}
    with Session(database) as session:
        source = session.get(CareerSource, source_data.source_id)
        profile = session.get(CompanyWebProfile, source_data.company_id)
        if (
            source is None
            or source.status != "active"
            or source.source_type != "ats"
            or source.access_strategy != "public_api"
            or source.ats_type != source_data.ats_type
            or source.source_url != source_data.source_url
            or profile is None
            or profile.status != "verified"
        ):
            raise ValueError("source_state_changed")

        existing = {
            posting.external_id: posting
            for posting in session.scalars(
                select(JobPosting).where(
                    JobPosting.career_source_id == source.id
                )
            ).all()
        }
        created = 0
        updated = 0
        unchanged = 0
        reopened = 0
        for external_id, job in jobs_by_id.items():
            posting = existing.get(external_id)
            if posting is None:
                session.add(
                    JobPosting(
                        career_source_id=source.id,
                        external_id=job.external_id,
                        job_url=job.job_url,
                        apply_url=job.apply_url,
                        title=job.title,
                        location=job.location,
                        department=job.department,
                        employment_type=job.employment_type,
                        description_text=job.description_text,
                        is_remote=job.is_remote,
                        published_at=job.published_at,
                        content_hash=job.content_hash,
                        status="active",
                        first_seen_at=now,
                        last_seen_at=now,
                        last_changed_at=now,
                    )
                )
                created += 1
                continue

            was_closed = posting.status == "closed"
            changed = posting.content_hash != job.content_hash
            posting.job_url = job.job_url
            posting.apply_url = job.apply_url
            posting.title = job.title
            posting.location = job.location
            posting.department = job.department
            posting.employment_type = job.employment_type
            posting.description_text = job.description_text
            posting.is_remote = job.is_remote
            posting.published_at = job.published_at
            posting.content_hash = job.content_hash
            posting.status = "active"
            posting.closed_at = None
            posting.last_seen_at = now
            posting.updated_at = now
            if changed or was_closed:
                posting.last_changed_at = now
                if was_closed:
                    reopened += 1
                else:
                    updated += 1
            else:
                unchanged += 1

        closed = 0
        for external_id, posting in existing.items():
            if external_id not in jobs_by_id and posting.status == "active":
                posting.status = "closed"
                posting.closed_at = now
                posting.last_changed_at = now
                posting.updated_at = now
                closed += 1

        source.last_http_status = 200
        source.consecutive_failures = 0
        source.last_error_code = None
        source.last_checked_at = now
        source.last_success_at = now
        source.next_check_at = now + timedelta(hours=6)
        source.updated_at = now
        session.commit()
    return {
        "fetched": len(jobs_by_id),
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "reopened": reopened,
        "closed": closed,
    }


def record_failure(
    database: Engine,
    source_id: UUID,
    error_code: str,
) -> None:
    now = datetime.now(timezone.utc)
    with Session(database) as session:
        source = session.get(CareerSource, source_id)
        if source is None or source.status != "active":
            return
        failures = min(source.consecutive_failures + 1, 1_000)
        source.consecutive_failures = failures
        source.last_error_code = error_code[:80]
        source.last_checked_at = now
        source.next_check_at = now + timedelta(
            minutes=min(30 * (2 ** min(failures - 1, 5)), 1_440)
        )
        if failures >= 5:
            source.status = "needs_review"
        source.updated_at = now
        session.commit()


def _limit(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Limit sayı olmalıdır.") from error
    if not 1 <= parsed <= MAX_SOURCES:
        raise argparse.ArgumentTypeError("Limit 1-10 aralığında olmalıdır.")
    return parsed


def _delay(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Bekleme sayı olmalıdır.") from error
    if not 1 <= parsed <= 60:
        raise argparse.ArgumentTypeError("Bekleme 1-60 saniye olmalıdır.")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest approved ATS jobs.")
    parser.add_argument("--source-id", type=UUID)
    parser.add_argument("--limit", type=_limit, default=3)
    parser.add_argument("--delay-seconds", type=_delay, default=3.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    from app.database import engine

    try:
        sources = select_sources(
            engine, limit=args.limit, source_id=args.source_id
        )
    except SQLAlchemyError:
        raise SystemExit("Veritabanı kullanılamıyor.") from None
    if args.dry_run:
        print(json.dumps({
            "mode": "dry_run",
            "sources": [
                {
                    "source_id": str(source.source_id),
                    "source_url": source.source_url,
                    "ats_type": source.ats_type,
                }
                for source in sources
            ],
        }, ensure_ascii=False, indent=2))
        return
    if args.source_id is not None and not sources:
        raise SystemExit("Etkin ve desteklenen ATS kaynağı bulunamadı.")

    failures = 0
    with PublicATSClient() as client:
        for index, source in enumerate(sources):
            try:
                jobs = client.fetch(source.source_url, source.ats_type)
                result = sync_jobs(engine, source, jobs)
            except ATSFetchError as error:
                failures += 1
                try:
                    record_failure(engine, source.source_id, error.code)
                except SQLAlchemyError:
                    pass
                output: dict[str, object] = {
                    "status": "error",
                    "error_code": error.code,
                }
            except ValueError:
                failures += 1
                output = {
                    "status": "error",
                    "error_code": "source_state_changed",
                }
            except SQLAlchemyError:
                failures += 1
                output = {
                    "status": "error",
                    "error_code": "persistence_error",
                }
            else:
                output = {"status": "completed", **result}
            output["source_id"] = str(source.source_id)
            print(json.dumps(output, ensure_ascii=False))
            if index + 1 < len(sources):
                time.sleep(args.delay_seconds)

    print(json.dumps({
        "status": "completed" if failures == 0 else "completed_with_errors",
        "selected_count": len(sources),
        "failure_count": failures,
    }))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
