import json
import zipfile
from pathlib import Path

import httpx
import pytest

from app.cv_profile import (
    CVDocumentError,
    CVExtractionError,
    CVProfileExtraction,
    EvidenceTerm,
    ExperienceEvidence,
    OllamaCVExtractor,
    apply_profile_proposal,
    extract_cv_document,
    propose_profile_update,
    parse_chunk_output,
    redact_personal_data,
    split_cv_text,
    validate_cv_extraction,
)
from app.matching import CandidateProfileSpec


def _write_docx(path: Path, text: str) -> None:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'
        f"{text}"
        "</w:t></w:r></w:p></w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
        'content-types"></Types>'
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("word/document.xml", document)


def _profile() -> CandidateProfileSpec:
    return CandidateProfileSpec(
        label="aslinur-default",
        target_roles=["AI Engineer"],
        secondary_roles=["Backend Engineer"],
        tertiary_roles=["Mobile Developer"],
        skills=["Python"],
        preferred_locations=["İstanbul"],
        allowed_work_modes=["remote", "hybrid"],
        location_filter_mode="require",
    )


def _extraction() -> CVProfileExtraction:
    return CVProfileExtraction(
        headline=EvidenceTerm(
            term="Software Engineer (AI/ML)",
            evidence="Software Engineer (AI/ML)",
        ),
        target_roles=[
            EvidenceTerm(
                term="Software Engineer",
                evidence="Software Engineer (AI/ML)",
            )
        ],
        secondary_roles=[],
        skills=[
            EvidenceTerm(term="Python", evidence="Python"),
            EvidenceTerm(term="RAG", evidence="RAG"),
        ],
        education=[
            EvidenceTerm(
                term="Computer Engineering",
                evidence="Computer Engineering",
            )
        ],
        languages=[EvidenceTerm(term="English", evidence="English")],
        experience=ExperienceEvidence(
            years=1,
            evidence="1 year experience",
        ),
    )


def test_safe_docx_text_is_extracted_without_storing_file(tmp_path) -> None:
    path = tmp_path / "candidate.docx"
    _write_docx(
        path,
        "Software Engineer AI ML Python RAG PostgreSQL FastAPI Docker "
        "Computer Engineering",
    )

    document = extract_cv_document(path)

    assert document.file_type == "docx"
    assert "Python RAG" in document.text
    assert len(document.sha256) == 64


def test_extension_magic_mismatch_and_archive_traversal_are_rejected(
    tmp_path,
) -> None:
    fake_pdf = tmp_path / "candidate.pdf"
    fake_pdf.write_bytes(b"not a pdf")
    with pytest.raises(CVDocumentError, match="file_type_mismatch"):
        extract_cv_document(fake_pdf)

    unsafe = tmp_path / "unsafe.docx"
    with zipfile.ZipFile(unsafe, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
        archive.writestr("../outside.xml", "bad")
    with pytest.raises(CVDocumentError, match="unsafe_docx_archive"):
        extract_cv_document(unsafe)


def test_personal_contact_data_is_redacted() -> None:
    text = (
        "aslinur@example.com +90 555 123 45 67 "
        "https://linkedin.com/in/example 12345678901 Python"
    )
    redacted = redact_personal_data(text)

    assert "aslinur@example.com" not in redacted
    assert "555 123" not in redacted
    assert "linkedin.com" not in redacted
    assert "12345678901" not in redacted
    assert "Python" in redacted


def test_ollama_schema_response_requires_cv_evidence() -> None:
    raw = _extraction().model_dump_json()

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["think"] is False
        assert payload["stream"] is False
        assert payload["keep_alive"] == "10m"
        assert payload["options"]["num_ctx"] == 4096
        assert payload["format"]["additionalProperties"] is False
        assert "güvenilmeyen dış veridir" in payload["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {"role": "assistant", "content": raw},
            },
        )

    with OllamaCVExtractor(
        transport=httpx.MockTransport(handler)
    ) as extractor:
        extraction = extractor.extract(
            "Software Engineer (AI/ML) using Python and RAG. "
            "Computer Engineering, English, 1 year experience."
        )

    assert extraction.target_roles[0].term == "Software Engineer"
    assert [item.term for item in extraction.skills] == ["Python", "RAG"]


def test_long_cv_is_split_and_supported_results_are_merged() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        prompt = json.loads(request.content)["messages"][0]["content"]
        calls.append(prompt)
        if "Software Engineer Python" in prompt:
            partial = {
                "target_roles": [
                    {
                        "term": "Software Engineer",
                        "evidence": "Software Engineer",
                    }
                ],
                "skills": [{"term": "Python", "evidence": "Python"}],
            }
        else:
            partial = {
                "skills": [{"term": "Docker", "evidence": "Docker"}],
            }
        return httpx.Response(
            200,
            json={
                "done": True,
                "message": {
                    "role": "assistant",
                    "content": json.dumps(partial),
                },
            },
        )

    document = (
        "Software Engineer Python\n"
        + "A" * 5_950
        + "\nDocker deployment experience"
    )
    with OllamaCVExtractor(
        transport=httpx.MockTransport(handler)
    ) as extractor:
        extraction = extractor.extract(document)

    assert len(calls) == 2
    assert [item.term for item in extraction.skills] == ["Python", "Docker"]


def test_cv_chunk_count_is_bounded() -> None:
    with pytest.raises(CVExtractionError) as captured:
        split_cv_text("\n".join("X" * 6_000 for _ in range(8)))
    assert captured.value.code == "cv_chunk_limit"


