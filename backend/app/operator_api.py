"""Authenticated API used by the local operator console."""

import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import engine
from app.company_profile_search_service import (
    CompanyProfileSearchError,
    discover_company_profile,
)
from app.browser_job_agent import (
    BrowserAgentError,
    browser_role_matches,
    collect_browser_jobs,
    normalize_browser_card_fields,
)
from app.company_discovery_runs import (
    CompanyDiscoveryRunError,
    CompanyDiscoveryRunSnapshot,
    execute_company_discovery_run,
    load_company_discovery_run,
    load_latest_company_discovery_run,
    pause_company_discovery_run,
    queue_company_discovery_run,
    resume_company_discovery_run,
)
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
)
from app.job_roles import ROLE_CATALOG
from app.local_job_agent import (
    LocalJobAgentError,
    LocalOllamaJobAgent,
    finalize_job_assessment,
    profile_agent_hash,
)
from app.matching import CandidateProfileSpec
from app.manual_job_import import (
    MANUAL_JOB_PROVIDERS,
    PLACEHOLDER_JOB_IDENTIFIERS,
    is_placeholder_job_identifier,
    normalize_manual_job_result,
)
from app.models import (
    CandidateProfile,
    CareerSource,
    Company,
    CompanyAffiliation,
    CompanyWebProfile,
    JobApplication,
    JobApplicationEvent,
    JobBoardCandidate,
    JobCandidateAssessment,
    JobCandidateDismissal,
    JobMatch,
    JobPosting,
    ProfileJobSearchRun,
)
from app.native_job_search import build_native_search_links
from app.operator_auth import require_operator_token
from app.operator_search_runs import (
    SearchAlreadyRunning,
    SearchCooldownActive,
    execute_profile_search_run,
    load_latest_profile_search,
    queue_profile_search,
)
from app.review_company_profile import review_company_profile
from app.review_job_board_candidate import review_job_board_candidate
from app.review_job_board_queue import RankedCandidate, load_queue, rank_candidate


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

APPLICATION_TRANSITIONS = {
    "to_apply": {"applied", "withdrawn"},
    "applied": {"interview", "rejected", "offer", "withdrawn"},
    "interview": {"rejected", "offer", "withdrawn"},
    "rejected": {"to_apply"},
    "offer": {"withdrawn"},
    "withdrawn": {"to_apply"},
}

ApplicationStatus = Literal[
    "to_apply",
    "applied",
    "interview",
    "rejected",
    "offer",
    "withdrawn",
]


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
    secondary_roles: list[str]
    tertiary_roles: list[str]
    skills: list[str]
    professional_experience_years: int = Field(ge=0, le=50)
    internship_months: int = Field(ge=0, le=120)
    next_search_at: datetime | None
    last_search_outcome: str | None


class UpdateProfileExperienceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    professional_experience_years: int = Field(ge=0, le=50)
    internship_months: int = Field(ge=0, le=120)


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


class DiscoverCompanyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed_external_search: Literal[True]


class CompanyDiscoveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    company_id: UUID
    company_name: str
    status: Literal["succeeded", "failed"]
    profile_status: Literal[
        "candidate_found",
        "verified",
        "needs_review",
        "not_found",
    ] | None
    confidence: Literal["high", "medium", "low"] | None
    result_count: int = Field(ge=0, le=100)
    attempt_count: int = Field(ge=0, le=10)
    profile_updated: bool
    verification_code: str | None
    error_code: str | None


class StartCompanyDiscoveryRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed_external_search: Literal[True]
    query_budget: int = Field(default=2200, ge=3, le=2200)


class ConfirmCompanyDiscoveryRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: Literal[True]


class CompanyDiscoveryRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    run_id: UUID
    status: Literal[
        "queued",
        "running",
        "pause_requested",
        "paused",
        "succeeded",
        "failed",
    ]
    scope: Literal["unprofiled"]
    query_budget: int = Field(ge=3, le=2200)
    query_count: int = Field(ge=0, le=2200)
    total_count: int = Field(ge=0, le=1000)
    queued_count: int = Field(ge=0, le=1000)
    running_count: int = Field(ge=0, le=4)
    succeeded_count: int = Field(ge=0, le=1000)
    failed_count: int = Field(ge=0, le=1000)
    profile_counts: dict[str, int]
    error_counts: dict[str, int]
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


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
    operator_viewed_at: datetime | None
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
    operator_viewed_at: datetime | None


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
    roles: list[str] = Field(min_length=1, max_length=10)
    search_mode: Literal["quick", "deep"] = "quick"
    locations: list[str] = Field(default_factory=list, max_length=3)
    work_modes: list[Literal["remote", "hybrid", "onsite"]] = Field(
        default_factory=lambda: ["remote", "hybrid", "onsite"],
        min_length=1,
        max_length=3,
    )
    sources: list[
        Literal[
            "linkedin",
            "kariyer",
            "indeed",
            "glassdoor",
            "ats",
            "turkey_tech",
            "remote_feeds",
        ]
    ] = Field(
        default_factory=lambda: [
            "linkedin",
            "kariyer",
            "indeed",
            "glassdoor",
            "ats",
            "turkey_tech",
            "remote_feeds",
        ],
        min_length=1,
        max_length=7,
    )
    max_age_days: int = Field(default=30, ge=1, le=90)
    confirmed_external_search: Literal[True]
    force: bool = False


class NativeSearchLinksRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: str = Field(min_length=1, max_length=100)
    location: str | None = Field(default=None, min_length=1, max_length=100)
    sources: list[
        Literal[
            "linkedin",
            "kariyer",
            "indeed",
            "glassdoor",
            "ats",
            "turkey_tech",
            "remote_feeds",
        ]
    ] = Field(min_length=1, max_length=7)


class NativeSearchLinkItem(BaseModel):
    provider: str
    label: str
    url: str
    query_prefilled: bool
    location_prefilled: bool
    note: str


class NativeSearchLinksResponse(BaseModel):
    role: str
    location: str | None
    links: list[NativeSearchLinkItem]
    unavailable_sources: list[str]


class ImportManualJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    listing_url: str = Field(min_length=1, max_length=2048)
    title: str = Field(min_length=1, max_length=300)
    company_name: str = Field(min_length=1, max_length=500)
    location: str | None = Field(default=None, min_length=1, max_length=500)
    work_mode: Literal["remote", "hybrid", "onsite", "unknown"] = "unknown"
    employment_type: Literal[
        "full_time",
        "part_time",
        "contract",
        "internship",
        "temporary",
        "unknown",
    ] = "unknown"
    description_text: str | None = Field(default=None, max_length=20_000)
    confirmed_visible: Literal[True]


class ImportManualJobResponse(BaseModel):
    candidate_id: UUID
    provider: str
    listing_url: str
    status: str
    created: bool
    changed: bool


class BrowserCollectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: Literal[
        "linkedin",
        "kariyer",
        "indeed",
        "glassdoor",
        "techcareer",
        "yenibiris",
        "secretcv",
        "toptalent",
        "weworkremotely",
        "remoteok",
        "remotive",
        "jobicy",
    ] | None = None
    providers: list[
        Literal[
            "linkedin",
            "kariyer",
            "indeed",
            "glassdoor",
            "techcareer",
            "yenibiris",
            "secretcv",
            "toptalent",
            "weworkremotely",
            "remoteok",
            "remotive",
            "jobicy",
        ]
    ] = Field(default_factory=list, max_length=12)
    role: str = Field(min_length=1, max_length=100)
    location: str | None = Field(default=None, min_length=1, max_length=100)
    work_modes: list[Literal["remote", "hybrid", "onsite"]] = Field(
        default_factory=lambda: ["remote", "hybrid", "onsite"],
        min_length=1,
        max_length=3,
    )
    max_results: int | None = Field(default=None, ge=1, le=20)
    max_results_per_provider: int = Field(default=10, ge=1, le=20)
    profile: str = Field(min_length=1, max_length=100)
    confirmed_browser_launch: Literal[True]


class BrowserSourceDiagnosticItem(BaseModel):
    provider: str
    label: str
    outcome: Literal[
        "collected",
        "no_results",
        "login_required",
        "rate_limited",
        "blocked",
        "source_cooldown",
        "source_disabled",
        "failed",
    ]
    collected_count: int
    error_code: str | None
    agent_used: bool = False
    agent_action_count: int = Field(default=0, ge=0, le=10)
    access_reason: str | None = Field(default=None, max_length=200)
    access_trace: list[str] = Field(default_factory=list, max_length=12)


