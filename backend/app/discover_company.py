import argparse
import json
import os
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import engine
from app.discovery.graph import (
    CompanyDiscoveryError,
    CompanyDiscoveryGraph,
)
from app.discovery.schemas import CompanyAssessment
from app.models import Company, CompanyWebProfile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env", override=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Discover web sources for one company."
    )
    parser.add_argument(
        "--company-id",
        type=UUID,
        default=None,
        help="Optional company UUID. Defaults to the next unprocessed company.",
    )
    return parser.parse_args()


def choose_company_id(requested_id: UUID | None) -> UUID:
    with Session(engine) as session:
        if requested_id is not None:
            company_id = session.scalar(
                select(Company.id).where(Company.id == requested_id)
            )
        else:
            company_id = session.scalar(
                select(Company.id)
                .outerjoin(
                    CompanyWebProfile,
                    CompanyWebProfile.company_id == Company.id,
                )
                .where(CompanyWebProfile.company_id.is_(None))
                .order_by(Company.name, Company.id)
                .limit(1)
            )

    if company_id is None:
        raise CompanyDiscoveryError("company_not_found")

    return company_id


def main() -> None:
    args = parse_args()

    serper_key = os.environ.get("SERPER_API_KEY", "").strip()
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()

    if not serper_key or not gemini_key:
        raise SystemExit("Gerekli API anahtarları yapılandırılmamış.")

    try:
        company_id = choose_company_id(args.company_id)

        with CompanyDiscoveryGraph(
            engine=engine,
            serper_key=serper_key,
            gemini_key=gemini_key,
        ) as discovery_graph:
            result = discovery_graph.run(company_id)

    except CompanyDiscoveryError as error:
        print(
            json.dumps(
                {
                    "status": "error",
                    "error_code": error.code,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(1) from None

    assessment = result.get("assessment")

    output = {
        "company_id": str(company_id),
        "company_name": result.get("company_name"),
        "query": result.get("active_query"),
        "result_count": len(result.get("search_results", [])),
        "attempt_count": len(result.get("attempts", [])),
        "persisted": result.get("persisted", False),
        "error_code": result.get("error_code"),
        "verification_code": result.get("verification_code"),
        "stored_status": result.get("stored_status"),
        "profile_updated": result.get("profile_updated", False),
        "assessment": (
            assessment.model_dump()
            if isinstance(assessment, CompanyAssessment)
            else None
        ),
    }

    print(json.dumps(output, ensure_ascii=False, indent=2))

    if output["error_code"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
