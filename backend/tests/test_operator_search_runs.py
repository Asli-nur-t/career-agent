from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.operator_search_runs import (
    SearchCooldownActive,
    _search_fingerprint,
    normalize_search_age,
    normalize_search_locations,
    normalize_search_mode,
    normalize_search_roles,
    normalize_search_sources,
    normalize_search_work_modes,
    queue_profile_search,
)


def test_search_roles_are_cleaned_and_deduplicated() -> None:
    assert normalize_search_roles([
        "  AI Engineer  ",
        "ai engineer",
        'Backend "OR" site:example.invalid',
    ]) == [
        "AI Engineer",
        'Backend "OR" site:example.invalid',
    ]


def test_search_roles_are_bounded() -> None:
    try:
        normalize_search_roles([f"Role {index}" for index in range(11)])
    except ValueError as error:
        assert str(error) == "search_roles_invalid"
    else:
        raise AssertionError("Unbounded role list was accepted")


def test_search_mode_is_allowlisted() -> None:
    assert normalize_search_mode(" QUICK ") == "quick"
    try:
        normalize_search_mode("unlimited")
    except ValueError as error:
        assert str(error) == "search_mode_invalid"
    else:
        raise AssertionError("Unknown search mode was accepted")


def test_general_search_scope_is_normalized_and_allowlisted() -> None:
    assert normalize_search_locations([" İstanbul ", "İSTANBUL", "Türkiye"]) == [
        "İstanbul",
        "Türkiye",
    ]
    assert normalize_search_work_modes(["remote", "hybrid"]) == [
        "remote",
        "hybrid",
    ]
    assert normalize_search_sources(["linkedin", "ats"]) == [
        "linkedin",
        "ats",
    ]
    assert normalize_search_age(14) == 14
    with pytest.raises(ValueError, match="search_sources_invalid"):
        normalize_search_sources(["unsafe"])
    with pytest.raises(ValueError, match="search_work_modes_invalid"):
        normalize_search_work_modes(["anywhere"])
    with pytest.raises(ValueError, match="search_age_invalid"):
        normalize_search_age(365)


def test_forced_identical_search_has_server_side_cooldown() -> None:
    now = datetime.now(timezone.utc)
    profile_id = uuid4()
    roles = ["AI Engineer"]
    fingerprint = _search_fingerprint("a" * 64, roles, "quick")
    recent = SimpleNamespace(
        result={"search_fingerprint": fingerprint},
        finished_at=now - timedelta(seconds=30),
    )
    session = MagicMock()
    session.__enter__.return_value = session
    session.execute.return_value.one_or_none.return_value = (
        profile_id,
        roles,
        "a" * 64,
    )
    session.scalars.return_value.all.return_value = [recent]

    with (
        patch("app.operator_search_runs.Session", return_value=session),
        patch("app.operator_search_runs._now", return_value=now),
        pytest.raises(SearchCooldownActive) as caught,
    ):
        queue_profile_search(
            MagicMock(),
            profile_label="aslinur-default",
            requested_roles=roles,
            search_mode="quick",
            force=True,
        )

    assert 269 <= caught.value.retry_after_seconds <= 270
    session.commit.assert_not_called()


def test_changed_roles_do_not_reuse_manual_cooldown() -> None:
    assert _search_fingerprint("a" * 64, ["AI Engineer"], "quick") != (
        _search_fingerprint("a" * 64, ["Backend Engineer"], "quick")
    )


def test_search_scope_changes_fingerprint() -> None:
    base = _search_fingerprint(
        "a" * 64,
        ["AI Engineer"],
        "quick",
        sources=["linkedin"],
        max_age_days=30,
    )
    changed_source = _search_fingerprint(
        "a" * 64,
        ["AI Engineer"],
        "quick",
        sources=["indeed"],
        max_age_days=30,
    )
    changed_age = _search_fingerprint(
        "a" * 64,
        ["AI Engineer"],
        "quick",
        sources=["linkedin"],
        max_age_days=7,
    )
    assert len({base, changed_source, changed_age}) == 3