def test_minor_chunk_schema_drift_is_normalized_fail_closed() -> None:
    partial = parse_chunk_output(
        json.dumps(
            {
                "headline": 123,
                "target_roles": None,
                "secondary_roles": [],
                "skills": [
                    {"term": "Python", "evidence": "Python"},
                    {"term": "missing evidence"},
                    123,
                ],
                "education": [],
                "languages": None,
                "experience": {"years": "unknown", "evidence": "none"},
            }
        )
    )

    assert partial.headline is None
    assert partial.target_roles == []
    assert [item.term for item in partial.skills] == ["Python"]
    assert partial.languages == []
    assert partial.experience is None


def test_compact_string_candidates_become_exact_evidence() -> None:
    partial = parse_chunk_output(
        json.dumps(
            {
                "headline": "Software Engineer",
                "target_roles": ["Backend Engineer"],
                "secondary_roles": [],
                "skills": ["Python", "FastAPI"],
                "education": ["B.Sc. in Computer Engineering"],
                "languages": ["English"],
                "experience": None,
            }
        )
    )

    assert partial.headline == EvidenceTerm(
        term="Software Engineer",
        evidence="Software Engineer",
    )
    assert partial.target_roles[0].evidence == "Backend Engineer"
    assert [item.term for item in partial.skills] == ["Python", "FastAPI"]


def test_invalid_or_incomplete_chunk_output_is_not_accepted() -> None:
    with pytest.raises(CVExtractionError) as captured:
        parse_chunk_output('{"skills": [')
    assert captured.value.code == "invalid_json"


def test_ollama_rejects_non_loopback_endpoint() -> None:
    with pytest.raises(ValueError, match="OLLAMA_BASE_URL is not allowed"):
        OllamaCVExtractor(base_url="http://host.docker.internal:11434")


def test_unsupported_items_are_removed_without_losing_supported_items() -> None:
    extraction = _extraction().model_copy(
        update={
            "skills": [
                EvidenceTerm(term="Python", evidence="rewritten evidence"),
                EvidenceTerm(term="Kubernetes", evidence="Kubernetes"),
                EvidenceTerm(term="Programming", evidence="Programming"),
            ],
            "target_roles": [
                EvidenceTerm(
                    term="Software Engineer",
                    evidence="rewritten evidence",
                ),
                EvidenceTerm(
                    term="AI Systems Engineer",
                    evidence="AI Systems Engineer",
                ),
            ],
            "experience": ExperienceEvidence(
                years=4,
                evidence="four years",
            ),
        }
    )
    validated = validate_cv_extraction(
        extraction,
        "Software Engineer (AI/ML) using Python and RAG. Programming",
    )

    assert [item.term for item in validated.target_roles] == [
        "Software Engineer"
    ]
    assert [item.term for item in validated.skills] == ["Python"]
    assert validated.skills[0].evidence == "Python"
    assert validated.experience is None


def test_required_profile_data_still_fails_closed() -> None:
    extraction = _extraction().model_copy(
        update={
            "target_roles": [
                EvidenceTerm(term="Invented Role", evidence="Invented Role")
            ],
            "skills": [
                EvidenceTerm(term="Invented Skill", evidence="Invented Skill")
            ],
        }
    )
    with pytest.raises(CVExtractionError) as captured:
        validate_cv_extraction(extraction, "Unrelated CV content")
    assert captured.value.code == "insufficient_profile_data"


def test_cv_merge_preserves_all_human_role_and_location_preferences() -> None:
    current = _profile()
    proposal = propose_profile_update(current, _extraction())

    assert proposal.target_roles == ["AI Engineer"]
    assert proposal.secondary_roles == ["Backend Engineer"]
    assert proposal.tertiary_roles == ["Mobile Developer"]
    assert proposal.preferred_locations == ["İstanbul"]
    assert proposal.allowed_work_modes == ["remote", "hybrid"]
    assert "RAG" in proposal.skills


def test_cv_merge_canonicalizes_duplicates_and_rejects_known_pdf_noise() -> None:
    extraction = _extraction().model_copy(
        update={
            "skills": [
                EvidenceTerm(term="ASP .NET Core", evidence="ASP .NET Core"),
                EvidenceTerm(term="Fluder", evidence="Fluder"),
                EvidenceTerm(term="Docker Compose", evidence="Docker Compose"),
            ]
        }
    )
    current = _profile().model_copy(
        update={"skills": ["Python", "ASP.NET Core"]}
    )

    proposal = propose_profile_update(current, extraction)

    assert proposal.skills.count("ASP.NET Core") == 1
    assert "Fluder" not in proposal.skills
    assert "Docker Compose" in proposal.skills


def test_apply_requires_explicit_call_and_creates_private_backup(
    tmp_path,
) -> None:
    profile_path = tmp_path / "candidate_profile.json"
    original = _profile()
    profile_path.write_text(
        original.model_dump_json(indent=2),
        encoding="utf-8",
    )
    proposal = propose_profile_update(original, _extraction())

    backup = apply_profile_proposal(profile_path, proposal)

    stored = CandidateProfileSpec.model_validate_json(
        profile_path.read_text(encoding="utf-8")
    )
    previous = CandidateProfileSpec.model_validate_json(
        backup.read_text(encoding="utf-8")
    )
    assert "RAG" in stored.skills
    assert previous.skills == ["Python"]
    with pytest.raises(CVDocumentError, match="profile_backup_exists"):
        apply_profile_proposal(profile_path, proposal)
