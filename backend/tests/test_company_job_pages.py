from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.list_company_job_pages import (
    linkedin_company_jobs_url,
    load_company_job_pages,
)


def test_linkedin_company_url_is_canonicalized_to_jobs_page() -> None:
    assert linkedin_company_jobs_url(
        "https://tr.linkedin.com/company/trade-complize/about/?trk=test"
    ) == "https://www.linkedin.com/company/trade-complize/jobs/"
    assert linkedin_company_jobs_url(
        "https://www.linkedin.com/company/Acme-42/jobs/"
    ) == "https://www.linkedin.com/company/Acme-42/jobs/"


def test_non_company_or_spoofed_linkedin_urls_are_rejected() -> None:
    invalid_urls = (
        "https://www.linkedin.com.evil.example/company/acme/jobs/",
        "https://www.linkedin.com/in/acme/",
        "https://user@www.linkedin.com/company/acme/",
        "https://www.linkedin.com/company/acme%2Fjobs/",
        "http://www.linkedin.com/company/acme/",
    )

    assert all(linkedin_company_jobs_url(url) is None for url in invalid_urls)


def test_company_job_pages_skip_invalid_stored_urls() -> None:
    company_id = uuid4()
    rows = [
        (
            company_id,
            "Trade Complize",
            "https://www.linkedin.com/company/trade-complize/",
            None,
            "https://tradecomplize.example/",
        ),
        (
            uuid4(),
            "Spoofed",
            "https://linkedin.example/company/spoofed/",
            None,
            "https://spoofed.example/",
        ),
    ]
    session = MagicMock()
    session.__enter__.return_value = session
    session.execute.return_value = SimpleNamespace(all=lambda: rows)

    with patch("app.list_company_job_pages.Session", return_value=session):
        pages = load_company_job_pages(MagicMock(), limit=100)

    assert len(pages) == 1
    assert pages[0].company_id == company_id
    assert pages[0].linkedin_company_url == (
        "https://www.linkedin.com/company/trade-complize/"
    )
    assert pages[0].linkedin_jobs_url == (
        "https://www.linkedin.com/company/trade-complize/jobs/"
    )


def test_missing_careers_filter_is_part_of_query() -> None:
    session = MagicMock()
    session.__enter__.return_value = session
    session.execute.return_value = SimpleNamespace(all=lambda: [])

    with patch("app.list_company_job_pages.Session", return_value=session):
        load_company_job_pages(
            MagicMock(),
            limit=10,
            missing_careers_only=True,
        )

    statement = session.execute.call_args.args[0]
    assert "company_web_profiles.careers_url IS NULL" in str(statement)
