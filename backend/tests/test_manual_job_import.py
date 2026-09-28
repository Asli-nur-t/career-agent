import os
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

os.environ.setdefault("APP_DB_PASSWORD", "test-only-password")

from app.main import app


TOKEN = "a" * 64


def _client() -> TestClient:
    return TestClient(app, base_url="http://localhost")


def _headers() -> dict[str, str]:
    return {"X-Operator-Token": TOKEN}


def _payload() -> dict[str, object]:
    return {
        "listing_url": "https://www.linkedin.com/jobs/view/4471899349",
        "title": "Software Engineer",
        "company_name": "Example",
        "location": "İstanbul, Türkiye",
        "work_mode": "hybrid",
        "employment_type": "full_time",
        "confirmed_visible": True,
    }


def test_manual_import_is_authenticated_and_validated(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    candidate_id = uuid4()
    result = {
        "candidate_id": candidate_id,
        "provider": "linkedin",
        "listing_url": "https://www.linkedin.com/jobs/view/4471899349",
        "status": "needs_review",
        "created": True,
        "changed": True,
    }
    with patch(
        "app.operator_api.import_manual_job_candidate",
        return_value=result,
    ) as importer:
        response = _client().post(
            "/operator/jobs/manual-import",
            headers=_headers(),
            json=_payload(),
        )

    assert response.status_code == 201
    assert response.json()["candidate_id"] == str(candidate_id)
    assert importer.call_args.args[1].confirmed_visible is True


def test_manual_import_requires_visible_confirmation(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    payload = _payload()
    payload["confirmed_visible"] = False

    response = _client().post(
        "/operator/jobs/manual-import",
        headers=_headers(),
        json=payload,
    )

    assert response.status_code == 422


def test_manual_import_hides_url_validation_details(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_API_TOKEN", TOKEN)
    with patch(
        "app.operator_api.import_manual_job_candidate",
        side_effect=ValueError("LinkedIn listing URL is invalid."),
    ):
        response = _client().post(
            "/operator/jobs/manual-import",
            headers=_headers(),
            json=_payload(),
        )

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "manual_job_invalid"
