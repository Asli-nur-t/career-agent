from app.browser_access_graph import AccessObservation, BrowserAccessGraph


def test_access_graph_proceeds_without_handoff_on_ready_target() -> None:
    waits: list[str] = []
    navigations: list[str] = []
    graph = BrowserAccessGraph(
        fetch_page=lambda: AccessObservation(
            "ok", "page_ready", "https://tr.indeed.com/jobs?q=ai"
        ),
        wait_for_human=lambda state: waits.append(state) or AccessObservation(
            "ok", "page_ready", "https://tr.indeed.com/jobs?q=ai"
        ),
        navigate_to_target=lambda: navigations.append("navigate")
        or AccessObservation("ok", "page_ready", "https://tr.indeed.com/jobs?q=ai"),
        is_target_page=lambda: True,
    )

    result = graph.run(
        provider="indeed",
        target_url="https://tr.indeed.com/jobs?q=ai",
    )

    assert result["decision"] == "proceed"
    assert waits == []
    assert navigations == []
    assert result["transition_trace"] == [
        "fetch_page",
        "classify:ok:page_ready",
        "proceed",
    ]


def test_access_graph_waits_without_navigation_when_challenge_clears() -> None:
    waits: list[str] = []
    navigations: list[str] = []
    graph = BrowserAccessGraph(
        fetch_page=lambda: AccessObservation(
            "challenge_required",
            "body_challenge:captcha",
            "https://tr.indeed.com/jobs?q=ai",
        ),
        wait_for_human=lambda state: waits.append(state) or AccessObservation(
            "ok", "page_ready", "https://tr.indeed.com/jobs?q=ai"
        ),
        navigate_to_target=lambda: navigations.append("navigate")
        or AccessObservation("ok", "page_ready", "https://tr.indeed.com/jobs?q=ai"),
        is_target_page=lambda: True,
    )

    result = graph.run(
        provider="indeed",
        target_url="https://tr.indeed.com/jobs?q=ai",
    )

    assert result["decision"] == "proceed"
    assert waits == ["challenge_required"]
    assert navigations == []
    assert result["handoff_attempts"] == 1


def test_access_graph_defers_after_one_handoff_and_one_navigation() -> None:
    target_reached = [False]
    waits: list[str] = []
    navigations: list[str] = []

    def navigate():
        navigations.append("navigate")
        return AccessObservation(
            "challenge_required",
            "url_challenge:/challenge/",
            "https://tr.indeed.com/challenge/",
        )

    graph = BrowserAccessGraph(
        fetch_page=lambda: AccessObservation(
            "challenge_required",
            "body_challenge:captcha",
            "https://tr.indeed.com/challenge/",
        ),
        wait_for_human=lambda state: waits.append(state) or AccessObservation(
            "ok", "page_ready", "https://tr.indeed.com/"
        ),
        navigate_to_target=navigate,
        is_target_page=lambda: target_reached[0],
    )

    result = graph.run(
        provider="indeed",
        target_url="https://tr.indeed.com/jobs?q=ai",
    )

    assert result["decision"] == "defer"
    assert result["error_code"] == "browser_source_human_verification_timeout"
    assert waits == ["challenge_required"]
    assert navigations == ["navigate"]
    assert result["handoff_attempts"] == 1
    assert result["navigation_attempts"] == 1
    assert result["transition_trace"].count("wait_for_human") == 1
    assert result["transition_trace"].count("retry_navigation") == 1


def test_access_graph_fails_closed_on_target_mismatch() -> None:
    graph = BrowserAccessGraph(
        fetch_page=lambda: AccessObservation(
            "ok", "page_ready", "https://tr.indeed.com/"
        ),
        wait_for_human=lambda _state: AccessObservation(
            "ok", "page_ready", "https://tr.indeed.com/"
        ),
        navigate_to_target=lambda: AccessObservation(
            "ok", "page_ready", "https://tr.indeed.com/"
        ),
        is_target_page=lambda: False,
    )

    result = graph.run(
        provider="indeed",
        target_url="https://tr.indeed.com/jobs?q=ai",
    )

    assert result["decision"] == "defer"
    assert result["outcome"] == "failed"
    assert result["error_code"] == "browser_source_navigation_mismatch"
