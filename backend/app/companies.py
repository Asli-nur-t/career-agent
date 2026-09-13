from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import engine
from app.models import Company, CompanyAffiliation


router = APIRouter(prefix="/companies", tags=["Companies"])


class CompanyItem(BaseModel):
    id: UUID
    name: str
    sector: str | None
    needs_review: bool
    teknoparks: list[str]


class CompanyPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CompanyItem]


@router.get("", response_model=CompanyPage)
def list_companies(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    q: Annotated[str | None, Query(min_length=1, max_length=200)] = None,
    teknopark: Annotated[
        str | None, Query(min_length=1, max_length=200)
    ] = None,
    needs_review: bool | None = None,
) -> CompanyPage:
    filters = []

    if q and q.strip():
        filters.append(Company.name.icontains(q.strip(), autoescape=True))

    if teknopark is not None:
        filters.append(
            Company.id.in_(
                select(CompanyAffiliation.company_id).where(
                    CompanyAffiliation.teknopark == teknopark
                )
            )
        )

    if needs_review is not None:
        filters.append(Company.needs_review == needs_review)

    try:
        with Session(engine) as session:
            total = session.scalar(
                select(func.count()).select_from(Company).where(*filters)
            )

            companies = session.scalars(
                select(Company)
                .where(*filters)
                .order_by(Company.name, Company.id)
                .offset(offset)
                .limit(limit)
            ).all()

            affiliations_by_company = {
                company.id: [] for company in companies
            }

            if companies:
                affiliations = session.execute(
                    select(
                        CompanyAffiliation.company_id,
                        CompanyAffiliation.teknopark,
                    )
                    .where(
                        CompanyAffiliation.company_id.in_(
                            list(affiliations_by_company)
                        )
                    )
                    .order_by(CompanyAffiliation.teknopark)
                ).all()

                for company_id, park in affiliations:
                    affiliations_by_company[company_id].append(park)

            items = [
                CompanyItem(
                    id=company.id,
                    name=company.name,
                    sector=company.sector,
                    needs_review=company.needs_review,
                    teknoparks=affiliations_by_company[company.id],
                )
                for company in companies
            ]

            return CompanyPage(
                total=total or 0,
                limit=limit,
                offset=offset,
                items=items,
            )
    except SQLAlchemyError:
        raise HTTPException(
            status_code=503,
            detail="Şirket listesi şu anda alınamıyor.",
        ) from None
