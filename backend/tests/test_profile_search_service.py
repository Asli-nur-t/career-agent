from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.profile_search_service import ProfileSearchError, run_profile_job_search


def test_profile_search_fails_closed_without_serper_key(monkeypatch) -> None:
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    state = SimpleNamespace(spec=SimpleNamespace(), profile_id="profile-id")

    with patch(
        "app.profile_search_service.load_profile_search_state",
        return_value=state,
    ):
        with pytest.raises(ProfileSearchError) as caught:
            run_profile_job_search(
                MagicMock(),
                profile_label="aslinur-default",
            )

    assert caught.value.code == "serper_not_configured"
