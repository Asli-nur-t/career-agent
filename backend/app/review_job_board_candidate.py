"""Approve or reject one third-party job-board candidate."""

import argparse
import hashlib
import json
import unicodedata
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.discovery.schemas import SearchResult
from app.company_names import company_name_key
from app.job_boards import UNKNOWN_EMPLOYER, normalize_job_board_result
from app.models import Company, JobBoardCandidate, JobPosting


def _has_reviewable_evidence(candidate: JobBoardCandidate) -> bool:
    evidence = candidate.evidence
    if not isinstance(evidence, list) or not evidence:
        return False
    for item in evidence:
        if not isinstance(item, dict):
            return False
        if (
            item.get("kind") in {"search_result", "profile_search_result"}
            and item.get("url") == candidate.listing_url
            and isinstance(item.get("query"), str)
            and bool(item["query"].strip())
        ):
            return True
    return False


def _validate_candidate(candidate: JobBoardCandidate) -> None:
    has_company = candidate.company_id is not None or bool(
        isinstance(candidate.company_name_raw, str)
        and candidate.company_name_raw.strip()
    )
    if not has_company or not _has_reviewable_evidence(candidate):
        raise ValueError("candidate_data_mismatch")
    try:
        normalized = normalize_job_board_result(
            SearchResult(
                title=candidate.title,
                url=candidate.listing_url,
                snippet=candidate.snippet or "",
                position=1,
            )
        )
    except ValueError as error:
        raise ValueError("candidate_data_mismatch") from error
    if (
        normalized.provider != candidate.provider
        or normalized.external_id != candidate.external_id
        or normalized.listing_url != candidate.listing_url
    ):
        raise ValueError("candidate_data_mismatch")


