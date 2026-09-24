from app.discovery.evaluator import CompanyEvaluator, HybridCompanyEvaluator


class EvaluatorConfigurationError(RuntimeError):
    pass


def build_evaluator(
    *,
    provider: str,
    gemini_key: str = "",
    ollama_model: str = "qwen3:8b",
    ollama_base_url: str = "http://127.0.0.1:11434",
) -> CompanyEvaluator:
    provider = provider.strip().casefold()

    try:
        if provider == "ollama":
            from app.discovery.ollama import OllamaEvaluator

            return HybridCompanyEvaluator(
                OllamaEvaluator(
                    model=ollama_model,
                    base_url=ollama_base_url,
                )
            )
        if provider == "gemini":
            from app.discovery.gemini import GeminiEvaluator

            if not gemini_key.strip():
                raise EvaluatorConfigurationError(
                    "GEMINI_API_KEY is required for Gemini."
                )
            return HybridCompanyEvaluator(GeminiEvaluator(gemini_key))
    except ValueError as error:
        raise EvaluatorConfigurationError(
            "Evaluator configuration is invalid."
        ) from error

    raise EvaluatorConfigurationError(
        "EVALUATOR_PROVIDER must be ollama or gemini."
    )
