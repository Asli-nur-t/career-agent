import json
import unittest

import httpx

from app.discovery.evaluator import EvaluationError, validate_assessment
from app.discovery.schemas import CompanyAssessment
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
            self.assertEqual(
                set(payload["format"]["required"]),
                set(payload["format"]["properties"]),
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

    def test_job_board_listing_is_not_a_careers_page(self) -> None:
        job_url = "https://www.kariyer.net/is-ilani/4arc-123"
        results = [
            *self.results,
            SearchResult(
                title="4ARC İş İlanı",
                url=job_url,
                snippet="Tekil ilan",
                position=2,
            ),
        ]
        assessment = CompanyAssessment(
            company_name=self.company_name,
            brand_name="4ARC",
            official_website_candidate="https://www.4arctech.com/",
            careers_url_candidate=job_url,
            official_linkedin_candidate=None,
            confidence="high",
            status="candidate_found",
            evidence=["https://www.4arctech.com/", job_url],
            reason="Kariyer sayfası bulundu.",
        )

        validated = validate_assessment(
            self.company_name,
            results,
            assessment,
        )

        self.assertIsNone(validated.careers_url_candidate)
        self.assertNotIn(job_url, validated.evidence)
        self.assertIn("kabul edilmedi", validated.reason)

    def test_allowlisted_external_ats_is_kept(self) -> None:
        ats_url = "https://jobs.ashbyhq.com/4arc"
        results = [
            *self.results,
            SearchResult(
                title="4ARC Jobs",
                url=ats_url,
                snippet="4ARC open roles",
                position=2,
            ),
        ]
        assessment = CompanyAssessment(
            company_name=self.company_name,
            brand_name="4ARC",
            official_website_candidate="https://www.4arctech.com/",
            careers_url_candidate=ats_url,
            official_linkedin_candidate=None,
            confidence="high",
            status="candidate_found",
            evidence=[ats_url],
            reason="Şirket panosu bulundu.",
        )

        validated = validate_assessment(
            self.company_name,
            results,
            assessment,
        )

        self.assertEqual(validated.careers_url_candidate, ats_url)

    def test_company_careers_subdomain_is_kept(self) -> None:
        careers_url = "https://careers.4arctech.com/jobs"
        results = [
            *self.results,
            SearchResult(
                title="4ARC Careers",
                url=careers_url,
                snippet="4ARC open roles",
                position=2,
            ),
        ]
        assessment = CompanyAssessment(
            company_name=self.company_name,
            brand_name="4ARC",
            official_website_candidate="https://www.4arctech.com/",
            careers_url_candidate=careers_url,
            official_linkedin_candidate=None,
            confidence="high",
            status="candidate_found",
            evidence=[careers_url],
            reason="Şirket kariyer sayfası bulundu.",
        )

        validated = validate_assessment(
            self.company_name,
            results,
            assessment,
        )

        self.assertEqual(validated.careers_url_candidate, careers_url)


if __name__ == "__main__":
    unittest.main()
