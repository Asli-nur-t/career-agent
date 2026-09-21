import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.configure_candidate_profile import load_profile
from app.matching import (
    CandidateProfileSpec,
    JobMatchInput,
    normalize_match_text,
    score_job,
)


@pytest.fixture
def profile() -> CandidateProfileSpec:
    return CandidateProfileSpec(
        label="test-profile",
        target_roles=["AI Engineer", "Machine Learning Engineer"],
        secondary_roles=["Backend Engineer", "Python Developer"],
        skills=["Python", "RAG", "LLM", "FastAPI", "PostgreSQL"],
        preferred_locations=["İstanbul"],
        excluded_keywords=["firmware", "sales"],
        max_years_experience=3,
        remote_allowed=True,
    )


def test_strong_match_is_explainable(profile: CandidateProfileSpec) -> None:
    now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    result = score_job(
        profile,
        JobMatchInput(
            title="AI Engineer",
            description_text=(
                "Build RAG and LLM applications with Python, FastAPI and "
                "PostgreSQL. Two years of experience."
            ),
            location="İstanbul / Hybrid",
            is_remote=True,
            work_mode="hybrid",
            published_at=now - timedelta(days=2),
        ),
        now=now,
    )

    assert result.score >= 75
    assert result.recommendation == "strong_apply"
    assert "target_role:AI Engineer" in result.matched_terms
    assert "skill:RAG" in result.matched_terms
    assert not result.risk_flags


def test_stale_listing_is_hard_skipped(
    profile: CandidateProfileSpec,
) -> None:
    now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    result = score_job(
        profile,
        JobMatchInput(
            title="AI Engineer",
            description_text="Python RAG LLM FastAPI PostgreSQL",
            location="İstanbul",
            published_at=now - timedelta(days=31),
        ),
        now=now,
    )

    assert result.score == 0
    assert result.recommendation == "skip"
    assert "stale_listing:31_days" in result.risk_flags


def test_required_location_and_work_mode_are_hard_filters() -> None:
    strict = CandidateProfileSpec(
        label="strict",
        target_roles=["AI Engineer"],
        skills=["Python"],
        preferred_locations=["İstanbul"],
        allowed_work_modes=["remote", "hybrid"],
        location_filter_mode="require",
    )
    now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)

    wrong_location = score_job(
        strict,
        JobMatchInput(
            title="AI Engineer",
            description_text="Python",
            location="Ankara",
            work_mode="hybrid",
            published_at=now,
        ),
        now=now,
    )
    wrong_mode = score_job(
        strict,
        JobMatchInput(
            title="AI Engineer",
            description_text="Python",
            location="İstanbul",
            work_mode="onsite",
            published_at=now,
        ),
        now=now,
    )

    assert wrong_location.recommendation == "skip"
    assert "location_not_preferred" in wrong_location.risk_flags
    assert wrong_mode.recommendation == "skip"
    assert "work_mode_not_allowed:onsite" in wrong_mode.risk_flags


def test_unknown_location_is_reviewable_not_silently_rejected() -> None:
    strict = CandidateProfileSpec(
        label="strict",
        target_roles=["AI Engineer"],
        skills=["Python"],
        preferred_locations=["İstanbul"],
        location_filter_mode="require",
    )
    now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    result = score_job(
        strict,
        JobMatchInput(
            title="AI Engineer",
            description_text="Python",
            published_at=now,
        ),
        now=now,
    )

    assert result.recommendation != "skip"
    assert "location_unknown" in result.risk_flags


def test_remote_location_must_be_eligible_when_explicit() -> None:
    strict = CandidateProfileSpec(
        label="strict-remote",
        target_roles=["AI Engineer"],
        skills=["Python"],
        preferred_locations=["İstanbul"],
        preferred_remote_locations=["Türkiye", "Turkey"],
        allowed_work_modes=["remote"],
        location_filter_mode="require",
        remote_allowed=True,
    )
    now = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)

    us_only = score_job(
        strict,
        JobMatchInput(
            title="AI Engineer",
            description_text="Python",
            location="Texas, United States",
            work_mode="remote",
            published_at=now,
        ),
        now=now,
    )
    turkey = score_job(
        strict,
        JobMatchInput(
            title="AI Engineer",
            description_text="Python",
            location="Turkey",
            work_mode="remote",
            published_at=now,
        ),
        now=now,
    )
    unknown = score_job(
        strict,
        JobMatchInput(
            title="AI Engineer",
            description_text="Python",
            work_mode="remote",
            published_at=now,
        ),
        now=now,
    )

    assert us_only.recommendation == "skip"
    assert "remote_location_not_eligible" in us_only.risk_flags
    assert turkey.recommendation != "skip"
    assert "location:remote:Turkey" in turkey.matched_terms
    assert unknown.recommendation == "skip"
    assert "remote_location_unknown" in unknown.risk_flags


def test_global_remote_requires_explicit_profile_scope() -> None:
    turkey_only = CandidateProfileSpec(
        label="turkey-only",
        target_roles=["AI Engineer"],
        skills=["Python"],
        preferred_remote_locations=["Türkiye", "Turkey"],
        allowed_work_modes=["remote"],
        location_filter_mode="require",
        remote_allowed=True,
    )
    global_profile = turkey_only.model_copy(
        update={"preferred_remote_locations": ["Worldwide"]}
    )
    job = JobMatchInput(
        title="AI Engineer",
        description_text="Python",
        location="Worldwide",
        work_mode="remote",
        published_at=datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc),
    )

    rejected = score_job(turkey_only, job)
    accepted = score_job(global_profile, job)

    assert rejected.recommendation == "skip"
    assert "remote_location_not_eligible" in rejected.risk_flags
    assert accepted.recommendation != "skip"
    assert "location:remote:Worldwide" in accepted.matched_terms


