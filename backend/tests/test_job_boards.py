import unittest
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from app.discovery.schemas import SearchResult
from app.discovery.web_verifier import PageEvidence, SafeWebsiteVerifier
from app.job_boards import (
    JobBoardActivityVerifier,
    JobBoardSearchConnector,
    build_job_board_query,
    build_profile_job_queries,
    choose_job_board_identity,
    infer_job_board_company,
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
    def test_profile_queries_are_bounded_and_tiered(self) -> None:
        queries = build_profile_job_queries(
            (
                ("AI Engineer", "Machine Learning Engineer"),
                ("Backend Engineer", 'Python "OR" site:evil.example'),
                ("Mobile Developer",),
            ),
            preferred_locations=("İstanbul",),
            preferred_remote_locations=("Türkiye", "Turkey"),
            remote_allowed=True,
            published_after=date(2026, 8, 22),
            max_queries=3,
        )

        self.assertEqual(len(queries), 3)
        self.assertTrue(all(len(query) <= 500 for query in queries))
        self.assertIn('"AI Engineer" OR "Machine Learning Engineer"', queries[0])
        self.assertNotIn('("İstanbul")', queries[0])
        self.assertNotIn("after:2026-08-22", queries[0])
        self.assertIn('"Python OR site:evil.example"', queries[1])
        self.assertTrue(all("jobs.lever.co" in query for query in queries))

        all_sources = build_profile_job_queries(
            (("AI Engineer",), ("Backend Engineer",), ("Mobile Developer",)),
            preferred_locations=("İstanbul",),
            max_queries=6,
        )
        self.assertEqual(len(all_sources), 6)
        self.assertTrue(
            all("jobs.ashbyhq.com" in query for query in all_sources[:3])
        )
        self.assertTrue(
            all("linkedin.com/jobs/view" in query for query in all_sources[3:])
        )
        self.assertTrue(
            all('("İstanbul")' in query for query in all_sources[3:])
        )

    def test_activity_provider_filter_verifies_only_official_ats(self) -> None:
        results = [
            SearchResult(
                title="AI Engineer",
                url="https://jobs.lever.co/acme/lever-job-123",
                snippet="Remote Turkey",
                position=1,
            ),
            SearchResult(
                title="AI Engineer - Acme - LinkedIn",
                url="https://www.linkedin.com/jobs/view/1234567890",
                snippet="İstanbul",
                position=2,
            ),
        ]
        calls: list[str] = []

        class Verifier:
            def check(self, listing):
                calls.append(listing.provider)
                return SimpleNamespace(
                    location="Remote - Turkey",
                    work_mode="remote",
                    employment_type="full_time",
                    published_at=datetime.now(timezone.utc),
                    state="active",
                    code="lever_public_api_present",
                    checked_url=listing.listing_url,
                    checked_at=datetime.now(timezone.utc),
                )

        connector = JobBoardSearchConnector(
            SimpleNamespace(search=lambda query, max_results: results),
            activity_verifier=Verifier(),
            activity_providers=("greenhouse", "lever", "ashby"),
        )

        discovery = connector.search_query(
            '("AI Engineer") site:jobs.lever.co',
            max_results=5,
        )

        self.assertEqual(calls, ["lever"])
        self.assertEqual(discovery.listings[0].activity_state, "active")
        self.assertEqual(discovery.listings[1].activity_state, "unknown")

    def test_company_hint_is_extracted_from_linkedin_title(self) -> None:
        self.assertEqual(
            infer_job_board_company(
                "Python Developer - ABE Teknoloji — İstanbul, Türkiye - LinkedIn",
                "linkedin",
            ),
            "ABE Teknoloji",
        )
        self.assertEqual(
            infer_job_board_company(
                "Acme hiring AI Engineer in İstanbul - LinkedIn",
                "linkedin",
            ),
            "Acme",
        )
        for noisy_title in (
            "Python Developer with Odoo - remote work - LinkedIn Malta",
            "Python Backend Engineer - Remote Work | REF#295156 - LinkedIn",
            "Python Developer - Back End - €50k-€60k (Fully Remote) - LinkedIn",
        ):
            with self.subTest(title=noisy_title):
                self.assertIsNone(
                    infer_job_board_company(noisy_title, "linkedin")
                )

    def test_profile_connector_does_not_require_known_company(self) -> None:
        client = SimpleNamespace(
            search=lambda query, max_results: [
                SearchResult(
                    title="AI Engineer - New Employer - LinkedIn",
                    url="https://www.linkedin.com/jobs/view/99887766",
                    snippet="İstanbul · 2 gün önce",
                    position=1,
                )
            ]
        )

        discovery = JobBoardSearchConnector(client).search_query(
            '("AI Engineer") site:linkedin.com/jobs/view',
            max_results=5,
        )

        self.assertEqual(len(discovery.listings), 1)
        self.assertEqual(
            discovery.listings[0].company_name_raw,
            "New Employer",
        )

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

    def test_linkedin_closed_and_active_markers_are_checked(self) -> None:
        listing = normalize_job_board_result(
            result("https://www.linkedin.com/jobs/view/1234567890")
        )
        closed_reader = SimpleNamespace(
            read_page_text=lambda url: (
                url,
                "This job is no longer accepting applications.",
            )
        )
        active_reader = SimpleNamespace(
            read_page_text=lambda url: (url, "AI Engineer Easy Apply")
        )

        closed = JobBoardActivityVerifier(closed_reader).check(listing)
        active = JobBoardActivityVerifier(active_reader).check(listing)

        self.assertEqual(closed.state, "closed")
        self.assertEqual(closed.code, "linkedin_closed_marker")
        self.assertEqual(active.state, "active")
        self.assertEqual(active.code, "linkedin_active_marker")

    def test_current_json_ld_expiry_is_verified_active(self) -> None:
        listing_url = "https://www.linkedin.com/jobs/view/1234567890"
        listing = normalize_job_board_result(result(listing_url))
        date_posted = datetime.now(timezone.utc).date().isoformat()
        valid_through = (
            datetime.now(timezone.utc) + timedelta(days=365)
        ).date().isoformat()
        reader = SimpleNamespace(
            read_page_evidence=lambda url: PageEvidence(
                final_url=url,
                visible_text="AI Engineer",
                json_ld=(
                    '{"@context":"https://schema.org",'
                    '"@type":"JobPosting",'
                    f'"datePosted":"{date_posted}",'
                    f'"validThrough":"{valid_through}"}}',
                ),
            )
        )

        activity = JobBoardActivityVerifier(reader).check(listing)

        self.assertEqual(activity.state, "active")
        self.assertEqual(
            activity.code,
            "linkedin_valid_through_current",
        )

    def test_expired_json_ld_overrides_active_page_text(self) -> None:
        listing_url = "https://www.linkedin.com/jobs/view/1234567890"
        listing = normalize_job_board_result(result(listing_url))
        reader = SimpleNamespace(
            read_page_evidence=lambda url: PageEvidence(
                final_url=url,
                visible_text="AI Engineer Easy Apply",
                json_ld=(
                    '{"@type":"JobPosting",'
                    '"datePosted":"2020-01-01",'
                    '"validThrough":"2020-02-01"}',
                ),
            )
        )

        activity = JobBoardActivityVerifier(reader).check(listing)

        self.assertEqual(activity.state, "closed")
        self.assertEqual(
            activity.code,
            "linkedin_valid_through_expired",
        )

    def test_ambiguous_or_implausible_json_ld_is_not_trusted(self) -> None:
        listing_url = "https://www.linkedin.com/jobs/view/1234567890"
        listing = normalize_job_board_result(result(listing_url))
        reader = SimpleNamespace(
            read_page_evidence=lambda url: PageEvidence(
                final_url=url,
                visible_text="AI Engineer",
                json_ld=(
                    '[{"@type":"JobPosting",'
                    '"validThrough":"2020-02-01"},'
                    '{"@type":"JobPosting",'
                    '"validThrough":"2027-02-01"},'
                    '{"@type":"JobPosting",'
                    '"validThrough":"2099-01-01"}]',
                ),
            )
        )

        activity = JobBoardActivityVerifier(reader).check(listing)

        self.assertEqual(activity.state, "unknown")
        self.assertEqual(activity.code, "no_closed_marker")

    def test_safe_reader_exposes_bounded_json_ld_separately(self) -> None:
        verifier = SafeWebsiteVerifier()
        verifier._fetch_document = lambda url: (  # type: ignore[method-assign]
            url,
            "text/html",
            """
            <html><body><h1>AI Engineer</h1>
            <script type="application/ld+json">
            {"@type":"JobPosting","validThrough":"2027-01-01"}
            </script>
            <script>Easy Apply injected script text</script>
            </body></html>
            """,
        )

        evidence = verifier.read_page_evidence(
            "https://www.linkedin.com/jobs/view/1234567890"
        )

        self.assertIn("AI Engineer", evidence.visible_text)
        self.assertNotIn("Easy Apply", evidence.visible_text)
        self.assertEqual(len(evidence.json_ld), 1)
        self.assertIn("JobPosting", evidence.json_ld[0])

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

    def test_official_ats_api_presence_proves_activity(self) -> None:
        listing = normalize_job_board_result(
            result("https://jobs.lever.co/acme/lever-job-123")
        )
        published_at = datetime.now(timezone.utc) - timedelta(days=2)
        job = SimpleNamespace(
            external_id="lever-job-123",
            job_url="https://jobs.lever.co/acme/lever-job-123",
            apply_url="https://jobs.lever.co/acme/lever-job-123/apply",
            title="AI Engineer",
            location="Remote - Turkey",
            department="AI",
            employment_type="Full-time",
            description_text="Python RAG",
            is_remote=True,
            published_at=published_at,
            content_hash="a" * 64,
        )
        ats_reader = SimpleNamespace(
            fetch=lambda source_url, ats_type: [job]
        )
        page_reader = SimpleNamespace(
            read_page_text=lambda url: self.fail("page fetch not expected")
        )

        activity = JobBoardActivityVerifier(
            page_reader,
            ats_reader=ats_reader,
        ).check(listing)

        self.assertEqual(activity.state, "active")
        self.assertEqual(activity.code, "lever_public_api_present")
        self.assertEqual(activity.location, "Remote - Turkey")
        self.assertEqual(activity.work_mode, "remote")
        self.assertEqual(activity.published_at, published_at)

    def test_official_ats_api_absence_proves_closure(self) -> None:
        listing = normalize_job_board_result(
            result("https://jobs.ashbyhq.com/acme/ashby-job-123")
        )
        verifier = JobBoardActivityVerifier(
            SimpleNamespace(),
            ats_reader=SimpleNamespace(
                fetch=lambda source_url, ats_type: []
            ),
        )

        activity = verifier.check(listing)

        self.assertEqual(activity.state, "closed")
        self.assertEqual(activity.code, "ashby_public_api_missing")

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
            (
                "greenhouse",
                "1234567",
                "https://job-boards.greenhouse.io/acme/jobs/1234567?gh_src=x",
                "https://job-boards.greenhouse.io/acme/jobs/1234567",
            ),
            (
                "lever",
                "job-id-12345",
                "https://jobs.lever.co/acme/job-id-12345/apply?source=x",
                "https://jobs.lever.co/acme/job-id-12345",
            ),
            (
                "ashby",
                "job-id-67890",
                "https://jobs.ashbyhq.com/acme/job-id-67890/application",
                "https://jobs.ashbyhq.com/acme/job-id-67890",
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
