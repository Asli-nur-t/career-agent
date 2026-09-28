import json

import httpx
import pytest

from app.local_job_agent import (
    LocalJobAgentError,
    LocalOllamaJobAgent,
    JobRequirementAnalysis,
    finalize_job_assessment,
    profile_agent_hash,
)


def test_local_agent_uses_structured_output_without_thinking() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        content = {
            "required_experience_min": 1,
            "required_experience_evidence": "en az 1 yıl deneyimli",
            "matched_requirements": ["Python", "FastAPI"],
            "missing_required_requirements": [],
            "preferred_requirements": ["AWS"],
            "hard_blockers": [],
            "summary": "Temel zorunlu şartlar adayla eşleşiyor.",
            "confidence": "high",
        }
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": json.dumps(content)}},
        )

    agent = LocalOllamaJobAgent(transport=httpx.MockTransport(handler))
    try:
        analysis = agent.assess_job(
            title="Junior AI Engineer",
            company_name="Acme",
            location="İstanbul",
            work_mode="hybrid",
            description_text="Python ve FastAPI bilen, en az 1 yıl deneyimli aday.",
            target_roles=["AI Engineer"],
            skills=["Python", "FastAPI"],
            professional_years=1,
            internship_months=6,
        )
    finally:
        agent.close()

    assert analysis.required_experience_min == 1
    assert analysis.matched_requirements == ["Python", "FastAPI"]
    assert analysis.preferred_requirements == ["AWS"]
    assert captured["model"] == "qwen3:8b"
    assert captured["think"] is False
    assert captured["stream"] is False
    assert isinstance(captured["format"], dict)


def test_local_agent_accepts_exactly_one_allowlisted_tool_call() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "fill_field",
                                "arguments": {
                                    "element_id": 2,
                                    "value_kind": "role",
                                },
                            }
                        }
                    ],
                }
            },
        )

    agent = LocalOllamaJobAgent(transport=httpx.MockTransport(handler))
    try:
        call = agent.next_browser_action(
            messages=[],
            element_snapshot='id=2 tag=input type=search label="Rol ara"',
        )
    finally:
        agent.close()

    assert call.name == "fill_field"
    assert call.arguments == {"element_id": 2, "value_kind": "role"}


def test_local_agent_drops_ungrounded_experience_and_blocker_claims() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        content = {
            "required_experience_min": 5,
            "required_experience_evidence": "En az 5 yıl deneyim",
            "matched_requirements": ["Python"],
            "missing_required_requirements": [],
            "preferred_requirements": [],
            "hard_blockers": ["AB vatandaşlığı zorunludur"],
            "summary": "Model metinde olmayan koşullar uydurdu.",
            "confidence": "low",
        }
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": json.dumps(content)}},
        )

    agent = LocalOllamaJobAgent(transport=httpx.MockTransport(handler))
    try:
        analysis = agent.assess_job(
            title="Junior AI Engineer",
            company_name="Acme",
            location="İstanbul",
            work_mode="hybrid",
            description_text="Python bilen yeni mezun adaylar başvurabilir.",
            target_roles=["AI Engineer"],
            skills=["Python"],
            professional_years=0,
            internship_months=6,
        )
    finally:
        agent.close()

    assert analysis.required_experience_min is None
    assert analysis.required_experience_evidence is None
    assert analysis.hard_blockers == []


def test_local_agent_rejects_nonlocal_endpoint_and_unapproved_model() -> None:
    with pytest.raises(ValueError, match="local_agent_base_url_not_allowed"):
        LocalOllamaJobAgent(base_url="https://example.com")
    with pytest.raises(ValueError, match="local_agent_model_not_allowed"):
        LocalOllamaJobAgent(model="untrusted:latest")


def test_local_agent_rejects_arbitrary_browser_tool() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "open_arbitrary_url",
                                "arguments": {"url": "https://example.com"},
                            }
                        }
                    ],
                }
            },
        )

    agent = LocalOllamaJobAgent(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(LocalJobAgentError, match="local_agent_tool_not_allowed"):
            agent.next_browser_action(messages=[], element_snapshot="none")
    finally:
        agent.close()


def test_experience_gap_caps_recommendation_but_junior_role_can_rank_high() -> None:
    junior = JobRequirementAnalysis(
        required_experience_min=0,
        matched_requirements=["Python", "FastAPI", "SQL"],
        summary="Junior rolün açık zorunlu şartları karşılanıyor.",
        confidence="high",
    )
    senior = JobRequirementAnalysis(
        required_experience_min=3,
        required_experience_evidence="En az 3 yıl deneyim",
        matched_requirements=["Python", "FastAPI"],
        missing_required_requirements=[],
        summary="Teknik beceriler uyuyor ancak profesyonel deneyim açığı var.",
        confidence="high",
    )

    junior_result = finalize_job_assessment(
        analysis=junior,
        base_score=85,
        professional_years=0,
        model="qwen3:8b",
    )
    senior_result = finalize_job_assessment(
        analysis=senior,
        base_score=90,
        professional_years=0,
        model="qwen3:8b",
    )

    assert junior_result.score >= 85
    assert junior_result.stars == 5
    assert junior_result.recommendation == "strong_apply"
    assert junior_result.preferred_requirements == []
    assert senior_result.experience_gap == 3
    assert senior_result.score <= 44
    assert senior_result.stars == 2
    assert senior_result.recommendation == "review"


def test_profile_hash_changes_when_internship_experience_changes() -> None:
    common = {
        "label": "default",
        "target_roles": ["AI Engineer"],
        "skills": ["Python"],
        "professional_years": 0,
        "preferred_locations": ["İstanbul"],
        "allowed_work_modes": ["hybrid"],
    }
    assert profile_agent_hash(**common, internship_months=0) != profile_agent_hash(
        **common,
        internship_months=6,
    )
