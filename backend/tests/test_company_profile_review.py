from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.review_company_profile import review_company_profile


def _review_session(*, linkedin_url: str | None, status: str = "candidate_found"):
    company = SimpleNamespace(id=uuid4(), name="Acme Teknoloji")
    profile = SimpleNamespace(
        official_website_url="https://acme.example/",
        careers_url="https://acme.example/careers/",
        official_linkedin_url=linkedin_url,
        status=status,
        evidence=[{"text": "discovered"}],
        last_verified_at=None,
        updated_at=None,
    )
    session = MagicMock()
    session.__enter__.return_value = session
    session.execute.return_value = SimpleNamespace(
        one_or_none=lambda: (company, profile)
    )
    return session, company, profile


def test_approval_requires_explicit_identity_confirmation() -> None:
    with pytest.raises(ValueError, match="identity_confirmation_required"):
        review_company_profile(
            MagicMock(),
            company_id=uuid4(),
            approve=True,
            confirmed_identity=False,
        )


def test_approval_normalizes_urls_and_records_evidence() -> None:
    session, company, profile = _review_session(
        linkedin_url="https://tr.linkedin.com/company/acme/about/?trk=test"
    )

    with patch("app.review_company_profile.Session", return_value=session):
        result = review_company_profile(
            MagicMock(),
            company_id=company.id,
            approve=True,
            confirmed_identity=True,
        )

    assert result["status"] == "verified"
    assert result["changed"] is True
    assert profile.status == "verified"
    assert profile.official_website_url == "https://acme.example/"
    assert profile.careers_url == "https://acme.example/careers"
    assert profile.official_linkedin_url == (
        "https://www.linkedin.com/company/acme/"
    )
    assert profile.evidence[-1]["decision"] == "approved"
    session.commit.assert_called_once_with()


def test_approval_rejects_spoofed_linkedin_url() -> None:
    session, company, _ = _review_session(
        linkedin_url="https://www.linkedin.com.evil.example/company/acme/"
    )

    with patch("app.review_company_profile.Session", return_value=session):
        with pytest.raises(ValueError, match="profile_linkedin_url_invalid"):
            review_company_profile(
                MagicMock(),
                company_id=company.id,
                approve=True,
                confirmed_identity=True,
            )

    session.commit.assert_not_called()


def test_approval_rejects_directory_as_official_website() -> None:
    session, company, profile = _review_session(linkedin_url=None)
    profile.official_website_url = "https://entertech.com.tr/firmalar/acme"

    with patch("app.review_company_profile.Session", return_value=session):
        with pytest.raises(
            ValueError,
            match="profile_official_website_denied",
        ):
            review_company_profile(
                MagicMock(),
                company_id=company.id,
                approve=True,
                confirmed_identity=True,
            )

    session.commit.assert_not_called()


def test_verified_profile_cannot_be_rejected() -> None:
    session, company, _ = _review_session(
        linkedin_url="https://www.linkedin.com/company/acme/",
        status="verified",
    )

    with patch("app.review_company_profile.Session", return_value=session):
        with pytest.raises(ValueError, match="verified_profile_protected"):
            review_company_profile(
                MagicMock(),
                company_id=company.id,
                approve=False,
                rejection_reason="insufficient_evidence",
            )

    session.commit.assert_not_called()


def test_rejection_clears_unverified_urls_and_leaves_review_queue() -> None:
    session, company, profile = _review_session(
        linkedin_url="https://www.linkedin.com/company/acme/"
    )
    profile.confidence = "high"

    with patch("app.review_company_profile.Session", return_value=session):
        result = review_company_profile(
            MagicMock(),
            company_id=company.id,
            approve=False,
            rejection_reason="wrong_company",
        )

    assert result["status"] == "not_found"
    assert profile.status == "not_found"
    assert profile.confidence == "low"
    assert profile.official_website_url is None
    assert profile.careers_url is None
    assert profile.official_linkedin_url is None
    assert profile.evidence[-1]["decision"] == "rejected"
    assert profile.evidence[-1]["reason"] == "wrong_company"
    session.commit.assert_called_once_with()


def test_rejection_requires_allowlisted_reason() -> None:
    with pytest.raises(ValueError, match="rejection_reason_invalid"):
        review_company_profile(
            MagicMock(),
            company_id=uuid4(),
            approve=False,
            rejection_reason="free-form input",
        )