class BrowserCollectResponse(BaseModel):
    provider: str
    providers: list[str] = Field(default_factory=list)
    collected_count: int
    created_count: int
    updated_count: int
    skipped_count: int = 0
    assessment_queued_count: int = 0
    candidate_ids: list[UUID]
    diagnostics: list[BrowserSourceDiagnosticItem] = Field(default_factory=list)


class BrowserCollectedItem(BaseModel):
    candidate_id: UUID
    provider: str
    title: str
    company_name: str
    listing_url: str
    location: str | None
    work_mode: Literal["remote", "hybrid", "onsite", "unknown"]
    status: str
    collected_at: datetime
    search_roles: list[str] = Field(default_factory=list)
    assessment_state: Literal["ready", "pending", "unavailable"] = "unavailable"
    fit_score: int | None = Field(default=None, ge=0, le=100)
    fit_stars: int | None = Field(default=None, ge=1, le=5)
    fit_recommendation: str | None = None
    fit_confidence: Literal["low", "medium", "high"] | None = None
    required_experience_min: int | None = Field(default=None, ge=0, le=50)
    experience_gap: int | None = Field(default=None, ge=0, le=50)
    matched_requirements: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    preferred_requirements: list[str] = Field(default_factory=list)
    hard_blockers: list[str] = Field(default_factory=list)
    fit_summary: str | None = None


class BrowserCollectedPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[BrowserCollectedItem]


class BrowserCleanupResponse(BaseModel):
    repaired_count: int
    quarantined_count: int


class BrowserStaleCleanupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    profile: str = Field(min_length=1, max_length=100)
    older_than_days: int = Field(default=0, ge=0, le=365)
    limit: int = Field(default=500, ge=1, le=2_000)
    apply: bool = False
    confirmed_cleanup: bool = False


class BrowserStaleCleanupResponse(BaseModel):
    matched_count: int
    quarantined_count: int
    applied: bool


class BrowserRoleCleanupRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    profile: str = Field(min_length=1, max_length=100)
    limit: int = Field(default=500, ge=1, le=2_000)
    apply: bool = False
    confirmed_cleanup: bool = False


class JobRoleCatalogItem(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)


class JobAssessmentBackfillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    profile: str = Field(min_length=1, max_length=100)
    limit: int = Field(default=25, ge=1, le=100)
    confirmed_local_processing: Literal[True]


class JobAssessmentBackfillResponse(BaseModel):
    queued_count: int = Field(ge=0, le=100)


class BrowserResultProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    profile: str = Field(min_length=1, max_length=100)


class BrowserResultDismissResponse(BaseModel):
    candidate_id: UUID
    dismissed: bool


class JobViewedResponse(BaseModel):
    candidate_id: UUID
    operator_viewed_at: datetime


class UpdateJobApplicationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    profile: str = Field(min_length=1, max_length=100)
    status: ApplicationStatus
    notes: str | None = Field(default=None, max_length=2000)


class JobApplicationItem(BaseModel):
    application_id: UUID
    candidate_id: UUID
    profile: str
    status: ApplicationStatus
    notes: str | None
    applied_at: datetime | None
    created_at: datetime
    updated_at: datetime
    provider: str
    title: str
    company_name: str
    listing_url: str
    location: str | None
    work_mode: str
    activity_state: str


class JobApplicationPage(BaseModel):
    profile: str
    total: int
    limit: int
    offset: int
    items: list[JobApplicationItem]


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
                CandidateProfile.secondary_roles,
                CandidateProfile.tertiary_roles,
                CandidateProfile.skills,
                CandidateProfile.max_years_experience,
                CandidateProfile.internship_months,
                CandidateProfile.job_search_next_check_at,
                CandidateProfile.job_search_last_outcome,
            ).order_by(CandidateProfile.label)
        ).all()
    return [
        OperatorProfileItem(
            label=label,
            target_roles=roles if isinstance(roles, list) else [],
            secondary_roles=(secondary if isinstance(secondary, list) else []),
            tertiary_roles=(tertiary if isinstance(tertiary, list) else []),
            skills=skills if isinstance(skills, list) else [],
            professional_experience_years=professional_years,
            internship_months=internship_months,
            next_search_at=next_search_at,
            last_search_outcome=last_outcome,
        )
        for (
            label,
            roles,
            secondary,
            tertiary,
            skills,
            professional_years,
            internship_months,
            next_search_at,
            last_outcome,
        ) in rows
    ]


def _candidate_profile_spec(profile: CandidateProfile) -> CandidateProfileSpec:
    return CandidateProfileSpec(
        label=profile.label,
        target_roles=profile.target_roles,
        secondary_roles=profile.secondary_roles,
        tertiary_roles=profile.tertiary_roles,
        skills=profile.skills,
        preferred_locations=profile.preferred_locations,
        preferred_remote_locations=profile.preferred_remote_locations,
        excluded_locations=profile.excluded_locations,
        allowed_work_modes=profile.allowed_work_modes,
        location_filter_mode=profile.location_filter_mode,
        max_listing_age_days=profile.max_listing_age_days,
        excluded_keywords=profile.excluded_keywords,
        max_years_experience=profile.max_years_experience,
        remote_allowed=profile.remote_allowed,
    )


def update_profile_experience(
    database: Engine,
    *,
    profile_label: str,
    professional_experience_years: int,
    internship_months: int,
) -> OperatorProfileItem | None:
    with Session(database) as session:
        profile = session.scalar(
            select(CandidateProfile)
            .where(CandidateProfile.label == profile_label)
            .with_for_update()
        )
        if profile is None:
            return None
        profile.max_years_experience = professional_experience_years
        profile.internship_months = internship_months
        profile.config_hash = _candidate_profile_spec(profile).config_hash()
        session.query(JobCandidateAssessment).filter(
            JobCandidateAssessment.profile_id == profile.id
        ).delete(synchronize_session=False)
        session.commit()
    return next(
        (
            item
            for item in load_operator_profiles(database)
            if item.label == profile_label
        ),
        None,
    )


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
        normalized = normalize_manual_job_result(
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
        operator_viewed_at=getattr(candidate, "operator_viewed_at", None),
    )


def mark_operator_job_viewed(
    database: Engine,
    candidate_id: UUID,
) -> JobViewedResponse | None:
    with Session(database) as session:
        candidate = session.scalar(
            select(JobBoardCandidate)
            .where(JobBoardCandidate.id == candidate_id)
            .with_for_update()
        )
        if candidate is None:
            return None
        if candidate.operator_viewed_at is None:
            candidate.operator_viewed_at = datetime.now(timezone.utc)
            session.commit()
        return JobViewedResponse(
            candidate_id=candidate.id,
            operator_viewed_at=candidate.operator_viewed_at,
        )


def import_manual_job_candidate(
    database: Engine,
    request: ImportManualJobRequest,
    *,
    origin: Literal["manual", "browser_agent"] = "manual",
    search_role: str | None = None,
) -> ImportManualJobResponse:
    listing = normalize_manual_job_result(
        SearchResult(
            title=request.title,
            url=request.listing_url,
            snippet="",
            position=1,
        )
    )
    if listing.provider not in MANUAL_JOB_PROVIDERS:
        raise ValueError("provider_invalid")

    company_name = safe_text(request.company_name, 500)
    title = safe_text(request.title, 300)
    location = safe_text(request.location, 500) if request.location else None
    description_text = (
        safe_text(request.description_text, 20_000)
        if request.description_text
        else None
    )
    if not company_name or not title:
        raise ValueError("manual_job_invalid")

    now = datetime.now(timezone.utc)
    evidence_kind = (
        "browser_agent_import" if origin == "browser_agent" else "manual_operator_import"
    )
    activity_code = (
        "browser_agent_listing_confirmation"
        if origin == "browser_agent"
        else "manual_operator_listing_confirmation"
    )
    evidence = {
        "kind": evidence_kind,
        "url": listing.listing_url,
        "confirmed_visible": "true",
        "checked_at": now.isoformat(),
    }
    if origin == "browser_agent" and search_role:
        evidence["search_role"] = safe_text(search_role, 100)
    with Session(database) as session:
        candidate = session.scalar(
            select(JobBoardCandidate)
            .where(
                JobBoardCandidate.provider == listing.provider,
                JobBoardCandidate.external_id == listing.external_id,
            )
            .with_for_update()
        )
        created = candidate is None
        changed = False
        if candidate is None:
            candidate = JobBoardCandidate(
                company_id=None,
                provider=listing.provider,
                external_id=listing.external_id,
                listing_url=listing.listing_url,
                title=title,
                company_name_raw=company_name,
                location=location,
                work_mode=request.work_mode,
                employment_type=request.employment_type,
                activity_state="active",
                activity_code=activity_code,
                activity_checked_at=now,
                snippet=description_text,
                status="needs_review",
                evidence=[evidence],
                last_seen_at=now,
                updated_at=now,
            )
            session.add(candidate)
            changed = True
        elif candidate.status == "needs_review":
            candidate.listing_url = listing.listing_url
            candidate.title = title
            candidate.company_name_raw = company_name
            candidate.location = location
            candidate.work_mode = request.work_mode
            candidate.employment_type = request.employment_type
            if description_text:
                candidate.snippet = description_text
            candidate.activity_state = "active"
            candidate.activity_code = activity_code
            candidate.activity_checked_at = now
            candidate.last_seen_at = now
            candidate.updated_at = now
            candidate.evidence = [*(candidate.evidence or [])[-19:], evidence]
            changed = True
        session.commit()
        session.refresh(candidate)
        return ImportManualJobResponse(
            candidate_id=candidate.id,
            provider=candidate.provider,
            listing_url=candidate.listing_url,
            status=candidate.status,
            created=created,
            changed=changed,
        )


