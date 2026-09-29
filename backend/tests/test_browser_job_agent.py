import pytest
from unittest.mock import MagicMock, patch

import app.browser_job_agent as browser_agent_module

from app.browser_job_agent import (
    BrowserAgentError,
    _card_fields,
    _defer_blocked_source,
    _is_safe_filter_control,
    _page_access_state,
    _source_cooldown_remaining,
    _wait_for_login_handoff,
    browser_agent_timing_from_env,
    browser_job_matches_scope,
    browser_location_matches,
    build_linkedin_search_url,
    canonical_linkedin_job_url,
    infer_browser_work_mode,
    normalize_browser_card_fields,
    normalize_browser_listing,
)


def test_browser_agent_timing_is_configurable_and_bounded(monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_AGENT_PAGE_SETTLE_SECONDS", "3")
    monkeypatch.setenv("BROWSER_AGENT_SOURCE_DELAY_SECONDS", "12")
    monkeypatch.setenv("BROWSER_AGENT_DETAIL_DELAY_SECONDS", "2.5")
    monkeypatch.setenv("BROWSER_AGENT_LOGIN_WAIT_SECONDS", "180")
    monkeypatch.setenv("BROWSER_AGENT_RUN_COOLDOWN_SECONDS", "1200")
    monkeypatch.setenv("BROWSER_AGENT_BLOCKED_SOURCE_COOLDOWN_SECONDS", "7200")

    timing = browser_agent_timing_from_env()

    assert timing.page_settle_seconds == 3
    assert timing.source_delay_seconds == 12
    assert timing.detail_delay_seconds == 2.5
    assert timing.login_wait_seconds == 180
    assert timing.run_cooldown_seconds == 1200
    assert timing.blocked_source_cooldown_seconds == 7200

    monkeypatch.setenv("BROWSER_AGENT_SOURCE_DELAY_SECONDS", "-1")
    with pytest.raises(BrowserAgentError, match="browser_agent_config_invalid"):
        browser_agent_timing_from_env()


def test_login_handoff_waits_for_manual_completion() -> None:
    page = MagicMock()
    with patch(
        "app.browser_job_agent._page_access_state",
        side_effect=["login_required", "login_required", "ok"],
    ):
        assert _wait_for_login_handoff(page, 10)

    assert [item.args[0] for item in page.wait_for_timeout.call_args_list] == [
        2_000,
        2_000,
    ]


def test_login_handoff_stops_after_bounded_timeout() -> None:
    page = MagicMock()
    with patch(
        "app.browser_job_agent._page_access_state",
        return_value="login_required",
    ):
        assert not _wait_for_login_handoff(page, 5)

    assert [item.args[0] for item in page.wait_for_timeout.call_args_list] == [
        2_000,
        2_000,
        1_000,
    ]


def test_access_state_does_not_treat_header_login_link_as_wall() -> None:
    page = MagicMock()
    page.url = "https://tr.indeed.com/jobs?q=engineer"
    page.locator.return_value.inner_text.return_value = (
        "İş ilanları\nGiriş yap\nSoftware Engineer"
    )

    assert _page_access_state(page, 200) == "ok"


def test_access_state_distinguishes_login_rate_limit_and_challenge() -> None:
    page = MagicMock()
    page.url = "https://www.linkedin.com/login"
    page.locator.return_value.inner_text.return_value = "Welcome"
    assert _page_access_state(page, 200) == "login_required"

    page.url = "https://www.linkedin.com/jobs/search/"
    page.locator.return_value.inner_text.return_value = "Too many requests"
    assert _page_access_state(page, 429) == "rate_limited"

    page.locator.return_value.inner_text.return_value = "Verify you are human"
    assert _page_access_state(page, 200) == "blocked"


def test_blocked_source_uses_a_bounded_process_cooldown(monkeypatch) -> None:
    monkeypatch.setattr(browser_agent_module, "_SOURCE_BLOCKED_UNTIL", {})
    now = [100.0]
    monkeypatch.setattr(browser_agent_module.time, "monotonic", lambda: now[0])

    _defer_blocked_source("linkedin", 60)
    now[0] = 130.0
    assert _source_cooldown_remaining("linkedin") == 30

    now[0] = 161.0
    assert _source_cooldown_remaining("linkedin") == 0


def test_collection_gate_prevents_overlap_and_immediate_repeat(monkeypatch) -> None:
    monkeypatch.setattr(browser_agent_module, "_COLLECTION_ACTIVE", False)
    monkeypatch.setattr(
        browser_agent_module,
        "_COLLECTION_LAST_FINISHED_AT",
        0.0,
    )
    monkeypatch.setattr(browser_agent_module.time, "monotonic", lambda: 100.0)

    browser_agent_module._begin_browser_collection(60)
    with pytest.raises(BrowserAgentError, match="browser_agent_already_running"):
        browser_agent_module._begin_browser_collection(60)
    browser_agent_module._finish_browser_collection()

    with pytest.raises(BrowserAgentError, match="browser_agent_cooldown_active"):
        browser_agent_module._begin_browser_collection(60)


def test_local_agent_only_sees_safe_search_and_filter_controls() -> None:
    assert _is_safe_filter_control("Pozisyon ara")
    assert _is_safe_filter_control("Remote filtresi")
    assert not _is_safe_filter_control("Şimdi başvur")
    assert not _is_safe_filter_control("CV yükle")
    assert not _is_safe_filter_control("Sign in to your account")


def test_linkedin_search_url_is_server_built_and_bounded() -> None:
    url = build_linkedin_search_url("AI Engineer", "İstanbul, Türkiye")
    assert url.startswith("https://www.linkedin.com/jobs/search/?")
    assert "keywords=AI+Engineer" in url
    assert "location=%C4%B0stanbul%2C+T%C3%BCrkiye" in url
    assert "f_TPR=r604800" in url


def test_linkedin_job_url_is_canonical_and_drops_tracking() -> None:
    assert canonical_linkedin_job_url(
        "https://tr.linkedin.com/jobs/view/software-engineer-4471899349?trackingId=secret"
    ) == "https://www.linkedin.com/jobs/view/4471899349"


def test_linkedin_job_url_accepts_relative_listing_path() -> None:
    assert canonical_linkedin_job_url(
        "/jobs/view/software-engineer-4471899349?trackingId=secret"
    ) == "https://www.linkedin.com/jobs/view/4471899349"


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/jobs/view/4471899349",
        "javascript:alert(1)",
        "https://www.linkedin.com/jobs/search/?keywords=engineer",
    ],
)
def test_linkedin_job_url_rejects_non_listing_targets(url: str) -> None:
    assert canonical_linkedin_job_url(url) is None


