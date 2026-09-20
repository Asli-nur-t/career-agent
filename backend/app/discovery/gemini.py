from google import genai
from google.genai import types

from app.discovery.evaluator import (
    EvaluationError,
    build_evaluation_prompt,
    parse_assessment,
    validate_assessment,
)
from app.discovery.schemas import CompanyAssessment, SearchResult


# Backward-compatible name for callers that imported the former exception.
GeminiEvaluationError = EvaluationError


def classify_gemini_error(error: Exception) -> str:
    """Classify provider failures without inspecting sensitive messages."""
    current: BaseException | None = error

    for _ in range(3):
        if current is None:
            break

        status_code = getattr(current, "status_code", None)
        if status_code is None:
            status_code = getattr(current, "code", None)

        if status_code == 429:
            return "rate_limited"
        if status_code in {401, 403}:
            return "authentication_error"
        if status_code == 404:
            return "model_not_found"
        if status_code in {408, 504}:
            return "timeout"
        if (
            isinstance(status_code, int)
            and 500 <= status_code < 600
        ):
            return "service_unavailable"

        error_name = type(current).__name__.casefold()
        if "ratelimit" in error_name:
            return "rate_limited"
        if "authentication" in error_name or "permission" in error_name:
            return "authentication_error"
        if "notfound" in error_name:
            return "model_not_found"
        if "timeout" in error_name:
            return "timeout"
        if "connection" in error_name or "network" in error_name:
            return "connection_error"

        current = current.__cause__

    return "api_error"


class GeminiEvaluator:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gemini-3.6-flash",
    ) -> None:
        api_key = api_key.strip()
        if (
            not 20 <= len(api_key) <= 2048
            or not api_key.isascii()
            or any(character.isspace() for character in api_key)
        ):
            raise ValueError("GEMINI_API_KEY is invalid.")
        if model != "gemini-3.6-flash":
            raise ValueError("Gemini model is not allowed.")

        self._model_name = model
        self.model = f"gemini/{model}"
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=30_000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )

    def __enter__(self) -> "GeminiEvaluator":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def evaluate(
        self,
        company_name: str,
        results: list[SearchResult],
    ) -> CompanyAssessment:
        prompt = build_evaluation_prompt(company_name, results)

        try:
            interaction = self._client.interactions.create(
                model=self._model_name,
                input=prompt,
            )
            raw_output = interaction.output_text or ""
        except Exception as error:
            raise EvaluationError(
                classify_gemini_error(error)
            ) from error

        return parse_assessment(raw_output, company_name, results)

    # Kept as wrappers for existing callers and focused unit tests.
    _build_prompt = staticmethod(build_evaluation_prompt)
    _validate_assessment = staticmethod(validate_assessment)