def _candidate_content_hash(candidate: JobBoardCandidate) -> str:
    content = "\0".join(
        (
            safe_text(candidate.title, 500),
            safe_text(candidate.company_name_raw, 500),
            safe_text(candidate.location, 500),
            safe_text(candidate.work_mode, 20),
            safe_text(candidate.snippet, 20_000),
        )
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _candidate_search_role(candidate: JobBoardCandidate) -> str | None:
    evidence = candidate.evidence if isinstance(candidate.evidence, list) else []
    for item in reversed(evidence[-20:]):
        if not isinstance(item, dict) or item.get("kind") != "browser_agent_import":
            continue
        role = safe_text(item.get("search_role"), 100)
        if role:
            return role
    return None


def _candidate_search_roles(candidate: JobBoardCandidate) -> list[str]:
    evidence = candidate.evidence if isinstance(candidate.evidence, list) else []
    roles: list[str] = []
    seen: set[str] = set()
    for item in evidence[-20:]:
        if not isinstance(item, dict) or item.get("kind") != "browser_agent_import":
            continue
        role = safe_text(item.get("search_role"), 100)
        role_key = role.casefold()
        if role and role_key not in seen:
            seen.add(role_key)
            roles.append(role)
    return roles


def assess_job_candidates(
    database: Engine,
    *,
    profile_label: str,
    candidate_ids: list[UUID],
) -> int:
    """Assess jobs from any ingestion source without discarding on agent failure."""

    bounded_ids = list(dict.fromkeys(candidate_ids))[:100]
    if not bounded_ids:
        return 0
    with Session(database) as session:
        profile = session.scalar(
            select(CandidateProfile).where(CandidateProfile.label == profile_label)
        )
        if profile is None:
            return 0
        profile_spec = _candidate_profile_spec(profile)
        profile_data = {
            "label": profile.label,
            "target_roles": list(profile.target_roles),
            "skills": list(profile.skills),
            "professional_years": profile.max_years_experience,
            "internship_months": profile.internship_months,
            "preferred_locations": list(profile.preferred_locations),
            "allowed_work_modes": list(profile.allowed_work_modes),
        }
        candidates = list(
            session.scalars(
                select(JobBoardCandidate).where(
                    JobBoardCandidate.id.in_(bounded_ids),
                    JobBoardCandidate.status != "filtered_out",
                )
            )
        )
        existing_rows = list(
            session.scalars(
                select(JobCandidateAssessment).where(
                    JobCandidateAssessment.profile_id == profile.id,
                    JobCandidateAssessment.candidate_id.in_(bounded_ids),
                )
            )
        )
        existing_hashes = {
            item.candidate_id: (item.job_content_hash, item.profile_hash)
            for item in existing_rows
        }
        profile_id = profile.id

    try:
        agent = LocalOllamaJobAgent(
            model=os.getenv("OLLAMA_AGENT_MODEL", "qwen3:8b"),
            base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
        )
    except ValueError:
        return 0

    assessed_count = 0
    try:
        for candidate in candidates:
            description = safe_text(candidate.snippet, 20_000)
            if len(description) < 100:
                continue
            requested_role = _candidate_search_role(candidate)
            effective_roles = list(
                dict.fromkeys(
                    [
                        role
                        for role in [requested_role, *profile_data["target_roles"]]
                        if role
                    ]
                )
            )[:30]
            candidate_profile_hash = profile_agent_hash(
                label=profile_data["label"],
                target_roles=effective_roles,
                skills=profile_data["skills"],
                professional_years=profile_data["professional_years"],
                internship_months=profile_data["internship_months"],
                preferred_locations=profile_data["preferred_locations"],
                allowed_work_modes=profile_data["allowed_work_modes"],
            )
            job_hash = _candidate_content_hash(candidate)
            if existing_hashes.get(candidate.id) == (
                job_hash,
                candidate_profile_hash,
            ):
                continue
            company_name = safe_text(candidate.company_name_raw, 500) or UNKNOWN_EMPLOYER
            base = rank_candidate(
                profile_spec.model_copy(update={"target_roles": effective_roles}),
                candidate_id=candidate.id,
                provider=candidate.provider,
                company_name=company_name,
                title=candidate.title,
                listing_url=candidate.listing_url,
                snippet=description,
                location=candidate.location,
                work_mode=candidate.work_mode,
                employment_type=candidate.employment_type,
                published_at=candidate.published_at,
                activity_state=candidate.activity_state,
                activity_code=candidate.activity_code,
                operator_viewed_at=candidate.operator_viewed_at,
            )
            try:
                analysis = agent.assess_job(
                    title=candidate.title,
                    company_name=company_name,
                    location=candidate.location,
                    work_mode=candidate.work_mode,
                    description_text=description,
                    target_roles=effective_roles,
                    skills=profile_data["skills"],
                    professional_years=profile_data["professional_years"],
                    internship_months=profile_data["internship_months"],
                )
            except LocalJobAgentError as error:
                if str(error) in {"local_agent_unavailable", "local_agent_timeout"}:
                    break
                continue
            final = finalize_job_assessment(
                analysis=analysis,
                base_score=base.score,
                professional_years=profile_data["professional_years"],
                model=agent.model,
            )
            values = {
                "profile_id": profile_id,
                "candidate_id": candidate.id,
                "score": final.score,
                "stars": final.stars,
                "recommendation": final.recommendation,
                "confidence": final.confidence,
                "required_experience_min": final.required_experience_min,
                "experience_gap": final.experience_gap,
                "matched_requirements": final.matched_requirements,
                "missing_requirements": final.missing_requirements,
                "preferred_requirements": final.preferred_requirements,
                "hard_blockers": final.hard_blockers,
                "summary": final.summary,
                "model": final.model,
                "prompt_version": final.prompt_version,
                "job_content_hash": job_hash,
                "profile_hash": candidate_profile_hash,
                "updated_at": datetime.now(timezone.utc),
            }
            statement = pg_insert(JobCandidateAssessment).values(**values)
            statement = statement.on_conflict_do_update(
                constraint="uq_job_candidate_assessments_profile_candidate",
                set_={
                    key: value
                    for key, value in values.items()
                    if key not in {"profile_id", "candidate_id"}
                },
            )
            with Session(database) as session:
                session.execute(statement)
                session.commit()
            assessed_count += 1
    finally:
        agent.close()
    return assessed_count


def load_assessable_candidate_ids(
    database: Engine,
    *,
    profile_label: str,
    limit: int = 100,
) -> list[UUID]:
    """Return unassessed candidates with enough description, regardless of source."""

    bounded_limit = max(1, min(limit, 100))
    with Session(database) as session:
        profile = session.scalar(
            select(CandidateProfile).where(CandidateProfile.label == profile_label)
        )
        if profile is None:
            raise ValueError("profile_not_found")
        return list(
            session.scalars(
                select(JobBoardCandidate.id)
                .outerjoin(
                    JobCandidateAssessment,
                    (
                        JobCandidateAssessment.candidate_id
                        == JobBoardCandidate.id
                    )
                    & (JobCandidateAssessment.profile_id == profile.id),
                )
                .where(
                    JobBoardCandidate.status != "filtered_out",
                    JobBoardCandidate.snippet.is_not(None),
                    func.length(func.btrim(JobBoardCandidate.snippet)) >= 100,
                    JobCandidateAssessment.id.is_(None),
                )
                .order_by(
                    JobBoardCandidate.last_seen_at.desc(),
                    JobBoardCandidate.id.desc(),
                )
                .limit(bounded_limit)
            )
        )


def assess_profile_search_run_candidates(
    database: Engine,
    run_id: UUID,
) -> int:
    """Assess candidates persisted by one completed automatic profile search."""

    with Session(database) as session:
        row = session.execute(
            select(ProfileJobSearchRun, CandidateProfile.label)
            .join(
                CandidateProfile,
                CandidateProfile.id == ProfileJobSearchRun.profile_id,
            )
            .where(ProfileJobSearchRun.id == run_id)
        ).one_or_none()
        if row is None or row[0].status != "succeeded":
            return 0
        run, profile_label = row
        result = run.result if isinstance(run.result, dict) else {}
        raw_candidates = result.get("matched_candidates", [])
        if not isinstance(raw_candidates, list):
            return 0
        candidate_ids: list[UUID] = []
        for item in raw_candidates[:100]:
            if not isinstance(item, dict):
                continue
            try:
                candidate_ids.append(UUID(str(item.get("candidate_id", ""))))
            except (TypeError, ValueError):
                continue
    return assess_job_candidates(
        database,
        profile_label=profile_label,
        candidate_ids=candidate_ids,
    )


def load_browser_collected_jobs(
    database: Engine,
    *,
    profile_label: str,
    limit: int,
    offset: int,
) -> BrowserCollectedPage:
    activity_codes = (
        "browser_agent_listing_confirmation",
        "manual_operator_listing_confirmation",
    )
    with Session(database) as session:
        profile = session.scalar(
            select(CandidateProfile).where(CandidateProfile.label == profile_label)
        )
        if profile is None:
            raise ValueError("profile_not_found")
        dismissed = select(JobCandidateDismissal.id).where(
            JobCandidateDismissal.candidate_id == JobBoardCandidate.id,
            JobCandidateDismissal.profile_id == profile.id,
        ).exists()
        conditions = (
            JobBoardCandidate.activity_code.in_(activity_codes),
            JobBoardCandidate.status != "filtered_out",
            JobBoardCandidate.external_id.not_in(PLACEHOLDER_JOB_IDENTIFIERS),
            ~dismissed,
        )
        total = session.scalar(
            select(func.count()).select_from(JobBoardCandidate).where(*conditions)
        ) or 0
        rows = session.execute(
            select(
                JobBoardCandidate,
                func.coalesce(Company.name, JobBoardCandidate.company_name_raw),
                JobCandidateAssessment,
            )
            .outerjoin(Company, Company.id == JobBoardCandidate.company_id)
            .outerjoin(
                JobCandidateAssessment,
                (
                    JobCandidateAssessment.candidate_id == JobBoardCandidate.id
                )
                & (JobCandidateAssessment.profile_id == profile.id),
            )
            .where(*conditions)
            .order_by(
                JobBoardCandidate.last_seen_at.desc(),
                JobBoardCandidate.id.desc(),
            )
            .offset(offset)
            .limit(limit)
        ).all()
        items: list[BrowserCollectedItem] = []
        for candidate, company_name, assessment in rows:
            if is_placeholder_job_identifier(candidate.external_id):
                continue
            try:
                title, displayed_company, location = normalize_browser_card_fields(
                    title=candidate.title,
                    company_name=company_name or UNKNOWN_EMPLOYER,
                    location=candidate.location,
                )
                listing_url = _validated_listing_url(
                    provider=candidate.provider,
                    title=title,
                    listing_url=candidate.listing_url,
                )
            except ValueError:
                continue
            items.append(
                BrowserCollectedItem(
                    candidate_id=candidate.id,
                    provider=candidate.provider,
                    title=title,
                    company_name=displayed_company,
                    listing_url=listing_url,
                    location=location,
                    work_mode=(
                        candidate.work_mode
                        if candidate.work_mode in {
                            "remote",
                            "hybrid",
                            "onsite",
                            "unknown",
                        }
                        else "unknown"
                    ),
                    status=candidate.status,
                    collected_at=candidate.last_seen_at,
                    search_roles=_candidate_search_roles(candidate),
                    assessment_state=(
                        "ready"
                        if assessment is not None
                        else "pending"
                        if candidate.snippet
                        else "unavailable"
                    ),
                    fit_score=(assessment.score if assessment else None),
                    fit_stars=(assessment.stars if assessment else None),
                    fit_recommendation=(
                        assessment.recommendation if assessment else None
                    ),
                    fit_confidence=(assessment.confidence if assessment else None),
                    required_experience_min=(
                        assessment.required_experience_min if assessment else None
                    ),
                    experience_gap=(
                        assessment.experience_gap if assessment else None
                    ),
                    matched_requirements=(
                        list(assessment.matched_requirements) if assessment else []
                    ),
                    missing_requirements=(
                        list(assessment.missing_requirements) if assessment else []
                    ),
                    preferred_requirements=(
                        list(assessment.preferred_requirements)
                        if assessment
                        else []
                    ),
                    hard_blockers=(
                        list(assessment.hard_blockers) if assessment else []
                    ),
                    fit_summary=(assessment.summary if assessment else None),
                )
            )
    return BrowserCollectedPage(
        total=max(0, total - (len(rows) - len(items))),
        limit=limit,
        offset=offset,
        items=items,
    )


def set_browser_candidate_dismissal(
    database: Engine,
    *,
    candidate_id: UUID,
    profile_label: str,
    dismissed: bool,
) -> BrowserResultDismissResponse:
    activity_codes = (
        "browser_agent_listing_confirmation",
        "manual_operator_listing_confirmation",
    )
    with Session(database) as session:
        profile = session.scalar(
            select(CandidateProfile).where(CandidateProfile.label == profile_label)
        )
        if profile is None:
            raise ValueError("profile_not_found")
        candidate = session.scalar(
            select(JobBoardCandidate).where(
                JobBoardCandidate.id == candidate_id,
                JobBoardCandidate.activity_code.in_(activity_codes),
                JobBoardCandidate.external_id.not_in(PLACEHOLDER_JOB_IDENTIFIERS),
            )
        )
        if candidate is None:
            raise ValueError("candidate_not_found")

        if dismissed:
            session.execute(
                pg_insert(JobCandidateDismissal)
                .values(profile_id=profile.id, candidate_id=candidate.id)
                .on_conflict_do_nothing(
                    constraint="uq_job_candidate_dismissals_profile_candidate"
                )
            )
        else:
            session.execute(
                delete(JobCandidateDismissal).where(
                    JobCandidateDismissal.profile_id == profile.id,
                    JobCandidateDismissal.candidate_id == candidate.id,
                )
            )
        session.commit()
    return BrowserResultDismissResponse(
        candidate_id=candidate_id,
        dismissed=dismissed,
    )


def _quarantine_browser_candidate(
    candidate: JobBoardCandidate,
    *,
    now: datetime,
    reason: str,
) -> None:
    candidate.status = "filtered_out"
    candidate.activity_state = "unknown"
    candidate.activity_code = "browser_agent_invalid_listing"
    candidate.updated_at = now
    candidate.evidence = [
        *(candidate.evidence or [])[-19:],
        {
            "kind": "browser_agent_quality_rejection",
            "reason": reason,
            "checked_at": now.isoformat(),
        },
    ]


def cleanup_browser_collected_jobs(database: Engine) -> BrowserCleanupResponse:
    """Repair browser text and quarantine invalid listing records, never delete."""

    activity_codes = (
        "browser_agent_listing_confirmation",
        "manual_operator_listing_confirmation",
    )
    now = datetime.now(timezone.utc)
    repaired_count = 0
    quarantined_count = 0
    with Session(database) as session:
        candidates = session.scalars(
            select(JobBoardCandidate)
            .where(
                JobBoardCandidate.activity_code.in_(activity_codes),
                JobBoardCandidate.status == "needs_review",
            )
            .with_for_update()
        ).all()
        for candidate in candidates:
            if is_placeholder_job_identifier(candidate.external_id):
                _quarantine_browser_candidate(
                    candidate,
                    now=now,
                    reason="placeholder_identifier",
                )
                quarantined_count += 1
                continue

            try:
                _validated_listing_url(
                    provider=candidate.provider,
                    title=candidate.title,
                    listing_url=candidate.listing_url,
                )
            except ValueError:
                _quarantine_browser_candidate(
                    candidate,
                    now=now,
                    reason="invalid_listing_url",
                )
                quarantined_count += 1
                continue

            try:
                title, company_name, location = normalize_browser_card_fields(
                    title=candidate.title,
                    company_name=candidate.company_name_raw,
                    location=candidate.location,
                )
            except ValueError:
                _quarantine_browser_candidate(
                    candidate,
                    now=now,
                    reason="invalid_listing_text",
                )
                quarantined_count += 1
                continue
            if (
                title != candidate.title
                or company_name != candidate.company_name_raw
                or location != candidate.location
            ):
                candidate.title = title
                candidate.company_name_raw = company_name
                candidate.location = location
                candidate.updated_at = now
                candidate.evidence = [
                    *(candidate.evidence or [])[-19:],
                    {
                        "kind": "browser_agent_quality_repair",
                        "checked_at": now.isoformat(),
                    },
                ]
                repaired_count += 1
        session.commit()
    return BrowserCleanupResponse(
        repaired_count=repaired_count,
        quarantined_count=quarantined_count,
    )


def cleanup_stale_unassessed_browser_jobs(
    database: Engine,
    *,
    profile_label: str,
    older_than_days: int,
    limit: int,
    apply: bool,
    confirmed_cleanup: bool,
) -> BrowserStaleCleanupResponse:
    """Preview or quarantine unusable old records without deleting history."""

    if apply and not confirmed_cleanup:
        raise ValueError("cleanup_confirmation_required")

    bounded_limit = max(1, min(limit, 2_000))
    activity_codes = (
        "browser_agent_listing_confirmation",
        "manual_operator_listing_confirmation",
    )
    with Session(database) as session:
        profile = session.scalar(
            select(CandidateProfile).where(CandidateProfile.label == profile_label)
        )
        if profile is None:
            raise ValueError("profile_not_found")

        has_assessment = select(JobCandidateAssessment.id).where(
            JobCandidateAssessment.candidate_id == JobBoardCandidate.id
        ).exists()
        has_application = select(JobApplication.id).where(
            JobApplication.candidate_id == JobBoardCandidate.id
        ).exists()
        conditions = [
            JobBoardCandidate.activity_code.in_(activity_codes),
            JobBoardCandidate.status == "needs_review",
            or_(
                JobBoardCandidate.snippet.is_(None),
                func.length(func.btrim(JobBoardCandidate.snippet)) < 100,
            ),
            ~has_assessment,
            ~has_application,
        ]
        if older_than_days > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
            conditions.append(JobBoardCandidate.last_seen_at < cutoff)
        statement = (
            select(JobBoardCandidate)
            .where(*conditions)
            .order_by(JobBoardCandidate.last_seen_at.asc())
            .limit(bounded_limit)
        )
        if apply:
            statement = statement.with_for_update()
        candidates = list(session.scalars(statement))

        if apply:
            now = datetime.now(timezone.utc)
            for candidate in candidates:
                _quarantine_browser_candidate(
                    candidate,
                    now=now,
                    reason="stale_unassessed_insufficient_description",
                )
            session.commit()

    return BrowserStaleCleanupResponse(
        matched_count=len(candidates),
        quarantined_count=(len(candidates) if apply else 0),
        applied=apply,
    )


def cleanup_role_mismatched_browser_jobs(
    database: Engine,
    *,
    profile_label: str,
    limit: int,
    apply: bool,
    confirmed_cleanup: bool,
) -> BrowserStaleCleanupResponse:
    """Preview or quarantine old browser noise that matches none of its roles."""

    if apply and not confirmed_cleanup:
        raise ValueError("cleanup_confirmation_required")

    bounded_limit = max(1, min(limit, 2_000))
    with Session(database) as session:
        profile_exists = session.scalar(
            select(CandidateProfile.id).where(
                CandidateProfile.label == profile_label
            )
        )
        if profile_exists is None:
            raise ValueError("profile_not_found")

        has_assessment = select(JobCandidateAssessment.id).where(
            JobCandidateAssessment.candidate_id == JobBoardCandidate.id
        ).exists()
        has_application = select(JobApplication.id).where(
            JobApplication.candidate_id == JobBoardCandidate.id
        ).exists()
        statement = (
            select(JobBoardCandidate)
            .where(
                JobBoardCandidate.activity_code
                == "browser_agent_listing_confirmation",
                JobBoardCandidate.status == "needs_review",
                ~has_assessment,
                ~has_application,
            )
            .order_by(
                JobBoardCandidate.last_seen_at.asc(),
                JobBoardCandidate.id,
            )
            .limit(bounded_limit)
        )
        if apply:
            statement = statement.with_for_update()
        candidates = list(session.scalars(statement))
        mismatched = [
            candidate
            for candidate in candidates
            if (roles := _candidate_search_roles(candidate))
            and not any(
                browser_role_matches(role, candidate.title) for role in roles
            )
        ]

        if apply:
            now = datetime.now(timezone.utc)
            for candidate in mismatched:
                _quarantine_browser_candidate(
                    candidate,
                    now=now,
                    reason="role_mismatch_unassessed",
                )
            session.commit()

    return BrowserStaleCleanupResponse(
        matched_count=len(mismatched),
        quarantined_count=(len(mismatched) if apply else 0),
        applied=apply,
    )


def _job_application_item(
    application: JobApplication,
    candidate: JobBoardCandidate,
    profile_label: str,
    company_name: str | None,
) -> JobApplicationItem:
    listing_url = _validated_listing_url(
        provider=candidate.provider,
        title=candidate.title,
        listing_url=candidate.listing_url,
    )
    return JobApplicationItem(
        application_id=application.id,
        candidate_id=candidate.id,
        profile=profile_label,
        status=application.status,
        notes=(safe_text(application.notes, 2000) if application.notes else None),
        applied_at=application.applied_at,
        created_at=application.created_at,
        updated_at=application.updated_at,
        provider=candidate.provider,
        title=safe_text(candidate.title, 500),
        company_name=safe_text(company_name, 500) or UNKNOWN_EMPLOYER,
        listing_url=listing_url,
        location=(safe_text(candidate.location, 500) if candidate.location else None),
        work_mode=candidate.work_mode,
        activity_state=candidate.activity_state,
    )


def load_job_applications(
    database: Engine,
    *,
    profile_label: str,
    application_status: str | None,
    limit: int,
    offset: int,
) -> JobApplicationPage:
    with Session(database) as session:
        profile = session.scalar(
            select(CandidateProfile).where(CandidateProfile.label == profile_label)
        )
        if profile is None:
            raise ValueError("profile_not_found")

        filters = [JobApplication.profile_id == profile.id]
        if application_status is not None:
            filters.append(JobApplication.status == application_status)
        total = int(
            session.scalar(
                select(func.count()).select_from(JobApplication).where(*filters)
            )
            or 0
        )
        rows = session.execute(
            select(
                JobApplication,
                JobBoardCandidate,
                func.coalesce(Company.name, JobBoardCandidate.company_name_raw),
            )
            .join(
                JobBoardCandidate,
                JobBoardCandidate.id == JobApplication.candidate_id,
            )
            .outerjoin(Company, Company.id == JobBoardCandidate.company_id)
            .where(*filters)
            .order_by(JobApplication.updated_at.desc(), JobApplication.id)
            .limit(limit)
            .offset(offset)
        ).all()

        items = [
            _job_application_item(application, candidate, profile.label, company_name)
            for application, candidate, company_name in rows
        ]
    return JobApplicationPage(
        profile=profile_label,
        total=total,
        limit=limit,
        offset=offset,
        items=items,
    )


def update_job_application(
    database: Engine,
    *,
    candidate_id: UUID,
    profile_label: str,
    new_status: str,
    notes: str | None,
    notes_supplied: bool,
) -> JobApplicationItem:
    now = datetime.now(timezone.utc)
    with Session(database) as session:
        profile = session.scalar(
            select(CandidateProfile)
            .where(CandidateProfile.label == profile_label)
            .with_for_update()
        )
        if profile is None:
            raise ValueError("profile_not_found")
        row = session.execute(
            select(
                JobBoardCandidate,
                func.coalesce(Company.name, JobBoardCandidate.company_name_raw),
            )
            .outerjoin(Company, Company.id == JobBoardCandidate.company_id)
            .where(JobBoardCandidate.id == candidate_id)
            .with_for_update(of=JobBoardCandidate)
        ).one_or_none()
        if row is None:
            raise ValueError("candidate_not_found")
        candidate, company_name = row
        _validated_listing_url(
            provider=candidate.provider,
            title=candidate.title,
            listing_url=candidate.listing_url,
        )

        application = session.scalar(
            select(JobApplication)
            .where(
                JobApplication.candidate_id == candidate_id,
                JobApplication.profile_id == profile.id,
            )
            .with_for_update()
        )
        previous_status: str | None = None
        if application is None:
            if new_status not in {"to_apply", "applied"}:
                raise ValueError("application_initial_status_invalid")
            application = JobApplication(
                candidate_id=candidate_id,
                profile_id=profile.id,
                status=new_status,
                notes=notes if notes_supplied else None,
                applied_at=(now if new_status == "applied" else None),
                updated_at=now,
            )
            session.add(application)
            session.flush()
        else:
            previous_status = application.status
            if new_status != previous_status and new_status not in APPLICATION_TRANSITIONS[
                previous_status
            ]:
                raise ValueError("application_transition_invalid")
            if notes_supplied:
                application.notes = notes
            if new_status != previous_status:
                application.status = new_status
                application.updated_at = now
                if application.applied_at is None and new_status in {
                    "applied",
                    "interview",
                    "rejected",
                    "offer",
                }:
                    application.applied_at = now

        if previous_status != new_status:
            session.add(
                JobApplicationEvent(
                    application_id=application.id,
                    previous_status=previous_status,
                    new_status=new_status,
                )
            )
        session.commit()
        session.refresh(application)
        return _job_application_item(
            application,
            candidate,
            profile.label,
            company_name,
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


def _application_error(error: ValueError) -> HTTPException:
    error_code = str(error)
    if error_code in {"profile_not_found", "candidate_not_found"}:
        status_code = status.HTTP_404_NOT_FOUND
    elif error_code in {
        "application_initial_status_invalid",
        "application_transition_invalid",
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


def _company_discovery_error(
    error: CompanyProfileSearchError,
) -> HTTPException:
    error_code = error.code
    if error_code == "company_not_found":
        status_code = status.HTTP_404_NOT_FOUND
    elif error_code in {
        "company_search_in_progress",
        "verified_profile_protected",
    }:
        status_code = status.HTTP_409_CONFLICT
    elif error_code == "company_search_cooldown":
        status_code = status.HTTP_429_TOO_MANY_REQUESTS
    elif error_code in {
        "serper_not_configured",
        "evaluator_not_configured",
        "database_unavailable",
    }:
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        status_code = status.HTTP_502_BAD_GATEWAY
    return HTTPException(
        status_code=status_code,
        detail={"error_code": error_code},
    )


def _company_discovery_run_error(
    error: CompanyDiscoveryRunError,
) -> HTTPException:
    error_code = error.code
    if error_code == "company_discovery_run_not_found":
        status_code = status.HTTP_404_NOT_FOUND
    elif error_code in {
        "company_discovery_run_active",
        "company_discovery_run_not_active",
        "company_discovery_run_not_paused",
    }:
        status_code = status.HTTP_409_CONFLICT
    else:
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
    return HTTPException(
        status_code=status_code,
        detail={"error_code": error_code},
    )


def _company_run_response(
    snapshot: CompanyDiscoveryRunSnapshot,
) -> CompanyDiscoveryRunResponse:
    return CompanyDiscoveryRunResponse.model_validate(snapshot)


def _search_run_response(
    run: ProfileJobSearchRun,
    *,
    profile: str,
    database: Engine | None = None,
) -> SearchRunResponse:
    raw = run.result if isinstance(run.result, dict) else {}
    allowed_result_keys = {
        "query_count",
        "raw_result_count",
        "inspected_listing_count",
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
        "requested_roles",
        "search_mode",
        "requested_locations",
        "requested_work_modes",
        "requested_sources",
        "source_diagnostics",
        "max_listing_age_days",
        "query_limit",
        "result_limit_per_query",
    }
    result = {key: raw[key] for key in allowed_result_keys if key in raw}
    raw_roles = raw.get("requested_roles")
    if isinstance(raw_roles, list):
        result["requested_roles"] = [
            role
            for value in raw_roles[:10]
            if (role := safe_text(value, 100))
        ]
    if result.get("search_mode") not in {"quick", "deep"}:
        result.pop("search_mode", None)
    for key, allowed, maximum in (
        ("requested_work_modes", {"remote", "hybrid", "onsite"}, 3),
        (
            "requested_sources",
            {
                "linkedin",
                "kariyer",
                "indeed",
                "glassdoor",
                "ats",
                "turkey_tech",
                "remote_feeds",
            },
            7,
        ),
    ):
        values = result.get(key)
        if isinstance(values, list):
            result[key] = [
                value for value in values[:maximum]
                if isinstance(value, str) and value in allowed
            ]
        else:
            result.pop(key, None)
    raw_locations = result.get("requested_locations")
    if isinstance(raw_locations, list):
        result["requested_locations"] = [
            location for value in raw_locations[:3]
            if (location := safe_text(value, 100))
        ]
    else:
        result.pop("requested_locations", None)
    source_diagnostics: list[dict[str, object]] = []
    raw_diagnostics = raw.get("source_diagnostics")
    if isinstance(raw_diagnostics, list):
        allowed_sources = {
            "linkedin",
            "kariyer",
            "indeed",
            "glassdoor",
            "ats",
            "turkey_tech",
            "remote_feeds",
        }
        allowed_outcomes = {"matched", "filtered", "no_results"}
        for item in raw_diagnostics[:7]:
            if not isinstance(item, dict):
                continue
            source = safe_text(item.get("source"), 20)
            outcome = safe_text(item.get("outcome"), 20)
            if source not in allowed_sources or outcome not in allowed_outcomes:
                continue
            cleaned: dict[str, object] = {
                "source": source,
                "outcome": outcome,
            }
            for key in (
                "query_count",
                "fallback_query_count",
                "raw_result_count",
                "inspected_listing_count",
                "normalized_result_count",
                "accepted_count",
            ):
                try:
                    cleaned[key] = max(0, min(int(item.get(key, 0)), 1000))
                except (TypeError, ValueError):
                    cleaned[key] = 0
            exclusions: dict[str, int] = {}
            raw_exclusions = item.get("exclusion_counts")
            if isinstance(raw_exclusions, dict):
                for reason, count in list(raw_exclusions.items())[:10]:
                    cleaned_reason = safe_text(reason, 80)
                    if not cleaned_reason:
                        continue
                    try:
                        exclusions[cleaned_reason] = max(
                            0, min(int(count), 1000)
                        )
                    except (TypeError, ValueError):
                        continue
            cleaned["exclusion_counts"] = exclusions
            source_diagnostics.append(cleaned)
    if isinstance(raw_diagnostics, list):
        result["source_diagnostics"] = source_diagnostics
    else:
        result.pop("source_diagnostics", None)
    if "max_listing_age_days" in result:
        try:
            result["max_listing_age_days"] = max(
                1, min(int(result["max_listing_age_days"]), 90)
            )
        except (TypeError, ValueError):
            result.pop("max_listing_age_days", None)
    for key, maximum in (
        ("inspected_listing_count", 10_000),
        ("query_limit", 20),
        ("result_limit_per_query", 10),
    ):
        if key in result:
            try:
                result[key] = max(0, min(int(result[key]), maximum))
            except (TypeError, ValueError):
                result.pop(key, None)
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
                "operator_viewed_at": (
                    safe_text(item.get("operator_viewed_at"), 50)
                    if item.get("operator_viewed_at")
                    else None
                ),
            })
    if database is not None and matched_candidates:
        candidate_ids = [UUID(item["candidate_id"]) for item in matched_candidates]
        with Session(database) as session:
            viewed_rows = session.execute(
                select(
                    JobBoardCandidate.id,
                    JobBoardCandidate.operator_viewed_at,
                ).where(JobBoardCandidate.id.in_(candidate_ids))
            ).all()
        viewed_by_id = {
            str(candidate_id): viewed_at.isoformat() if viewed_at else None
            for candidate_id, viewed_at in viewed_rows
        }
        for item in matched_candidates:
            item["operator_viewed_at"] = viewed_by_id.get(
                item["candidate_id"],
                item["operator_viewed_at"],
            )
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


@router.get("/job-roles", response_model=list[JobRoleCatalogItem])
def operator_job_roles() -> list[JobRoleCatalogItem]:
    return [
        JobRoleCatalogItem(
            name=definition.name,
            aliases=list(definition.aliases),
        )
        for definition in ROLE_CATALOG
    ]


@router.post(
    "/job-assessments/backfill",
    response_model=JobAssessmentBackfillResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def operator_backfill_job_assessments(
    request: JobAssessmentBackfillRequest,
    background_tasks: BackgroundTasks,
) -> JobAssessmentBackfillResponse:
    try:
        candidate_ids = load_assessable_candidate_ids(
            engine,
            profile_label=request.profile,
            limit=request.limit,
        )
    except ValueError as error:
        if str(error) == "profile_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error_code": "profile_not_found"},
            ) from None
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "assessment_backfill_invalid"},
        ) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "database_unavailable"},
        ) from None
    if candidate_ids:
        background_tasks.add_task(
            assess_job_candidates,
            engine,
            profile_label=request.profile,
            candidate_ids=candidate_ids,
        )
    return JobAssessmentBackfillResponse(queued_count=len(candidate_ids))


