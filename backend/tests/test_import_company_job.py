from datetime import datetime

import pytest

from app.import_company_job import MANUAL_ACTIVE_CODE, prepare_manual_listing


def test_manual_linkedin_job_is_canonicalized_and_marked_active() -> None:
    listing = prepare_manual_listing(
        listing_url=(
            "https://tr.linkedin.com/jobs/view/python-developer-at-acme-"
            "1234567890?trackingId=secret"
        ),
        title="Python Developer",
        snippet="İstanbul · Tam zamanlı",
        location="İstanbul, Türkiye",
        work_mode="hybrid",
        employment_type="full_time",
        confirmed_active=True,
    )

    assert listing.provider == "linkedin"
    assert listing.external_id == "1234567890"
    assert listing.listing_url == (
        "https://www.linkedin.com/jobs/view/1234567890"
    )
    assert listing.location == "İstanbul, Türkiye"
    assert listing.work_mode == "hybrid"
    assert listing.employment_type == "full_time"
    assert listing.activity_state == "active"
    assert listing.activity_code == MANUAL_ACTIVE_CODE
    assert isinstance(listing.activity_checked_at, datetime)


def test_manual_import_requires_explicit_active_confirmation() -> None:
    with pytest.raises(ValueError, match="active_confirmation_required"):
        prepare_manual_listing(
            listing_url="https://www.linkedin.com/jobs/view/1234567890",
            title="Python Developer",
            snippet=None,
            location=None,
            work_mode="unknown",
            employment_type="unknown",
            confirmed_active=False,
        )


def test_manual_import_rejects_non_linkedin_job_url() -> None:
    with pytest.raises(ValueError, match="linkedin_job_url_required"):
        prepare_manual_listing(
            listing_url="https://jobs.lever.co/acme/job-12345",
            title="Python Developer",
            snippet=None,
            location=None,
            work_mode="unknown",
            employment_type="unknown",
            confirmed_active=True,
        )