def test_card_fields_extracts_bounded_visible_text() -> None:
    title, company, location = _card_fields(
        "AI Engineer\nAcme Teknoloji\nİstanbul, Türkiye\nPromoted",
        "AI Engineer",
    )
    assert (title, company, location) == (
        "AI Engineer",
        "Acme Teknoloji",
        "İstanbul, Türkiye",
    )


def test_browser_listing_normalizes_indeed_click_link() -> None:
    assert normalize_browser_listing(
        provider="indeed",
        page_url="https://tr.indeed.com/jobs?q=engineer",
        href="/rc/clk?jk=abcde12345&from=search",
        title="Software Engineer",
    ) == "https://tr.indeed.com/viewjob?jk=abcde12345"


def test_indeed_accessibility_chrome_is_not_saved_as_job_data() -> None:
    title, company, location = _card_fields(
        "AI Engineering Manager ile ilgili tüm ayrıntılar\n"
        "AI Engineering Manager\nKolayca başvur",
        "AI Engineering Manager ile ilgili tüm ayrıntılar",
    )
    assert title == "AI Engineering Manager"
    assert company == "İşveren adı doğrulanmadı"
    assert location is None


def test_existing_browser_fields_are_safely_repaired_for_display() -> None:
    assert normalize_browser_card_fields(
        title="Yapay Zeka Stajyeri ile ilgili tüm ayrıntılar",
        company_name="Yapay Zeka Stajyeri",
        location="Kolayca başvur",
    ) == (
        "Yapay Zeka Stajyeri",
        "İşveren adı doğrulanmadı",
        None,
    )


def test_browser_listing_rejects_obvious_fixture_identifier() -> None:
    assert normalize_browser_listing(
        provider="indeed",
        page_url="https://tr.indeed.com/jobs?q=engineer",
        href="/viewjob?jk=123456789abcdef0",
        title="Machine Learning Engineer",
    ) is None


def test_browser_listing_rejects_cross_provider_and_external_hosts() -> None:
    assert normalize_browser_listing(
        provider="kariyer",
        page_url="https://www.kariyer.net/is-ilanlari/yazilim",
        href="https://evil.example/is-ilani/example-12345",
        title="Software Engineer",
    ) is None
    assert normalize_browser_listing(
        provider="kariyer",
        page_url="https://www.kariyer.net/is-ilanlari/yazilim",
        href="https://www.linkedin.com/jobs/view/example-4471899349",
        title="Software Engineer",
    ) is None


@pytest.mark.parametrize(
    "href",
    [
        "/insan-kaynaklari-platformu",
        "/assessment-aday-degerlendirme-ve-ise-alim-testleri",
        "/isveren",
        "/ucretsiz-is-ilani-ver",
        "/sirket",
        "/awards",
        "/yetenek-testleri",
        "/online-egitim-sertifika-programlari",
        "/etkinlikler",
    ],
)
def test_toptalent_navigation_pages_are_not_listings(href: str) -> None:
    assert normalize_browser_listing(
        provider="toptalent",
        page_url="https://toptalent.co/is-ilanlari",
        href=href,
        title="Navigation",
    ) is None


def test_toptalent_real_listing_url_is_accepted() -> None:
    assert normalize_browser_listing(
        provider="toptalent",
        page_url="https://toptalent.co/is-ilanlari",
        href="/akcadag-grup-satin-alma-uzmani-121728",
        title="Satın Alma Uzmanı",
    ) == "https://toptalent.co/akcadag-grup-satin-alma-uzmani-121728"


def test_browser_scope_distinguishes_city_and_work_mode() -> None:
    assert browser_location_matches("İstanbul, Türkiye", "İstanbul Anadolu")
    assert not browser_location_matches("İstanbul, Türkiye", "Konya")

    assert infer_browser_work_mode(
        provider="toptalent",
        text="Acme İstanbul Avrupa",
        location="İstanbul Avrupa",
    ) == "onsite"
    assert infer_browser_work_mode(
        provider="linkedin",
        text="Software Engineer · Hibrit",
        location="İstanbul",
    ) == "hybrid"
    assert infer_browser_work_mode(
        provider="remotive",
        text="Software Engineer",
        location=None,
    ) == "remote"

    assert browser_job_matches_scope(
        requested_location="İstanbul",
        allowed_work_modes={"onsite", "hybrid"},
        actual_location="İstanbul Avrupa",
        work_mode="onsite",
    )
    assert not browser_job_matches_scope(
        requested_location="İstanbul",
        allowed_work_modes={"onsite", "hybrid"},
        actual_location="Konya",
        work_mode="onsite",
    )
    assert browser_job_matches_scope(
        requested_location="İstanbul",
        allowed_work_modes={"remote"},
        actual_location=None,
        work_mode="remote",
    )
