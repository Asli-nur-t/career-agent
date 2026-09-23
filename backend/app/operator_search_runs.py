"""Persistent, single-flight profile searches started by the operator UI."""

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import Engine, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import CandidateProfile, ProfileJobSearchRun
from app.profile_search_service import ProfileSearchError, run_profile_job_search


STALE_AFTER = timedelta(minutes=30)
SAFE_VALUE_ERRORS = {"profile_not_found", "profile_hash_mismatch"}


class SearchAlreadyRunning(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def queue_profile_search(
    database: Engine,
    *,
    profile_label: str,
) -> UUID:
    now = _now()
    with Session(database) as session:
        profile_id = session.scalar(
            select(CandidateProfile.id).where(
                CandidateProfile.label == profile_label
            )
        )
        if profile_id is None:
            raise ValueError("profile_not_found")
        session.execute(
            update(ProfileJobSearchRun)
            .where(
                ProfileJobSearchRun.profile_id == profile_id,
                ProfileJobSearchRun.status.in_(("queued", "running")),
                ProfileJobSearchRun.created_at < now - STALE_AFTER,
            )
            .values(
                status="failed",
                error_code="worker_interrupted",
                finished_at=now,
            )
        )
        run = ProfileJobSearchRun(profile_id=profile_id, status="queued")
        session.add(run)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise SearchAlreadyRunning("search_already_running") from None
        session.refresh(run)
        return run.id


def execute_profile_search_run(
    database: Engine,
    run_id: UUID,
    *,
    force: bool = False,
) -> None:
    now = _now()
    with Session(database) as session:
        row = session.execute(
            select(ProfileJobSearchRun, CandidateProfile.label)
            .join(CandidateProfile, CandidateProfile.id == ProfileJobSearchRun.profile_id)
            .where(ProfileJobSearchRun.id == run_id)
            .with_for_update()
        ).one_or_none()
        if row is None or row[0].status != "queued":
            return
        run, profile_label = row
        run.status = "running"
        run.started_at = now
        session.commit()

    try:
        result = run_profile_job_search(
            database,
            profile_label=profile_label,
            force=force,
        )
    except ProfileSearchError as error:
        _finish_failed(database, run_id, error.code)
        return
    except ValueError as error:
        code = str(error)
        _finish_failed(
            database,
            run_id,
            code if code in SAFE_VALUE_ERRORS else "search_validation_error",
        )
        return
    except SQLAlchemyError:
        _finish_failed(database, run_id, "database_error")
        return
    except Exception:
        _finish_failed(database, run_id, "search_failed")
        return

    with Session(database) as session:
        run = session.get(ProfileJobSearchRun, run_id)
        if run is None or run.status != "running":
            return
        run.status = "succeeded"
        run.result = result
        run.error_code = None
        run.finished_at = _now()
        session.commit()


def _finish_failed(database: Engine, run_id: UUID, error_code: str) -> None:
    with Session(database) as session:
        run = session.get(ProfileJobSearchRun, run_id)
        if run is None or run.status not in {"queued", "running"}:
            return
        run.status = "failed"
        run.result = {}
        run.error_code = error_code[:80]
        run.finished_at = _now()
        session.commit()


def load_latest_profile_search(
    database: Engine,
    *,
    profile_label: str,
) -> ProfileJobSearchRun | None:
    with Session(database) as session:
        return session.scalar(
            select(ProfileJobSearchRun)
            .join(
                CandidateProfile,
                CandidateProfile.id == ProfileJobSearchRun.profile_id,
            )
            .where(CandidateProfile.label == profile_label)
            .order_by(ProfileJobSearchRun.created_at.desc())
            .limit(1)
        )
