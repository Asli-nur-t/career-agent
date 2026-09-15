import argparse
import json
import os
import time
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
MAX_BATCH_SIZE = 25
DEFAULT_BATCH_SIZE = 5
DEFAULT_DELAY_SECONDS = 3.0

load_dotenv(PROJECT_ROOT / ".env", override=False)


def batch_size(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "Limit tam sayı olmalıdır."
        ) from error

    if not 1 <= parsed <= MAX_BATCH_SIZE:
        raise argparse.ArgumentTypeError(
            f"Limit 1 ile {MAX_BATCH_SIZE} arasında olmalıdır."
        )

    return parsed


def delay_seconds(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "Bekleme süresi sayı olmalıdır."
        ) from error

    if not 1.0 <= parsed <= 60.0:
        raise argparse.ArgumentTypeError(
            "Bekleme süresi 1 ile 60 saniye arasında olmalıdır."
        )

    return parsed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Discover web sources for a bounded batch of "
            "unprocessed companies."
        )
    )
    parser.add_argument(
        "--limit",
        type=batch_size,
        default=DEFAULT_BATCH_SIZE,
        help=(
            "Number of companies to process. "
            f"Allowed range: 1-{MAX_BATCH_SIZE}. "
            f"Default: {DEFAULT_BATCH_SIZE}."
        ),
    )
    parser.add_argument(
        "--delay-seconds",
        type=delay_seconds,
        default=DEFAULT_DELAY_SECONDS,
        help=(
            "Delay between companies. "
            f"Default: {DEFAULT_DELAY_SECONDS} seconds."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show selected companies without making API requests.",
    )
    return parser.parse_args()


def choose_companies(limit: int) -> list[tuple[UUID, str]]:
    with Session(engine) as session:
        rows = session.execute(
            select(Company.id, Company.name)
            .outerjoin(
                CompanyWebProfile,
                CompanyWebProfile.company_id == Company.id,
            )
            .where(CompanyWebProfile.company_id.is_(None))
            .order_by(Company.name, Company.id)
            .limit(limit)
        ).all()

    return [
        (company_id, company_name)
        for company_id, company_name in rows
    ]


def print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    args = parse_args()
    companies = choose_companies(args.limit)

    if args.dry_run:
        print_json(
            {
                "mode": "dry_run",
                "selected_count": len(companies),
                "companies": [
                    {
                        "company_id": str(company_id),
                        "company_name": company_name,
                    }
                    for company_id, company_name in companies
                ],
            }
        )
        return

    if not companies:
        print_json(
            {
                "status": "completed",
                "selected_count": 0,
                "success_count": 0,
                "failure_count": 0,
                "message": "İşlenmemiş şirket bulunamadı.",
            }
        )
        return

    serper_key = os.environ.get("SERPER_API_KEY", "").strip()
    gemini_key = os.environ.get("GEMINI_API_KEY", "").strip()

    if not serper_key or not gemini_key:
        raise SystemExit(
            "Gerekli API anahtarları yapılandırılmamış."
        )

    success_count = 0
    failure_count = 0

    with CompanyDiscoveryGraph(
        engine=engine,
        serper_key=serper_key,
        gemini_key=gemini_key,
    ) as discovery_graph:
        for position, (company_id, company_name) in enumerate(
            companies,
            start=1,
        ):
            started_at = time.monotonic()

            try:
                result = discovery_graph.run(company_id)
            except CompanyDiscoveryError as error:
                failure_count += 1
                print_json(
                    {
                        "position": position,
                        "company_id": str(company_id),
                        "company_name": company_name,
                        "status": "error",
                        "error_code": error.code,
                    }
                )
            except Exception:
                failure_count += 1
                print_json(
                    {
                        "position": position,
                        "company_id": str(company_id),
                        "company_name": company_name,
                        "status": "error",
                        "error_code": "unexpected_error",
                    }
                )
            else:
                assessment = result.get("assessment")
                error_code = result.get("error_code")
                persisted = bool(result.get("persisted", False))
                succeeded = persisted and not error_code

                if succeeded:
                    success_count += 1
                else:
                    failure_count += 1

                print_json(
                    {
                        "position": position,
                        "company_id": str(company_id),
                        "company_name": result.get(
                            "company_name",
                            company_name,
                        ),
                        "status": (
                            "success" if succeeded else "error"
                        ),
                        "profile_status": (
                            assessment.status
                            if isinstance(
                                assessment,
                                CompanyAssessment,
                            )
                            else None
                        ),
                        "confidence": (
                            assessment.confidence
                            if isinstance(
                                assessment,
                                CompanyAssessment,
                            )
                            else None
                        ),
                        "verification_code": result.get(
                            "verification_code"
                        ),
                        "attempt_count": len(
                            result.get("attempts", [])
                        ),
                        "persisted": persisted,
                        "error_code": error_code,
                        "elapsed_seconds": round(
                            time.monotonic() - started_at,
                            2,
                        ),
                    }
                )

            if position < len(companies):
                time.sleep(args.delay_seconds)

    print_json(
        {
            "status": (
                "completed"
                if failure_count == 0
                else "completed_with_errors"
            ),
            "selected_count": len(companies),
            "success_count": success_count,
            "failure_count": failure_count,
        }
    )

    if failure_count:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
