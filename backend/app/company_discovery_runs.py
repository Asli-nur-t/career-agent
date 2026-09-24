"""Durable, budget-bounded bulk company profile discovery runs."""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.company_profile_search_service import (
    CompanyProfileSearchError,
    CompanyProfileSearchResult,
    discover_company_profile,
)
from app.discovery.safety import safe_text
from app.models import (
    Company,
    CompanyDiscoveryRun,
    CompanyDiscoveryRunItem,
    CompanyWebProfile,
    DiscoveryAttempt,
)


logger = logging.getLogger(__name__)
MAX_QUERY_BUDGET = 2200
DEFAULT_QUERY_BUDGET = 2200
MAX_WORKERS = 4
MAX_QUERIES_PER_COMPANY = 3
STALE_RUN_AFTER = timedelta(minutes=15)
PROVIDER_PAUSE_CODES = {
    "authentication_error",
    "evaluator_not_configured",
    "model_not_found",
    "rate_limited",
    "serper_not_configured",
}
ACTIVE_STATUSES = {"queued", "running", "pause_requested", "paused"}


class CompanyDiscoveryRunError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Company discovery run failed.")
        self.code = code


@dataclass(frozen=True)
class CompanyDiscoveryRunSnapshot:
    run_id: UUID
    status: str
    scope: str
    query_budget: int
    query_count: int
    total_count: int
    queued_count: int
    running_count: int
    succeeded_count: int
    failed_count: int
    profile_counts: dict[str, int]
    error_counts: dict[str, int]
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


@dataclass(frozen=True)
class CompanyRunOutcome:
    value: CompanyProfileSearchResult | CompanyProfileSearchError
    attempt_count: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_error_code(value: object) -> str:
    return safe_text(value, 80) or "company_discovery_failed"


def _recover_stale_run(database: Engine, run_id: UUID) -> None:
    """Make an interrupted worker explicitly resumable without duplicating work."""
    cutoff = _now() - STALE_RUN_AFTER
    with Session(database) as session:
        run = session.scalar(
            select(CompanyDiscoveryRun)
            .where(
                CompanyDiscoveryRun.id == run_id,
                CompanyDiscoveryRun.status.in_(
                    ("queued", "running", "pause_requested")
                ),
                CompanyDiscoveryRun.updated_at <= cutoff,
            )
            .with_for_update()
        )
        if run is None:
            return
        session.query(CompanyDiscoveryRunItem).filter(
            CompanyDiscoveryRunItem.run_id == run_id,
            CompanyDiscoveryRunItem.status == "running",
        ).update(
            {
                CompanyDiscoveryRunItem.status: "queued",
                CompanyDiscoveryRunItem.started_at: None,
            },
            synchronize_session=False,
        )
        run.status = "paused"
        run.error_code = "worker_interrupted"
        run.updated_at = _now()
        session.commit()


