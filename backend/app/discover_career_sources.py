"""Find reviewable career sources for companies with verified web profiles.

Run with PYTHONPATH=backend python -m app.discover_career_sources --dry-run.
"""

import argparse
import json
import time
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import Engine, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.career_sources import SourceCandidate, classify_career_source
from app.discovery.web_verifier import (
    SafeWebsiteVerifier,
    WebsiteVerificationError,
)
from app.models import CareerSource, Company, CompanyWebProfile


MAX_COMPANIES = 10
MAX_SOURCES_PER_COMPANY = 10


@dataclass(frozen=True)
class VerifiedCompany:
    company_id: UUID
    name: str
    website: str
    careers_url: str | None


@dataclass(frozen=True)
class SourceWithProvenance:
    candidate: SourceCandidate
    discovered_from_url: str | None
    note: str


def _site_hostname(url: str) -> str:
    hostname = (urlsplit(url).hostname or "").lower()
    return hostname.removeprefix("www.")


def _same_site(url: str, website: str) -> bool:
    # Both inputs have already passed classify_career_source or were fetched
    # by SafeWebsiteVerifier. A subdomain still requires manual review.
    host = _site_hostname(url)
    site = _site_hostname(website)
    return bool(site and (host == site or host.endswith(f".{site}")))


def _without_query(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def candidates_from_page(
    website: str,
    page_url: str,
    links: tuple[str, ...],
    profile_careers_url: str | None,
) -> list[SourceWithProvenance]:
    """Only a verified website's HTML links establish external provenance."""
    found: dict[str, SourceWithProvenance] = {}
    origin = _without_query(page_url)

    for link in links:
        try:
            candidate = classify_career_source(link)
        except ValueError:
            continue
        if candidate is None:
            continue
        found.setdefault(
            candidate.source_url,
            SourceWithProvenance(
                candidate=candidate,
                discovered_from_url=origin,
                note="Bağlantı doğrulanmış şirket sitesinin ana sayfasında bulundu.",
            ),
        )
        if len(found) >= MAX_SOURCES_PER_COMPANY:
            break

    # A same-site career URL saved by the earlier discovery stage remains
    # reviewable even when absent from the landing page. Unlinked external
    # ATS candidates are excluded; no company-to-board relationship is proven.
    if profile_careers_url and len(found) < MAX_SOURCES_PER_COMPANY:
        try:
            profile_candidate = classify_career_source(profile_careers_url)
        except ValueError:
            profile_candidate = None
        if (
            profile_candidate is not None
            and _same_site(profile_candidate.source_url, website)
        ):
            found.setdefault(
                profile_candidate.source_url,
                SourceWithProvenance(
                    candidate=profile_candidate,
                    discovered_from_url=_without_query(website),
                    note="Önceki keşifte bulunan kariyer adresi şirket alan adındadır.",
                ),
            )

    return list(found.values())


def select_companies(
    database: Engine,
    *,
    limit: int,
    company_id: UUID | None = None,
) -> list[VerifiedCompany]:
    with Session(database) as session:
        statement = (
            select(
                Company.id,
                Company.name,
                CompanyWebProfile.official_website_url,
                CompanyWebProfile.careers_url,
            )
            .join(CompanyWebProfile, CompanyWebProfile.company_id == Company.id)
            .where(
                CompanyWebProfile.status == "verified",
                CompanyWebProfile.official_website_url.is_not(None),
            )
        )
        if company_id is not None:
            statement = statement.where(Company.id == company_id)
        else:
            statement = statement.where(
                ~exists(
                    select(CareerSource.id).where(
                        CareerSource.company_id == Company.id
                    )
                )
            )
        rows = session.execute(
            statement.order_by(Company.name, Company.id).limit(limit)
        ).all()

    return [
        VerifiedCompany(id_, name, website, careers_url)
        for id_, name, website, careers_url in rows
    ]


def save_candidates(
    database: Engine,
    company: VerifiedCompany,
    sources: list[SourceWithProvenance],
) -> int:
    """Insert only new candidates; preserve reviewed and active records."""
    with Session(database) as session:
        profile = session.get(CompanyWebProfile, company.company_id)
        if (
            profile is None
            or profile.status != "verified"
            or profile.official_website_url != company.website
        ):
            raise ValueError("verified_profile_changed")

        created = 0
        for source in sources[:MAX_SOURCES_PER_COMPANY]:
            candidate = source.candidate
            statement = insert(CareerSource).values(
                company_id=company.company_id,
                source_url=candidate.source_url,
                source_type=candidate.source_type,
                ats_type=candidate.ats_type,
                access_strategy=candidate.access_strategy,
                status="needs_review",
                discovered_from_url=source.discovered_from_url,
                evidence=[{"text": source.note}],
            )
            statement = statement.on_conflict_do_nothing(
                constraint="uq_career_sources_company_url"
            ).returning(CareerSource.id)
            if session.scalar(statement) is not None:
                created += 1
        session.commit()
    return created


def _limit(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Limit sayı olmalıdır.") from error
    if not 1 <= parsed <= MAX_COMPANIES:
        raise argparse.ArgumentTypeError("Limit 1-10 aralığında olmalıdır.")
    return parsed


def _delay(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Bekleme sayı olmalıdır.") from error
    if not 1.0 <= parsed <= 60.0:
        raise argparse.ArgumentTypeError("Bekleme 1-60 saniye olmalıdır.")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description="Find career source candidates.")
    parser.add_argument("--company-id", type=UUID)
    parser.add_argument("--limit", type=_limit, default=3)
    parser.add_argument("--delay-seconds", type=_delay, default=3.0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    from app.database import engine

    try:
        companies = select_companies(
            engine, limit=args.limit, company_id=args.company_id
        )
    except SQLAlchemyError:
        raise SystemExit("Veritabanı kullanılamıyor.") from None

    if args.dry_run:
        print(json.dumps({
            "mode": "dry_run",
            "companies": [
                {"company_id": str(item.company_id), "name": item.name}
                for item in companies
            ],
        }, ensure_ascii=False, indent=2))
        return

    if args.company_id is not None and not companies:
        raise SystemExit("Şirket için doğrulanmış web profili bulunamadı.")

    verifier = SafeWebsiteVerifier()
    for index, company in enumerate(companies):
        try:
            page_url, links = verifier.find_page_links(company.website)
            sources = candidates_from_page(
                company.website, page_url, links, company.careers_url
            )
            created = save_candidates(engine, company, sources)
        except (WebsiteVerificationError, ValueError) as error:
            code = (
                error.code
                if isinstance(error, WebsiteVerificationError)
                else "invalid_or_changed_profile"
            )
            result = {"status": "error", "error_code": code}
        except SQLAlchemyError:
            result = {"status": "error", "error_code": "persistence_error"}
        else:
            result = {
                "status": "completed",
                "candidate_count": len(sources),
                "new_sources": created,
            }
        result["company_id"] = str(company.company_id)
        print(json.dumps(result, ensure_ascii=False))
        if index + 1 < len(companies):
            time.sleep(args.delay_seconds)


if __name__ == "__main__":
    main()
