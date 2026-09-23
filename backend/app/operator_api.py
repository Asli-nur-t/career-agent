"""Authenticated API used by the local operator console."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import engine
from app.discovery.safety import (
    is_denied_official_website,
    normalize_linkedin_company_url,
    normalize_public_url,
    safe_text,
)
from app.discovery.schemas import SearchResult
from app.job_boards import (
    SUPPORTED_JOB_PROVIDERS,
    UNKNOWN_EMPLOYER,
    normalize_job_board_result,
)
from app.models import (
    CandidateProfile,
    CareerSource,
    Company,
    CompanyAffiliation,
    CompanyWebProfile,
    JobBoardCandidate,
    JobMatch,
    JobPosting,
    ProfileJobSearchRun,
)
from app.operator_auth import require_operator_token
from app.operator_search_runs import (
    SearchAlreadyRunning,
    execute_profile_search_run,
    load_latest_profile_search,
    queue_profile_search,
)
from app.review_company_profile import review_company_profile
from app.review_job_board_candidate import review_job_board_candidate
from app.review_job_board_queue import RankedCandidate, load_queue


router = APIRouter(
    prefix="/operator",
    tags=["Operator"],
    dependencies=[Depends(require_operator_token)],
)

SEARCH_DISPOSITIONS = {
    "active_review",
    "activity_unknown",
    "already_approved",
    "closed",
    "location_or_policy",
    "location_unknown",
    "profile_filtered",
    "previously_rejected",
}

COMPANY_PROFILE_STATUSES = {
    "all",
    "unprofiled",
    "candidate_found",
    "verified",
    "needs_review",
    "not_found",
}


class OperatorSummary(BaseModel):
    companies: int
    verified_companies: int
    active_sources: int
    active_postings: int
    pending_candidates: int
    verified_active_candidates: int
    new_matches: int
    profiles: int


class OperatorProfileItem(BaseModel):
    label: str
    target_roles: list[str]
    next_search_at: datetime | None
    last_search_outcome: str | None


class OperatorCompanyItem(BaseModel):
    company_id: UUID
    name: str
    sector: str | None
    needs_review: bool
    teknoparks: list[str]
    profile_status: Literal[
        "unprofiled",
        "candidate_found",
        "verified",
        "needs_review",
        "not_found",
    ]
    brand_name: str | None
    confidence: Literal["high", "medium", "low"] | None
    official_website_url: str | None
    careers_url: str | None
    official_linkedin_url: str | None
    last_verified_at: datetime | None
    updated_at: datetime | None


class OperatorCompanyPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[OperatorCompanyItem]


class OperatorCompanyDetail(OperatorCompanyItem):
    evidence: list[dict[str, str | int | float | bool | None]]
    search_provider: str | None
    evaluator_model: str | None
    last_searched_at: datetime | None
    career_sources_last_checked_at: datetime | None
    career_sources_next_check_at: datetime | None
    career_sources_last_outcome: str | None
    career_sources_last_error_code: str | None
    career_sources_candidate_count: int
    job_boards_last_checked_at: datetime | None
    job_boards_next_check_at: datetime | None
    job_boards_last_outcome: str | None
    job_boards_last_error_code: str | None
    job_boards_candidate_count: int
    reviewable: bool


class ApproveCompanyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed_identity: Literal[True]


class RejectCompanyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed_rejection: Literal[True]
    reason: Literal[
        "wrong_company",
        "unsafe_or_invalid_url",
        "insufficient_evidence",
    ]


class CompanyReviewResponse(BaseModel):
    company_id: UUID
    company_name: str
    status: Literal["verified", "not_found"]
    changed: bool


class OperatorJobItem(BaseModel):
    candidate_id: UUID
    provider: str
    company_name: str
    title: str
    listing_url: str
    location: str | None
    work_mode: str
    employment_type: str
    published_at: datetime | None
    activity_state: str
    activity_code: str
    score: int
    recommendation: str
    matched_terms: list[str]
    risk_flags: list[str]


class OperatorJobPage(BaseModel):
    profile: str
    activity_filter: Literal["active_only", "active_and_unverified"]
    count: int
    items: list[OperatorJobItem]


class OperatorJobDetail(BaseModel):
    candidate_id: UUID
    provider: str
    company_name: str
    company_identity_required: bool
    title: str
    listing_url: str
    snippet: str | None
    location: str | None
    work_mode: str
    employment_type: str
    published_at: datetime | None
    activity_state: str
    activity_code: str
    activity_checked_at: datetime | None
    status: str
    evidence: list[dict[str, str | int | float | bool | None]]
    first_seen_at: datetime
    last_seen_at: datetime


class ApproveJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    confirmed_active: Literal[True]
    company_name: str | None = Field(default=None, min_length=1, max_length=500)


class RejectJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed_rejection: Literal[True]


class StartSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    profile: str = Field(min_length=1, max_length=100)
    confirmed_external_search: Literal[True]
    force: bool = False


class SearchRunResponse(BaseModel):
    run_id: UUID
    profile: str
    status: Literal["queued", "running", "succeeded", "failed"]
    result: dict[str, object]
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobReviewResponse(BaseModel):
    candidate_id: str
    provider: str
    title: str
    listing_url: str
    status: str
    posting_id: str | None
    company_id: str | None
    company_name: str | None
    company_created: bool
    changed: bool


def load_operator_summary(database: Engine) -> OperatorSummary:
    with Session(database) as session:
        counts = [
            session.scalar(select(func.count()).select_from(Company)),
            session.scalar(
                select(func.count())
                .select_from(CompanyWebProfile)
                .where(CompanyWebProfile.status == "verified")
            ),
            session.scalar(
                select(func.count())
                .select_from(CareerSource)
                .where(CareerSource.status == "active")
            ),
            session.scalar(
                select(func.count())
                .select_from(JobPosting)
                .where(JobPosting.status == "active")
            ),
            session.scalar(
                select(func.count())
                .select_from(JobBoardCandidate)
                .where(JobBoardCandidate.status == "needs_review")
            ),
            session.scalar(
                select(func.count())
                .select_from(JobBoardCandidate)
                .where(
                    JobBoardCandidate.status == "needs_review",
                    JobBoardCandidate.activity_state == "active",
                )
            ),
            session.scalar(
                select(func.count())
                .select_from(JobMatch)
                .where(JobMatch.review_status == "new")
            ),
            session.scalar(select(func.count()).select_from(CandidateProfile)),
        ]
    return OperatorSummary(
        companies=counts[0] or 0,
        verified_companies=counts[1] or 0,
        active_sources=counts[2] or 0,
        active_postings=counts[3] or 0,
        pending_candidates=counts[4] or 0,
        verified_active_candidates=counts[5] or 0,
        new_matches=counts[6] or 0,
        profiles=counts[7] or 0,
    )


def load_operator_profiles(database: Engine) -> list[OperatorProfileItem]:
    with Session(database) as session:
        rows = session.execute(
            select(
                CandidateProfile.label,
                CandidateProfile.target_roles,
                CandidateProfile.job_search_next_check_at,
                CandidateProfile.job_search_last_outcome,
            ).order_by(CandidateProfile.label)
        ).all()
    return [
        OperatorProfileItem(
            label=label,
            target_roles=roles if isinstance(roles, list) else [],
            next_search_at=next_search_at,
            last_search_outcome=last_outcome,
        )
        for label, roles, next_search_at, last_outcome in rows
    ]


def _safe_public_url(value: object, *, linkedin: bool = False) -> str | None:
    if not value:
        return None
    try:
        if linkedin:
            return normalize_linkedin_company_url(value)
        return normalize_public_url(value)
    except ValueError:
        return None


def load_operator_companies(
    database: Engine,
    *,
    limit: int,
    offset: int,
    query: str | None,
    profile_status: str,
) -> OperatorCompanyPage:
    if profile_status not in COMPANY_PROFILE_STATUSES:
        raise ValueError("company_profile_status_invalid")

    filters = []
    cleaned_query = safe_text(query, 100).strip() if query else ""
    if cleaned_query:
        filters.append(
            or_(
                Company.name.icontains(cleaned_query, autoescape=True),
                CompanyWebProfile.brand_name.icontains(
                    cleaned_query,
                    autoescape=True,
                ),
            )
        )
    if profile_status == "unprofiled":
        filters.append(CompanyWebProfile.company_id.is_(None))
    elif profile_status != "all":
        filters.append(CompanyWebProfile.status == profile_status)

    count_statement = (
        select(func.count())
        .select_from(Company)
        .outerjoin(
            CompanyWebProfile,
            CompanyWebProfile.company_id == Company.id,
        )
        .where(*filters)
    )
    page_statement = (
        select(Company, CompanyWebProfile)
        .outerjoin(
            CompanyWebProfile,
            CompanyWebProfile.company_id == Company.id,
        )
        .where(*filters)
        .order_by(Company.name, Company.id)
        .offset(offset)
        .limit(limit)
    )

    with Session(database) as session:
        total = session.scalar(count_statement) or 0
        rows = session.execute(page_statement).all()
        company_ids = [company.id for company, _profile in rows]
        affiliations: dict[UUID, list[str]] = {
            company_id: [] for company_id in company_ids
        }
        if company_ids:
            affiliation_rows = session.execute(
                select(
                    CompanyAffiliation.company_id,
                    CompanyAffiliation.teknopark,
                )
                .where(CompanyAffiliation.company_id.in_(company_ids))
                .order_by(
                    CompanyAffiliation.company_id,
                    CompanyAffiliation.teknopark,
                )
            ).all()
            for company_id, teknopark in affiliation_rows:
                affiliations[company_id].append(safe_text(teknopark, 200))

    items: list[OperatorCompanyItem] = []
    for company, profile in rows:
        website_url = (
            _safe_public_url(profile.official_website_url)
            if profile is not None
            else None
        )
        if website_url and is_denied_official_website(website_url):
            website_url = None
        items.append(
            OperatorCompanyItem(
                company_id=company.id,
                name=safe_text(company.name, 500),
                sector=(
                    safe_text(company.sector, 300) if company.sector else None
                ),
                needs_review=bool(company.needs_review),
                teknoparks=affiliations.get(company.id, [])[:25],
                profile_status=(profile.status if profile else "unprofiled"),
                brand_name=(
                    safe_text(profile.brand_name, 300)
                    if profile is not None and profile.brand_name
                    else None
                ),
                confidence=(profile.confidence if profile else None),
                official_website_url=website_url,
                careers_url=(
                    _safe_public_url(profile.careers_url)
                    if profile is not None
                    else None
                ),
                official_linkedin_url=(
                    _safe_public_url(
                        profile.official_linkedin_url,
                        linkedin=True,
                    )
                    if profile is not None
                    else None
                ),
                last_verified_at=(
                    profile.last_verified_at if profile is not None else None
                ),
                updated_at=(profile.updated_at if profile is not None else None),
            )
        )
    return OperatorCompanyPage(
        total=total,
        limit=limit,
        offset=offset,
        items=items,
    )


def load_operator_company_detail(
    database: Engine,
    company_id: UUID,
) -> OperatorCompanyDetail | None:
    with Session(database) as session:
        row = session.execute(
            select(Company, CompanyWebProfile)
            .outerjoin(
                CompanyWebProfile,
                CompanyWebProfile.company_id == Company.id,
            )
            .where(Company.id == company_id)
        ).one_or_none()
        if row is None:
            return None
        company, profile = row
        teknoparks = [
            safe_text(value, 200)
            for value in session.scalars(
                select(CompanyAffiliation.teknopark)
                .where(CompanyAffiliation.company_id == company_id)
                .order_by(CompanyAffiliation.teknopark)
                .limit(25)
            ).all()
        ]

    website_url = (
        _safe_public_url(profile.official_website_url)
        if profile is not None
        else None
    )
    if website_url and is_denied_official_website(website_url):
        website_url = None
    profile_status = profile.status if profile is not None else "unprofiled"
    return OperatorCompanyDetail(
        company_id=company.id,
        name=safe_text(company.name, 500),
        sector=safe_text(company.sector, 300) if company.sector else None,
        needs_review=bool(company.needs_review),
        teknoparks=teknoparks,
        profile_status=profile_status,
        brand_name=(
            safe_text(profile.brand_name, 300)
            if profile is not None and profile.brand_name
            else None
        ),
        confidence=profile.confidence if profile is not None else None,
        official_website_url=website_url,
        careers_url=(
            _safe_public_url(profile.careers_url)
            if profile is not None
            else None
        ),
        official_linkedin_url=(
            _safe_public_url(profile.official_linkedin_url, linkedin=True)
            if profile is not None
            else None
        ),
        last_verified_at=(
            profile.last_verified_at if profile is not None else None
        ),
        updated_at=profile.updated_at if profile is not None else None,
        evidence=(
            _safe_evidence(profile.evidence) if profile is not None else []
        ),
        search_provider=(
            safe_text(profile.search_provider, 50)
            if profile is not None and profile.search_provider
            else None
        ),
        evaluator_model=(
            safe_text(profile.evaluator_model, 100)
            if profile is not None and profile.evaluator_model
            else None
        ),
        last_searched_at=(
            profile.last_searched_at if profile is not None else None
        ),
        career_sources_last_checked_at=(
            profile.career_sources_last_checked_at
            if profile is not None
            else None
        ),
        career_sources_next_check_at=(
            profile.career_sources_next_check_at
            if profile is not None
            else None
        ),
        career_sources_last_outcome=(
            safe_text(profile.career_sources_last_outcome, 30)
            if profile is not None and profile.career_sources_last_outcome
            else None
        ),
        career_sources_last_error_code=(
            safe_text(profile.career_sources_last_error_code, 80)
            if profile is not None
            and profile.career_sources_last_error_code
            else None
        ),
        career_sources_candidate_count=(
            max(0, min(profile.career_sources_candidate_count, 100))
            if profile is not None
            else 0
        ),
        job_boards_last_checked_at=(
            profile.job_boards_last_checked_at
            if profile is not None
            else None
        ),
        job_boards_next_check_at=(
            profile.job_boards_next_check_at
            if profile is not None
            else None
        ),
        job_boards_last_outcome=(
            safe_text(profile.job_boards_last_outcome, 30)
            if profile is not None and profile.job_boards_last_outcome
            else None
        ),
        job_boards_last_error_code=(
            safe_text(profile.job_boards_last_error_code, 80)
            if profile is not None and profile.job_boards_last_error_code
            else None
        ),
        job_boards_candidate_count=(
            max(0, min(profile.job_boards_candidate_count, 100))
            if profile is not None
            else 0
        ),
        reviewable=profile_status in {"candidate_found", "needs_review"},
    )


def _validated_listing_url(
    *,
    provider: str,
    title: str,
    listing_url: str,
) -> str:
    try:
        normalized = normalize_job_board_result(
            SearchResult(
                title=title,
                url=listing_url,
                snippet="",
                position=1,
            )
        )
    except ValueError as error:
        raise ValueError("candidate_data_mismatch") from error
    if normalized.provider != provider or normalized.listing_url != listing_url:
        raise ValueError("candidate_data_mismatch")
    return normalized.listing_url


def _ranked_item(candidate: RankedCandidate) -> OperatorJobItem | None:
    try:
        listing_url = _validated_listing_url(
            provider=candidate.provider,
            title=candidate.title,
            listing_url=candidate.listing_url,
        )
    except ValueError:
        return None
    return OperatorJobItem(
        **{
            **candidate.__dict__,
            "company_name": safe_text(candidate.company_name, 500),
            "title": safe_text(candidate.title, 500),
            "listing_url": listing_url,
            "location": (
                safe_text(candidate.location, 500)
                if candidate.location
                else None
            ),
            "matched_terms": [
                safe_text(value, 300) for value in candidate.matched_terms[:50]
            ],
            "risk_flags": [
                safe_text(value, 100) for value in candidate.risk_flags[:50]
            ],
        }
    )


def _safe_evidence(value: object) -> list[dict[str, str | int | float | bool | None]]:
    if not isinstance(value, list):
        return []
    cleaned: list[dict[str, str | int | float | bool | None]] = []
    for item in value[:25]:
        if not isinstance(item, dict):
            continue
        safe_item: dict[str, str | int | float | bool | None] = {}
        for raw_key, raw_value in list(item.items())[:25]:
            key = safe_text(raw_key, 80)
            if not key:
                continue
            if isinstance(raw_value, str):
                safe_item[key] = safe_text(raw_value, 2000)
            elif raw_value is None or isinstance(raw_value, (int, float, bool)):
                safe_item[key] = raw_value
        if safe_item:
            cleaned.append(safe_item)
    return cleaned


def load_operator_job_detail(
    database: Engine,
    candidate_id: UUID,
) -> OperatorJobDetail | None:
    with Session(database) as session:
        row = session.execute(
            select(
                JobBoardCandidate,
                func.coalesce(Company.name, JobBoardCandidate.company_name_raw),
            )
            .outerjoin(Company, Company.id == JobBoardCandidate.company_id)
            .where(JobBoardCandidate.id == candidate_id)
        ).one_or_none()
    if row is None:
        return None
    candidate, company_name = row
    listing_url = _validated_listing_url(
        provider=candidate.provider,
        title=candidate.title,
        listing_url=candidate.listing_url,
    )
    displayed_company = safe_text(company_name, 500) or UNKNOWN_EMPLOYER
    return OperatorJobDetail(
        candidate_id=candidate.id,
        provider=candidate.provider,
        company_name=displayed_company,
        company_identity_required=candidate.company_id is None,
        title=safe_text(candidate.title, 500),
        listing_url=listing_url,
        snippet=(safe_text(candidate.snippet, 8000) if candidate.snippet else None),
        location=(safe_text(candidate.location, 500) if candidate.location else None),
        work_mode=candidate.work_mode,
        employment_type=candidate.employment_type,
        published_at=candidate.published_at,
        activity_state=candidate.activity_state,
        activity_code=candidate.activity_code,
        activity_checked_at=candidate.activity_checked_at,
        status=candidate.status,
        evidence=_safe_evidence(candidate.evidence),
        first_seen_at=candidate.first_seen_at,
        last_seen_at=candidate.last_seen_at,
    )


def _review_error(error: ValueError) -> HTTPException:
    error_code = str(error)
    if error_code in {"candidate_not_found", "candidate_company_not_found"}:
        status_code = status.HTTP_404_NOT_FOUND
    elif error_code in {
        "rejected_candidate_cannot_be_approved",
        "approved_candidate_cannot_be_rejected",
        "approved_candidate_missing_posting",
        "candidate_posting_already_exists",
        "invalid_candidate_status",
    }:
        status_code = status.HTTP_409_CONFLICT
    else:
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    return HTTPException(
        status_code=status_code,
        detail={"error_code": error_code},
    )


def _company_review_error(error: ValueError) -> HTTPException:
    error_code = str(error)
    if error_code == "company_profile_not_found":
        status_code = status.HTTP_404_NOT_FOUND
    elif error_code in {
        "verified_profile_protected",
        "profile_not_reviewable",
    }:
        status_code = status.HTTP_409_CONFLICT
    else:
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    return HTTPException(
        status_code=status_code,
        detail={"error_code": error_code},
    )


def _search_run_response(
    run: ProfileJobSearchRun,
    *,
    profile: str,
) -> SearchRunResponse:
    raw = run.result if isinstance(run.result, dict) else {}
    allowed_result_keys = {
        "query_count",
        "raw_result_count",
        "excluded_result_count",
        "matched_candidate_count",
        "candidate_count",
        "new_candidates",
        "refreshed_candidates",
        "suppressed_candidates",
        "reconciled_candidate_count",
        "exclusion_counts",
        "activity_checked_count",
        "activity_changed_count",
        "activity_counts",
    }
    result = {key: raw[key] for key in allowed_result_keys if key in raw}
    matched_candidates: list[dict[str, object]] = []
    raw_candidates = raw.get("matched_candidates")
    if isinstance(raw_candidates, list):
        for item in raw_candidates[:100]:
            if not isinstance(item, dict):
                continue
            try:
                candidate_id = str(UUID(str(item.get("candidate_id", ""))))
                title = safe_text(item.get("title"), 500)
                provider = safe_text(item.get("provider"), 30)
                listing_url = _validated_listing_url(
                    provider=provider,
                    title=title,
                    listing_url=safe_text(item.get("listing_url"), 2048),
                )
                score = int(item.get("score", 0))
            except (TypeError, ValueError):
                continue
            disposition = safe_text(item.get("disposition"), 80)
            if disposition not in SEARCH_DISPOSITIONS:
                disposition = "profile_filtered"
            matched_candidates.append({
                "candidate_id": candidate_id,
                "provider": provider,
                "title": title,
                "company_name": safe_text(item.get("company_name"), 500),
                "listing_url": listing_url,
                "location": (
                    safe_text(item.get("location"), 500)
                    if item.get("location")
                    else None
                ),
                "score": max(0, min(score, 100)),
                "recommendation": safe_text(
                    item.get("recommendation"), 30
                ),
                "status": safe_text(item.get("status"), 30),
                "activity_state": safe_text(
                    item.get("activity_state"), 30
                ),
                "activity_code": safe_text(
                    item.get("activity_code"), 80
                ),
                "disposition": disposition,
            })
    result["matched_candidates"] = matched_candidates
    return SearchRunResponse(
        run_id=run.id,
        profile=profile,
        status=run.status,
        result=result,
        error_code=(safe_text(run.error_code, 80) if run.error_code else None),
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


@router.get("/summary", response_model=OperatorSummary)
def operator_summary() -> OperatorSummary:
    try:
        return load_operator_summary(engine)
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "database_unavailable"},
        ) from None


@router.get("/profiles", response_model=list[OperatorProfileItem])
def operator_profiles() -> list[OperatorProfileItem]:
    try:
        return load_operator_profiles(engine)
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "database_unavailable"},
        ) from None


@router.get("/companies", response_model=OperatorCompanyPage)
def operator_companies(
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    q: Annotated[str | None, Query(min_length=1, max_length=100)] = None,
    profile_status: Annotated[str, Query(max_length=30)] = "all",
) -> OperatorCompanyPage:
    try:
        return load_operator_companies(
            engine,
            limit=limit,
            offset=offset,
            query=q,
            profile_status=profile_status,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error_code": str(error)},
        ) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "database_unavailable"},
        ) from None


@router.get(
    "/companies/{company_id}",
    response_model=OperatorCompanyDetail,
)
def operator_company_detail(company_id: UUID) -> OperatorCompanyDetail:
    try:
        detail = load_operator_company_detail(engine, company_id)
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "database_unavailable"},
        ) from None
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "company_not_found"},
        )
    return detail


@router.post(
    "/companies/{company_id}/approve",
    response_model=CompanyReviewResponse,
)
def operator_approve_company(
    company_id: UUID,
    request: ApproveCompanyRequest,
) -> CompanyReviewResponse:
    try:
        result = review_company_profile(
            engine,
            company_id=company_id,
            approve=True,
            confirmed_identity=request.confirmed_identity,
        )
        return CompanyReviewResponse.model_validate(result)
    except ValueError as error:
        raise _company_review_error(error) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "database_unavailable"},
        ) from None


@router.post(
    "/companies/{company_id}/reject",
    response_model=CompanyReviewResponse,
)
def operator_reject_company(
    company_id: UUID,
    request: RejectCompanyRequest,
) -> CompanyReviewResponse:
    try:
        result = review_company_profile(
            engine,
            company_id=company_id,
            approve=False,
            rejection_reason=request.reason,
        )
        return CompanyReviewResponse.model_validate(result)
    except ValueError as error:
        raise _company_review_error(error) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "database_unavailable"},
        ) from None


@router.post(
    "/search-runs",
    response_model=SearchRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def operator_start_search(
    request: StartSearchRequest,
    background_tasks: BackgroundTasks,
) -> SearchRunResponse:
    try:
        run_id = queue_profile_search(engine, profile_label=request.profile)
        run = load_latest_profile_search(engine, profile_label=request.profile)
    except SearchAlreadyRunning:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "search_already_running"},
        ) from None
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": str(error)},
        ) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "database_unavailable"},
        ) from None
    if run is None or run.id != run_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "search_run_unavailable"},
        )
    background_tasks.add_task(
        execute_profile_search_run,
        engine,
        run_id,
        force=request.force,
    )
    return _search_run_response(run, profile=request.profile)


@router.get(
    "/search-runs/latest",
    response_model=SearchRunResponse | None,
)
def operator_latest_search(
    profile: Annotated[str, Query(min_length=1, max_length=100)],
) -> SearchRunResponse | None:
    try:
        run = load_latest_profile_search(engine, profile_label=profile)
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "database_unavailable"},
        ) from None
    if run is None:
        return None
    return _search_run_response(run, profile=profile)


@router.get("/jobs", response_model=OperatorJobPage)
def operator_jobs(
    profile: Annotated[str, Query(min_length=1, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    minimum_score: Annotated[int, Query(ge=0, le=100)] = 0,
    provider: Annotated[str | None, Query()] = None,
    include_unverified: bool = False,
) -> OperatorJobPage:
    if provider is not None and provider not in SUPPORTED_JOB_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "provider_invalid"},
        )
    try:
        candidates = load_queue(
            engine,
            profile_label=profile,
            limit=limit,
            minimum_score=minimum_score,
            provider=provider,
            include_unverified=include_unverified,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": str(error)},
        ) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "database_unavailable"},
        ) from None
    items = [
        item
        for candidate in candidates
        if (item := _ranked_item(candidate)) is not None
    ]
    return OperatorJobPage(
        profile=profile,
        activity_filter=(
            "active_and_unverified" if include_unverified else "active_only"
        ),
        count=len(items),
        items=items,
    )


@router.get("/jobs/{candidate_id}", response_model=OperatorJobDetail)
def operator_job_detail(candidate_id: UUID) -> OperatorJobDetail:
    try:
        detail = load_operator_job_detail(engine, candidate_id)
    except ValueError as error:
        raise _review_error(error) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "database_unavailable"},
        ) from None
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "candidate_not_found"},
        )
    return detail


@router.post(
    "/jobs/{candidate_id}/approve",
    response_model=JobReviewResponse,
)
def operator_approve_job(
    candidate_id: UUID,
    request: ApproveJobRequest,
) -> JobReviewResponse:
    try:
        result = review_job_board_candidate(
            engine,
            candidate_id=candidate_id,
            approve=True,
            confirmed_active=request.confirmed_active,
            company_name=request.company_name,
        )
    except ValueError as error:
        raise _review_error(error) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "database_unavailable"},
        ) from None
    return JobReviewResponse.model_validate(result)


@router.post(
    "/jobs/{candidate_id}/reject",
    response_model=JobReviewResponse,
)
def operator_reject_job(
    candidate_id: UUID,
    request: RejectJobRequest,
) -> JobReviewResponse:
    if request.confirmed_rejection is not True:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "rejection_confirmation_required"},
        )
    try:
        result = review_job_board_candidate(
            engine,
            candidate_id=candidate_id,
            approve=False,
        )
    except ValueError as error:
        raise _review_error(error) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "database_unavailable"},
        ) from None
    return JobReviewResponse.model_validate(result)
