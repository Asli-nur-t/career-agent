"""Approve or reject one discovered career source."""

import argparse
import json
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.career_sources import classify_career_source
from app.models import CareerSource, CompanyWebProfile


ALLOWED_EVIDENCE_KINDS = {
    "ats_slug_match",
    "same_site_search",
    "verified_profile_career_url",
    "verified_site_link",
}


def _has_reviewable_evidence(value: object) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(
            isinstance(item, dict)
            and item.get("kind") in ALLOWED_EVIDENCE_KINDS
            and isinstance(item.get("text"), str)
            and bool(item["text"].strip())
            for item in value
        )
    )


def review_source(
    database: object,
    *,
    source_id: UUID,
    approve: bool,
) -> dict[str, object]:
    with Session(database) as session:
        source = session.get(CareerSource, source_id)
        if source is None:
            raise ValueError("source_not_found")

        if approve:
            profile = session.get(CompanyWebProfile, source.company_id)
            if profile is None or profile.status != "verified":
                raise ValueError("company_profile_not_verified")
            try:
                candidate = classify_career_source(source.source_url)
            except ValueError as error:
                raise ValueError("invalid_source_url") from error
            if (
                candidate is None
                or candidate.source_url != source.source_url
                or candidate.source_type != source.source_type
                or candidate.ats_type != source.ats_type
                or candidate.access_strategy != source.access_strategy
                or not _has_reviewable_evidence(source.evidence)
            ):
                raise ValueError("source_data_mismatch")
            source.status = "active"
            source.last_error_code = None
        else:
            source.status = "inactive"
            source.next_check_at = None
        session.commit()
        return {
            "source_id": str(source.id),
            "source_url": source.source_url,
            "status": source.status,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Review one career source.")
    parser.add_argument("--source-id", type=UUID, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--approve", action="store_true")
    action.add_argument("--reject", action="store_true")
    args = parser.parse_args()
    from app.database import engine

    try:
        result = review_source(
            engine,
            source_id=args.source_id,
            approve=args.approve,
        )
    except ValueError as error:
        print(json.dumps({"status": "error", "error_code": str(error)}))
        raise SystemExit(1) from None
    except SQLAlchemyError:
        print(json.dumps({"status": "error", "error_code": "persistence_error"}))
        raise SystemExit(1) from None
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