def test_senior_excluded_job_is_skipped(profile: CandidateProfileSpec) -> None:
    result = score_job(
        profile,
        JobMatchInput(
            title="Senior AI Engineer",
            description_text=(
                "Minimum 7 years experience. Python and LLM firmware "
                "development."
            ),
            location="İstanbul",
        ),
    )

    assert result.recommendation == "skip"
    assert "senior_title:senior" in result.risk_flags
    assert "experience:7_years" in result.risk_flags
    assert "excluded:firmware" in result.risk_flags


def test_turkish_text_and_short_terms_use_word_boundaries(
    profile: CandidateProfileSpec,
) -> None:
    assert normalize_match_text("İstanbul'da Yapay Zekâ") == "istanbul da ai"
    unrelated = score_job(
        profile,
        JobMatchInput(
            title="Training Coordinator",
            description_text="Coordinate training material.",
            location="Ankara",
        ),
    )
    assert "target_role:AI Engineer" not in unrelated.matched_terms
    assert "role_not_matched" in unrelated.risk_flags


def test_turkish_role_equivalent_matches_english_profile(
    profile: CandidateProfileSpec,
) -> None:
    turkish = score_job(
        profile,
        JobMatchInput(
            title="Python Geliştirici",
            description_text="Python ile servis geliştirme",
            location="İstanbul",
        ),
    )
    english = score_job(
        profile,
        JobMatchInput(
            title="Python Developer",
            description_text="Python ile servis geliştirme",
            location="İstanbul",
        ),
    )

    assert turkish.score == english.score
    assert turkish.score >= 35
    assert turkish.recommendation == "review"
    assert "secondary_role:Python Developer" in turkish.matched_terms


def test_tertiary_role_surfaces_as_review_without_primary_priority() -> None:
    profile = CandidateProfileSpec(
        label="tertiary-profile",
        target_roles=["AI Engineer"],
        secondary_roles=["Backend Engineer"],
        tertiary_roles=["Mobile Developer"],
        skills=["Flutter", "Dart"],
        preferred_locations=["İstanbul"],
    )
    result = score_job(
        profile,
        JobMatchInput(
            title="Mobile Developer",
            description_text="Flutter and Dart mobile application development",
            location="İstanbul",
            work_mode="hybrid",
        ),
    )

    assert result.recommendation == "review"
    assert result.score < 55
    assert "tertiary_role:Mobile Developer" in result.matched_terms


def test_turkish_specialist_matches_without_senior_penalty() -> None:
    profile = CandidateProfileSpec(
        label="specialist-profile",
        target_roles=["AI Specialist"],
        skills=["Python"],
    )
    turkish = score_job(
        profile,
        JobMatchInput(
            title="Yapay Zekâ Uzmanı",
            description_text="Python ile yapay zekâ çözümleri geliştirir.",
        ),
    )
    english = score_job(
        profile,
        JobMatchInput(
            title="AI Specialist",
            description_text="Python ile yapay zekâ çözümleri geliştirir.",
        ),
    )

    assert turkish.score == english.score
    assert "target_role:AI Specialist" in turkish.matched_terms
    assert not any(
        flag.startswith("senior_title:")
        for flag in turkish.risk_flags
    )


def test_turkish_senior_level_is_canonicalized(
    profile: CandidateProfileSpec,
) -> None:
    result = score_job(
        profile,
        JobMatchInput(
            title="Kıdemli Python Geliştirici",
            description_text="Python ile servis geliştirme",
        ),
    )

    assert normalize_match_text(
        "Kıdemli Python Geliştirici"
    ) == "senior python developer"
    assert "secondary_role:Python Developer" in result.matched_terms
    assert "senior_title:senior" in result.risk_flags


def test_profile_hash_ignores_list_order_and_duplicates() -> None:
    first = CandidateProfileSpec(
        label="same",
        target_roles=["AI Engineer", "ML Engineer"],
        skills=["Python", "RAG"],
    )
    second = CandidateProfileSpec(
        label="same",
        target_roles=["ML Engineer", "AI Engineer", "AI Engineer"],
        skills=["RAG", "Python"],
    )
    assert first.config_hash() == second.config_hash()


def test_default_filter_fields_preserve_legacy_profile_hash() -> None:
    profile = CandidateProfileSpec(
        label="legacy",
        target_roles=["AI Engineer"],
        skills=["Python"],
        preferred_locations=["İstanbul"],
    )
    legacy_payload = {
        "excluded_keywords": [],
        "label": "legacy",
        "max_years_experience": 3,
        "preferred_locations": ["İstanbul"],
        "remote_allowed": True,
        "secondary_roles": [],
        "skills": ["Python"],
        "target_roles": ["AI Engineer"],
    }
    encoded = json.dumps(
        legacy_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert profile.config_hash() == hashlib.sha256(encoded).hexdigest()


def test_profile_requires_nonempty_primary_terms() -> None:
    with pytest.raises(ValidationError):
        CandidateProfileSpec(
            label="invalid",
            target_roles=[" "],
            skills=["Python"],
        )


def test_profile_file_rejects_unknown_fields(tmp_path) -> None:
    path = tmp_path / "profile.json"
    path.write_text(json.dumps({
        "label": "invalid",
        "target_roles": ["AI Engineer"],
        "skills": ["Python"],
        "api_key": "must-not-be-here",
    }), encoding="utf-8")

    with pytest.raises(ValueError, match="Profil doğrulanamadı"):
        load_profile(path)
