import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from app.review_career_source import review_source


class CareerSourceReviewTests(unittest.TestCase):
    def test_approve_requires_verified_profile_and_evidence(self) -> None:
        source_id = uuid4()
        company_id = uuid4()
        source = SimpleNamespace(
            id=source_id,
            company_id=company_id,
            source_url="https://jobs.lever.co/acme",
            source_type="ats",
            ats_type="lever",
            access_strategy="public_api",
            status="needs_review",
            evidence=[{
                "kind": "verified_site_link",
                "text": "Verified company site links to this board.",
                "source_url": "https://acme.example/careers",
            }],
            last_error_code="timeout",
        )
        profile = SimpleNamespace(status="verified")
        with patch("app.review_career_source.Session") as session_class:
            session = session_class.return_value.__enter__.return_value
            session.get.side_effect = [source, profile]
            result = review_source(
                object(), source_id=source_id, approve=True
            )
        self.assertEqual(result["status"], "active")
        self.assertEqual(source.status, "active")
        self.assertIsNone(source.last_error_code)
        session.commit.assert_called_once()

    def test_approve_rejects_noncanonical_or_empty_evidence(self) -> None:
        source_id = uuid4()
        source = SimpleNamespace(
            id=source_id,
            company_id=uuid4(),
            source_url="https://jobs.lever.co/acme?unreviewed=true",
            source_type="ats",
            ats_type="lever",
            access_strategy="public_api",
            status="needs_review",
            evidence=[],
        )
        profile = SimpleNamespace(status="verified")
        with patch("app.review_career_source.Session") as session_class:
            session = session_class.return_value.__enter__.return_value
            session.get.side_effect = [source, profile]
            with self.assertRaisesRegex(ValueError, "source_data_mismatch"):
                review_source(object(), source_id=source_id, approve=True)
        session.commit.assert_not_called()

    def test_reject_remains_available_if_profile_is_unverified(self) -> None:
        source_id = uuid4()
        source = SimpleNamespace(
            id=source_id,
            company_id=uuid4(),
            source_url="https://jobs.lever.co/acme",
            status="needs_review",
            next_check_at=object(),
        )
        with patch("app.review_career_source.Session") as session_class:
            session = session_class.return_value.__enter__.return_value
            session.get.return_value = source
            result = review_source(
                object(), source_id=source_id, approve=False
            )
        self.assertEqual(result["status"], "inactive")
        self.assertIsNone(source.next_check_at)
        self.assertEqual(session.get.call_count, 1)
        session.commit.assert_called_once()

    def test_approve_rejects_legacy_unstructured_evidence(self) -> None:
        source_id = uuid4()
        source = SimpleNamespace(
            id=source_id,
            company_id=uuid4(),
            source_url="https://jobs.lever.co/acme",
            source_type="ats",
            ats_type="lever",
            access_strategy="public_api",
            status="needs_review",
            evidence=[{"text": "Search query happened to contain Acme."}],
        )
        profile = SimpleNamespace(status="verified")
        with patch("app.review_career_source.Session") as session_class:
            session = session_class.return_value.__enter__.return_value
            session.get.side_effect = [source, profile]
            with self.assertRaisesRegex(ValueError, "source_data_mismatch"):
                review_source(object(), source_id=source_id, approve=True)
        session.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