@router.patch(
    "/profiles/{profile_label}/experience",
    response_model=OperatorProfileItem,
)
def operator_update_profile_experience(
    profile_label: Annotated[str, Path(min_length=1, max_length=100)],
    request: UpdateProfileExperienceRequest,
    background_tasks: BackgroundTasks,
) -> OperatorProfileItem:
    try:
        profile = update_profile_experience(
            engine,
            profile_label=profile_label,
            professional_experience_years=(
                request.professional_experience_years
            ),
            internship_months=request.internship_months,
        )
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "database_unavailable"},
        ) from None
    if profile is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "profile_not_found"},
        )
    try:
        candidate_ids = load_assessable_candidate_ids(
            engine,
            profile_label=profile_label,
        )
    except (SQLAlchemyError, ValueError):
        candidate_ids = []
    if candidate_ids:
        background_tasks.add_task(
            assess_job_candidates,
            engine,
            profile_label=profile_label,
            candidate_ids=candidate_ids,
        )
    return profile


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
    "/companies/{company_id}/discover",
    response_model=CompanyDiscoveryResponse,
)
def operator_discover_company(
    company_id: UUID,
    request: DiscoverCompanyRequest,
) -> CompanyDiscoveryResponse:
    # ``confirmed_external_search`` is intentionally required by the request
    # schema. The endpoint is synchronous and bounded to this one UUID.
    _ = request.confirmed_external_search
    try:
        result = discover_company_profile(
            engine,
            company_id=company_id,
        )
        return CompanyDiscoveryResponse.model_validate(result)
    except CompanyProfileSearchError as error:
        raise _company_discovery_error(error) from None