def queue_company_discovery_run(
    database: Engine,
    *,
    query_budget: int = DEFAULT_QUERY_BUDGET,
) -> UUID:
    if not 3 <= query_budget <= MAX_QUERY_BUDGET:
        raise ValueError("company_query_budget_invalid")
    company_limit = min(1000, query_budget // MAX_QUERIES_PER_COMPANY)
    now = _now()

    with Session(database) as session:
        active_run = session.scalar(
            select(CompanyDiscoveryRun.id).where(
                CompanyDiscoveryRun.status.in_(ACTIVE_STATUSES)
            )
        )
        if active_run is not None:
            raise CompanyDiscoveryRunError("company_discovery_run_active")

        company_ids = session.scalars(
            select(Company.id)
            .outerjoin(
                CompanyWebProfile,
                CompanyWebProfile.company_id == Company.id,
            )
            .where(CompanyWebProfile.company_id.is_(None))
            .order_by(Company.name, Company.id)
            .limit(company_limit)
        ).all()

        run = CompanyDiscoveryRun(
            status="queued" if company_ids else "succeeded",
            scope="unprofiled",
            query_budget=query_budget,
            query_count=0,
            total_count=len(company_ids),
            finished_at=None if company_ids else now,
        )
        session.add(run)
        session.flush()
        session.add_all(
            [
                CompanyDiscoveryRunItem(
                    run_id=run.id,
                    company_id=company_id,
                    position=position,
                    status="queued",
                )
                for position, company_id in enumerate(company_ids, start=1)
            ]
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise CompanyDiscoveryRunError(
                "company_discovery_run_active"
            ) from None
        return run.id


def _prepare_run(database: Engine, run_id: UUID) -> bool:
    with Session(database) as session:
        run = session.scalar(
            select(CompanyDiscoveryRun)
            .where(CompanyDiscoveryRun.id == run_id)
            .with_for_update()
        )
        if run is None or run.status not in {"queued", "paused"}:
            return False
        session.query(CompanyDiscoveryRunItem).filter(
            CompanyDiscoveryRunItem.run_id == run_id,
            CompanyDiscoveryRunItem.status == "running",
        ).update(
            {
                CompanyDiscoveryRunItem.status: "queued",
                CompanyDiscoveryRunItem.started_at: None,
            },
            synchronize_session=False,
        )
        run.status = "running"
        run.error_code = None
        run.started_at = run.started_at or _now()
        session.commit()
        return True


def _claim_items(
    database: Engine,
    run_id: UUID,
) -> tuple[str, list[UUID]]:
    with Session(database) as session:
        run = session.scalar(
            select(CompanyDiscoveryRun)
            .where(CompanyDiscoveryRun.id == run_id)
            .with_for_update()
        )
        if run is None:
            return "missing", []
        if run.status == "pause_requested":
            run.status = "paused"
            session.commit()
            return "paused", []
        if run.status != "running":
            return run.status, []

        items = session.scalars(
            select(CompanyDiscoveryRunItem)
            .where(
                CompanyDiscoveryRunItem.run_id == run_id,
                CompanyDiscoveryRunItem.status == "queued",
            )
            .order_by(CompanyDiscoveryRunItem.position)
            .limit(MAX_WORKERS)
            .with_for_update(skip_locked=True)
        ).all()
        now = _now()
        for item in items:
            item.status = "running"
            item.started_at = now
        session.commit()
        return "running", [item.company_id for item in items]


def _run_one_company(
    database: Engine,
    company_id: UUID,
) -> CompanyRunOutcome:
    started_at = _now()
    try:
        value: CompanyProfileSearchResult | CompanyProfileSearchError = (
            discover_company_profile(database, company_id=company_id)
        )
    except CompanyProfileSearchError as error:
        value = error
    except Exception:
        logger.exception(
            "Unexpected bulk company discovery failure for company_id=%s",
            company_id,
        )
        value = CompanyProfileSearchError("company_discovery_failed")

    if isinstance(value, CompanyProfileSearchResult):
        attempt_count = value.attempt_count
    else:
        try:
            with Session(database) as session:
                attempt_count = session.scalar(
                    select(func.count())
                    .select_from(DiscoveryAttempt)
                    .where(
                        DiscoveryAttempt.company_id == company_id,
                        DiscoveryAttempt.created_at >= started_at,
                    )
                ) or 0
        except SQLAlchemyError:
            attempt_count = 0
    return CompanyRunOutcome(
        value=value,
        attempt_count=min(attempt_count, MAX_QUERIES_PER_COMPANY),
    )


def _record_item(
    database: Engine,
    run_id: UUID,
    company_id: UUID,
    outcome: CompanyRunOutcome,
) -> str | None:
    with Session(database) as session:
        run = session.scalar(
            select(CompanyDiscoveryRun)
            .where(CompanyDiscoveryRun.id == run_id)
            .with_for_update()
        )
        item = session.scalar(
            select(CompanyDiscoveryRunItem)
            .where(
                CompanyDiscoveryRunItem.run_id == run_id,
                CompanyDiscoveryRunItem.company_id == company_id,
            )
            .with_for_update()
        )
        if run is None or item is None or item.status != "running":
            return None

        value = outcome.value
        item.attempt_count = outcome.attempt_count
        run.query_count = min(
            run.query_budget,
            run.query_count + outcome.attempt_count,
        )
        if isinstance(value, CompanyProfileSearchError):
            error_code = _safe_error_code(value.code)
            item.status = "failed"
            item.error_code = error_code
            item.profile_status = None
        else:
            error_code = _safe_error_code(value.error_code) if value.error_code else None
            item.status = "failed" if value.status == "failed" else "succeeded"
            item.error_code = error_code
            item.profile_status = value.profile_status
        item.finished_at = _now()
        if error_code in PROVIDER_PAUSE_CODES and run.status == "running":
            run.status = "pause_requested"
            run.error_code = error_code
        session.commit()
        return error_code


def _finish_if_complete(database: Engine, run_id: UUID) -> bool:
    with Session(database) as session:
        run = session.scalar(
            select(CompanyDiscoveryRun)
            .where(CompanyDiscoveryRun.id == run_id)
            .with_for_update()
        )
        if run is None:
            return True
        if run.status == "pause_requested":
            run.status = "paused"
            session.commit()
            return True
        if run.status != "running":
            return True
        remaining = session.scalar(
            select(func.count())
            .select_from(CompanyDiscoveryRunItem)
            .where(
                CompanyDiscoveryRunItem.run_id == run_id,
                CompanyDiscoveryRunItem.status.in_(("queued", "running")),
            )
        ) or 0
        if remaining:
            return False
        run.status = "succeeded"
        run.error_code = None
        run.finished_at = _now()
        session.commit()
        return True


def execute_company_discovery_run(database: Engine, run_id: UUID) -> None:
    try:
        if not _prepare_run(database, run_id):
            return
        while True:
            run_status, company_ids = _claim_items(database, run_id)
            if run_status != "running":
                return
            if not company_ids:
                _finish_if_complete(database, run_id)
                return
            with ThreadPoolExecutor(
                max_workers=min(MAX_WORKERS, len(company_ids)),
                thread_name_prefix="company-discovery",
            ) as executor:
                futures = {
                    executor.submit(_run_one_company, database, company_id): company_id
                    for company_id in company_ids
                }
                for future in as_completed(futures):
                    company_id = futures[future]
                    _record_item(database, run_id, company_id, future.result())
            if _finish_if_complete(database, run_id):
                return
    except SQLAlchemyError:
        logger.exception("Company discovery run database failure run_id=%s", run_id)
        _mark_run_paused(database, run_id, "database_unavailable")
    except Exception:
        logger.exception("Unexpected company discovery run failure run_id=%s", run_id)
        _mark_run_paused(database, run_id, "company_discovery_run_failed")


def _mark_run_paused(database: Engine, run_id: UUID, error_code: str) -> None:
    try:
        with Session(database) as session:
            run = session.get(CompanyDiscoveryRun, run_id)
            if run is None or run.status not in ACTIVE_STATUSES:
                return
            run.status = "paused"
            run.error_code = _safe_error_code(error_code)
            session.commit()
    except SQLAlchemyError:
        logger.exception("Could not persist paused company run run_id=%s", run_id)


def pause_company_discovery_run(database: Engine, run_id: UUID) -> None:
    with Session(database) as session:
        run = session.scalar(
            select(CompanyDiscoveryRun)
            .where(CompanyDiscoveryRun.id == run_id)
            .with_for_update()
        )
        if run is None:
            raise CompanyDiscoveryRunError("company_discovery_run_not_found")
        if run.status == "queued":
            run.status = "paused"
        elif run.status == "running":
            run.status = "pause_requested"
        elif run.status not in {"paused", "pause_requested"}:
            raise CompanyDiscoveryRunError("company_discovery_run_not_active")
        session.commit()


def resume_company_discovery_run(database: Engine, run_id: UUID) -> None:
    with Session(database) as session:
        run = session.scalar(
            select(CompanyDiscoveryRun)
            .where(CompanyDiscoveryRun.id == run_id)
            .with_for_update()
        )
        if run is None:
            raise CompanyDiscoveryRunError("company_discovery_run_not_found")
        if run.status != "paused":
            raise CompanyDiscoveryRunError("company_discovery_run_not_paused")
        run.status = "queued"
        run.error_code = None
        session.commit()


def load_company_discovery_run(
    database: Engine,
    run_id: UUID,
) -> CompanyDiscoveryRunSnapshot | None:
    with Session(database) as session:
        run = session.get(CompanyDiscoveryRun, run_id)
        if run is None:
            return None
        status_rows = session.execute(
            select(CompanyDiscoveryRunItem.status, func.count())
            .where(CompanyDiscoveryRunItem.run_id == run_id)
            .group_by(CompanyDiscoveryRunItem.status)
        ).all()
        profile_rows = session.execute(
            select(CompanyDiscoveryRunItem.profile_status, func.count())
            .where(
                CompanyDiscoveryRunItem.run_id == run_id,
                CompanyDiscoveryRunItem.profile_status.is_not(None),
            )
            .group_by(CompanyDiscoveryRunItem.profile_status)
        ).all()
        error_rows = session.execute(
            select(CompanyDiscoveryRunItem.error_code, func.count())
            .where(
                CompanyDiscoveryRunItem.run_id == run_id,
                CompanyDiscoveryRunItem.error_code.is_not(None),
            )
            .group_by(CompanyDiscoveryRunItem.error_code)
        ).all()

    counts = {status: count for status, count in status_rows}
    return CompanyDiscoveryRunSnapshot(
        run_id=run.id,
        status=run.status,
        scope=run.scope,
        query_budget=run.query_budget,
        query_count=run.query_count,
        total_count=run.total_count,
        queued_count=counts.get("queued", 0),
        running_count=counts.get("running", 0),
        succeeded_count=counts.get("succeeded", 0),
        failed_count=counts.get("failed", 0),
        profile_counts={status: count for status, count in profile_rows},
        error_counts={code: count for code, count in error_rows},
        error_code=run.error_code,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def load_latest_company_discovery_run(
    database: Engine,
) -> CompanyDiscoveryRunSnapshot | None:
    with Session(database) as session:
        run_id = session.scalar(
            select(CompanyDiscoveryRun.id)
            .order_by(CompanyDiscoveryRun.created_at.desc())
            .limit(1)
        )
    if run_id is not None:
        _recover_stale_run(database, run_id)
    return (
        load_company_discovery_run(database, run_id)
        if run_id is not None
        else None
    )
