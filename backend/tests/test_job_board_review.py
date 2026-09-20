import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from app.review_job_board_candidate import review_job_board_candidate


def candidate(*, status: str = "needs_review") -> SimpleNamespace:
    listing_url = (
        "https://www.kariyer.net/is-ilani/"
        "acme-ai-engineer-4034270"
    )
    return SimpleNamespace(
        id=uuid4(),
        company_id=uuid4(),
        provider="kariyer",
        external_id="4034270",
        listing_url=listing_url,
        title="AI Engineer",
        location=None,
        work_mode="unknown",
        employment_type="unknown",
        published_at=None,
        snippet="Build safe AI systems",
        status=status,
        approved_at=None,
        evidence=[
            {
                "kind": "search_result",
                "query": '"ACME" site:kariyer.net/is-ilani',
                "url": listing_url,
                "title": "AI Engineer",
                "position": "1",
            }
        ],
    )


class JobBoardReviewTests(unittest.TestCase):
    def test_approval_requires_explicit_active_confirmation(self) -> None:
        with patch(
            "app.review_job_board_candidate.Session"
        ) as session_class:
            with self.assertRaisesRegex(
                ValueError,
                "active_confirmation_required",
            ):
                review_job_board_candidate(
                    object(),
                    candidate_id=uuid4(),
                    approve=True,
                )
        session_class.assert_not_called()

    def test_approved_candidate_is_promoted_transactionally(self) -> None:
        item = candidate()
        with patch(
            "app.review_job_board_candidate.Session"
        ) as session_class:
            session = session_class.return_value.__enter__.return_value
            session.scalar.side_effect = [item, None]
            result = review_job_board_candidate(
                object(),
                candidate_id=item.id,
                approve=True,
                confirmed_active=True,
            )

        posting = session.add.call_args.args[0]
        self.assertEqual(item.status, "approved")
        self.assertIsNotNone(item.approved_at)
        self.assertEqual(posting.job_board_candidate_id, item.id)
        self.assertIsNone(posting.career_source_id)
        self.assertEqual(posting.status, "active")
        self.assertEqual(len(posting.content_hash), 64)
        self.assertEqual(result["status"], "approved")
        self.assertTrue(result["changed"])
        session.flush.assert_called_once()
        session.commit.assert_called_once()

    def test_tampered_candidate_is_not_promoted(self) -> None:
        item = candidate()
        item.provider = "linkedin"
        with patch(
            "app.review_job_board_candidate.Session"
        ) as session_class:
            session = session_class.return_value.__enter__.return_value
            session.scalar.side_effect = [item, None]
            with self.assertRaisesRegex(
                ValueError,
                "candidate_data_mismatch",
            ):
                review_job_board_candidate(
                    object(),
                    candidate_id=item.id,
                    approve=True,
                    confirmed_active=True,
                )
        session.add.assert_not_called()
        session.commit.assert_not_called()

    def test_rejection_is_idempotent_but_approved_row_is_protected(
        self,
    ) -> None:
        rejected = candidate(status="rejected")
        with patch(
            "app.review_job_board_candidate.Session"
        ) as session_class:
            session = session_class.return_value.__enter__.return_value
            session.scalar.side_effect = [rejected, None]
            result = review_job_board_candidate(
                object(),
                candidate_id=rejected.id,
                approve=False,
            )
        self.assertFalse(result["changed"])
        session.commit.assert_called_once()

        approved = candidate(status="approved")
        approved.approved_at = object()
        posting = SimpleNamespace(id=uuid4())
        with patch(
            "app.review_job_board_candidate.Session"
        ) as session_class:
            session = session_class.return_value.__enter__.return_value
            session.scalar.side_effect = [approved, posting]
            with self.assertRaisesRegex(
                ValueError,
                "approved_candidate_cannot_be_rejected",
            ):
                review_job_board_candidate(
                    object(),
                    candidate_id=approved.id,
                    approve=False,
                )
        session.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
