"""List safe manual job-page links for verified companies."""

import argparse
import json
import re
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.discovery.safety import normalize_public_url, safe_text
from app.models import Company, CompanyWebProfile


LINKEDIN_HOST = re.compile(r"(?:(?:www|[a-z]{2})\.)?linkedin\.com\Z")
LINKEDIN_COMPANY_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,99}\Z")


@dataclass(frozen=True)
class CompanyJobPage:
    company_id: UUID
    company_name: str
    linkedin_company_url: str
    linkedin_jobs_url: str
    careers_url: str | None
    official_website_url: str


def _bounded_limit(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Limit tam sayı olmalıdır.") from error
    if not 1 <= parsed <= 500:
        raise argparse.ArgumentTypeError("Limit 1-500 aralığında olmalıdır.")
    return parsed


def linkedin_company_jobs_url(value: object) -> str | None:
    """Return a canonical LinkedIn jobs page without requesting LinkedIn."""
    if not isinstance(value, str):
        return None
    raw_url = value.strip()
    if not raw_url or len(raw_url) > 2048:
        return None

    try:
        normalized = normalize_public_url(raw_url)
        parsed = urlsplit(normalized)
    except ValueError:
        return None

    hostname = (parsed.hostname or "").rstrip(".").lower()
    if parsed.scheme != "https" or LINKEDIN_HOST.fullmatch(hostname) is None:
        return None

    segments = parsed.path.strip("/").split("/")
    if len(segments) < 2 or segments[0].lower() != "company":
        return None
    slug = segments[1]
    if LINKEDIN_COMPANY_SLUG.fullmatch(slug) is None:
        return None

    return f"https://www.linkedin.com/company/{slug}/jobs/"


def load_company_job_pages(
    database: Engine,
    *,
    limit: int,
    missing_careers_only: bool = False,
) -> list[CompanyJobPage]:
    statement = (
        select(
            Company.id,
            Company.name,
            CompanyWebProfile.official_linkedin_url,
            CompanyWebProfile.careers_url,
            CompanyWebProfile.official_website_url,
        )
        .join(
            CompanyWebProfile,
            CompanyWebProfile.company_id == Company.id,
        )
        .where(
            CompanyWebProfile.status == "verified",
            CompanyWebProfile.official_linkedin_url.is_not(None),
        )
        .order_by(Company.name, Company.id)
        .limit(limit)
    )
    if missing_careers_only:
        statement = statement.where(CompanyWebProfile.careers_url.is_(None))

    with Session(database) as session:
        rows = session.execute(statement).all()

    pages: list[CompanyJobPage] = []
    for company_id, name, linkedin_url, careers_url, website_url in rows:
        jobs_url = linkedin_company_jobs_url(linkedin_url)
        if jobs_url is None or not website_url:
            continue
        pages.append(
            CompanyJobPage(
                company_id=company_id,
                company_name=safe_text(name, 500),
                linkedin_company_url=jobs_url.removesuffix("jobs/"),
                linkedin_jobs_url=jobs_url,
                careers_url=careers_url,
                official_website_url=website_url,
            )
        )
    return pages


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Doğrulanmış şirketler için güvenli, elle açılabilir iş sayfası "
            "bağlantılarını listeler; LinkedIn'i taramaz."
        )
    )
    parser.add_argument("--limit", type=_bounded_limit, default=100)
    parser.add_argument(
        "--missing-careers-only",
        action="store_true",
        help="Yalnızca doğrulanmış kariyer sayfası olmayan şirketleri göster.",
    )
    args = parser.parse_args()
    from app.database import engine

    try:
        pages = load_company_job_pages(
            engine,
            limit=args.limit,
            missing_careers_only=args.missing_careers_only,
        )
    except SQLAlchemyError:
        raise SystemExit("Şirket iş sayfaları veritabanından okunamadı.") from None

    print(
        json.dumps(
            {
                "count": len(pages),
                "companies": [
                    {
                        **asdict(page),
                        "company_id": str(page.company_id),
                    }
                    for page in pages
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
