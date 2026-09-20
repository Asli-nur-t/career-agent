import argparse
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from dotenv import load_dotenv
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.discovery.graph import (
    CompanyDiscoveryError,
    CompanyDiscoveryGraph,
)
from app.discovery.evaluator_factory import (
    EvaluatorConfigurationError,
    build_evaluator,
)
from app.discovery_schedule import (
    IMMEDIATE_PROVIDER_STOP_ERRORS,
    RETRYABLE_OUTCOMES,
    TRANSIENT_PROVIDER_ERRORS,
    retry_due,
    should_open_provider_circuit,
)
from app.discovery.schemas import CompanyAssessment
from app.models import Company, CompanyWebProfile, DiscoveryAttempt


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


def choose_companies(
    limit: int,
    *,
    database: Engine,
    now: datetime | None = None,
) -> list[tuple[UUID, str]]:
    now = now or datetime.now(timezone.utc)
    with Session(database) as session:
        rows = session.execute(
            select(Company.id, Company.name)
            .outerjoin(
                CompanyWebProfile,
                CompanyWebProfile.company_id == Company.id,
            )
            .where(CompanyWebProfile.company_id.is_(None))
            .order_by(Company.name, Company.id)
        ).all()

        if not rows:
            return []

        company_ids = [company_id for company_id, _ in rows]
        attempt_rows = session.execute(
            select(
                DiscoveryAttempt.company_id,
                DiscoveryAttempt.outcome,
                DiscoveryAttempt.error_code,
                DiscoveryAttempt.created_at,
            ).where(
                DiscoveryAttempt.company_id.in_(company_ids),
                DiscoveryAttempt.outcome.in_(RETRYABLE_OUTCOMES),
            )
        ).all()

    attempts_by_company: dict[
        UUID, list[tuple[str, str | None, datetime]]
    ] = defaultdict(list)
    for company_id, outcome, error_code, created_at in attempt_rows:
        attempts_by_company[company_id].append(
            (outcome, error_code, created_at)
        )

    selected: list[tuple[UUID, str]] = []
    for company_id, company_name in rows:
        if retry_due(attempts_by_company[company_id], now):
            selected.append((company_id, company_name))
            if len(selected) == limit:
                break
    return selected


def print_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main() -> None:
    args = parse_args()
    from app.database import engine

    companies = choose_companies(args.limit, database=engine)

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
    if not serper_key:
        raise SystemExit("SERPER_API_KEY yapılandırılmamış.")

    try:
        evaluator = build_evaluator(
            provider=os.environ.get("EVALUATOR_PROVIDER", "ollama"),
            gemini_key=os.environ.get("GEMINI_API_KEY", ""),
            ollama_model=os.environ.get("OLLAMA_MODEL", "qwen3:8b"),
            ollama_base_url=os.environ.get(
                "OLLAMA_BASE_URL",
                "http://127.0.0.1:11434",
            ),
        )
    except EvaluatorConfigurationError:
        raise SystemExit(
            "Değerlendirici yapılandırması geçersiz."
        ) from None

    success_count = 0
    failure_count = 0
    processed_count = 0
    consecutive_provider_failures = 0
    circuit_error_code: str | None = None

    with CompanyDiscoveryGraph(
        engine=engine,
        serper_key=serper_key,
        evaluator=evaluator,
    ) as discovery_graph:
        for position, (company_id, company_name) in enumerate(
            companies,
            start=1,
        ):
            processed_count += 1
            started_at = time.monotonic()
            current_error_code: str | None = None

            try:
                result = discovery_graph.run(company_id)
            except CompanyDiscoveryError as error:
                failure_count += 1
                current_error_code = error.code
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
                current_error_code = "unexpected_error"
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
                current_error_code = error_code
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
                        "profile_status": result.get(
                            "stored_status"
                        ),
                        "assessment_status": (
                            assessment.status
                            if isinstance(
                                assessment,
                                CompanyAssessment,
                            )
                            else None
                        ),
                        "profile_updated": result.get(
                            "profile_updated",
                            False,
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

            if current_error_code in TRANSIENT_PROVIDER_ERRORS:
                consecutive_provider_failures += 1
            elif current_error_code not in IMMEDIATE_PROVIDER_STOP_ERRORS:
                consecutive_provider_failures = 0

            if should_open_provider_circuit(
                current_error_code,
                consecutive_provider_failures,
            ):
                circuit_error_code = current_error_code
                break

            if position < len(companies):
                time.sleep(args.delay_seconds)

    aborted_count = len(companies) - processed_count
    print_json(
        {
            "status": (
                "aborted_provider_error"
                if circuit_error_code
                else (
                    "completed"
                    if failure_count == 0
                    else "completed_with_errors"
                )
            ),
            "selected_count": len(companies),
            "processed_count": processed_count,
            "aborted_count": aborted_count,
            "success_count": success_count,
            "failure_count": failure_count,
            "circuit_error_code": circuit_error_code,
        }
    )

    if failure_count or circuit_error_code:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
