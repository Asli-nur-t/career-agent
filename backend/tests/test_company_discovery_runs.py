from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.company_discovery_runs import (
    _finish_if_complete,
    _run_one_company,
    queue_company_discovery_run,
    resume_company_discovery_run,
)
from app.company_profile_search_service import CompanyProfileSearchError


def _session_mock() -> MagicMock:
    session = MagicMock()
    session.__enter__.return_value = session
    return session


@pytest.mark.parametrize("budget", [0, 2, 2201, 100_000])
def test_company_discovery_run_rejects_out_of_range_budget(budget: int) -> None:
    with pytest.raises(ValueError, match="company_query_budget_invalid"):
        queue_company_discovery_run(MagicMock(), query_budget=budget)


def test_failed_company_search_counts_persisted_external_attempts() -> None:
    company_id = uuid4()
    session = _session_mock()
    session.scalar.return_value = 2

    with (
        patch(
            "app.company_discovery_runs.discover_company_profile",
            side_effect=CompanyProfileSearchError("rate_limited"),
        ),
        patch("app.company_discovery_runs.Session", return_value=session),
    ):
        outcome = _run_one_company(MagicMock(), company_id)

    assert isinstance(outcome.value, CompanyProfileSearchError)
    assert outcome.value.code == "rate_limited"
    assert outcome.attempt_count == 2


def test_pause_request_becomes_resumable_after_running_batch() -> None:
    run = SimpleNamespace(status="pause_requested")
    session = _session_mock()
    session.scalar.return_value = run

    with patch("app.company_discovery_runs.Session", return_value=session):
        complete = _finish_if_complete(MagicMock(), uuid4())

    assert complete is True
    assert run.status == "paused"
    session.commit.assert_called_once()


def test_resume_moves_paused_run_back_to_queue() -> None:
    run = SimpleNamespace(status="paused", error_code="worker_interrupted")
    session = _session_mock()
    session.scalar.return_value = run

    with patch("app.company_discovery_runs.Session", return_value=session):
        resume_company_discovery_run(MagicMock(), uuid4())

    assert run.status == "queued"
    assert run.error_code is None
    session.commit.assert_called_once()
