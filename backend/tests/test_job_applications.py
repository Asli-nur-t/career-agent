import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

os.environ.setdefault("APP_DB_PASSWORD", "test-only-password")

from app.main import app
from app.models import JobApplicationEvent
from app.operator_api import JobApplicationItem, update_job_application


TOKEN = "a" * 64


def _headers() -> dict[str, str]:
    return {"X-Operator-Token": TOKEN}


def _client() -> TestClient:
    return TestClient(app, base_url="http://localhost")


def _application_item(status: str = "to_apply") -> JobApplicationItem:
    now = datetime.now(timezone.utc)
    return JobApplicationItem(
        application_id=uuid4(),
        candidate_id=uuid4(),
        profile="aslinur-default",
        status=status,
        notes=None,
        applied_at=now if status != "to_apply" else None,
        created_at=now,
        updated_at=now,
        provider="linkedin",
        title="AI Engineer",
        company_name="Acme",
        listing_url="https://www.linkedin.com/jobs/view/123456",
        location="İstanbul",
        work_mode="hybrid",
        activity_state="active",
    )


def test_application_update_is_authenticated(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    item = _application_item("applied")
    with patch(
        "app.operator_api.update_job_application",
        return_value=item,
    ) as update:
        response = _client().post(
            f"/operator/jobs/{item.candidate_id}/application",
            headers=_headers(),
            json={"profile": "aslinur-default", "status": "applied"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "applied"
    assert update.call_args.kwargs["candidate_id"] == item.candidate_id
    assert update.call_args.kwargs["notes_supplied"] is False


def test_application_update_rejects_unknown_status(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    response = _client().post(
        f"/operator/jobs/{uuid4()}/application",
        headers=_headers(),
        json={"profile": "aslinur-default", "status": "deleted"},
    )

    assert response.status_code == 422


def test_application_transition_error_maps_to_conflict(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    with patch(
        "app.operator_api.update_job_application",
        side_effect=ValueError("application_transition_invalid"),
    ):
        response = _client().post(
            f"/operator/jobs/{uuid4()}/application",
            headers=_headers(),
            json={"profile": "aslinur-default", "status": "offer"},
        )

    assert response.status_code == 409
    assert response.json()["detail"]["error_code"] == (
        "application_transition_invalid"
    )


def test_application_list_is_profile_scoped_and_bounded(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    page = {
        "profile": "aslinur-default",
        "total": 0,
        "limit": 25,
        "offset": 0,
        "items": [],
    }
    with patch(
        "app.operator_api.load_job_applications",
        return_value=page,
    ) as load:
        response = _client().get(
            "/operator/applications?profile=aslinur-default"
            "&application_status=applied&limit=25",
            headers=_headers(),
        )

    assert response.status_code == 200
    assert load.call_args.kwargs == {
        "profile_label": "aslinur-default",
        "application_status": "applied",
        "limit": 25,
        "offset": 0,
    }


def test_application_status_change_is_atomic_and_audited() -> None:
    now = datetime.now(timezone.utc)
    profile = SimpleNamespace(id=uuid4(), label="aslinur-default")
    candidate = SimpleNamespace(
        id=uuid4(),
        provider="linkedin",
        title="AI Engineer",
        listing_url="https://www.linkedin.com/jobs/view/123456",
        location="İstanbul",
        work_mode="hybrid",
        activity_state="active",
    )
    application = SimpleNamespace(
        id=uuid4(),
        candidate_id=candidate.id,
        profile_id=profile.id,
        status="to_apply",
        notes=None,
        applied_at=None,
        created_at=now,
        updated_at=now,
    )
    session = MagicMock()
    session.__enter__.return_value = session
    session.scalar.side_effect = [profile, application]
    session.execute.return_value = SimpleNamespace(
        one_or_none=lambda: (candidate, "Acme")
    )

    with patch("app.operator_api.Session", return_value=session):
        result = update_job_application(
            MagicMock(),
            candidate_id=candidate.id,
            profile_label=profile.label,
            new_status="applied",
            notes="Başvuru formu gönderildi",
            notes_supplied=True,
        )

    assert result.status == "applied"
    assert result.notes == "Başvuru formu gönderildi"
    assert result.applied_at is not None
    events = [
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], JobApplicationEvent)
    ]
    assert len(events) == 1
    assert events[0].previous_status == "to_apply"
    assert events[0].new_status == "applied"
    session.commit.assert_called_once()


def test_invalid_application_transition_fails_closed() -> None:
    now = datetime.now(timezone.utc)
    profile = SimpleNamespace(id=uuid4(), label="aslinur-default")
    candidate = SimpleNamespace(
        id=uuid4(),
        provider="linkedin",
        title="AI Engineer",
        listing_url="https://www.linkedin.com/jobs/view/123456",
        location="İstanbul",
        work_mode="hybrid",
        activity_state="active",
    )
    application = SimpleNamespace(
        id=uuid4(),
        status="to_apply",
        notes=None,
        applied_at=None,
        created_at=now,
        updated_at=now,
    )
    session = MagicMock()
    session.__enter__.return_value = session
    session.scalar.side_effect = [profile, application]
    session.execute.return_value = SimpleNamespace(
        one_or_none=lambda: (candidate, "Acme")
    )

    with patch("app.operator_api.Session", return_value=session):
        try:
            update_job_application(
                MagicMock(),
                candidate_id=candidate.id,
                profile_label=profile.label,
                new_status="offer",
                notes=None,
                notes_supplied=False,
            )
        except ValueError as error:
            assert str(error) == "application_transition_invalid"
        else:
            raise AssertionError("invalid transition unexpectedly succeeded")

    session.commit.assert_not_called()
