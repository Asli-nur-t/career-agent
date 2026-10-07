import pytest
from unittest.mock import MagicMock, patch

import app.browser_job_agent as browser_agent_module

from app.browser_access_graph import AccessObservation
from app.browser_job_agent import (
    BrowserAgentError,
    _card_fields,
    _defer_blocked_source,
    _is_safe_filter_control,
    _page_access_state,
    _page_access_observation,
    _resolve_access_with_graph,
    _resolve_initial_access,
    _source_cooldown_remaining,
    _wait_for_human_handoff,
    browser_access_graph_providers_from_env,
    browser_agent_disabled_providers_from_env,
    browser_agent_timing_from_env,
    browser_job_matches_scope,
    browser_location_matches,
    browser_provider_matches_work_modes,
    browser_role_matches,
    build_linkedin_search_url,
    canonical_linkedin_job_url,
    infer_browser_work_mode,
    normalize_browser_card_fields,
    normalize_browser_listing,
)
from app.job_roles import ROLE_ALIAS_GROUPS, ROLE_CATALOG


def test_browser_agent_timing_is_configurable_and_bounded(monkeypatch) -> None:
    monkeypatch.setenv("BROWSER_AGENT_PAGE_SETTLE_SECONDS", "3")
    monkeypatch.setenv("BROWSER_AGENT_SOURCE_DELAY_SECONDS", "12")
    monkeypatch.setenv("BROWSER_AGENT_DETAIL_DELAY_SECONDS", "2.5")
    monkeypatch.setenv("BROWSER_AGENT_LOGIN_WAIT_SECONDS", "180")
    monkeypatch.setenv("BROWSER_AGENT_HUMAN_CHECK_WAIT_SECONDS", "420")
    monkeypatch.setenv("BROWSER_AGENT_HUMAN_CHECK_SETTLE_SECONDS", "11")
    monkeypatch.setenv("BROWSER_AGENT_RUN_COOLDOWN_SECONDS", "1200")
    monkeypatch.setenv("BROWSER_AGENT_BLOCKED_SOURCE_COOLDOWN_SECONDS", "7200")

    timing = browser_agent_timing_from_env()

    assert timing.page_settle_seconds == 3
    assert timing.source_delay_seconds == 12
    assert timing.detail_delay_seconds == 2.5
    assert timing.login_wait_seconds == 180
    assert timing.challenge_wait_seconds == 420
    assert timing.handoff_settle_seconds == 11
    assert timing.run_cooldown_seconds == 1200
    assert timing.blocked_source_cooldown_seconds == 7200

    monkeypatch.setenv("BROWSER_AGENT_SOURCE_DELAY_SECONDS", "-1")
    with pytest.raises(BrowserAgentError, match="browser_agent_config_invalid"):
        browser_agent_timing_from_env()


def test_human_handoff_waits_for_manual_login_completion() -> None:
    page = MagicMock()
    with patch(
        "app.browser_job_agent._page_access_observation",
        side_effect=[
            AccessObservation("login_required", "url_login:/login", "https://example/login"),
            AccessObservation("login_required", "url_login:/login", "https://example/login"),
            AccessObservation("ok", "page_ready", "https://example/jobs"),
        ],
    ):
        assert _wait_for_human_handoff(page, 10)

    assert [item.args[0] for item in page.wait_for_timeout.call_args_list] == [
        2_000,
        2_000,
    ]


def test_human_handoff_stops_after_bounded_timeout() -> None:
    page = MagicMock()
    with patch(
        "app.browser_job_agent._page_access_observation",
        return_value=AccessObservation(
            "login_required", "url_login:/login", "https://example/login"
        ),
    ):
        assert not _wait_for_human_handoff(page, 5)

    assert [item.args[0] for item in page.wait_for_timeout.call_args_list] == [
        2_000,
        2_000,
        1_000,
    ]


