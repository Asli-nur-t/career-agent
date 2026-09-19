import unittest
from datetime import datetime, timedelta, timezone

from app.discovery_schedule import retry_due, should_open_provider_circuit


class CompanyDiscoveryBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 19, 18, 0, tzinfo=timezone.utc)

    def test_new_company_is_due_immediately(self) -> None:
        self.assertTrue(retry_due([], self.now))

    def test_transient_failures_use_exponential_bounded_backoff(self) -> None:
        one_failure = [
            (
                "evaluation_error",
                "api_error",
                self.now - timedelta(minutes=30),
            )
        ]
        self.assertFalse(retry_due(one_failure, self.now))

        two_failures = [
            (
                "evaluation_error",
                "api_error",
                self.now - timedelta(days=2),
            ),
            (
                "search_error",
                "connection_error",
                self.now - timedelta(hours=1),
            ),
        ]
        self.assertFalse(retry_due(two_failures, self.now))

        many_failures = [
            (
                "search_error",
                "connection_error",
                self.now - timedelta(days=index + 2),
            )
            for index in range(8)
        ]
        many_failures.append(
            (
                "evaluation_error",
                "service_unavailable",
                self.now - timedelta(hours=23),
            )
        )
        self.assertFalse(retry_due(many_failures, self.now))
        many_failures[-1] = (
            "evaluation_error",
            "service_unavailable",
            self.now - timedelta(hours=25),
        )
        self.assertTrue(retry_due(many_failures, self.now))

    def test_rejected_output_waits_seven_days(self) -> None:
        attempts = [
            ("rejected", "invalid_output", self.now - timedelta(days=6))
        ]
        self.assertFalse(retry_due(attempts, self.now))
        attempts = [
            ("rejected", "invalid_output", self.now - timedelta(days=8))
        ]
        self.assertTrue(retry_due(attempts, self.now))

    def test_naive_database_timestamp_is_treated_as_utc(self) -> None:
        naive = (self.now - timedelta(hours=2)).replace(tzinfo=None)
        self.assertTrue(
            retry_due(
                [("evaluation_error", "timeout", naive)],
                self.now,
            )
        )

    def test_rate_limit_waits_six_hours(self) -> None:
        attempts = [
            (
                "evaluation_error",
                "rate_limited",
                self.now - timedelta(hours=5),
            )
        ]
        self.assertFalse(retry_due(attempts, self.now))
        attempts[0] = (
            "evaluation_error",
            "rate_limited",
            self.now - timedelta(hours=7),
        )
        self.assertTrue(retry_due(attempts, self.now))

    def test_configuration_errors_wait_seven_days(self) -> None:
        for error_code in ("authentication_error", "model_not_found"):
            attempts = [
                (
                    "evaluation_error",
                    error_code,
                    self.now - timedelta(days=6),
                )
            ]
            self.assertFalse(retry_due(attempts, self.now))

    def test_provider_circuit_breaker(self) -> None:
        self.assertTrue(should_open_provider_circuit("rate_limited", 1))
        self.assertFalse(should_open_provider_circuit("timeout", 1))
        self.assertTrue(should_open_provider_circuit("timeout", 2))
        self.assertFalse(should_open_provider_circuit("invalid_output", 9))


if __name__ == "__main__":
    unittest.main()
