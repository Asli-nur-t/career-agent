"""Deterministic, explainable first-pass job matching."""

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


MATCHER_VERSION = "rules-v1"
_WHITESPACE = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^a-z0-9]+")
_SENIOR_TERMS = (
    "senior",
    "sr",
    "lead",
    "principal",
    "staff",
    "manager",
    "mudur",
    "yonetici",
    "uzman",
)
_JUNIOR_TERMS = (
    "junior",
    "jr",
    "new grad",
    "graduate",
    "entry level",
    "yeni mezun",
    "yetistirilmek uzere",
)
_EXPERIENCE_PATTERNS = (
    re.compile(r"\b(\d{1,2})\s*\+?\s*(?:years?|yrs?)\b"),
    re.compile(r"\b(?:minimum|at least|min)\s*(\d{1,2})\s*(?:years?|yrs?)\b"),
    re.compile(r"\b(?:en az|min(?:imum)?)\s*(\d{1,2})\s*yil\b"),
    re.compile(r"\b(\d{1,2})\s*\+?\s*yil(?:lik)?\b"),
)


def normalize_match_text(value: str | None) -> str:
    """Normalize Turkish and English text for bounded phrase matching."""

    if not value:
        return ""
    value = value.replace("İ", "I").replace("ı", "i")
    value = value.replace("C#", "CSharp").replace("c#", "csharp")
    value = value.replace("ASP.NET", "ASPNet").replace("asp.net", "aspnet")
    value = value.replace(".NET", " DotNet ").replace(".net", " dotnet ")
    value = unicodedata.normalize("NFKD", value).casefold()
    value = "".join(
        character
        for character in value
        if not unicodedata.combining(character)
    )
    return _WHITESPACE.sub(" ", _NON_WORD.sub(" ", value)).strip()


def _contains(text: str, term: str) -> bool:
    normalized = normalize_match_text(term)
    if not normalized:
        return False
    return re.search(
        rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])",
        text,
    ) is not None


def _clean_unique(values: list[str], *, maximum: int) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = " ".join(value.split())[:maximum]
        key = normalize_match_text(item)
        if item and key and key not in seen:
            seen.add(key)
            cleaned.append(item)
    return cleaned


class CandidateProfileSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    label: str = Field(min_length=1, max_length=100)
    target_roles: list[str] = Field(min_length=1, max_length=30)
    secondary_roles: list[str] = Field(default_factory=list, max_length=30)
    skills: list[str] = Field(min_length=1, max_length=100)
    preferred_locations: list[str] = Field(
        default_factory=list,
        max_length=30,
    )
    excluded_keywords: list[str] = Field(
        default_factory=list,
        max_length=50,
    )
    max_years_experience: int = Field(default=3, ge=0, le=50)
    remote_allowed: bool = True

    @field_validator(
        "target_roles",
        "secondary_roles",
        "skills",
        "preferred_locations",
        "excluded_keywords",
    )
    @classmethod
    def validate_terms(cls, values: list[str]) -> list[str]:
        cleaned = _clean_unique(values, maximum=100)
        if not cleaned and values:
            raise ValueError("Terim listesi boş değerlerden oluşamaz.")
        return cleaned

    @model_validator(mode="after")
    def require_primary_terms(self) -> "CandidateProfileSpec":
        if not self.target_roles:
            raise ValueError("En az bir hedef rol gereklidir.")
        if not self.skills:
            raise ValueError("En az bir beceri gereklidir.")
        return self

    def config_hash(self) -> str:
        data = self.model_dump(mode="json")
        for key in (
            "target_roles",
            "secondary_roles",
            "skills",
            "preferred_locations",
            "excluded_keywords",
        ):
            data[key] = sorted(data[key], key=normalize_match_text)
        payload = json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class JobMatchInput:
    title: str
    description_text: str | None = None
    location: str | None = None
    department: str | None = None
    employment_type: str | None = None
    is_remote: bool | None = None


