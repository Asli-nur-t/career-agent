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
        Index(
            "ix_company_web_profiles_career_scan_due",
            "status",
            "career_sources_next_check_at",
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
            "(status = 'active' AND closed_at IS NULL) OR "
            "(status = 'closed' AND closed_at IS NOT NULL)",
            name="ck_job_postings_closed_state",
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
    career_source_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("career_sources.id", ondelete="CASCADE"),
        nullable=False,
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
            "('linkedin', 'kariyer', 'indeed', 'glassdoor')",
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
            "status IN ('needs_review', 'approved', 'rejected')",
            name="ck_job_board_candidates_status",
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
