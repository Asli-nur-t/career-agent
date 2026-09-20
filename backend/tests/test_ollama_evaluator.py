import json
import unittest

import httpx

from app.discovery.evaluator import EvaluationError
from app.discovery.ollama import OllamaEvaluator
from app.discovery.schemas import SearchResult


class OllamaEvaluatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.company_name = "4ARC YAZILIM TEKNOLOJİLERİ A.Ş."
        self.results = [
            SearchResult(
                title="4ARC Technology",
                url="https://www.4arctech.com/",
                snippet="4ARC Yazılım Teknolojileri A.Ş.",
                position=1,
            )
        ]

    def test_local_structured_response_is_validated(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.host, "127.0.0.1")
            self.assertEqual(request.url.port, 11434)
            payload = json.loads(request.content)
            self.assertEqual(payload["model"], "qwen3:8b")
            self.assertFalse(payload["stream"])
            self.assertFalse(payload["think"])
            self.assertEqual(
                payload["format"]["additionalProperties"],
                False,
            )
            assessment = {
                "company_name": self.company_name,
                "brand_name": "4ARC",
                "official_website_candidate": (
                    "https://www.4arctech.com/"
                ),
                "careers_url_candidate": None,
                "official_linkedin_candidate": None,
                "confidence": "high",
                "status": "candidate_found",
                "evidence": ["Şirket adı eşleşti."],
                "reason": "Arama sonucu şirketle eşleşiyor.",
            }
            return httpx.Response(
                200,
                json={
                    "model": "qwen3:8b",
                    "done": True,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(assessment),
                    },
                },
            )

        evaluator = OllamaEvaluator(
            transport=httpx.MockTransport(handler)
        )
        try:
            assessment = evaluator.evaluate(
                self.company_name,
                self.results,
            )
        finally:
            evaluator.close()

        self.assertEqual(assessment.brand_name, "4ARC")
        self.assertEqual(
            assessment.official_website_candidate,
            "https://www.4arctech.com/",
        )

    def test_non_loopback_base_url_is_rejected(self) -> None:
        for base_url in (
            "https://example.com",
            "http://0.0.0.0:11434",
            "http://127.0.0.1:8080",
        ):
            with self.subTest(base_url=base_url):
                with self.assertRaises(ValueError):
                    OllamaEvaluator(base_url=base_url)

    def test_invented_url_is_rejected(self) -> None:
        assessment = {
            "company_name": self.company_name,
            "brand_name": "4ARC",
            "official_website_candidate": "https://attacker.example/",
            "careers_url_candidate": None,
            "official_linkedin_candidate": None,
            "confidence": "high",
            "status": "candidate_found",
            "evidence": ["Geçersiz kanıt."],
            "reason": "Geçersiz URL.",
        }

        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "done": True,
                    "message": {"content": json.dumps(assessment)},
                },
            )

        evaluator = OllamaEvaluator(
            transport=httpx.MockTransport(handler)
        )
        try:
            with self.assertRaisesRegex(
                EvaluationError,
                "Company evaluation failed",
            ) as raised:
                evaluator.evaluate(self.company_name, self.results)
        finally:
            evaluator.close()

        self.assertEqual(raised.exception.code, "invented_url")

    def test_missing_model_is_classified_without_error_body(self) -> None:
        def handler(_: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                json={"error": "sensitive provider detail"},
            )

        evaluator = OllamaEvaluator(
            transport=httpx.MockTransport(handler)
        )
        try:
            with self.assertRaises(EvaluationError) as raised:
                evaluator.evaluate(self.company_name, self.results)
        finally:
            evaluator.close()

        self.assertEqual(raised.exception.code, "model_not_found")
        self.assertNotIn(
            "sensitive provider detail",
            str(raised.exception),
        )


if __name__ == "__main__":
    unittest.main()
