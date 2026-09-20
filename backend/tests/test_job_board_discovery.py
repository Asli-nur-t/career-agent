import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.discover_job_board_jobs import (
    company_for_search,
    persist_job_board_candidates,
)
from app.job_boards import JobBoardListing


class JobBoardDiscoveryTests(unittest.TestCase):
    def test_verified_brand_is_used_as_search_identity(self) -> None:
        company_id = uuid4()
        cases = (
            ("verified", "Acme", "Acme"),
            ("candidate_found", "Unverified Brand", "ACME A.Ş."),
        )
        for status, brand_name, expected in cases:
            with self.subTest(status=status):
                row = SimpleNamespace(
                    id=company_id,
                    name="ACME A.Ş.",
                    brand_name=brand_name,
                    status=status,
                )
                with patch(
                    "app.discover_job_board_jobs.Session"
                ) as session_class:
                    session = (
                        session_class.return_value.__enter__.return_value
                    )
                    session.execute.return_value.one_or_none.return_value = row
                    result = company_for_search(object(), company_id)
                self.assertEqual(result, (company_id, "ACME A.Ş.", expected))

    def test_candidates_are_inserted_or_refreshed_without_auto_approval(
        self,
    ) -> None:
        company_id = uuid4()
        listings = (
            JobBoardListing(
                provider="linkedin",
                external_id="123456",
                listing_url=(
                    "https://www.linkedin.com/jobs/view/123456"
                ),
                title="AI Engineer",
                snippet="Build AI systems",
                search_position=1,
            ),
            JobBoardListing(
                provider="kariyer",
                external_id="654321",
                listing_url=(
                    "https://www.kariyer.net/is-ilani/acme-654321"
                ),
                title="Software Engineer",
                snippet=None,
                search_position=2,
            ),
        )
        existing_result = SimpleNamespace(
            all=lambda: [("linkedin", "123456", "needs_review")]
        )
        insert_result = SimpleNamespace()

        with patch(
            "app.discover_job_board_jobs.Session"
        ) as session_class:
            session = session_class.return_value.__enter__.return_value
            session.scalar.return_value = "ACME TEKNOLOJİ A.Ş."
            session.execute.side_effect = [existing_result, insert_result]
            counts = persist_job_board_candidates(
                object(),
                company_id=company_id,
                company_name="ACME TEKNOLOJİ A.Ş.",
                query='"ACME TEKNOLOJİ" site:linkedin.com/jobs/view',
                listings=listings,
            )

        self.assertEqual(counts["new_candidates"], 1)
        self.assertEqual(counts["refreshed_candidates"], 1)
        self.assertEqual(session.execute.call_count, 2)
        upsert = session.execute.call_args_list[1].args[0]
        compiled = upsert.compile(dialect=postgresql.dialect())
        sql = str(compiled)
        self.assertIn("CASE WHEN", sql)
        self.assertIn("needs_review", compiled.params.values())
        session.commit.assert_called_once()

    def test_empty_candidate_set_does_not_open_transaction(self) -> None:
        with patch(
            "app.discover_job_board_jobs.Session"
        ) as session_class:
            counts = persist_job_board_candidates(
                object(),
                company_id=uuid4(),
                company_name="ACME",
                query='"ACME"',
                listings=(),
            )
        self.assertEqual(counts["new_candidates"], 0)
        self.assertEqual(counts["refreshed_candidates"], 0)
        self.assertEqual(counts["auto_rejected_candidates"], 0)
        session_class.assert_not_called()

    def test_explicitly_closed_candidate_is_stored_rejected(self) -> None:
        company_id = uuid4()
        listing_url = (
            "https://www.kariyer.net/is-ilani/"
            "acme-back-end-developer-4034270"
        )
        closed = JobBoardListing(
            provider="kariyer",
            external_id="4034270",
            listing_url=listing_url,
            title="Back End Developer",
            snippet=None,
            search_position=1,
            activity_state="closed",
            activity_code="kariyer_closed_marker",
            activity_url=listing_url,
        )
        existing_result = SimpleNamespace(all=lambda: [])

        with patch(
            "app.discover_job_board_jobs.Session"
        ) as session_class:
            session = session_class.return_value.__enter__.return_value
            session.scalar.return_value = "ACME"
            session.execute.side_effect = [
                existing_result,
                SimpleNamespace(),
            ]
            counts = persist_job_board_candidates(
                object(),
                company_id=company_id,
                company_name="ACME",
                query='"ACME"',
                listings=(closed,),
            )

        upsert = session.execute.call_args_list[1].args[0]
        compiled = upsert.compile(dialect=postgresql.dialect())
        self.assertIn("rejected", compiled.params.values())
        self.assertEqual(counts["auto_rejected_candidates"], 1)

    def test_duplicate_candidate_keys_are_collapsed(self) -> None:
        company_id = uuid4()
        first = JobBoardListing(
            provider="linkedin",
            external_id="123456",
            listing_url="https://www.linkedin.com/jobs/view/123456",
            title="AI Engineer",
            snippet=None,
            search_position=1,
        )
        duplicate = JobBoardListing(
            provider="linkedin",
            external_id="123456",
            listing_url="https://www.linkedin.com/jobs/view/123456",
            title="AI Engineer",
            snippet="Duplicate result",
            search_position=2,
        )
        existing_result = SimpleNamespace(all=lambda: [])

        with patch(
            "app.discover_job_board_jobs.Session"
        ) as session_class:
            session = session_class.return_value.__enter__.return_value
            session.scalar.return_value = "ACME"
            session.execute.side_effect = [
                existing_result,
                SimpleNamespace(),
            ]
            counts = persist_job_board_candidates(
                object(),
                company_id=company_id,
                company_name="ACME",
                query='"ACME"',
                listings=(first, duplicate),
            )

        self.assertEqual(counts["new_candidates"], 1)
        self.assertEqual(counts["refreshed_candidates"], 0)


if __name__ == "__main__":
    unittest.main()
