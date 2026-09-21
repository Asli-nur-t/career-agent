from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from app.audit_job_board_activity import audit_job_board_activity
from app.job_boards import JobBoardActivity


def test_activity_audit_reextracts_metadata_without_mutating_dry_run() -> None:
    candidate = SimpleNamespace(
        id=uuid4(),
        provider="linkedin",
        external_id="4458927093",
        listing_url="https://www.linkedin.com/jobs/view/4458927093",
        title="Acme hiring AI Engineer - Remote in Azerbaijan | LinkedIn",
        snippet="Python · Easy Apply",
        company_id=None,
        company_name_raw="LinkedIn Azerbaijan",
        location=None,
        work_mode="remote",
        employment_type="unknown",
        published_at=None,
    )
    checked_at = datetime(2026, 9, 21, tzinfo=timezone.utc)
    verifier = SimpleNamespace(
        check=lambda listing: JobBoardActivity(
            state="active",
            code="linkedin_active_marker",
            checked_url=listing.listing_url,
            checked_at=checked_at,
        )
    )

    with patch("app.audit_job_board_activity.Session") as session_class:
        session = session_class.return_value.__enter__.return_value
        session.scalars.return_value.all.return_value = [candidate]
        result = audit_job_board_activity(
            object(),
            verifier=verifier,
            limit=100,
            workers=1,
            apply=False,
        )

    assert result["mode"] == "dry_run"
    assert result["activity_counts"]["active"] == 1
    assert result["sample"][0]["location"] == "Azerbaijan"
    assert result["sample"][0]["company_name"] == "Acme"
    session.commit.assert_not_called()
