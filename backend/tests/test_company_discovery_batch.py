import unittest
from datetime import datetime, timedelta, timezone

from app.discovery_schedule import retry_due


class CompanyDiscoveryBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 19, 18, 0, tzinfo=timezone.utc)

    def test_new_company_is_due_immediately(self) -> None:
        self.assertTrue(retry_due([], self.now))

    def test_transient_failures_use_exponential_bounded_backoff(self) -> None:
        one_failure = [
            ("evaluation_error", self.now - timedelta(minutes=30))
        ]
        self.assertFalse(retry_due(one_failure, self.now))

        two_failures = [
            ("evaluation_error", self.now - timedelta(days=2)),
            ("search_error", self.now - timedelta(hours=1)),
        ]
        self.assertFalse(retry_due(two_failures, self.now))

        many_failures = [
            ("search_error", self.now - timedelta(days=index + 2))
            for index in range(8)
        ]
        many_failures.append(
            ("evaluation_error", self.now - timedelta(hours=23))
        )
        self.assertFalse(retry_due(many_failures, self.now))
        many_failures[-1] = (
            "evaluation_error",
            self.now - timedelta(hours=25),
        )
        self.assertTrue(retry_due(many_failures, self.now))

    def test_rejected_output_waits_seven_days(self) -> None:
        attempts = [("rejected", self.now - timedelta(days=6))]
        self.assertFalse(retry_due(attempts, self.now))
        attempts = [("rejected", self.now - timedelta(days=8))]
        self.assertTrue(retry_due(attempts, self.now))

    def test_naive_database_timestamp_is_treated_as_utc(self) -> None:
        naive = (self.now - timedelta(hours=2)).replace(tzinfo=None)
        self.assertTrue(
            retry_due([("evaluation_error", naive)], self.now)
        )


if __name__ == "__main__":
    unittest.main()
