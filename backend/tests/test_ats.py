import unittest

from app.ats import ATSFetchError, PublicATSClient, _board_slug


class ATSTests(unittest.TestCase):
    def test_greenhouse_normalization_removes_active_html(self) -> None:
        jobs = PublicATSClient._greenhouse({
            "jobs": [{
                "id": 42,
                "title": "AI Engineer",
                "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/42",
                "location": {"name": "Istanbul"},
                "departments": [{"name": "Engineering"}],
                "first_published": "2026-09-19T10:00:00Z",
                "content": "<p>Build agents</p><script>steal()</script>",
            }]
        })
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].external_id, "42")
        self.assertEqual(jobs[0].description_text, "Build agents")
        self.assertEqual(len(jobs[0].content_hash), 64)

    def test_lever_and_ashby_normalization(self) -> None:
        lever = PublicATSClient._lever([{
            "id": "lever-1",
            "text": "ML Engineer",
            "hostedUrl": "https://jobs.lever.co/acme/lever-1",
            "applyUrl": "https://jobs.lever.co/acme/lever-1/apply",
            "descriptionPlain": "Train models",
            "categories": {
                "location": "Remote",
                "team": "AI",
                "commitment": "Full-time",
            },
            "workplaceType": "remote",
            "createdAt": 1_790_000_000_000,
        }])
        ashby = PublicATSClient._ashby({"jobs": [{
            "id": "ashby-1",
            "title": "Data Scientist",
            "jobUrl": "https://jobs.ashbyhq.com/acme/ashby-1",
            "applyUrl": "https://jobs.ashbyhq.com/acme/ashby-1/apply",
            "descriptionPlain": "Analyze data",
            "location": "Istanbul",
            "isRemote": False,
            "publishedAt": "2026-09-19T10:00:00Z",
        }]})
        self.assertTrue(lever[0].is_remote)
        self.assertEqual(lever[0].department, "AI")
        self.assertFalse(ashby[0].is_remote)
        self.assertEqual(ashby[0].title, "Data Scientist")

    def test_source_host_and_type_must_match(self) -> None:
        self.assertEqual(
            _board_slug("https://jobs.lever.co/acme", "lever"),
            "acme",
        )
        with self.assertRaises(ATSFetchError):
            _board_slug("https://jobs.lever.co.evil.org/acme", "lever")
        with self.assertRaises(ATSFetchError):
            _board_slug("https://jobs.lever.co/acme", "ashby")

    def test_oversized_provider_list_is_rejected(self) -> None:
        with self.assertRaisesRegex(ATSFetchError, "ATS fetch failed"):
            PublicATSClient._lever([{}] * 1_001)


if __name__ == "__main__":
    unittest.main()
