from datetime import datetime, timedelta, timezone

from app.job_metadata import extract_job_metadata


def test_turkish_linkedin_metadata_is_extracted_conservatively() -> None:
    observed = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    metadata = extract_job_metadata(
        "Python Geliştirici - ABE Teknoloji — İstanbul, Türkiye - LinkedIn",
        "Hibrit · Tam Zamanlı · 5 yıl önce · Artık başvuru kabul etmiyor",
        observed_at=observed,
    )

    assert metadata.location == "İstanbul, Türkiye"
    assert metadata.work_mode == "hybrid"
    assert metadata.employment_type == "full_time"
    assert metadata.published_at == observed - timedelta(days=5 * 365)
    assert metadata.published_precision == "year"
    assert metadata.activity_state == "closed"
    assert metadata.activity_code == "search_text_closed_marker"
    assert metadata.activity_checked_at == observed


def test_recent_remote_listing_and_active_marker_are_extracted() -> None:
    observed = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    metadata = extract_job_metadata(
        "Backend Developer — Türkiye - LinkedIn",
        "Remote · Full-time · 2 days ago · Actively recruiting",
        observed_at=observed,
    )

    assert metadata.location == "Türkiye"
    assert metadata.work_mode == "remote"
    assert metadata.employment_type == "full_time"
    assert metadata.published_at == observed - timedelta(days=2)
    assert metadata.activity_state == "active"


def test_missing_metadata_remains_unknown() -> None:
    metadata = extract_job_metadata(
        "Python Developer",
        "Build reliable services",
    )

    assert metadata.location is None
    assert metadata.work_mode == "unknown"
    assert metadata.employment_type == "unknown"
    assert metadata.published_at is None
    assert metadata.activity_state == "unknown"
    assert metadata.activity_checked_at is None
