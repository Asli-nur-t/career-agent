import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from app.discover_career_sources import (
    VerifiedCompany,
    candidates_from_page,
    save_candidates,
)
from app.discovery.web_verifier import (
    SafeWebsiteVerifier,
    WebsiteVerificationError,
    _FetchResponse,
)


class FakeVerifier(SafeWebsiteVerifier):
    def __init__(self, response: _FetchResponse) -> None:
        super().__init__()
        self.response = response

    def _request_once(self, url: str) -> _FetchResponse:
        return self.response


class CareerSourceDiscoveryTests(unittest.TestCase):
    def test_company_page_links_and_text_can_be_read_without_network(self) -> None:
        html = (
            b"<html><script>ignore me</script><body>ACME LIMITED SIRKETI"
            b'<a href="/tr/kariyer">Kariyer</a>'
            b'<a href="https://jobs.lever.co/acme/job-123?tracking=1">Jobs</a>'
            b'<a href="https://jobs.lever.co.evil.org/acme">Wrong host</a>'
            b"</body></html>"
        )
        verifier = FakeVerifier(
            _FetchResponse(200, None, "text/html", "utf-8", html)
        )
        page_url, links = verifier.find_page_links("https://www.acme.com/")
        self.assertEqual(page_url, "https://www.acme.com/")
        self.assertIn("https://www.acme.com/tr/kariyer", links)
        self.assertIn("ACME LIMITED SIRKETI", verifier._fetch_text(page_url)[1])
        self.assertNotIn("ignore me", verifier._fetch_text(page_url)[1])

        sources = candidates_from_page(
            "https://www.acme.com/", page_url, links, None
        )
        by_url = {item.candidate.source_url: item for item in sources}
        self.assertEqual(len(by_url), 2)
        self.assertIn("https://www.acme.com/tr/kariyer", by_url)
        lever = by_url["https://jobs.lever.co/acme"]
        self.assertEqual(lever.candidate.ats_type, "lever")
        self.assertEqual(lever.candidate.status, "needs_review")
        self.assertEqual(lever.discovered_from_url, page_url)

    def test_unlinked_external_candidate_is_excluded(self) -> None:
        sources = candidates_from_page(
            "https://acme.com/",
            "https://acme.com/",
            (),
            "https://jobs.ashbyhq.com/anothercompany",
        )
        self.assertEqual(sources, [])

    def test_cross_site_redirect_is_rejected_before_second_request(self) -> None:
        verifier = FakeVerifier(
            _FetchResponse(
                302,
                "https://different-company.com/private",
                "text/html",
                "utf-8",
                b"",
            )
        )
        with self.assertRaises(WebsiteVerificationError) as context:
            verifier.find_page_links("https://acme.com/")
        self.assertEqual(context.exception.code, "cross_site_redirect")

        verifier.response = _FetchResponse(
            302, "https://127.0.0.1/private", "text/html", "utf-8", b""
        )
        with self.assertRaises(WebsiteVerificationError) as context:
            verifier.find_page_links("https://acme.com/")
        self.assertEqual(context.exception.code, "redirect_rejected")

    def test_changed_profile_aborts_before_insert(self) -> None:
        company = VerifiedCompany(uuid4(), "ACME", "https://acme.com/", None)
        source = candidates_from_page(
            company.website,
            company.website,
            ("https://acme.com/careers",),
            None,
        )
        with patch("app.discover_career_sources.Session") as session_class:
            session = session_class.return_value.__enter__.return_value
            session.get.return_value = SimpleNamespace(
                status="candidate_found",
                official_website_url=company.website,
            )
            with self.assertRaisesRegex(ValueError, "verified_profile_changed"):
                save_candidates(object(), company, source)
            session.scalar.assert_not_called()
            session.commit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
