from typing import NotRequired, TypedDict, cast
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from sqlalchemy import Engine, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.discovery.gemini import (
    GeminiEvaluationError,
    GeminiEvaluator,
)
from app.discovery.safety import build_company_queries, safe_text
from app.discovery.schemas import CompanyAssessment, SearchResult
from app.discovery.serper import SerperClient, SerperError
from app.discovery.web_verifier import (
    SafeWebsiteVerifier,
    WebsiteVerificationError,
)
from app.models import Company, CompanyWebProfile, DiscoveryAttempt


class AttemptData(TypedDict):
    query: str
    result_count: int
    outcome: str
    error_code: str | None
    evaluator_model: str | None


class DiscoveryState(TypedDict):
    company_id: UUID
    company_name: NotRequired[str]
    queries: NotRequired[tuple[str, ...]]
    active_query: NotRequired[str]
    search_results: NotRequired[list[SearchResult]]
    assessment: NotRequired[CompanyAssessment]
    attempts: NotRequired[list[AttemptData]]
    error_code: NotRequired[str | None]
    site_verified: NotRequired[bool]

    verification_url: NotRequired[str | None]

    verification_code: NotRequired[str | None]
    stored_status: NotRequired[str | None]

    profile_updated: NotRequired[bool]
    persisted: NotRequired[bool]


class CompanyDiscoveryError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Company discovery failed.")
        self.code = code


REJECTED_EVALUATION_CODES = {
    "candidate_without_website",
    "careers_without_website",
    "company_name_mismatch",
    "denied_official_domain",
    "invented_url",
    "invalid_linkedin_domain",
    "invalid_url",
    "not_found_with_urls",
}


