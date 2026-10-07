"""Serializable LangGraph orchestration for bounded browser access decisions.

The live Playwright page deliberately stays outside graph state.  Callbacks close
over that page, while every value passed through LangGraph remains serializable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, NotRequired, TypedDict, cast

from langgraph.graph import END, START, StateGraph


AccessState = Literal[
    "ok",
    "login_required",
    "challenge_required",
    "blocked",
    "rate_limited",
]
AccessDecision = Literal["proceed", "defer"]


@dataclass(frozen=True)
class AccessObservation:
    """A bounded observation produced from the live page."""

    state: AccessState
    reason: str
    current_url: str


class BrowserAccessState(TypedDict):
    provider: str
    target_url: str
    access_state: NotRequired[AccessState]
    access_reason: NotRequired[str]
    current_url: NotRequired[str]
    handoff_attempts: int
    navigation_attempts: int
    transition_trace: list[str]
    decision: NotRequired[AccessDecision]
    outcome: NotRequired[str]
    error_code: NotRequired[str]


def _defer_diagnostic(state: BrowserAccessState) -> tuple[str, str]:
    access_state = state["access_state"]
    if access_state == "rate_limited":
        return "rate_limited", "browser_source_rate_limited"
    if access_state == "blocked":
        return "blocked", "browser_source_security_challenge"
    if access_state == "challenge_required":
        return "blocked", "browser_source_human_verification_timeout"
    if access_state == "login_required":
        return "login_required", "browser_source_login_required"
    return "failed", "browser_source_navigation_mismatch"


class BrowserAccessGraph:
    """Bounded access state machine with no generic tool execution."""

    def __init__(
        self,
        *,
        fetch_page: Callable[[], AccessObservation],
        wait_for_human: Callable[[AccessState], AccessObservation],
        navigate_to_target: Callable[[], AccessObservation],
        is_target_page: Callable[[], bool],
        max_handoff_attempts: int = 1,
        max_navigation_attempts: int = 1,
    ) -> None:
        if not 0 <= max_handoff_attempts <= 2:
            raise ValueError("browser_access_handoff_limit_invalid")
        if not 0 <= max_navigation_attempts <= 2:
            raise ValueError("browser_access_navigation_limit_invalid")
        self._fetch_page = fetch_page
        self._wait_for_human = wait_for_human
        self._navigate_to_target = navigate_to_target
        self._is_target_page = is_target_page
        self._max_handoff_attempts = max_handoff_attempts
        self._max_navigation_attempts = max_navigation_attempts
        self._graph = self._build_graph()

    def run(
        self,
        *,
        provider: str,
        target_url: str,
    ) -> BrowserAccessState:
        result = self._graph.invoke(
            BrowserAccessState(
                provider=provider,
                target_url=target_url,
                handoff_attempts=0,
                navigation_attempts=0,
                transition_trace=[],
            )
        )
        return cast(BrowserAccessState, result)

    def _build_graph(self):
        graph = StateGraph(BrowserAccessState)
        graph.add_node("fetch_page", self._fetch)
        graph.add_node("classify_access", self._record_classification)
        graph.add_node("wait_for_human", self._wait)
        graph.add_node("retry_navigation", self._navigate)
        graph.add_node("defer_source", self._defer)
        graph.add_node("proceed", self._proceed)

        graph.add_edge(START, "fetch_page")
        graph.add_edge("fetch_page", "classify_access")
        graph.add_conditional_edges(
            "classify_access",
            self._route_after_classification,
            {
                "wait": "wait_for_human",
                "navigate": "retry_navigation",
                "defer": "defer_source",
                "proceed": "proceed",
            },
        )
        graph.add_edge("wait_for_human", "classify_access")
        graph.add_edge("retry_navigation", "classify_access")
        graph.add_edge("defer_source", END)
        graph.add_edge("proceed", END)
        return graph.compile()

    def _fetch(self, state: BrowserAccessState) -> BrowserAccessState:
        observation = self._fetch_page()
        trace = list(state["transition_trace"])
        trace.append("fetch_page")
        return {
            **state,
            "access_state": observation.state,
            "access_reason": observation.reason,
            "current_url": observation.current_url,
            "transition_trace": trace,
        }

    @staticmethod
    def _record_classification(state: BrowserAccessState) -> BrowserAccessState:
        trace = list(state["transition_trace"])
        trace.append(
            f"classify:{state['access_state']}:{state['access_reason']}"
        )
        return {**state, "transition_trace": trace}

    def _route_after_classification(self, state: BrowserAccessState) -> str:
        access_state = state["access_state"]
        if access_state == "ok":
            if self._is_target_page():
                return "proceed"
            if state["navigation_attempts"] < self._max_navigation_attempts:
                return "navigate"
            return "defer"
        if access_state in {"login_required", "challenge_required"}:
            if state["handoff_attempts"] < self._max_handoff_attempts:
                return "wait"
        return "defer"

    def _wait(self, state: BrowserAccessState) -> BrowserAccessState:
        observation = self._wait_for_human(state["access_state"])
        trace = list(state["transition_trace"])
        trace.append("wait_for_human")
        return {
            **state,
            "access_state": observation.state,
            "access_reason": observation.reason,
            "current_url": observation.current_url,
            "handoff_attempts": state["handoff_attempts"] + 1,
            "transition_trace": trace,
        }

    def _navigate(self, state: BrowserAccessState) -> BrowserAccessState:
        observation = self._navigate_to_target()
        trace = list(state["transition_trace"])
        trace.append("retry_navigation")
        return {
            **state,
            "access_state": observation.state,
            "access_reason": observation.reason,
            "current_url": observation.current_url,
            "navigation_attempts": state["navigation_attempts"] + 1,
            "transition_trace": trace,
        }

    @staticmethod
    def _defer(state: BrowserAccessState) -> BrowserAccessState:
        outcome, error_code = _defer_diagnostic(state)
        trace = list(state["transition_trace"])
        trace.append("defer_source")
        return {
            **state,
            "decision": "defer",
            "outcome": outcome,
            "error_code": error_code,
            "transition_trace": trace,
        }

    @staticmethod
    def _proceed(state: BrowserAccessState) -> BrowserAccessState:
        trace = list(state["transition_trace"])
        trace.append("proceed")
        return {
            **state,
            "decision": "proceed",
            "outcome": "ready",
            "error_code": "",
            "transition_trace": trace,
        }
