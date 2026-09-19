import unittest
from unittest.mock import MagicMock, patch

from app.discovery.gemini import GeminiEvaluator, classify_gemini_error


class StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__()
        self.status_code = status_code


class GeminiErrorTests(unittest.TestCase):
    def test_provider_statuses_are_classified(self) -> None:
        expected = {
            401: "authentication_error",
            403: "authentication_error",
            404: "model_not_found",
            408: "timeout",
            429: "rate_limited",
            500: "service_unavailable",
            503: "service_unavailable",
            504: "timeout",
        }

        for status_code, error_code in expected.items():
            with self.subTest(status_code=status_code):
                self.assertEqual(
                    classify_gemini_error(StatusError(status_code)),
                    error_code,
                )

    def test_nested_provider_error_is_classified(self) -> None:
        outer = RuntimeError()
        outer.__cause__ = StatusError(429)
        self.assertEqual(classify_gemini_error(outer), "rate_limited")

    def test_unknown_error_is_generic(self) -> None:
        self.assertEqual(classify_gemini_error(RuntimeError()), "api_error")

    @patch("app.discovery.gemini.genai.Client")
    def test_sdk_retries_are_disabled(self, client: MagicMock) -> None:
        evaluator = GeminiEvaluator("x" * 20)
        http_options = client.call_args.kwargs["http_options"]

        self.assertEqual(http_options.timeout, 30_000)
        self.assertEqual(http_options.retry_options.attempts, 1)
        evaluator.close()


if __name__ == "__main__":
    unittest.main()
