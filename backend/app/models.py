from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(name)) > 0",
            name="ck_companies_name_not_blank",
        ),
        CheckConstraint(
            "char_length(name_key) = 64",
            name="ck_companies_name_key_length",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid4
    )
    name: Mapped[str] = mapped_column(String(500))
    name_key: Mapped[str] = mapped_column(String(64), unique=True)
    sector: Mapped[str | None] = mapped_column(String(300))
    needs_review: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CompanyAffiliation(Base):
    __tablename__ = "company_affiliations"
    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(teknopark)) > 0",
            name="ck_affiliations_teknopark_not_blank",
        ),
    )

    company_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    teknopark: Mapped[str] = mapped_column(
        String(200), primary_key=True
    )
    source_target_sector: Mapped[bool | None] = mapped_column(Boolean)


class CompanyWebProfile(Base):
    __tablename__ = "company_web_profiles"
    __table_args__ = (
        CheckConstraint(
            "confidence IN ('high', 'medium', 'low')",
            name="ck_company_web_profiles_confidence",
        ),
        CheckConstraint(
            "status IN "
            "('candidate_found', 'verified', 'needs_review', 'not_found')",
            name="ck_company_web_profiles_status",
        ),
        CheckConstraint(
            "status <> 'not_found' OR confidence = 'low'",
            name=(
                "ck_company_web_profiles_"
                "not_found_confidence"
            ),
        ),
        CheckConstraint(
            "official_website_url IS NULL OR "
            "char_length(btrim(official_website_url)) > 0",
            name="ck_company_web_profiles_website_not_blank",
        ),
        CheckConstraint(
            "careers_url IS NULL OR char_length(btrim(careers_url)) > 0",
            name="ck_company_web_profiles_careers_not_blank",
        ),
        CheckConstraint(
            "official_linkedin_url IS NULL OR "
            "char_length(btrim(official_linkedin_url)) > 0",
            name="ck_company_web_profiles_linkedin_not_blank",
        ),
        CheckConstraint(
            "status NOT IN ('candidate_found', 'verified') OR "
            "official_website_url IS NOT NULL",
            name="ck_company_web_profiles_candidate_has_website",
        ),
        CheckConstraint(
            "status <> 'verified' OR "
            "(last_verified_at IS NOT NULL "
            "AND official_website_url IS NOT NULL)",
            name="ck_company_web_profiles_verified_fields",
        ),
        CheckConstraint(
            "status <> 'not_found' OR "
            "(official_website_url IS NULL "
            "AND careers_url IS NULL "
            "AND official_linkedin_url IS NULL)",
            name="ck_company_web_profiles_not_found_has_no_urls",
        ),
        CheckConstraint(
            "career_sources_last_outcome IS NULL OR "
            "career_sources_last_outcome IN "
            "('candidates_found', 'no_results', 'error')",
            name="ck_company_web_profiles_career_scan_outcome",
        ),
        CheckConstraint(
            "career_sources_consecutive_failures BETWEEN 0 AND 1000",
            name="ck_company_web_profiles_career_failure_count",
        ),
        CheckConstraint(
            "career_sources_candidate_count BETWEEN 0 AND 100",
            name="ck_company_web_profiles_career_candidate_count",
        ),
        CheckConstraint(
            "job_boards_last_outcome IS NULL OR "
            "job_boards_last_outcome IN "
            "('candidates_found', 'no_results', 'error')",
            name="ck_company_web_profiles_job_board_scan_outcome",
        ),
        CheckConstraint(
            "job_boards_consecutive_failures BETWEEN 0 AND 1000",
            name="ck_company_web_profiles_job_board_failure_count",
        ),
        CheckConstraint(
            "job_boards_candidate_count BETWEEN 0 AND 100",
            name="ck_company_web_profiles_job_board_candidate_count",
        ),
        Index(
            "ix_company_web_profiles_career_scan_due",
            "status",
            "career_sources_next_check_at",
        ),
        Index(
            "ix_company_web_profiles_job_board_scan_due",
            "status",
            "job_boards_next_check_at",
        ),
    )

    company_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    brand_name: Mapped[str | None] = mapped_column(String(300))
    official_website_url: Mapped[str | None] = mapped_column(String(2048))
    careers_url: Mapped[str | None] = mapped_column(String(2048))
    official_linkedin_url: Mapped[str | None] = mapped_column(String(2048))
    confidence: Mapped[str] = mapped_column(
        String(20),
        default="low",
        server_default="low",
    )
    status: Mapped[str] = mapped_column(
        String(30),
        default="needs_review",
        server_default="needs_review",
    )
    evidence: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
    )
    search_provider: Mapped[str] = mapped_column(
        String(50),
        default="serper",
        server_default="serper",
    )
    evaluator_model: Mapped[str] = mapped_column(
        String(100),
        default="gemini-3.6-flash",
        server_default="gemini-3.6-flash",
    )
    last_searched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    career_sources_last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    career_sources_next_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    career_sources_last_outcome: Mapped[str | None] = mapped_column(
        String(30)
    )
    career_sources_last_error_code: Mapped[str | None] = mapped_column(
        String(80)
    )
    career_sources_consecutive_failures: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    career_sources_candidate_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    job_boards_last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    job_boards_next_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    job_boards_last_outcome: Mapped[str | None] = mapped_column(String(30))
    job_boards_last_error_code: Mapped[str | None] = mapped_column(
        String(80)
    )
    job_boards_consecutive_failures: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    job_boards_candidate_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class DiscoveryAttempt(Base):
    __tablename__ = "discovery_attempts"
    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(query)) > 0",
            name="ck_discovery_attempts_query_not_blank",
        ),
        CheckConstraint(
            "result_count BETWEEN 0 AND 100",
            name="ck_discovery_attempts_result_count",
        ),
        CheckConstraint(
            "outcome IN "
            "('success', 'no_results', 'search_error', "
            "'evaluation_error', 'rejected')",
            name="ck_discovery_attempts_outcome",
        ),
        Index(
            "ix_discovery_attempts_company_created",
            "company_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    company_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("companies.id", ondelete="CASCADE"),
    )
    query: Mapped[str] = mapped_column(String(500))
    search_provider: Mapped[str] = mapped_column(
        String(50),
        default="serper",
        server_default="serper",
    )
    evaluator_model: Mapped[str | None] = mapped_column(String(100))
    result_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
    )
    outcome: Mapped[str] = mapped_column(String(30))
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class CompanyDiscoveryRun(Base):
    __tablename__ = "company_discovery_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'pause_requested', "
            "'paused', 'succeeded', 'failed')",
            name="ck_company_discovery_runs_status",
        ),
        CheckConstraint(
            "scope = 'unprofiled'",
            name="ck_company_discovery_runs_scope",
        ),
        CheckConstraint(
            "query_budget BETWEEN 3 AND 2200",
            name="ck_company_discovery_runs_query_budget",
        ),
        CheckConstraint(
            "query_count BETWEEN 0 AND query_budget",
            name="ck_company_discovery_runs_query_count",
        ),
        CheckConstraint(
            "total_count BETWEEN 0 AND 1000",
            name="ck_company_discovery_runs_total_count",
        ),
        CheckConstraint(
            "error_code IS NULL OR char_length(btrim(error_code)) > 0",
            name="ck_company_discovery_runs_error_not_blank",
        ),
        Index(
            "uq_company_discovery_runs_active",
            "scope",
            unique=True,
            postgresql_where=text(
                "status IN ('queued', 'running', 'pause_requested', 'paused')"
            ),
        ),
        Index(
            "ix_company_discovery_runs_created",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    status: Mapped[str] = mapped_column(
        String(30), default="queued", server_default="queued", nullable=False
    )
    scope: Mapped[str] = mapped_column(
        String(30), default="unprofiled", server_default="unprofiled", nullable=False
    )
    query_budget: Mapped[int] = mapped_column(Integer, nullable=False)
    query_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    total_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class CompanyDiscoveryRunItem(Base):
    __tablename__ = "company_discovery_run_items"
    __table_args__ = (
        CheckConstraint(
            "position BETWEEN 1 AND 1000",
            name="ck_company_discovery_run_items_position",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_company_discovery_run_items_status",
        ),
        CheckConstraint(
            "profile_status IS NULL OR profile_status IN "
            "('candidate_found', 'verified', 'needs_review', 'not_found')",
            name="ck_company_discovery_run_items_profile_status",
        ),
        CheckConstraint(
            "attempt_count BETWEEN 0 AND 3",
            name="ck_company_discovery_run_items_attempt_count",
        ),
        CheckConstraint(
            "error_code IS NULL OR char_length(btrim(error_code)) > 0",
            name="ck_company_discovery_run_items_error_not_blank",
        ),
        UniqueConstraint(
            "run_id",
            "position",
            name="uq_company_discovery_run_items_position",
        ),
        Index(
            "ix_company_discovery_run_items_queue",
            "run_id",
            "status",
            "position",
        ),
    )

    run_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("company_discovery_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    company_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), default="queued", server_default="queued", nullable=False
    )
    profile_status: Mapped[str | None] = mapped_column(String(30))
    attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(80))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CareerSource(Base):
    __tablename__ = "career_sources"
    __table_args__ = (
        UniqueConstraint(
            "company_id",
            "source_url",
            name="uq_career_sources_company_url",
        ),
        CheckConstraint(
            "char_length(btrim(source_url)) > 0",
            name="ck_career_sources_url_not_blank",
        ),
        CheckConstraint(
            "source_type IN "
            "('ats', 'career_page', 'jobs_page', 'manual')",
            name="ck_career_sources_type",
        ),
        CheckConstraint(
            "ats_type IS NULL OR ats_type IN "
            "('lever', 'greenhouse', 'smartrecruiters', "
            "'recruitee', 'workable', 'ashby', "
            "'teamtailor', 'custom')",
            name="ck_career_sources_ats_type",
        ),
        CheckConstraint(
            "source_type <> 'ats' OR ats_type IS NOT NULL",
            name="ck_career_sources_ats_requires_type",
        ),
        CheckConstraint(
            "access_strategy IN "
            "('public_api', 'allowed_crawl', "
            "'browser_assisted', 'manual_import', 'blocked')",
            name="ck_career_sources_access_strategy",
        ),
        CheckConstraint(
            "status IN "
            "('candidate', 'active', 'inactive', "
            "'blocked', 'needs_review')",
            name="ck_career_sources_status",
        ),
        CheckConstraint(
            "consecutive_failures BETWEEN 0 AND 1000",
            name="ck_career_sources_failure_count",
        ),
        CheckConstraint(
            "last_http_status IS NULL OR "
            "last_http_status BETWEEN 100 AND 599",
            name="ck_career_sources_http_status",
        ),
        Index(
            "ix_career_sources_status_next_check",
            "status",
            "next_check_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    company_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_url: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    ats_type: Mapped[str | None] = mapped_column(
        String(30)
    )
    access_strategy: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(30),
        default="candidate",
        server_default="candidate",
        nullable=False,
    )
    discovered_from_url: Mapped[str | None] = mapped_column(
        String(2048)
    )
    evidence: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    last_http_status: Mapped[int | None] = mapped_column(
        Integer
    )
    consecutive_failures: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    last_error_code: Mapped[str | None] = mapped_column(
        String(80)
    )
    last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    next_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class JobPosting(Base):
    __tablename__ = "job_postings"
    __table_args__ = (
        UniqueConstraint(
            "career_source_id",
            "external_id",
            name="uq_job_postings_source_external_id",
        ),
        UniqueConstraint(
            "job_board_candidate_id",
            name="uq_job_postings_job_board_candidate",
        ),
        CheckConstraint(
            "char_length(btrim(external_id)) > 0",
            name="ck_job_postings_external_id_not_blank",
        ),
        CheckConstraint(
            "char_length(btrim(title)) > 0",
            name="ck_job_postings_title_not_blank",
        ),
        CheckConstraint(
            "char_length(btrim(job_url)) > 0",
            name="ck_job_postings_url_not_blank",
        ),
        CheckConstraint(
            "char_length(content_hash) = 64",
            name="ck_job_postings_content_hash_length",
        ),
        CheckConstraint(
            "status IN ('active', 'closed')",
            name="ck_job_postings_status",
        ),
        CheckConstraint(
            "work_mode IN ('remote', 'hybrid', 'onsite', 'unknown')",
            name="ck_job_postings_work_mode",
        ),
        CheckConstraint(
            "(status = 'active' AND closed_at IS NULL) OR "
            "(status = 'closed' AND closed_at IS NOT NULL)",
            name="ck_job_postings_closed_state",
        ),
        CheckConstraint(
            "(career_source_id IS NOT NULL AND "
            "job_board_candidate_id IS NULL) OR "
            "(career_source_id IS NULL AND "
            "job_board_candidate_id IS NOT NULL)",
            name="ck_job_postings_exactly_one_source",
        ),
        Index(
            "ix_job_postings_source_status",
            "career_source_id",
            "status",
        ),
        Index(
            "ix_job_postings_status_last_seen",
            "status",
            "last_seen_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    career_source_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("career_sources.id", ondelete="CASCADE"),
    )
    job_board_candidate_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("job_board_candidates.id", ondelete="RESTRICT"),
    )
    external_id: Mapped[str] = mapped_column(String(500), nullable=False)
    job_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    apply_url: Mapped[str | None] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    location: Mapped[str | None] = mapped_column(String(500))
    department: Mapped[str | None] = mapped_column(String(300))
    employment_type: Mapped[str | None] = mapped_column(String(100))
    description_text: Mapped[str | None] = mapped_column(Text)
    is_remote: Mapped[bool | None] = mapped_column(Boolean)
    work_mode: Mapped[str] = mapped_column(
        String(20),
        default="unknown",
        server_default="unknown",
        nullable=False,
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        default="active",
        server_default="active",
        nullable=False,
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class JobBoardCandidate(Base):
    __tablename__ = "job_board_candidates"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "external_id",
            name="uq_job_board_candidates_provider_external_id",
        ),
        CheckConstraint(
            "provider IN "
            "('linkedin', 'kariyer', 'indeed', 'glassdoor', "
            "'techcareer', 'yenibiris', 'secretcv', 'toptalent', "
            "'weworkremotely', 'remoteok', 'remotive', 'jobicy', "
            "'greenhouse', 'lever', 'ashby')",
            name="ck_job_board_candidates_provider",
        ),
        CheckConstraint(
            "char_length(btrim(external_id)) > 0",
            name="ck_job_board_candidates_external_id_not_blank",
        ),
        CheckConstraint(
            "char_length(btrim(listing_url)) > 0",
            name="ck_job_board_candidates_url_not_blank",
        ),
        CheckConstraint(
            "char_length(btrim(title)) > 0",
            name="ck_job_board_candidates_title_not_blank",
        ),
        CheckConstraint(
            "company_id IS NOT NULL OR (company_name_raw IS NOT NULL AND "
            "char_length(btrim(company_name_raw)) > 0)",
            name="ck_job_board_candidates_company_identity",
        ),
        CheckConstraint(
            "status IN "
            "('needs_review', 'approved', 'rejected', 'filtered_out')",
            name="ck_job_board_candidates_status",
        ),
        CheckConstraint(
            "work_mode IN ('remote', 'hybrid', 'onsite', 'unknown')",
            name="ck_job_board_candidates_work_mode",
        ),
        CheckConstraint(
            "employment_type IN "
            "('full_time', 'part_time', 'contract', 'internship', "
            "'temporary', 'unknown')",
            name="ck_job_board_candidates_employment_type",
        ),
        CheckConstraint(
            "activity_state IN ('active', 'closed', 'unknown')",
            name="ck_job_board_candidates_activity_state",
        ),
        CheckConstraint(
            "char_length(btrim(activity_code)) > 0",
            name="ck_job_board_candidates_activity_code_not_blank",
        ),
        CheckConstraint(
            "status <> 'approved' OR company_id IS NOT NULL",
            name="ck_job_board_candidates_approval_company",
        ),
        CheckConstraint(
            "(status = 'approved' AND approved_at IS NOT NULL) OR "
            "(status <> 'approved' AND approved_at IS NULL)",
            name="ck_job_board_candidates_approval_time",
        ),
        Index(
            "ix_job_board_candidates_status_last_seen",
            "status",
            "last_seen_at",
        ),
        Index(
            "ix_job_board_candidates_company_status",
            "company_id",
            "status",
        ),
        Index(
            "ix_job_board_candidates_status_published",
            "status",
            "published_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    company_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("companies.id", ondelete="RESTRICT"),
    )
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    external_id: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    listing_url: Mapped[str] = mapped_column(
        String(2048),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    company_name_raw: Mapped[str | None] = mapped_column(String(500))
    location: Mapped[str | None] = mapped_column(String(500))
    work_mode: Mapped[str] = mapped_column(
        String(20),
        default="unknown",
        server_default="unknown",
        nullable=False,
    )
    employment_type: Mapped[str] = mapped_column(
        String(30),
        default="unknown",
        server_default="unknown",
        nullable=False,
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    activity_state: Mapped[str] = mapped_column(
        String(20),
        default="unknown",
        server_default="unknown",
        nullable=False,
    )
    activity_code: Mapped[str] = mapped_column(
        String(80),
        default="not_checked",
        server_default="not_checked",
        nullable=False,
    )
    activity_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    snippet: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(30),
        default="needs_review",
        server_default="needs_review",
        nullable=False,
    )
    evidence: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    approved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    operator_viewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class JobCandidateAssessment(Base):
    __tablename__ = "job_candidate_assessments"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "candidate_id",
            name="uq_job_candidate_assessments_profile_candidate",
        ),
        CheckConstraint(
            "score BETWEEN 0 AND 100",
            name="ck_job_candidate_assessments_score",
        ),
        CheckConstraint(
            "stars BETWEEN 1 AND 5",
            name="ck_job_candidate_assessments_stars",
        ),
        CheckConstraint(
            "recommendation IN ('strong_apply', 'apply', 'review', 'skip')",
            name="ck_job_candidate_assessments_recommendation",
        ),
        CheckConstraint(
            "confidence IN ('low', 'medium', 'high')",
            name="ck_job_candidate_assessments_confidence",
        ),
        CheckConstraint(
            "required_experience_min IS NULL OR "
            "required_experience_min BETWEEN 0 AND 50",
            name="ck_job_candidate_assessments_required_experience",
        ),
        CheckConstraint(
            "experience_gap IS NULL OR experience_gap BETWEEN 0 AND 50",
            name="ck_job_candidate_assessments_experience_gap",
        ),
        CheckConstraint(
            "char_length(job_content_hash) = 64",
            name="ck_job_candidate_assessments_job_hash",
        ),
        CheckConstraint(
            "char_length(profile_hash) = 64",
            name="ck_job_candidate_assessments_profile_hash",
        ),
        CheckConstraint(
            "char_length(btrim(summary)) > 0",
            name="ck_job_candidate_assessments_summary",
        ),
        Index(
            "ix_job_candidate_assessments_profile_score",
            "profile_id",
            "score",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    profile_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("candidate_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("job_board_candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    stars: Mapped[int] = mapped_column(Integer, nullable=False)
    recommendation: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[str] = mapped_column(String(20), nullable=False)
    required_experience_min: Mapped[int | None] = mapped_column(Integer)
    experience_gap: Mapped[int | None] = mapped_column(Integer)
    matched_requirements: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    missing_requirements: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    preferred_requirements: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    hard_blockers: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    summary: Mapped[str] = mapped_column(String(1000), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(50), nullable=False)
    job_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    profile_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class JobCandidateDismissal(Base):
    __tablename__ = "job_candidate_dismissals"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "candidate_id",
            name="uq_job_candidate_dismissals_profile_candidate",
        ),
        Index(
            "ix_job_candidate_dismissals_profile_created",
            "profile_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    profile_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("candidate_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("job_board_candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class JobApplication(Base):
    __tablename__ = "job_applications"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id",
            "profile_id",
            name="uq_job_applications_candidate_profile",
        ),
        CheckConstraint(
            "status IN "
            "('to_apply', 'applied', 'interview', 'rejected', "
            "'offer', 'withdrawn')",
            name="ck_job_applications_status",
        ),
        CheckConstraint(
            "notes IS NULL OR char_length(notes) <= 2000",
            name="ck_job_applications_notes_length",
        ),
        Index(
            "ix_job_applications_profile_status_updated",
            "profile_id",
            "status",
            "updated_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    candidate_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("job_board_candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    profile_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("candidate_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(30),
        default="to_apply",
        server_default="to_apply",
        nullable=False,
    )
    notes: Mapped[str | None] = mapped_column(Text)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class JobApplicationEvent(Base):
    __tablename__ = "job_application_events"
    __table_args__ = (
        CheckConstraint(
            "previous_status IS NULL OR previous_status IN "
            "('to_apply', 'applied', 'interview', 'rejected', "
            "'offer', 'withdrawn')",
            name="ck_job_application_events_previous_status",
        ),
        CheckConstraint(
            "new_status IN "
            "('to_apply', 'applied', 'interview', 'rejected', "
            "'offer', 'withdrawn')",
            name="ck_job_application_events_new_status",
        ),
        Index(
            "ix_job_application_events_application_created",
            "application_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    application_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("job_applications.id", ondelete="CASCADE"),
        nullable=False,
    )
    previous_status: Mapped[str | None] = mapped_column(String(30))
    new_status: Mapped[str] = mapped_column(String(30), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CandidateProfile(Base):
    __tablename__ = "candidate_profiles"
    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(label)) > 0",
            name="ck_candidate_profiles_label_not_blank",
        ),
        CheckConstraint(
            "char_length(config_hash) = 64",
            name="ck_candidate_profiles_hash_length",
        ),
        CheckConstraint(
            "jsonb_typeof(target_roles) = 'array'",
            name="ck_candidate_profiles_target_roles_array",
        ),
        CheckConstraint(
            "jsonb_typeof(secondary_roles) = 'array'",
            name="ck_candidate_profiles_secondary_roles_array",
        ),
        CheckConstraint(
            "jsonb_typeof(tertiary_roles) = 'array'",
            name="ck_candidate_profiles_tertiary_roles_array",
        ),
        CheckConstraint(
            "jsonb_typeof(skills) = 'array'",
            name="ck_candidate_profiles_skills_array",
        ),
        CheckConstraint(
            "jsonb_typeof(preferred_locations) = 'array'",
            name="ck_candidate_profiles_locations_array",
        ),
        CheckConstraint(
            "jsonb_typeof(preferred_remote_locations) = 'array'",
            name="ck_candidate_profiles_remote_locations_array",
        ),
        CheckConstraint(
            "jsonb_typeof(excluded_locations) = 'array'",
            name="ck_candidate_profiles_excluded_locations_array",
        ),
        CheckConstraint(
            "jsonb_typeof(allowed_work_modes) = 'array'",
            name="ck_candidate_profiles_work_modes_array",
        ),
        CheckConstraint(
            "location_filter_mode IN ('prefer', 'require')",
            name="ck_candidate_profiles_location_filter_mode",
        ),
        CheckConstraint(
            "max_listing_age_days BETWEEN 1 AND 3650",
            name="ck_candidate_profiles_max_listing_age",
        ),
        CheckConstraint(
            "jsonb_typeof(excluded_keywords) = 'array'",
            name="ck_candidate_profiles_excluded_array",
        ),
        CheckConstraint(
            "max_years_experience BETWEEN 0 AND 50",
            name="ck_candidate_profiles_max_experience",
        ),
        CheckConstraint(
            "internship_months BETWEEN 0 AND 120",
            name="ck_candidate_profiles_internship_months",
        ),
        CheckConstraint(
            "job_search_last_outcome IS NULL OR "
            "job_search_last_outcome IN "
            "('candidates_found', 'no_results', 'error')",
            name="ck_candidate_profiles_job_search_outcome",
        ),
        CheckConstraint(
            "job_search_consecutive_failures BETWEEN 0 AND 1000",
            name="ck_candidate_profiles_job_search_failure_count",
        ),
        CheckConstraint(
            "job_search_candidate_count BETWEEN 0 AND 1000",
            name="ck_candidate_profiles_job_search_candidate_count",
        ),
        CheckConstraint(
            "job_search_profile_hash IS NULL OR "
            "char_length(job_search_profile_hash) = 64",
            name="ck_candidate_profiles_job_search_hash_length",
        ),
        Index(
            "ix_candidate_profiles_job_search_due",
            "job_search_next_check_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    label: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
    )
    target_roles: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
    )
    secondary_roles: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    tertiary_roles: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    skills: Mapped[list[str]] = mapped_column(
        JSONB,
        nullable=False,
    )
    preferred_locations: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    preferred_remote_locations: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    excluded_locations: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    allowed_work_modes: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    location_filter_mode: Mapped[str] = mapped_column(
        String(20),
        default="prefer",
        server_default="prefer",
        nullable=False,
    )
    max_listing_age_days: Mapped[int] = mapped_column(
        Integer,
        default=30,
        server_default="30",
        nullable=False,
    )
    excluded_keywords: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    max_years_experience: Mapped[int] = mapped_column(
        Integer,
        default=3,
        server_default="3",
        nullable=False,
    )
    internship_months: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    remote_allowed: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=text("true"),
        nullable=False,
    )
    job_search_last_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    job_search_next_check_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    job_search_last_outcome: Mapped[str | None] = mapped_column(String(30))
    job_search_last_error_code: Mapped[str | None] = mapped_column(
        String(80)
    )
    job_search_consecutive_failures: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    job_search_candidate_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )
    job_search_profile_hash: Mapped[str | None] = mapped_column(String(64))
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ProfileJobSearchRun(Base):
    __tablename__ = "profile_job_search_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed')",
            name="ck_profile_job_search_runs_status",
        ),
        CheckConstraint(
            "error_code IS NULL OR char_length(btrim(error_code)) > 0",
            name="ck_profile_job_search_runs_error_not_blank",
        ),
        Index(
            "uq_profile_job_search_runs_active_profile",
            "profile_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index(
            "ix_profile_job_search_runs_profile_created",
            "profile_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    profile_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("candidate_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20), default="queued", server_default="queued", nullable=False
    )
    result: Mapped[dict[str, object]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb"), nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JobMatch(Base):
    __tablename__ = "job_matches"
    __table_args__ = (
        UniqueConstraint(
            "profile_id",
            "job_posting_id",
            name="uq_job_matches_profile_posting",
        ),
        CheckConstraint(
            "score BETWEEN 0 AND 100",
            name="ck_job_matches_score",
        ),
        CheckConstraint(
            "recommendation IN "
            "('strong_apply', 'apply', 'review', 'skip')",
            name="ck_job_matches_recommendation",
        ),
        CheckConstraint(
            "review_status IN "
            "('new', 'shortlisted', 'dismissed', 'applied')",
            name="ck_job_matches_review_status",
        ),
        CheckConstraint(
            "char_length(job_content_hash) = 64",
            name="ck_job_matches_job_hash_length",
        ),
        CheckConstraint(
            "char_length(profile_hash) = 64",
            name="ck_job_matches_profile_hash_length",
        ),
        CheckConstraint(
            "char_length(btrim(reason)) > 0",
            name="ck_job_matches_reason_not_blank",
        ),
        CheckConstraint(
            "char_length(btrim(matcher_version)) > 0",
            name="ck_job_matches_version_not_blank",
        ),
        Index(
            "ix_job_matches_profile_recommendation_score",
            "profile_id",
            "recommendation",
            "score",
        ),
        Index(
            "ix_job_matches_review_status_score",
            "review_status",
            "score",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid,
        primary_key=True,
        default=uuid4,
    )
    profile_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("candidate_profiles.id", ondelete="CASCADE"),
        nullable=False,
    )
    job_posting_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("job_postings.id", ondelete="CASCADE"),
        nullable=False,
    )
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    recommendation: Mapped[str] = mapped_column(
        String(30), nullable=False
    )
    review_status: Mapped[str] = mapped_column(
        String(30),
        default="new",
        server_default="new",
        nullable=False,
    )
    matched_terms: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    risk_flags: Mapped[list[str]] = mapped_column(
        JSONB,
        default=list,
        server_default=text("'[]'::jsonb"),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    matcher_version: Mapped[str] = mapped_column(
        String(50), nullable=False
    )
    job_content_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    profile_hash: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