@router.post(
    "/company-discovery-runs",
    response_model=CompanyDiscoveryRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def operator_start_company_discovery_run(
    request: StartCompanyDiscoveryRunRequest,
    background_tasks: BackgroundTasks,
) -> CompanyDiscoveryRunResponse:
    _ = request.confirmed_external_search
    try:
        run_id = queue_company_discovery_run(
            engine,
            query_budget=request.query_budget,
        )
        snapshot = load_company_discovery_run(engine, run_id)
    except CompanyDiscoveryRunError as error:
        raise _company_discovery_run_error(error) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "database_unavailable"},
        ) from None
    if snapshot is None or snapshot.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "company_discovery_run_unavailable"},
        )
    if snapshot.total_count:
        background_tasks.add_task(
            execute_company_discovery_run,
            engine,
            run_id,
        )
    return _company_run_response(snapshot)


@router.get(
    "/company-discovery-runs/latest",
    response_model=CompanyDiscoveryRunResponse | None,
)
def operator_latest_company_discovery_run(
) -> CompanyDiscoveryRunResponse | None:
    try:
        snapshot = load_latest_company_discovery_run(engine)
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "database_unavailable"},
        ) from None
    return _company_run_response(snapshot) if snapshot is not None else None


@router.post(
    "/company-discovery-runs/{run_id}/pause",
    response_model=CompanyDiscoveryRunResponse,
)
def operator_pause_company_discovery_run(
    run_id: UUID,
    request: ConfirmCompanyDiscoveryRunRequest,
) -> CompanyDiscoveryRunResponse:
    _ = request.confirmed
    try:
        pause_company_discovery_run(engine, run_id)
        snapshot = load_company_discovery_run(engine, run_id)
    except CompanyDiscoveryRunError as error:
        raise _company_discovery_run_error(error) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "database_unavailable"},
        ) from None
    if snapshot is None or snapshot.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "company_discovery_run_not_found"},
        )
    return _company_run_response(snapshot)


