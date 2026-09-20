import unittest
from types import SimpleNamespace

from app.discovery.schemas import SearchResult
from app.job_boards import (
    JobBoardActivityVerifier,
    JobBoardSearchConnector,
    build_job_board_query,
    choose_job_board_identity,
    listing_matches_company,
    normalize_job_board_result,
)


def result(url: str, position: int = 1) -> SearchResult:
    return SearchResult(
        title="AI Engineer",
        url=url,
        snippet="ACME TEKNOLOJİ şirketinde açık pozisyon",
        position=position,
    )


class JobBoardTests(unittest.TestCase):
    def test_kariyer_closed_marker_is_detected(self) -> None:
        listing_url = (
            "https://www.kariyer.net/is-ilani/"
            "acme-back-end-developer-4034270"
        )
        reader = SimpleNamespace(
            read_page_text=lambda url: (
                url,
                "This job posting is no longer accepting applications.",
            )
        )
        listing = normalize_job_board_result(result(listing_url))

        activity = JobBoardActivityVerifier(reader).check(listing)

        self.assertEqual(activity.state, "closed")
        self.assertEqual(activity.code, "kariyer_closed_marker")
        self.assertEqual(activity.checked_url, listing_url)
        self.assertIsNotNone(activity.checked_at)

    def test_absence_of_closed_marker_does_not_imply_active(self) -> None:
        listing_url = (
            "https://www.kariyer.net/is-ilani/acme-ai-engineer-4034271"
        )
        reader = SimpleNamespace(
            read_page_text=lambda url: (url, "AI Engineer About Job")
        )
        listing = normalize_job_board_result(result(listing_url))

        activity = JobBoardActivityVerifier(reader).check(listing)

        self.assertEqual(activity.state, "unknown")
        self.assertEqual(activity.code, "no_closed_marker")
        self.assertIsNotNone(activity.checked_at)

    def test_activity_redirect_to_different_job_is_closed(self) -> None:
        listing_url = (
            "https://www.kariyer.net/is-ilani/acme-ai-engineer-4034271"
        )
        reader = SimpleNamespace(
            read_page_text=lambda url: (
                "https://www.kariyer.net/is-ilani/other-job-9999999",
                "This job posting is no longer accepting applications.",
            )
        )
        listing = normalize_job_board_result(result(listing_url))

        activity = JobBoardActivityVerifier(reader).check(listing)

        self.assertEqual(activity.state, "closed")
        self.assertEqual(activity.code, "redirected_to_different_job")
        self.assertIsNotNone(activity.checked_at)

    def test_supported_job_board_urls_are_canonicalized(self) -> None:
        cases = (
            (
                "linkedin",
                "1234567890",
                "https://tr.linkedin.com/jobs/view/ai-engineer-1234567890?trk=x",
                "https://www.linkedin.com/jobs/view/1234567890",
            ),
            (
                "kariyer",
                "3611956",
                "https://www.kariyer.net/is-ilani/acme-ai-engineer-3611956?ref=x",
                "https://www.kariyer.net/is-ilani/acme-ai-engineer-3611956",
            ),
            (
                "indeed",
                "abc_12345",
                "https://tr.indeed.com/viewjob?jk=abc_12345&from=search",
                "https://tr.indeed.com/viewjob?jk=abc_12345",
            ),
            (
                "glassdoor",
                "987654321",
                "https://www.glassdoor.com/job-listing/ai-engineer-acme.htm?jl=987654321&src=GD_JOB_AD",
                "https://www.glassdoor.com/job-listing/ai-engineer-acme.htm?jl=987654321",
            ),
        )
        for provider, external_id, url, canonical in cases:
            with self.subTest(provider=provider):
                listing = normalize_job_board_result(result(url))
                self.assertEqual(listing.provider, provider)
                self.assertEqual(listing.external_id, external_id)
                self.assertEqual(listing.listing_url, canonical)

    def test_spoofed_or_non_job_urls_are_rejected(self) -> None:
        urls = (
            "https://linkedin.com.evil.example/jobs/view/123456",
            "https://www.linkedin.com/company/acme",
            "https://www.kariyer.net/firma-profil/acme",
            "https://tr.indeed.com/viewjob?from=search",
            "https://www.glassdoor.com/Reviews/acme.htm",
        )
        for url in urls:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    normalize_job_board_result(result(url))

    def test_connector_filters_unrelated_and_duplicate_results(self) -> None:
        linkedin = result(
            "https://www.linkedin.com/jobs/view/123456",
            position=1,
        )
        duplicate = result(
            "https://tr.linkedin.com/jobs/view/role-123456?trk=duplicate",
            position=2,
        )
        unrelated = result("https://example.com/jobs/42", position=3)
        client = SimpleNamespace(
            search=lambda query, max_results: [
                linkedin,
                duplicate,
                unrelated,
            ]
        )

        discovery = JobBoardSearchConnector(client).search(
            "ACME TEKNOLOJİ A.Ş.",
            max_results=3,
        )

        self.assertIn('"ACME TEKNOLOJİ"', discovery.query)
        self.assertEqual(discovery.raw_result_count, 3)
        self.assertEqual(len(discovery.listings), 1)
        self.assertEqual(discovery.listings[0].provider, "linkedin")

    def test_query_is_bounded_and_uses_supported_sites(self) -> None:
        query = build_job_board_query("ACME TEKNOLOJİ A.Ş.")
        self.assertLessEqual(len(query), 500)
        self.assertIn("linkedin.com/jobs/view", query)
        self.assertIn("kariyer.net/is-ilani", query)
        self.assertIn("tr.indeed.com/viewjob", query)
        self.assertIn("glassdoor.com/job-listing", query)

    def test_query_identity_cannot_break_quoted_group(self) -> None:
        query = build_job_board_query('ACME "OR" site:evil.example')
        self.assertNotIn('"ACME "', query)
        self.assertIn('"ACME OR site:evil.example"', query)

    def test_ambiguous_single_word_brand_uses_legal_identity(self) -> None:
        self.assertEqual(
            choose_job_board_identity(
                "4ARC YAZILIM TEKNOLOJİLERİ A.Ş.",
                "4ARC",
            ),
            "4ARC YAZILIM TEKNOLOJİLERİ",
        )
        self.assertEqual(
            choose_job_board_identity(
                "ABE TEKNOLOJİ MÜHENDİSLİK HİZMETLERİ A.Ş.",
                "ABE Teknoloji",
            ),
            "ABE Teknoloji",
        )

    def test_company_identity_filter_rejects_search_collisions(self) -> None:
        unrelated = normalize_job_board_result(
            SearchResult(
                title="Accounting Manager at Active System",
                url="https://www.linkedin.com/jobs/view/4417373125",
                snippet="Remote role in Brazil",
                position=1,
            )
        )
        related = normalize_job_board_result(
            SearchResult(
                title="Python Developer - ABE Teknoloji",
                url="https://www.linkedin.com/jobs/view/2765864334",
                snippet="İstanbul, Türkiye",
                position=1,
            )
        )

        self.assertFalse(
            listing_matches_company(
                unrelated,
                ("4ARC YAZILIM TEKNOLOJİLERİ A.Ş.", "4ARC"),
            )
        )
        self.assertTrue(
            listing_matches_company(
                related,
                (
                    "ABE TEKNOLOJİ MÜHENDİSLİK HİZMETLERİ A.Ş.",
                    "ABE Teknoloji",
                ),
            )
        )

    def test_search_result_metadata_is_attached_to_listing(self) -> None:
        listing = normalize_job_board_result(
            SearchResult(
                title=(
                    "Python Geliştirici - ABE Teknoloji — "
                    "İstanbul, Türkiye - LinkedIn"
                ),
                url="https://www.linkedin.com/jobs/view/2765864334",
                snippet="Hibrit · Tam Zamanlı · 2 gün önce",
                position=1,
            )
        )

        self.assertEqual(listing.location, "İstanbul, Türkiye")
        self.assertEqual(listing.work_mode, "hybrid")
        self.assertEqual(listing.employment_type, "full_time")
        self.assertIsNotNone(listing.published_at)
        self.assertEqual(listing.published_precision, "day")


if __name__ == "__main__":
    unittest.main()