@dataclass(frozen=True)
class JobMatchResult:
    score: int
    recommendation: str
    matched_terms: list[str]
    risk_flags: list[str]
    reason: str


def _first_match(text: str, terms: list[str]) -> str | None:
    return next((term for term in terms if _contains(text, term)), None)


def _required_experience(text: str) -> int | None:
    values = [
        int(match.group(1))
        for pattern in _EXPERIENCE_PATTERNS
        for match in pattern.finditer(text)
    ]
    return max(values) if values else None


def _recommendation(score: int) -> str:
    if score >= 75:
        return "strong_apply"
    if score >= 55:
        return "apply"
    if score >= 35:
        return "review"
    return "skip"


def score_job(
    profile: CandidateProfileSpec,
    job: JobMatchInput,
) -> JobMatchResult:
    title = normalize_match_text(job.title)
    description = normalize_match_text(job.description_text)
    metadata = normalize_match_text(
        " ".join(
            value
            for value in (
                job.location,
                job.department,
                job.employment_type,
            )
            if value
        )
    )
    full_text = " ".join(filter(None, (title, description, metadata)))
    matched: list[str] = []
    risks: list[str] = []
    score = 0

    target_title = _first_match(title, profile.target_roles)
    target_description = _first_match(description, profile.target_roles)
    secondary_title = _first_match(title, profile.secondary_roles)
    secondary_description = _first_match(description, profile.secondary_roles)
    if target_title:
        score += 40
        matched.append(f"target_role:{target_title}")
    elif target_description:
        score += 15
        matched.append(f"target_role_context:{target_description}")
    elif secondary_title:
        score += 28
        matched.append(f"secondary_role:{secondary_title}")
    elif secondary_description:
        score += 10
        matched.append(f"secondary_role_context:{secondary_description}")
    else:
        risks.append("role_not_matched")

    skill_matches = [
        skill for skill in profile.skills if _contains(full_text, skill)
    ]
    score += min(len(skill_matches) * 5, 30)
    matched.extend(f"skill:{skill}" for skill in skill_matches[:6])
    if not skill_matches:
        risks.append("skill_not_matched")

    location_match = _first_match(metadata, profile.preferred_locations)
    if location_match:
        score += 10
        matched.append(f"location:{location_match}")
    elif job.is_remote is True and profile.remote_allowed:
        score += 10
        matched.append("location:remote")
    elif job.is_remote is True and not profile.remote_allowed:
        score -= 20
        risks.append("remote_not_preferred")

    junior_marker = _first_match(full_text, list(_JUNIOR_TERMS))
    if junior_marker:
        score += 8
        matched.append(f"level:{junior_marker}")

    senior_marker = _first_match(title, list(_SENIOR_TERMS))
    if senior_marker:
        score -= 35
        risks.append(f"senior_title:{senior_marker}")

    required_years = _required_experience(full_text)
    if (
        required_years is not None
        and required_years > profile.max_years_experience
    ):
        excess = required_years - profile.max_years_experience
        score -= min(excess * 8, 32)
        risks.append(f"experience:{required_years}_years")

    excluded = _first_match(full_text, profile.excluded_keywords)
    if excluded:
        score -= 50
        risks.append(f"excluded:{excluded}")

    if not description:
        score -= 10
        risks.append("missing_description")

    score = max(0, min(score, 100))
    recommendation = _recommendation(score)
    positives = ", ".join(matched[:8]) or "belirgin eşleşme yok"
    warnings = ", ".join(risks[:8]) or "kritik risk yok"
    reason = (
        f"Kural tabanlı puan {score}/100. "
        f"Eşleşmeler: {positives}. Riskler: {warnings}."
    )[:1000]
    return JobMatchResult(
        score=score,
        recommendation=recommendation,
        matched_terms=matched,
        risk_flags=risks,
        reason=reason,
    )


def profile_spec_from_mapping(data: dict[str, Any]) -> CandidateProfileSpec:
    return CandidateProfileSpec.model_validate(data)
