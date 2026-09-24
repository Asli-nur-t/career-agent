from unittest.mock import MagicMock

from app.discovery.evaluator import HybridCompanyEvaluator
from app.discovery.schemas import CompanyAssessment, SearchResult


def _fallback_assessment(company_name: str) -> CompanyAssessment:
    return CompanyAssessment(
        company_name=company_name,
        brand_name=None,
        official_website_candidate=None,
        careers_url_candidate=None,
        official_linkedin_candidate=None,
        confidence="low",
        status="not_found",
        evidence=[],
        reason="Belirsiz sonuç model tarafından değerlendirildi.",
    )


def test_exact_identity_and_domain_skip_model() -> None:
    fallback = MagicMock()
    fallback.model = "ollama/qwen3:8b"
    evaluator = HybridCompanyEvaluator(fallback)

    assessment = evaluator.evaluate(
        "ÖRNEK ROBOTİK TEKNOLOJİ A.Ş.",
        [
            SearchResult(
                title="Örnek Robotik Teknoloji",
                url="https://www.ornekrobotik.com.tr/hakkimizda",
                snippet="Örnek Robotik Teknoloji A.Ş. resmî web sitesi.",
                position=1,
            ),
            SearchResult(
                title="Örnek Robotik LinkedIn",
                url="https://tr.linkedin.com/company/ornekrobotik",
                snippet="Örnek Robotik şirket sayfası",
                position=2,
            ),
        ],
    )

    assert assessment.status == "candidate_found"
    assert assessment.official_website_candidate == "https://www.ornekrobotik.com.tr/"
    assert assessment.official_linkedin_candidate == "https://www.linkedin.com/company/ornekrobotik/"
    fallback.evaluate.assert_not_called()


def test_ambiguous_domains_use_model_fallback() -> None:
    company_name = "NOVA YAZILIM A.Ş."
    fallback = MagicMock()
    fallback.model = "gemini/gemini-3.6-flash"
    fallback.evaluate.return_value = _fallback_assessment(company_name)
    evaluator = HybridCompanyEvaluator(fallback)
    results = [
        SearchResult(
            title="Nova Yazılım",
            url="https://nova-one.example/",
            snippet="Nova Yazılım ürünleri",
            position=1,
        ),
        SearchResult(
            title="Nova Yazılım",
            url="https://nova-two.example/",
            snippet="Nova Yazılım çözümleri",
            position=2,
        ),
    ]

    assessment = evaluator.evaluate(company_name, results)

    assert assessment.status == "not_found"
    fallback.evaluate.assert_called_once_with(company_name, results)


def test_directory_result_never_becomes_official_website() -> None:
    company_name = "ÖRNEK TEKNOLOJİ A.Ş."
    fallback = MagicMock()
    fallback.model = "ollama/qwen3:8b"
    fallback.evaluate.return_value = _fallback_assessment(company_name)
    evaluator = HybridCompanyEvaluator(fallback)
    results = [
        SearchResult(
            title="Örnek Teknoloji LinkedIn",
            url="https://www.linkedin.com/company/ornek-teknoloji/",
            snippet="Örnek Teknoloji şirket profili",
            position=1,
        )
    ]

    assessment = evaluator.evaluate(company_name, results)

    assert assessment.official_website_candidate is None
    fallback.evaluate.assert_called_once()