@router.post(
    "/company-discovery-runs/{run_id}/resume",
    response_model=CompanyDiscoveryRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def operator_resume_company_discovery_run(
    run_id: UUID,
    request: ConfirmCompanyDiscoveryRunRequest,
    background_tasks: BackgroundTasks,
) -> CompanyDiscoveryRunResponse:
    _ = request.confirmed
    try:
        resume_company_discovery_run(engine, run_id)
        snapshot = load_company_discovery_run(engine, run_id)
    except CompanyDiscoveryRunError as error:
        raise _company_discovery_run_error(error) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "database_unavailable"},
        ) from None
    if snapshot is None or snapshot.run_id != run_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "company_discovery_run_not_found"},
        )
    background_tasks.add_task(
        execute_company_discovery_run,
        engine,
        run_id,
    )
    return _company_run_response(snapshot)


@router.post(
    "/native-search-links",
    response_model=NativeSearchLinksResponse,
)
def operator_native_search_links(
    request: NativeSearchLinksRequest,
) -> NativeSearchLinksResponse:
    try:
        links, unavailable_sources = build_native_search_links(
            role=request.role,
            location=request.location,
            sources=request.sources,
        )
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": str(error)},
        ) from None
    return NativeSearchLinksResponse(
        role=request.role,
        location=request.location,
        links=[NativeSearchLinkItem(**link.to_dict()) for link in links],
        unavailable_sources=unavailable_sources,
    )


