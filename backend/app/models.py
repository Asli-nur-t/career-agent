from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Uuid,
    false,
    func,
)
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
