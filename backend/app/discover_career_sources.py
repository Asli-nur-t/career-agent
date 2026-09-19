"""Find reviewable career sources for companies with verified web profiles.

Run with PYTHONPATH=backend python -m app.discover_career_sources --dry-run.
"""

import argparse
import json
import os
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from sqlalchemy import Engine, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.career_sources import SourceCandidate, classify_career_source
from app.discovery.safety import clean_company_name, safe_text
from app.discovery.schemas import SearchResult
from app.discovery.serper import SerperClient, SerperError
from app.discovery.web_verifier import (
    SafeWebsiteVerifier,
    WebsiteVerificationError,
)
from app.models import CareerSource, Company, CompanyWebProfile


MAX_COMPANIES = 10
MAX_SOURCES_PER_COMPANY = 10


class CareerSourceScanError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Career source scan failed.")
        self.code = code


@dataclass(frozen=True)
class VerifiedCompany:
    company_id: UUID
    name: str
    brand_name: str | None
    website: str
    careers_url: str | None


@dataclass(frozen=True)
class SourceWithProvenance:
    candidate: SourceCandidate
    discovered_from_url: str | None
    evidence_kind: str
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


def _comparison_text(value: str) -> str:
    value = value.replace("ı", "i").replace("İ", "I")
    value = unicodedata.normalize("NFKD", value).casefold()
    return " ".join(
        "".join(
            character if character.isalnum() else " "
            for character in value
            if not unicodedata.combining(character)
        ).split()
    )


def _company_aliases(company: VerifiedCompany) -> tuple[str, ...]:
    aliases: list[str] = []
    values = [company.brand_name, company.name]
    try:
        values.append(clean_company_name(company.name))
    except ValueError:
        pass
    for value in values:
        normalized = _comparison_text(value or "")
        if (
            len(normalized) >= 4
            and not normalized.isdecimal()
            and normalized not in aliases
        ):
            aliases.append(normalized)
    return tuple(aliases)


def _ats_slug_matches_company(
    company: VerifiedCompany,
    source_url: str,
) -> bool:
    slug = urlsplit(source_url).path.strip("/").split("/", 1)[0]
    compact_slug = "".join(
        character
        for character in _comparison_text(slug)
        if character.isalnum()
    )
    if not compact_slug:
        return False
    identities = {
        "".join(character for character in alias if character.isalnum())
        for alias in _company_aliases(company)
    }
    return compact_slug in identities


def build_search_queries(company: VerifiedCompany) -> tuple[str, str]:
    search_name = safe_text(
        company.brand_name or clean_company_name(company.name),
        200,
    )
    return (
        f'"{search_name}" kariyer careers jobs',
        (
            f'"{search_name}" '
            "(site:jobs.lever.co OR site:jobs.eu.lever.co OR "
            "site:boards.greenhouse.io OR "
            "site:job-boards.greenhouse.io OR site:jobs.ashbyhq.com)"
        ),
    )


def candidates_from_search(
    company: VerifiedCompany,
    query: str,
    results: list[SearchResult],
) -> list[SourceWithProvenance]:
    """Accept same-site pages or ATS boards whose slug matches the company."""
    found: dict[str, SourceWithProvenance] = {}
    for result in results:
        try:
            candidate = classify_career_source(result.url)
        except ValueError:
            continue
        if candidate is None:
            continue
        same_site = _same_site(candidate.source_url, company.website)
        ats_slug_match = (
            candidate.source_type == "ats"
            and _ats_slug_matches_company(company, candidate.source_url)
        )
        if not same_site and not ats_slug_match:
            continue
        if same_site:
            evidence_kind = "same_site_search"
            note = "Arama sonucu doğrulanmış şirket alan adındadır."
        else:
            evidence_kind = "ats_slug_match"
            note = "ATS pano anahtarı şirket veya marka kimliğiyle eşleşti."
        note = safe_text(
            f"{note} Sonuç: {result.title} | Sorgu: {query}", 500
        )
        found.setdefault(
            candidate.source_url,
            SourceWithProvenance(
                candidate=candidate,
                discovered_from_url=_without_query(result.url),
                evidence_kind=evidence_kind,
                note=note,
            ),
        )
        if len(found) >= MAX_SOURCES_PER_COMPANY:
            break
    return list(found.values())


def search_candidates(
    client: SerperClient,
    company: VerifiedCompany,
) -> tuple[list[SourceWithProvenance], tuple[str, ...]]:
    found: dict[str, SourceWithProvenance] = {}
    errors: list[str] = []
    for query in build_search_queries(company):
        try:
            results = client.search(query)
        except SerperError as error:
            errors.append(error.code)
            continue
        for source in candidates_from_search(company, query, results):
            found.setdefault(source.candidate.source_url, source)
        if len(found) >= MAX_SOURCES_PER_COMPANY:
            break
    return list(found.values()), tuple(errors)


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
                evidence_kind="verified_site_link",
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
                    evidence_kind="verified_profile_career_url",
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
                CompanyWebProfile.brand_name,
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
                or_(
                    CompanyWebProfile.career_sources_next_check_at.is_(None),
                    CompanyWebProfile.career_sources_next_check_at
                    <= datetime.now(timezone.utc),
                )
            )
        rows = session.execute(
            statement.order_by(Company.name, Company.id).limit(limit)
        ).all()

    return [
        VerifiedCompany(id_, name, brand_name, website, careers_url)
        for id_, name, brand_name, website, careers_url in rows
    ]


