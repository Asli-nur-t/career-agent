"""Refresh pending job-board metadata and explicit activity signals."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ats import PublicATSClient
from app.discovery.web_verifier import SafeWebsiteVerifier
from app.job_boards import (
    UNKNOWN_EMPLOYER,
    JobBoardActivity,
    JobBoardActivityVerifier,
    JobBoardListing,
    infer_job_board_company,
)
from app.job_metadata import ExtractedJobMetadata, extract_job_metadata
from app.models import JobBoardCandidate


@dataclass(frozen=True)
class CandidateSnapshot:
    id: UUID
    provider: str
    external_id: str
    listing_url: str
    title: str
    snippet: str | None
    company_id: UUID | None
    company_name_raw: str | None
    location: str | None
    work_mode: str
    employment_type: str
    published_at: datetime | None


@dataclass(frozen=True)
class CandidateAudit:
    candidate: CandidateSnapshot
    metadata: ExtractedJobMetadata
    activity: JobBoardActivity
    company_name_raw: str


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


def _inspect_candidate(
    candidate: CandidateSnapshot,
    verifier: JobBoardActivityVerifier,
) -> CandidateAudit:
    metadata = extract_job_metadata(candidate.title, candidate.snippet)
    listing = JobBoardListing(
        provider=candidate.provider,
        external_id=candidate.external_id,
        listing_url=candidate.listing_url,
        title=candidate.title,
        snippet=candidate.snippet,
        search_position=1,
        company_name_raw=candidate.company_name_raw,
        location=candidate.location,
        work_mode=candidate.work_mode,
        employment_type=candidate.employment_type,
        published_at=candidate.published_at,
        activity_state=(
            "closed" if metadata.activity_state == "closed" else "unknown"
        ),
        activity_code=(
            metadata.activity_code
            if metadata.activity_state == "closed"
            else "not_checked"
        ),
        activity_checked_at=(
            metadata.activity_checked_at
            if metadata.activity_state == "closed"
            else None
        ),
    )
    activity = verifier.check(listing)
    inferred_company = infer_job_board_company(
        candidate.title,
        candidate.provider,
    )
    return CandidateAudit(
        candidate=candidate,
        metadata=metadata,
        activity=activity,
        company_name_raw=inferred_company or UNKNOWN_EMPLOYER,
    )


def audit_job_board_activity(
    database: Engine,
    *,
    verifier: JobBoardActivityVerifier,
    limit: int,
    workers: int,
    apply: bool,
    profile_id: UUID | None = None,
    only_unverified: bool = False,
) -> dict[str, object]:
    conditions = [JobBoardCandidate.status == "needs_review"]
    if profile_id is not None:
        conditions.append(
            JobBoardCandidate.evidence.contains([
                {
                    "kind": "profile_search_result",
                    "profile_id": str(profile_id),
                }
            ])
        )
    if only_unverified:
        conditions.append(JobBoardCandidate.activity_state == "unknown")
    with Session(database) as session:
        rows = session.scalars(
            select(JobBoardCandidate)
            .where(*conditions)
            .order_by(
                JobBoardCandidate.last_seen_at.desc(),
                JobBoardCandidate.id,
            )
            .limit(limit)
        ).all()
        candidates = [
            CandidateSnapshot(
                id=row.id,
                provider=row.provider,
                external_id=row.external_id,
                listing_url=row.listing_url,
                title=row.title,
                snippet=row.snippet,
                company_id=row.company_id,
                company_name_raw=row.company_name_raw,
                location=row.location,
                work_mode=row.work_mode,
                employment_type=row.employment_type,
                published_at=row.published_at,
            )
            for row in rows
        ]

    with ThreadPoolExecutor(max_workers=workers) as executor:
        audits = list(
            executor.map(
                lambda item: _inspect_candidate(item, verifier),
                candidates,
            )
        )

    changed_count = 0
    if apply:
        for audit in audits:
            with Session(database) as session:
                stored = session.scalar(
                    select(JobBoardCandidate)
                    .where(JobBoardCandidate.id == audit.candidate.id)
                    .with_for_update()
                )
                if (
                    stored is None
                    or stored.status != "needs_review"
                    or stored.listing_url != audit.candidate.listing_url
                ):
                    continue
                metadata = audit.metadata
                activity = audit.activity
                effective_location = activity.location or metadata.location
                effective_work_mode = (
                    activity.work_mode
                    if activity.work_mode != "unknown"
                    else metadata.work_mode
                )
                effective_employment_type = (
                    activity.employment_type
                    if activity.employment_type != "unknown"
                    else metadata.employment_type
                )
                if effective_location:
                    stored.location = effective_location
                if effective_work_mode != "unknown":
                    stored.work_mode = effective_work_mode
                if effective_employment_type != "unknown":
                    stored.employment_type = effective_employment_type
                if stored.published_at is None:
                    stored.published_at = (
                        activity.published_at or metadata.published_at
                    )
                if stored.company_id is None:
                    stored.company_name_raw = audit.company_name_raw
                stored.activity_state = activity.state
                stored.activity_code = activity.code
                stored.activity_checked_at = activity.checked_at
                if activity.state == "closed":
                    stored.status = "rejected"
                    stored.approved_at = None
                evidence = (
                    list(stored.evidence)
                    if isinstance(stored.evidence, list)
                    else []
                )
                evidence.append({
                    "kind": "activity_check",
                    "state": activity.state,
                    "code": activity.code,
                    "url": activity.checked_url,
                    "checked_at": (
                        activity.checked_at.isoformat()
                        if activity.checked_at
                        else None
                    ),
                })
                stored.evidence = evidence[-50:]
                stored.updated_at = datetime.now(timezone.utc)
                session.commit()
                changed_count += 1

    counts = {"active": 0, "closed": 0, "unknown": 0}
    for audit in audits:
        counts[audit.activity.state] += 1
    return {
        "mode": "apply" if apply else "dry_run",
        "checked_count": len(audits),
        "changed_count": changed_count,
        "activity_counts": counts,
        "sample": [
            {
                "candidate_id": str(audit.candidate.id),
                "provider": audit.candidate.provider,
                "title": audit.candidate.title,
                "location": (
                    audit.activity.location
                    or audit.metadata.location
                    or audit.candidate.location
                ),
                "company_name": audit.company_name_raw,
                "activity_state": audit.activity.state,
                "activity_code": audit.activity.code,
            }
            for audit in audits[:20]
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Refresh pending job metadata and explicit activity signals."
        )
    )
    parser.add_argument(
        "--limit",
        type=lambda value: _bounded_int(value, minimum=1, maximum=500),
        default=100,
    )
    parser.add_argument(
        "--workers",
        type=lambda value: _bounded_int(value, minimum=1, maximum=4),
        default=4,
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    from app.database import engine

    try:
        page_reader = SafeWebsiteVerifier(
            timeout_seconds=6.0,
            max_response_bytes=750_000,
            max_redirects=2,
            max_pages=1,
        )
        with PublicATSClient(
            timeout_seconds=8.0,
            max_response_bytes=2_000_000,
        ) as ats_reader:
            result = audit_job_board_activity(
                engine,
                verifier=JobBoardActivityVerifier(
                    page_reader,
                    ats_reader=ats_reader,
                ),
                limit=args.limit,
                workers=args.workers,
                apply=args.apply,
            )
    except (SQLAlchemyError, ValueError):
        raise SystemExit("İlan aktiflik denetimi tamamlanamadı.") from None
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
