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
