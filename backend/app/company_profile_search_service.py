"""Bounded, operator-triggered discovery for one company profile."""

import logging
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from uuid import UUID

from sqlalchemy import Engine, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.discovery.evaluator_factory import (
    EvaluatorConfigurationError,
    build_evaluator,
)
from app.discovery.graph import CompanyDiscoveryError, CompanyDiscoveryGraph
from app.discovery.safety import safe_text
from app.discovery.schemas import CompanyAssessment
from app.models import Company, CompanyWebProfile, DiscoveryAttempt


logger = logging.getLogger(__name__)
MANUAL_SEARCH_COOLDOWN = timedelta(minutes=5)
PROFILE_STATUSES = {
    "candidate_found",
    "verified",
    "needs_review",
    "not_found",
}
CONFIDENCE_LEVELS = {"high", "medium", "low"}
ERROR_CODE_PATTERN = re.compile(r"[a-z0-9_]{1,80}\Z")

_active_searches: set[UUID] = set()
_active_searches_lock = Lock()


class CompanyProfileSearchError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Company profile search failed.")
        self.code = code


@dataclass(frozen=True)
class CompanyProfileSearchResult:
    company_id: UUID
    company_name: str
    status: str
    profile_status: str | None
    confidence: str | None
    result_count: int
    attempt_count: int
    profile_updated: bool
    verification_code: str | None
    error_code: str | None


def _safe_code(value: object) -> str | None:
    code = safe_text(value, 80)
    return code if ERROR_CODE_PATTERN.fullmatch(code) else None


@contextmanager
def _single_company_search(company_id: UUID):
    with _active_searches_lock:
        if company_id in _active_searches:
            raise CompanyProfileSearchError("company_search_in_progress")
        _active_searches.add(company_id)
    try:
        yield
    finally:
        with _active_searches_lock:
            _active_searches.discard(company_id)


def _assert_search_allowed(
    database: Engine,
    company_id: UUID,
    *,
    now: datetime,
) -> None:
    with Session(database) as session:
        row = session.execute(
            select(Company.id, CompanyWebProfile.status)
            .outerjoin(
                CompanyWebProfile,
                CompanyWebProfile.company_id == Company.id,
            )
            .where(Company.id == company_id)
        ).one_or_none()
        if row is None:
            raise CompanyProfileSearchError("company_not_found")
        _stored_company_id, profile_status = row
        if profile_status == "verified":
            raise CompanyProfileSearchError("verified_profile_protected")

        latest_attempt = session.scalar(
            select(func.max(DiscoveryAttempt.created_at)).where(
                DiscoveryAttempt.company_id == company_id
            )
        )

    if latest_attempt is None:
        return
    if latest_attempt.tzinfo is None:
        latest_attempt = latest_attempt.replace(tzinfo=timezone.utc)
    if latest_attempt > now - MANUAL_SEARCH_COOLDOWN:
        raise CompanyProfileSearchError("company_search_cooldown")


def discover_company_profile(
    database: Engine,
    *,
    company_id: UUID,
    now: datetime | None = None,
) -> CompanyProfileSearchResult:
    now = now or datetime.now(timezone.utc)
    with _single_company_search(company_id):
        try:
            _assert_search_allowed(database, company_id, now=now)
        except SQLAlchemyError as error:
            raise CompanyProfileSearchError(
                "database_unavailable"
            ) from error

        serper_key = os.environ.get("SERPER_API_KEY", "").strip()
        if not serper_key:
            raise CompanyProfileSearchError("serper_not_configured")

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
        except EvaluatorConfigurationError as error:
            raise CompanyProfileSearchError(
                "evaluator_not_configured"
            ) from error

        try:
            with CompanyDiscoveryGraph(
                engine=database,
                serper_key=serper_key,
                evaluator=evaluator,
            ) as discovery_graph:
                raw = discovery_graph.run(company_id)
        except CompanyDiscoveryError as error:
            error_code = _safe_code(error.code) or "company_discovery_failed"
            raise CompanyProfileSearchError(error_code) from error
        except Exception as error:
            logger.exception(
                "Unexpected company profile discovery failure for company_id=%s",
                company_id,
            )
            raise CompanyProfileSearchError(
                "company_discovery_failed"
            ) from error

    assessment = raw.get("assessment")
    stored_status = _safe_code(raw.get("stored_status"))
    if stored_status not in PROFILE_STATUSES:
        stored_status = None
    confidence = (
        assessment.confidence
        if isinstance(assessment, CompanyAssessment)
        and assessment.confidence in CONFIDENCE_LEVELS
        else None
    )
    error_code = _safe_code(raw.get("error_code"))
    search_results = raw.get("search_results", [])
    attempts = raw.get("attempts", [])
    return CompanyProfileSearchResult(
        company_id=company_id,
        company_name=safe_text(raw.get("company_name"), 500),
        status="failed" if error_code else "succeeded",
        profile_status=stored_status,
        confidence=confidence,
        result_count=(
            min(len(search_results), 100)
            if isinstance(search_results, list)
            else 0
        ),
        attempt_count=(
            min(len(attempts), 10) if isinstance(attempts, list) else 0
        ),
        profile_updated=bool(raw.get("profile_updated", False)),
        verification_code=_safe_code(raw.get("verification_code")),
        error_code=error_code,
    )