def _clean_review_company_name(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("company_name_required")
    normalized = unicodedata.normalize("NFKC", value)
    if any(
        unicodedata.category(character).startswith("C")
        for character in normalized
    ):
        raise ValueError("company_name_invalid")
    cleaned = " ".join(normalized.split())
    if not cleaned or len(cleaned) > 500 or cleaned == UNKNOWN_EMPLOYER:
        raise ValueError("company_name_required")
    return cleaned


def _resolve_candidate_company(
    session: Session,
    candidate: JobBoardCandidate,
    *,
    company_name: str | None,
) -> tuple[Company, bool]:
    if candidate.company_id is not None:
        if company_name is not None:
            raise ValueError("company_override_not_allowed")
        company = session.get(Company, candidate.company_id)
        if company is None:
            raise ValueError("candidate_company_not_found")
        return company, False

    name = _clean_review_company_name(
        company_name if company_name is not None else candidate.company_name_raw
    )
    key = company_name_key(name)
    company = session.scalar(select(Company).where(Company.name_key == key))
    created = company is None
    if company is None:
        company = Company(
            id=uuid4(),
            name=name,
            name_key=key,
            sector=None,
            needs_review=True,
        )
        session.add(company)
        session.flush()
    candidate.company_id = company.id
    candidate.company_name_raw = name
    return company, created


def _candidate_content_hash(candidate: JobBoardCandidate) -> str:
    payload = {
        "external_id": candidate.external_id,
        "job_url": candidate.listing_url,
        "apply_url": None,
        "title": candidate.title,
        "location": candidate.location,
        "department": None,
        "employment_type": (
            None
            if candidate.employment_type == "unknown"
            else candidate.employment_type
        ),
        "work_mode": candidate.work_mode,
        "description_text": candidate.snippet,
        "is_remote": (
            True
            if candidate.work_mode == "remote"
            else False
            if candidate.work_mode == "onsite"
            else None
        ),
        "published_at": (
            candidate.published_at.isoformat()
            if candidate.published_at
            else None
        ),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def review_job_board_candidate(
    database: object,
    *,
    candidate_id: UUID,
    approve: bool,
    confirmed_active: bool = False,
    company_name: str | None = None,
) -> dict[str, object]:
    if approve and not confirmed_active:
        raise ValueError("active_confirmation_required")
    if not approve and confirmed_active:
        raise ValueError("confirmation_not_allowed_for_rejection")
    if not approve and company_name is not None:
        raise ValueError("company_override_not_allowed")

    with Session(database) as session:
        candidate = session.scalar(
            select(JobBoardCandidate)
            .where(JobBoardCandidate.id == candidate_id)
            .with_for_update()
        )
        if candidate is None:
            raise ValueError("candidate_not_found")

        existing_posting = session.scalar(
            select(JobPosting).where(
                JobPosting.job_board_candidate_id == candidate.id
            )
        )
        changed = False
        company_created = False
        reviewed_company: Company | None = None

        if approve:
            _validate_candidate(candidate)
            if candidate.status == "rejected":
                raise ValueError("rejected_candidate_cannot_be_approved")
            if candidate.status == "approved":
                if existing_posting is None:
                    raise ValueError("approved_candidate_missing_posting")
                posting = existing_posting
            else:
                if candidate.status != "needs_review":
                    raise ValueError("invalid_candidate_status")
                if existing_posting is not None:
                    raise ValueError("candidate_posting_already_exists")
                reviewed_company, company_created = _resolve_candidate_company(
                    session,
                    candidate,
                    company_name=company_name,
                )
                now = datetime.now(timezone.utc)
                posting = JobPosting(
                    id=uuid4(),
                    career_source_id=None,
                    job_board_candidate_id=candidate.id,
                    external_id=candidate.external_id,
                    job_url=candidate.listing_url,
                    apply_url=None,
                    title=candidate.title,
                    location=candidate.location,
                    department=None,
                    employment_type=(
                        None
                        if candidate.employment_type == "unknown"
                        else candidate.employment_type
                    ),
                    description_text=candidate.snippet,
                    is_remote=(
                        True
                        if candidate.work_mode == "remote"
                        else False
                        if candidate.work_mode == "onsite"
                        else None
                    ),
                    work_mode=candidate.work_mode,
                    published_at=candidate.published_at,
                    content_hash=_candidate_content_hash(candidate),
                    status="active",
                    first_seen_at=now,
                    last_seen_at=now,
                    last_changed_at=now,
                    closed_at=None,
                )
                session.add(posting)
                candidate.status = "approved"
                candidate.approved_at = now
                candidate.updated_at = now
                session.flush()
                changed = True
        else:
            if candidate.status == "approved" or existing_posting is not None:
                raise ValueError("approved_candidate_cannot_be_rejected")
            if candidate.status == "needs_review":
                candidate.status = "rejected"
                candidate.approved_at = None
                candidate.updated_at = datetime.now(timezone.utc)
                changed = True
            elif candidate.status != "rejected":
                raise ValueError("invalid_candidate_status")
            posting = None

        session.commit()
        return {
            "candidate_id": str(candidate.id),
            "provider": candidate.provider,
            "title": candidate.title,
            "listing_url": candidate.listing_url,
            "status": candidate.status,
            "posting_id": str(posting.id) if posting is not None else None,
            "company_id": (
                str(candidate.company_id) if candidate.company_id else None
            ),
            "company_name": (
                reviewed_company.name
                if reviewed_company is not None
                else getattr(candidate, "company_name_raw", None)
            ),
            "company_created": company_created,
            "changed": changed,
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Review one third-party job-board candidate."
    )
    parser.add_argument("--candidate-id", type=UUID, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--approve", action="store_true")
    action.add_argument("--reject", action="store_true")
    parser.add_argument(
        "--confirmed-active",
        action="store_true",
        help="Confirm that the listing was manually opened and is active.",
    )
    parser.add_argument(
        "--company-name",
        help=(
            "Set the reviewed employer name for a profile-discovered job."
        ),
    )
    args = parser.parse_args()
    from app.database import engine

    try:
        result = review_job_board_candidate(
            engine,
            candidate_id=args.candidate_id,
            approve=args.approve,
            confirmed_active=args.confirmed_active,
            company_name=args.company_name,
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

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