@router.post(
    "/jobs/manual-import",
    response_model=ImportManualJobResponse,
    status_code=status.HTTP_201_CREATED,
)
def operator_import_manual_job(
    request: ImportManualJobRequest,
) -> ImportManualJobResponse:
    try:
        return import_manual_job_candidate(engine, request)
    except ValueError as error:
        error_code = str(error)
        if error_code not in {"provider_invalid", "manual_job_invalid"}:
            error_code = "manual_job_invalid"
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": error_code},
        ) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "manual_job_storage_unavailable"},
        ) from None


@router.post(
    "/browser-agent/collect",
    response_model=BrowserCollectResponse,
)
def operator_browser_collect(
    request: BrowserCollectRequest,
    background_tasks: BackgroundTasks,
) -> BrowserCollectResponse:
    try:
        with Session(engine) as session:
            profile_exists = session.scalar(
                select(func.count())
                .select_from(CandidateProfile)
                .where(CandidateProfile.label == request.profile)
            )
        if not profile_exists:
            raise ValueError("profile_not_found")
        providers = request.providers or (
            [request.provider] if request.provider is not None else []
        )
        providers = list(dict.fromkeys(providers))
        if not providers:
            raise ValueError("browser_provider_required")
        collection = collect_browser_jobs(
            role=request.role,
            location=request.location,
            providers=providers,
            work_modes=request.work_modes,
            max_results_per_provider=(
                request.max_results
                if request.max_results is not None and len(providers) == 1
                else request.max_results_per_provider
            ),
        )
        imported: list[ImportManualJobResponse] = []
        assessment_ids: list[UUID] = []
        skipped_count = 0
        for item in collection.jobs:
            try:
                imported_item = import_manual_job_candidate(
                    engine,
                    ImportManualJobRequest(
                        listing_url=item.listing_url,
                        title=item.title,
                        company_name=item.company_name,
                        location=item.location,
                        work_mode=item.work_mode,
                        employment_type="unknown",
                        description_text=item.description_text,
                        confirmed_visible=True,
                    ),
                    origin="browser_agent",
                    search_role=request.role,
                )
                imported.append(imported_item)
                if item.description_text:
                    assessment_ids.append(imported_item.candidate_id)
            except ValueError:
                skipped_count += 1
    except BrowserAgentError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": str(error)},
        ) from None
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "browser_request_invalid"},
        ) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "browser_import_storage_unavailable"},
        ) from None
    if assessment_ids:
        background_tasks.add_task(
            assess_job_candidates,
            engine,
            profile_label=request.profile,
            candidate_ids=assessment_ids,
        )
    return BrowserCollectResponse(
        provider=providers[0] if len(providers) == 1 else "multi",
        providers=providers,
        collected_count=len(collection.jobs),
        created_count=sum(1 for item in imported if item.created),
        updated_count=sum(1 for item in imported if item.changed and not item.created),
        skipped_count=skipped_count,
        assessment_queued_count=len(assessment_ids),
        candidate_ids=[item.candidate_id for item in imported],
        diagnostics=[
            BrowserSourceDiagnosticItem(
                provider=item.provider,
                label=item.label,
                outcome=item.outcome,
                collected_count=item.collected_count,
                error_code=item.error_code,
                agent_used=item.agent_used,
                agent_action_count=item.agent_action_count,
                access_reason=item.access_reason,
                access_trace=list(item.access_trace),
            )
            for item in collection.diagnostics
        ],
    )


