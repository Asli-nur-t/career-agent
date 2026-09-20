import httpx

from app.discovery.evaluator import (
    EvaluationError,
    build_evaluation_prompt,
    company_assessment_output_schema,
    parse_assessment,
)
from app.discovery.schemas import CompanyAssessment, SearchResult


ALLOWED_OLLAMA_BASE_URLS = {
    "http://127.0.0.1:11434",
    "http://[::1]:11434",
}
ALLOWED_OLLAMA_MODELS = {"qwen3:8b"}
MAX_RESPONSE_BYTES = 256_000


def _status_error_code(status_code: int) -> str:
    if status_code == 404:
        return "model_not_found"
    if status_code == 429:
        return "rate_limited"
    if status_code in {408, 504}:
        return "timeout"
    if 500 <= status_code < 600:
        return "service_unavailable"
    return "api_error"


class OllamaEvaluator:
    def __init__(
        self,
        *,
        model: str = "qwen3:8b",
        base_url: str = "http://127.0.0.1:11434",
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        base_url = base_url.strip().rstrip("/")
        model = model.strip()

        if base_url not in ALLOWED_OLLAMA_BASE_URLS:
            raise ValueError("OLLAMA_BASE_URL is not allowed.")
        if model not in ALLOWED_OLLAMA_MODELS:
            raise ValueError("OLLAMA_MODEL is not allowed.")

        self._model_name = model
        self.model = f"ollama/{model}"
        self._client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(120.0, connect=2.0),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def __enter__(self) -> "OllamaEvaluator":
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
        payload = {
            "model": self._model_name,
            "messages": [{"role": "user", "content": prompt}],
            "format": company_assessment_output_schema(),
            "stream": False,
            "think": False,
            "options": {"temperature": 0},
        }

        try:
            response = self._client.post("/api/chat", json=payload)
        except httpx.TimeoutException as error:
            raise EvaluationError("timeout") from error
        except httpx.NetworkError as error:
            raise EvaluationError("connection_error") from error
        except httpx.HTTPError as error:
            raise EvaluationError("api_error") from error

        if response.status_code != 200:
            raise EvaluationError(
                _status_error_code(response.status_code)
            )
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise EvaluationError("response_too_large")

        try:
            envelope = response.json()
        except ValueError as error:
            raise EvaluationError("invalid_output") from error

        if not isinstance(envelope, dict) or envelope.get("done") is not True:
            raise EvaluationError("invalid_output")
        message = envelope.get("message")
        if not isinstance(message, dict):
            raise EvaluationError("invalid_output")
        raw_output = message.get("content")
        if not isinstance(raw_output, str) or not raw_output.strip():
            raise EvaluationError("invalid_output")

        return parse_assessment(raw_output, company_name, results)
