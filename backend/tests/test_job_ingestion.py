import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from app.ats import NormalizedJob
from app.ingest_jobs import ActiveSource, record_failure, sync_jobs


def job(external_id: str, content_hash: str) -> NormalizedJob:
    return NormalizedJob(
        external_id=external_id,
        job_url=f"https://jobs.lever.co/acme/{external_id}",
        apply_url=f"https://jobs.lever.co/acme/{external_id}/apply",
        title="AI Engineer",
        location="Istanbul",
        department="AI",
        employment_type="Full-time",
        description_text="Build agents",
        is_remote=False,
        published_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
        content_hash=content_hash,
    )


class JobIngestionTests(unittest.TestCase):
    def test_successful_sync_updates_creates_and_closes(self) -> None:
        source_id = uuid4()
        company_id = uuid4()
        source_data = ActiveSource(
            source_id,
            company_id,
            "https://jobs.lever.co/acme",
            "lever",
        )
        source = SimpleNamespace(
            id=source_id,
            status="active",
            source_type="ats",
            access_strategy="public_api",
            ats_type="lever",
            source_url=source_data.source_url,
            consecutive_failures=0,
        )
        profile = SimpleNamespace(status="verified")
        changed = SimpleNamespace(
            external_id="changed",
            status="active",
            content_hash="a" * 64,
            closed_at=None,
        )
        missing = SimpleNamespace(
            external_id="missing",
            status="active",
            content_hash="b" * 64,
            closed_at=None,
        )
        with patch("app.ingest_jobs.Session") as session_class:
            session = session_class.return_value.__enter__.return_value
            session.get.side_effect = [source, profile]
            session.scalars.return_value.all.return_value = [changed, missing]
            result = sync_jobs(
                object(),
                source_data,
                [job("changed", "c" * 64), job("new", "d" * 64)],
            )
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["updated"], 1)
        self.assertEqual(result["closed"], 1)
        self.assertEqual(changed.content_hash, "c" * 64)
        self.assertEqual(missing.status, "closed")
        self.assertIsNotNone(missing.closed_at)
        self.assertEqual(source.consecutive_failures, 0)
        session.add.assert_called_once()
        session.commit.assert_called_once()

    def test_fifth_failure_requires_review(self) -> None:
        source = SimpleNamespace(
            consecutive_failures=4,
            status="active",
        )
        with patch("app.ingest_jobs.Session") as session_class:
            session = session_class.return_value.__enter__.return_value
            session.get.return_value = source
            record_failure(object(), uuid4(), "timeout")
        self.assertEqual(source.consecutive_failures, 5)
        self.assertEqual(source.status, "needs_review")
        self.assertEqual(source.last_error_code, "timeout")
        self.assertIsNotNone(source.next_check_at)
        session.commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