def test_human_handoff_waits_on_challenge_without_navigation() -> None:
    page = MagicMock()
    with patch(
        "app.browser_job_agent._page_access_observation",
        side_effect=[
            AccessObservation(
                "challenge_required", "body_challenge:captcha", "https://example/jobs"
            ),
            AccessObservation(
                "challenge_required", "body_challenge:captcha", "https://example/jobs"
            ),
            AccessObservation("ok", "page_ready", "https://example/jobs"),
        ],
    ):
        assert _wait_for_human_handoff(page, 10)

    page.goto.assert_not_called()
    assert [item.args[0] for item in page.wait_for_timeout.call_args_list] == [
        2_000,
        2_000,
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
    assert _page_access_state(page, 200) == "challenge_required"

    page.locator.return_value.inner_text.return_value = "Access denied"
    assert _page_access_state(page, 403) == "blocked"


def test_access_observation_reports_the_static_trigger_only() -> None:
    page = MagicMock()
    page.url = "https://tr.indeed.com/jobs?q=private-search"
    page.locator.return_value.inner_text.return_value = "Verify you are human"

    observation = _page_access_observation(page, 200)

    assert observation.state == "challenge_required"
    assert observation.reason == "body_challenge:verify you are human"
    assert "private-search" not in observation.reason


def test_resolved_challenge_does_not_reload_the_search_page() -> None:
    page = MagicMock()
    target_url = "https://tr.indeed.com/jobs?q=backend"
    page.url = target_url
    timing = browser_agent_timing_from_env()

    with (
        patch(
            "app.browser_job_agent._wait_for_human_handoff",
            return_value=True,
        ),
        patch("app.browser_job_agent._page_access_state", return_value="ok"),
        patch("app.browser_job_agent._wait_page") as wait,
    ):
        state = _resolve_initial_access(
            page,
            provider="indeed",
            target_url=target_url,
            access_state="challenge_required",
            timing=timing,
        )

    assert state == "ok"
    page.goto.assert_not_called()
    wait.assert_called_once_with(page, timing.handoff_settle_seconds)


def test_access_graph_resolution_keeps_live_page_outside_state() -> None:
    page = MagicMock()
    target_url = "https://tr.indeed.com/jobs?q=backend"
    page.url = target_url
    page.goto.return_value.status = 200
    timing = browser_agent_timing_from_env()

    with (
        patch(
            "app.browser_job_agent._wait_for_human_access",
            return_value=AccessObservation("ok", "page_ready", target_url),
        ),
        patch(
            "app.browser_job_agent._page_access_observation",
            side_effect=[
                AccessObservation(
                    "challenge_required",
                    "body_challenge:captcha",
                    target_url,
                ),
                AccessObservation("ok", "page_ready", target_url),
            ],
        ),
        patch("app.browser_job_agent._wait_page"),
    ):
        result = _resolve_access_with_graph(
            page,
            provider="indeed",
            target_url=target_url,
            timing=timing,
        )

    assert result.ready
    assert result.access_state == "ok"
    assert result.transition_trace == (
        "fetch_page",
        "classify:challenge_required:body_challenge:captcha",
        "wait_for_human",
        "classify:ok:page_ready",
        "proceed",
    )
    page.goto.assert_called_once_with(
        target_url,
        wait_until="domcontentloaded",
        timeout=25_000,
    )


def test_resolved_challenge_returns_to_search_at_most_once_when_needed() -> None:
    page = MagicMock()
    page.url = "https://tr.indeed.com/"
    page.goto.return_value.status = 200
    target_url = "https://tr.indeed.com/jobs?q=backend"
    timing = browser_agent_timing_from_env()

    with (
        patch(
            "app.browser_job_agent._wait_for_human_handoff",
            return_value=True,
        ),
        patch("app.browser_job_agent._page_access_state", return_value="ok"),
        patch("app.browser_job_agent._wait_page"),
    ):
        state = _resolve_initial_access(
            page,
            provider="indeed",
            target_url=target_url,
            access_state="challenge_required",
            timing=timing,
        )

    assert state == "ok"
    page.goto.assert_called_once_with(
        target_url,
        wait_until="domcontentloaded",
        timeout=25_000,
    )


def test_disabled_provider_env_is_allowlisted(monkeypatch) -> None:
    monkeypatch.setenv(
        "BROWSER_AGENT_DISABLED_PROVIDERS",
        "yenibiris, glassdoor",
    )
    assert browser_agent_disabled_providers_from_env() == frozenset(
        {"yenibiris", "glassdoor"}
    )

    monkeypatch.setenv("BROWSER_AGENT_DISABLED_PROVIDERS", "unknown-site")
    with pytest.raises(BrowserAgentError, match="browser_agent_config_invalid"):
        browser_agent_disabled_providers_from_env()


def test_access_graph_provider_pilot_is_allowlisted(monkeypatch) -> None:
    monkeypatch.delenv("BROWSER_AGENT_ACCESS_GRAPH_PROVIDERS", raising=False)
    assert browser_access_graph_providers_from_env() == frozenset({"indeed"})

    monkeypatch.setenv(
        "BROWSER_AGENT_ACCESS_GRAPH_PROVIDERS",
        "indeed,glassdoor",
    )
    assert browser_access_graph_providers_from_env() == frozenset(
        {"indeed", "glassdoor"}
    )

    monkeypatch.setenv("BROWSER_AGENT_ACCESS_GRAPH_PROVIDERS", "evil-site")
    with pytest.raises(BrowserAgentError, match="browser_agent_config_invalid"):
        browser_access_graph_providers_from_env()


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


def test_browser_role_filter_rejects_unrelated_search_noise() -> None:
    assert browser_role_matches("AI Engineer", "AI Engineering Manager")
    assert browser_role_matches("iş analisti", "Grátis - İş Analisti")
    assert browser_role_matches("Machine Learning Engineer", "ML Engineer")
    assert not browser_role_matches("AI Engineer", "Okul Hekimi")
    assert not browser_role_matches("iş analisti", "Proje Lideri")


def test_shared_role_catalog_drives_browser_alias_matching() -> None:
    names = [definition.name for definition in ROLE_CATALOG]

    assert len(names) == len(set(names))
    assert [group[0] for group in ROLE_ALIAS_GROUPS] == names
    assert browser_role_matches("Computer Vision Engineer", "CV Engineer")
    assert browser_role_matches("Kubernetes Engineer", "K8s Engineer")
    assert browser_role_matches("SAP Consultant", "SAP Danışmanı")


def test_remote_only_provider_is_skipped_when_remote_is_not_selected() -> None:
    assert browser_provider_matches_work_modes(
        "remoteok",
        {"remote", "hybrid"},
    )
    assert not browser_provider_matches_work_modes(
        "remoteok",
        {"hybrid", "onsite"},
    )
    assert browser_provider_matches_work_modes(
        "kariyer",
        {"hybrid", "onsite"},
    )
    browser_access_graph_providers_from_env,
