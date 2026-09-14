from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=2048)
    snippet: str = Field(default="", max_length=1000)
    position: int = Field(ge=1, le=100)


class CompanyAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    company_name: str = Field(min_length=1, max_length=500)
    brand_name: str | None = Field(default=None, max_length=300)
    official_website_candidate: str | None = Field(
        default=None,
        max_length=2048,
    )
    careers_url_candidate: str | None = Field(
        default=None,
        max_length=2048,
    )
    official_linkedin_candidate: str | None = Field(
        default=None,
        max_length=2048,
    )
    confidence: Literal["high", "medium", "low"]
    status: Literal["candidate_found", "needs_review", "not_found"]
    evidence: list[str] = Field(default_factory=list, max_length=5)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("evidence")
    @classmethod
    def validate_evidence(cls, values: list[str]) -> list[str]:
        cleaned = [" ".join(value.split())[:500] for value in values]

        if any(not value for value in cleaned):
            raise ValueError("Evidence entries cannot be blank.")

        return cleaned
