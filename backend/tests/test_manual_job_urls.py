import pytest

from app.discovery.schemas import SearchResult
from app.manual_job_import import normalize_manual_job_result


@pytest.mark.parametrize(
    ("provider", "url"),
    [
        ("techcareer", "https://www.techcareer.net/jobs/detail/python-developer-12345"),
        ("yenibiris", "https://www.yenibiris.com/is-ilani/python-developer/12345"),
        ("secretcv", "https://www.secretcv.com/acme/python-developer-is-ilanlari-12345"),
        ("toptalent", "https://toptalent.co/python-developer-12345"),
        ("weworkremotely", "https://weworkremotely.com/remote-jobs/acme-python-developer"),
        ("remoteok", "https://remoteok.com/remote-jobs/12345-python-developer"),
        ("remotive", "https://remotive.com/remote-jobs/software-dev/python-developer"),
        ("jobicy", "https://jobicy.com/jobs-offers/python-developer"),
    ],
)
def test_manual_listing_urls_are_allowlisted(provider: str, url: str) -> None:
    listing = normalize_manual_job_result(
        SearchResult(title="Python Developer", url=url, snippet="", position=1)
    )
    assert listing.provider == provider
    assert listing.listing_url.startswith("https://")
    assert listing.external_id


def test_manual_listing_rejects_search_pages_and_unknown_hosts() -> None:
    for url in (
        "https://remoteok.com/remote-jobs",
        "https://evil.example/remote-jobs/python-developer",
        "https://toptalent.co/insan-kaynaklari-platformu",
        "https://toptalent.co/assessment-aday-degerlendirme-ve-ise-alim-testleri",
        "https://toptalent.co/isveren",
        "https://toptalent.co/ucretsiz-is-ilani-ver",
        "https://toptalent.co/sirket",
        "https://toptalent.co/awards",
        "https://toptalent.co/yetenek-testleri",
        "https://toptalent.co/online-egitim-sertifika-programlari",
        "https://toptalent.co/etkinlikler",
    ):
        with pytest.raises(ValueError):
            normalize_manual_job_result(
                SearchResult(title="Python Developer", url=url, snippet="", position=1)
            )


def test_manual_listing_rejects_obvious_placeholder_identifier() -> None:
    with pytest.raises(ValueError):
        normalize_manual_job_result(
            SearchResult(
                title="Machine Learning Engineer",
                url="https://tr.indeed.com/viewjob?jk=123456789abcdef0",
                snippet="",
                position=1,
            )
        )
