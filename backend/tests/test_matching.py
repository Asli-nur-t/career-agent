import json

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
        secondary_roles=["Backend Engineer"],
        skills=["Python", "RAG", "LLM", "FastAPI", "PostgreSQL"],
        preferred_locations=["İstanbul"],
        excluded_keywords=["firmware", "sales"],
        max_years_experience=3,
        remote_allowed=True,
    )


def test_strong_match_is_explainable(profile: CandidateProfileSpec) -> None:
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
        ),
    )

    assert result.score >= 75
    assert result.recommendation == "strong_apply"
    assert "target_role:AI Engineer" in result.matched_terms
    assert "skill:RAG" in result.matched_terms
    assert not result.risk_flags


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
    assert normalize_match_text("İstanbul'da Yapay Zekâ") == (
        "istanbul da yapay zeka"
    )
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