def save_candidates(
    database: Engine,
    company: VerifiedCompany,
    sources: list[SourceWithProvenance],
    *,
    scan_error_code: str | None = None,
) -> int:
    """Insert only new candidates; preserve reviewed and active records."""
    now = datetime.now(timezone.utc)
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
                evidence=[{
                    "kind": source.evidence_kind,
                    "text": source.note,
                    "source_url": source.discovered_from_url or "",
                }],
            )
            statement = statement.on_conflict_do_nothing(
                constraint="uq_career_sources_company_url"
            ).returning(CareerSource.id)
            if session.scalar(statement) is not None:
                created += 1
        profile.career_sources_last_checked_at = now
        profile.career_sources_candidate_count = len(sources)
        profile.career_sources_consecutive_failures = 0
        profile.career_sources_last_error_code = scan_error_code
        if sources:
            profile.career_sources_last_outcome = "candidates_found"
            profile.career_sources_next_check_at = now + timedelta(days=30)
        else:
            profile.career_sources_last_outcome = "no_results"
            profile.career_sources_next_check_at = now + timedelta(
                days=1 if scan_error_code else 7
            )
        session.commit()
    return created


def record_scan_failure(
    database: Engine,
    company: VerifiedCompany,
    error_code: str,
) -> None:
    now = datetime.now(timezone.utc)
    with Session(database) as session:
        profile = session.get(CompanyWebProfile, company.company_id)
        if (
            profile is None
            or profile.status != "verified"
            or profile.official_website_url != company.website
        ):
            return
        failures = min(
            profile.career_sources_consecutive_failures + 1,
            1_000,
        )
        profile.career_sources_last_checked_at = now
        profile.career_sources_next_check_at = now + timedelta(
            hours=min(2 ** min(failures - 1, 5), 24)
        )
        profile.career_sources_last_outcome = "error"
        profile.career_sources_last_error_code = error_code[:80]
        profile.career_sources_consecutive_failures = failures
        profile.career_sources_candidate_count = 0
        session.commit()


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

    serper_key = os.environ.get("SERPER_API_KEY", "").strip()
    if not serper_key:
        raise SystemExit("SERPER_API_KEY yapılandırılmamış.")

    verifier = SafeWebsiteVerifier()
    failure_count = 0
    source_count = 0
    with SerperClient(serper_key) as search_client:
        for index, company in enumerate(companies):
            page_error: str | None = None
            search_errors: tuple[str, ...] = ()
            fallback_used = False
            try:
                try:
                    page_url, links = verifier.find_page_links(company.website)
                except WebsiteVerificationError as error:
                    page_error = error.code
                    sources = []
                else:
                    sources = candidates_from_page(
                        company.website,
                        page_url,
                        links,
                        company.careers_url,
                    )

                if not sources:
                    fallback_used = True
                    sources, search_errors = search_candidates(
                        search_client,
                        company,
                    )

                if (
                    not sources
                    and page_error
                    and len(search_errors) == 2
                ):
                    raise CareerSourceScanError(
                        "all_discovery_methods_failed"
                    )

                partial_error = (
                    search_errors[0]
                    if search_errors
                    else page_error
                )
                created = save_candidates(
                    engine,
                    company,
                    sources,
                    scan_error_code=partial_error,
                )
            except CareerSourceScanError as error:
                try:
                    record_scan_failure(engine, company, error.code)
                except SQLAlchemyError:
                    pass
                result = {
                    "status": "error",
                    "error_code": error.code,
                }
            except ValueError:
                result = {
                    "status": "error",
                    "error_code": "invalid_or_changed_profile",
                }
            except SQLAlchemyError:
                result = {
                    "status": "error",
                    "error_code": "persistence_error",
                }
            else:
                source_count += created
                result = {
                    "status": "completed",
                    "candidate_count": len(sources),
                    "new_sources": created,
                    "fallback_used": fallback_used,
                    "page_error": page_error,
                    "search_errors": list(search_errors),
                }
            if result["status"] == "error":
                failure_count += 1
            result["company_id"] = str(company.company_id)
            print(json.dumps(result, ensure_ascii=False))
            if index + 1 < len(companies):
                time.sleep(args.delay_seconds)

    print(json.dumps({
        "status": "completed" if failure_count == 0 else "completed_with_errors",
        "selected_count": len(companies),
        "new_sources": source_count,
        "failure_count": failure_count,
    }, ensure_ascii=False))
    if failure_count:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
