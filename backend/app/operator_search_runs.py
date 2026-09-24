"""Persistent, single-flight profile searches started by the operator UI."""

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import Engine, select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import CandidateProfile, ProfileJobSearchRun
from app.profile_search_service import ProfileSearchError, run_profile_job_search
from app.discovery.safety import safe_text


STALE_AFTER = timedelta(minutes=30)
MANUAL_SEARCH_COOLDOWN = timedelta(minutes=5)
MANUAL_GLOBAL_COOLDOWN = timedelta(seconds=30)
SAFE_VALUE_ERRORS = {"profile_not_found", "profile_hash_mismatch"}
MAX_REQUESTED_ROLES = 10
SEARCH_MODES = {"quick", "deep"}


class SearchAlreadyRunning(RuntimeError):
    pass


class SearchCooldownActive(RuntimeError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("search_cooldown")
        self.retry_after_seconds = max(1, retry_after_seconds)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_search_roles(values: list[str]) -> list[str]:
    if not 1 <= len(values) <= MAX_REQUESTED_ROLES:
        raise ValueError("search_roles_invalid")
    roles: list[str] = []
    seen: set[str] = set()
    for value in values:
        role = safe_text(value, 100)
        marker = role.casefold()
        if not role or not marker or marker in seen:
            continue
        seen.add(marker)
        roles.append(role)
    if not roles:
        raise ValueError("search_roles_invalid")
    return roles


def normalize_search_mode(value: object) -> str:
    mode = safe_text(value, 20).casefold()
    if mode not in SEARCH_MODES:
        raise ValueError("search_mode_invalid")
    return mode


def _search_fingerprint(
    profile_hash: str,
    roles: list[str],
    search_mode: str,
) -> str:
    payload = json.dumps(
        {
            "profile_hash": profile_hash,
            "roles": sorted((role.casefold() for role in roles)),
            "search_mode": search_mode,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def queue_profile_search(
    database: Engine,
    *,
    profile_label: str,
    requested_roles: list[str] | None = None,
    search_mode: str = "quick",
    force: bool = False,
) -> UUID:
    now = _now()
    with Session(database) as session:
        profile_row = session.execute(
            select(
                CandidateProfile.id,
                CandidateProfile.target_roles,
                CandidateProfile.config_hash,
            ).where(CandidateProfile.label == profile_label)
        ).one_or_none()
        if profile_row is None:
            raise ValueError("profile_not_found")
        profile_id, profile_roles, profile_hash = profile_row
        roles = normalize_search_roles(
            requested_roles if requested_roles is not None else profile_roles
        )
        mode = normalize_search_mode(search_mode)
        fingerprint = _search_fingerprint(profile_hash, roles, mode)
        if force:
            recent_successes = session.scalars(
                select(ProfileJobSearchRun)
                .where(
                    ProfileJobSearchRun.profile_id == profile_id,
                    ProfileJobSearchRun.status == "succeeded",
                    ProfileJobSearchRun.finished_at >= now - MANUAL_SEARCH_COOLDOWN,
                )
                .order_by(ProfileJobSearchRun.finished_at.desc())
                .limit(20)
            ).all()
            for position, previous in enumerate(recent_successes):
                if previous.finished_at is None:
                    continue
                previous_result = (
                    previous.result if isinstance(previous.result, dict) else {}
                )
                elapsed = now - previous.finished_at
                if (
                    position == 0
                    and previous_result.get("manual_force") is True
                    and elapsed < MANUAL_GLOBAL_COOLDOWN
                ):
                    remaining = MANUAL_GLOBAL_COOLDOWN - elapsed
                    raise SearchCooldownActive(
                        math.ceil(max(remaining.total_seconds(), 1))
                    )
                if previous_result.get("search_fingerprint") == fingerprint:
                    remaining = MANUAL_SEARCH_COOLDOWN - elapsed
                    raise SearchCooldownActive(
                        math.ceil(max(remaining.total_seconds(), 1))
                    )
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
        run = ProfileJobSearchRun(
            profile_id=profile_id,
            status="queued",
            result={
                "requested_roles": roles,
                "search_mode": mode,
                "search_fingerprint": fingerprint,
                "force": bool(force),
            },
        )
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
            select(
                ProfileJobSearchRun,
                CandidateProfile.label,
                CandidateProfile.target_roles,
            )
            .join(CandidateProfile, CandidateProfile.id == ProfileJobSearchRun.profile_id)
            .where(ProfileJobSearchRun.id == run_id)
            .with_for_update()
        ).one_or_none()
        if row is None or row[0].status != "queued":
            return
        run, profile_label, profile_roles = row
        raw_roles = (
            run.result.get("requested_roles")
            if isinstance(run.result, dict)
            else None
        )
        try:
            requested_roles = normalize_search_roles(
                raw_roles if isinstance(raw_roles, list) else profile_roles
            )
            search_mode = normalize_search_mode(
                run.result.get("search_mode", "quick")
                if isinstance(run.result, dict)
                else "quick"
            )
            queued_force = bool(
                run.result.get("force")
                if isinstance(run.result, dict)
                else False
            )
        except ValueError:
            run.status = "failed"
            run.error_code = "search_roles_invalid"
            run.finished_at = now
            session.commit()
            return
        run.status = "running"
        run.started_at = now
        session.commit()

    try:
        result = run_profile_job_search(
            database,
            profile_label=profile_label,
            force=force or queued_force,
            requested_roles=requested_roles,
            search_mode=search_mode,
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
        queued_result = run.result if isinstance(run.result, dict) else {}
        result["search_fingerprint"] = queued_result.get("search_fingerprint")
        result["search_mode"] = search_mode
        result["manual_force"] = bool(queued_result.get("force"))
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