class CompanyDiscoveryGraph:
    def __init__(
        self,
        *,
        engine: Engine,
        serper_key: str,
        gemini_key: str,
    ) -> None:
        self._engine = engine
        self._search_client = SerperClient(serper_key)
        self._evaluator = GeminiEvaluator(gemini_key)
        self._website_verifier = SafeWebsiteVerifier()
        self._graph = self._build_graph()

    def __enter__(self) -> "CompanyDiscoveryGraph":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._search_client.close()
        self._evaluator.close()

    def run(self, company_id: UUID) -> DiscoveryState:
        result = self._graph.invoke({"company_id": company_id})
        return cast(DiscoveryState, result)

    def _build_graph(self):
        graph = StateGraph(DiscoveryState)

        graph.add_node("load_company", self._load_company)
        graph.add_node("search_sources", self._search_sources)
        graph.add_node("evaluate_sources", self._evaluate_sources)
        graph.add_node("verify_candidate", self._verify_candidate)
        graph.add_node("mark_not_found", self._mark_not_found)
        graph.add_node("persist_result", self._persist_result)

        graph.add_edge(START, "load_company")
        graph.add_edge("load_company", "search_sources")
        graph.add_conditional_edges(
            "search_sources",
            self._route_after_search,
            {
                "evaluate": "evaluate_sources",
                "not_found": "mark_not_found",
                "persist_failure": "persist_result",
            },
        )
        graph.add_conditional_edges(
            "evaluate_sources",
            self._route_after_evaluation,
            {
                "verify": "verify_candidate",
                "persist": "persist_result",
            },
        )

        graph.add_edge("verify_candidate", "persist_result")
        graph.add_edge("mark_not_found", "persist_result")
        graph.add_edge("persist_result", END)

        return graph.compile()

    def _load_company(
        self,
        state: DiscoveryState,
    ) -> DiscoveryState:
        with Session(self._engine) as session:
            company = session.get(Company, state["company_id"])

            if company is None:
                raise CompanyDiscoveryError("company_not_found")

            company_name = safe_text(company.name, 500)

        if not company_name:
            raise CompanyDiscoveryError("invalid_company_name")

        try:
            queries = build_company_queries(company_name)
        except ValueError as error:
            raise CompanyDiscoveryError(
                "invalid_company_name"
            ) from error

        return {
            **state,
            "company_name": company_name,
            "queries": queries,
            "attempts": [],
            "error_code": None,
        }

    def _search_sources(
        self,
        state: DiscoveryState,
    ) -> DiscoveryState:
        attempts = list(state.get("attempts", []))

        for query in state["queries"]:
            try:
                results = self._search_client.search(query)
            except SerperError as error:
                attempts.append(
                    AttemptData(
                        query=query,
                        result_count=0,
                        outcome="search_error",
                        error_code=error.code,
                        evaluator_model=None,
                    )
                )
                return {
                    **state,
                    "active_query": query,
                    "search_results": [],
                    "attempts": attempts,
                    "error_code": error.code,
                }

            if results:
                attempts.append(
                    AttemptData(
                        query=query,
                        result_count=len(results),
                        outcome="success",
                        error_code=None,
                        evaluator_model=self._evaluator.model,
                    )
                )
                return {
                    **state,
                    "active_query": query,
                    "search_results": results,
                    "attempts": attempts,
                    "error_code": None,
                }

            attempts.append(
                AttemptData(
                    query=query,
                    result_count=0,
                    outcome="no_results",
                    error_code=None,
                    evaluator_model=None,
                )
            )

        return {
            **state,
            "active_query": state["queries"][-1],
            "search_results": [],
            "attempts": attempts,
            "error_code": None,
        }

    @staticmethod
    def _route_after_search(state: DiscoveryState) -> str:
        if state.get("error_code"):
            return "persist_failure"
        if state.get("search_results"):
            return "evaluate"
        return "not_found"

    def _evaluate_sources(
        self,
        state: DiscoveryState,
    ) -> DiscoveryState:
        attempts = list(state["attempts"])

        try:
            assessment = self._evaluator.evaluate(
                state["company_name"],
                state["search_results"],
            )
        except GeminiEvaluationError as error:
            outcome = (
                "rejected"
                if error.code in REJECTED_EVALUATION_CODES
                else "evaluation_error"
            )
            attempts[-1] = {
                **attempts[-1],
                "outcome": outcome,
                "error_code": error.code,
            }
            return {
                **state,
                "attempts": attempts,
                "error_code": error.code,
            }

        return {
            **state,
            "assessment": assessment,
            "attempts": attempts,
            "error_code": None,
        }

    @staticmethod
    def _route_after_evaluation(
        state: DiscoveryState,
    ) -> str:
        assessment = state.get("assessment")

        if state.get("error_code") or assessment is None:
            return "persist"

        if (
            assessment.status in {"candidate_found", "verified"}
            and assessment.official_website_candidate
        ):
            return "verify"

        return "persist"

    def _verify_candidate(
        self,
        state: DiscoveryState,
    ) -> DiscoveryState:
        assessment = state.get("assessment")

        if (
            assessment is None
            or not assessment.official_website_candidate
        ):
            return {
                **state,
                "site_verified": False,
                "verification_url": None,
                "verification_code": "not_applicable",
            }

        try:
            result = self._website_verifier.verify(
                company_name=state["company_name"],
                official_website=(
                    assessment.official_website_candidate
                ),
                search_results=state.get("search_results", []),
            )
        except WebsiteVerificationError as error:
            return {
                **state,
                "site_verified": False,
                "verification_url": None,
                "verification_code": error.code,
            }
        except ValueError:
            return {
                **state,
                "site_verified": False,
                "verification_url": None,
                "verification_code": "invalid_verification_input",
            }

        if result.verified and result.matched_url:
            verification_evidence = (
                f"{result.matched_url} sayfasında şirketin "
                "tam ticari unvanı doğrulandı."
            )

            assessment = assessment.model_copy(
                update={
                    "status": "verified",
                    "evidence": [
                        *assessment.evidence,
                        verification_evidence,
                    ],
                }
            )
        elif assessment.status == "verified":
            assessment = assessment.model_copy(
                update={"status": "candidate_found"}
            )

        return {
            **state,
            "assessment": assessment,
            "site_verified": result.verified,
            "verification_url": result.matched_url,
            "verification_code": result.code,
        }
    @staticmethod
    def _mark_not_found(
        state: DiscoveryState,
    ) -> DiscoveryState:
        assessment = CompanyAssessment(
            company_name=state["company_name"],
            brand_name=None,
            official_website_candidate=None,
            careers_url_candidate=None,
            official_linkedin_candidate=None,
            confidence="low",
            status="not_found",
            evidence=[],
            reason="Üç kontrollü sorguda uygun web kaynağı bulunamadı.",
        )

        return {
            **state,
            "assessment": assessment,
            "error_code": None,
        }

    def _persist_result(
        self,
        state: DiscoveryState,
    ) -> DiscoveryState:
        stored_status: str | None = None
        profile_updated = False
        try:
            with Session(self._engine) as session:
                for attempt in state.get("attempts", []):
                    session.add(
                        DiscoveryAttempt(
                            company_id=state["company_id"],
                            query=attempt["query"],
                            search_provider="serper",
                            evaluator_model=attempt["evaluator_model"],
                            result_count=attempt["result_count"],
                            outcome=attempt["outcome"],
                            error_code=attempt["error_code"],
                        )
                    )

                assessment = state.get("assessment")

                if assessment is not None:
                    evidence = [
                        {"text": safe_text(item, 500)}
                        for item in assessment.evidence
                    ]

                    site_verified = bool(
                        state.get("site_verified")
                    )

                    profile_status = (
                        "verified"
                        if site_verified
                        else (
                            "candidate_found"
                            if assessment.status == "verified"
                            else assessment.status
                        )
                    )

                    verified_at = (
                        func.now()
                        if site_verified
                        else None
                    )
                    statement = insert(CompanyWebProfile).values(
                        company_id=state["company_id"],
                        brand_name=assessment.brand_name,
                        official_website_url=(
                            assessment.official_website_candidate
                        ),
                        careers_url=assessment.careers_url_candidate,
                        official_linkedin_url=(
                            assessment.official_linkedin_candidate
                        ),
                        confidence=assessment.confidence,
                        status=profile_status,
                        evidence=evidence,
                        search_provider="serper",
                        evaluator_model=self._evaluator.model,
                        last_searched_at=func.now(),
                        last_verified_at=verified_at,
                    )

                    statement = statement.on_conflict_do_update(
                        index_elements=[CompanyWebProfile.company_id],
                        set_={
                            "brand_name": statement.excluded.brand_name,
                            "official_website_url": (
                                statement.excluded.official_website_url
                            ),
                            "careers_url": statement.excluded.careers_url,
                            "official_linkedin_url": (
                                statement.excluded.official_linkedin_url
                            ),
                            "confidence": statement.excluded.confidence,
                            "status": statement.excluded.status,
                            "evidence": statement.excluded.evidence,
                            "search_provider": (
                                statement.excluded.search_provider
                            ),
                            "evaluator_model": (
                                statement.excluded.evaluator_model
                            ),
                            "last_searched_at": func.now(),
                            "last_verified_at": verified_at,
                            "updated_at": func.now(),
                        },
                        where=CompanyWebProfile.status != "verified",
                    )

                    execution_result = session.execute(statement.returning(CompanyWebProfile.status))
                    profile_updated = (
                        execution_result.scalar_one_or_none()
                        is not None
                    )

                stored_status = session.scalar(
                    select(CompanyWebProfile.status).where(
                        CompanyWebProfile.company_id
                        == state["company_id"]
                    )
                )
                session.commit()

        except SQLAlchemyError as error:
            raise CompanyDiscoveryError(
                "persistence_error"
            ) from error

        return {
            **state,
            "stored_status": stored_status,
            "profile_updated": profile_updated,
            "persisted": True,
        }