@router.get(
    "/browser-agent/results",
    response_model=BrowserCollectedPage,
)
def operator_browser_results(
    profile: Annotated[str, Query(min_length=1, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> BrowserCollectedPage:
    try:
        return load_browser_collected_jobs(
            engine,
            profile_label=profile,
            limit=limit,
            offset=offset,
        )
    except ValueError as error:
        if str(error) == "profile_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error_code": "profile_not_found"},
            ) from None
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "browser_results_invalid"},
        ) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "browser_results_unavailable"},
        ) from None


@router.post(
    "/browser-agent/results/{candidate_id}/dismiss",
    response_model=BrowserResultDismissResponse,
)
def operator_dismiss_browser_result(
    candidate_id: UUID,
    request: BrowserResultProfileRequest,
) -> BrowserResultDismissResponse:
    try:
        return set_browser_candidate_dismissal(
            engine,
            candidate_id=candidate_id,
            profile_label=request.profile,
            dismissed=True,
        )
    except ValueError as error:
        code = str(error)
        if code in {"profile_not_found", "candidate_not_found"}:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error_code": code},
            ) from None
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "browser_result_dismiss_invalid"},
        ) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "browser_results_unavailable"},
        ) from None


@router.post(
    "/browser-agent/results/{candidate_id}/restore",
    response_model=BrowserResultDismissResponse,
)
def operator_restore_browser_result(
    candidate_id: UUID,
    request: BrowserResultProfileRequest,
) -> BrowserResultDismissResponse:
    try:
        return set_browser_candidate_dismissal(
            engine,
            candidate_id=candidate_id,
            profile_label=request.profile,
            dismissed=False,
        )
    except ValueError as error:
        code = str(error)
        if code in {"profile_not_found", "candidate_not_found"}:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error_code": code},
            ) from None
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "browser_result_restore_invalid"},
        ) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "browser_results_unavailable"},
        ) from None


@router.post(
    "/browser-agent/results/cleanup",
    response_model=BrowserCleanupResponse,
)
def operator_browser_results_cleanup() -> BrowserCleanupResponse:
    try:
        return cleanup_browser_collected_jobs(engine)
    except (SQLAlchemyError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "browser_cleanup_unavailable"},
        ) from None


@router.post(
    "/browser-agent/results/stale-cleanup",
    response_model=BrowserStaleCleanupResponse,
)
def operator_browser_stale_results_cleanup(
    request: BrowserStaleCleanupRequest,
) -> BrowserStaleCleanupResponse:
    try:
        return cleanup_stale_unassessed_browser_jobs(
            engine,
            profile_label=request.profile,
            older_than_days=request.older_than_days,
            limit=request.limit,
            apply=request.apply,
            confirmed_cleanup=request.confirmed_cleanup,
        )
    except ValueError as error:
        code = str(error)
        if code == "profile_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error_code": code},
            ) from None
        if code == "cleanup_confirmation_required":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error_code": code},
            ) from None
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "browser_stale_cleanup_invalid"},
        ) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "browser_cleanup_unavailable"},
        ) from None


@router.post(
    "/browser-agent/results/role-cleanup",
    response_model=BrowserStaleCleanupResponse,
)
def operator_browser_role_results_cleanup(
    request: BrowserRoleCleanupRequest,
) -> BrowserStaleCleanupResponse:
    try:
        return cleanup_role_mismatched_browser_jobs(
            engine,
            profile_label=request.profile,
            limit=request.limit,
            apply=request.apply,
            confirmed_cleanup=request.confirmed_cleanup,
        )
    except ValueError as error:
        code = str(error)
        if code == "profile_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error_code": code},
            ) from None
        if code == "cleanup_confirmation_required":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error_code": code},
            ) from None
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error_code": "browser_role_cleanup_invalid"},
        ) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "browser_cleanup_unavailable"},
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
        run_id = queue_profile_search(
            engine,
            profile_label=request.profile,
            requested_roles=request.roles,
            search_mode=request.search_mode,
            requested_locations=request.locations,
            requested_work_modes=request.work_modes,
            requested_sources=request.sources,
            max_listing_age_days=request.max_age_days,
            force=request.force,
        )
        run = load_latest_profile_search(engine, profile_label=request.profile)
    except SearchAlreadyRunning:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "search_already_running"},
        ) from None
    except SearchCooldownActive as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"error_code": "search_cooldown"},
            headers={"Retry-After": str(error.retry_after_seconds)},
        ) from None
    except ValueError as error:
        error_code = str(error)
        raise HTTPException(
            status_code=(
                status.HTTP_404_NOT_FOUND
                if error_code == "profile_not_found"
                else status.HTTP_422_UNPROCESSABLE_ENTITY
            ),
            detail={"error_code": error_code},
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
    background_tasks.add_task(
        assess_profile_search_run_candidates,
        engine,
        run_id,
    )
    return _search_run_response(run, profile=request.profile, database=engine)


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
    return _search_run_response(run, profile=profile, database=engine)


@router.get("/jobs", response_model=OperatorJobPage)
def operator_jobs(
    profile: Annotated[str, Query(min_length=1, max_length=100)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    minimum_score: Annotated[int, Query(ge=0, le=100)] = 0,
    provider: Annotated[str | None, Query()] = None,
    include_unverified: bool = False,
) -> OperatorJobPage:
    if provider is not None and provider not in MANUAL_JOB_PROVIDERS:
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
    "/jobs/{candidate_id}/viewed",
    response_model=JobViewedResponse,
)
def operator_mark_job_viewed(candidate_id: UUID) -> JobViewedResponse:
    try:
        viewed = mark_operator_job_viewed(engine, candidate_id)
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "database_unavailable"},
        ) from None
    if viewed is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error_code": "candidate_not_found"},
        )
    return viewed


@router.get("/applications", response_model=JobApplicationPage)
def operator_applications(
    profile: Annotated[str, Query(min_length=1, max_length=100)],
    application_status: Annotated[ApplicationStatus | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
) -> JobApplicationPage:
    try:
        return load_job_applications(
            engine,
            profile_label=profile,
            application_status=application_status,
            limit=limit,
            offset=offset,
        )
    except ValueError as error:
        raise _application_error(error) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "database_unavailable"},
        ) from None


@router.post(
    "/jobs/{candidate_id}/application",
    response_model=JobApplicationItem,
)
def operator_update_job_application(
    candidate_id: UUID,
    request: UpdateJobApplicationRequest,
) -> JobApplicationItem:
    try:
        return update_job_application(
            engine,
            candidate_id=candidate_id,
            profile_label=request.profile,
            new_status=request.status,
            notes=request.notes,
            notes_supplied="notes" in request.model_fields_set,
        )
    except ValueError as error:
        raise _application_error(error) from None
    except SQLAlchemyError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error_code": "database_unavailable"},
        ) from None


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
