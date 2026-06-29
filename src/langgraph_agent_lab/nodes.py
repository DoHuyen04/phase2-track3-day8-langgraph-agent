"""Node functions for the LangGraph workflow.

Each function receives AgentState and returns a partial state update dict.
Do NOT mutate input state — return new values only.

LLM REQUIREMENT:
- classify_node MUST use a real LLM call (structured output for intent classification)
- answer_node MUST use a real LLM call (grounded response generation)
- evaluate_node SHOULD use LLM-as-judge (bonus points; heuristic acceptable for base score)
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .state import AgentState, ApprovalDecision, make_event


# ─── EXAMPLE: working node (provided for reference) ──────────────────
def intake_node(state: AgentState) -> dict:
    """Normalize raw query. This node is provided as a working example."""
    query = state.get("query", "").strip()
    return {
        "query": query,
        "messages": [f"intake:{query[:40]}"],
        "events": [make_event("intake", "completed", "query normalized")],
    }


# ─── TODO(student): implement ALL nodes below ────────────────────────


class ClassificationResult(BaseModel):
    """Structured output for intent classification."""

    route: str = Field(
        description="One of: simple, tool, missing_info, risky, error"
    )
    risk_level: str = Field(
        description="'high' for risky/destructive actions, 'low' otherwise"
    )


def classify_node(state: AgentState) -> dict:
    """Classify the query into a route using an LLM.

    *** MUST use a real LLM call — keyword-only heuristics will lose points. ***

    Use .with_structured_output() or equivalent to get reliable enum classification.
    The LLM should classify into one of: simple, tool, missing_info, risky, error.
    """
    from .llm import get_llm

    llm = get_llm()
    structured_llm = llm.with_structured_output(ClassificationResult)

    query = state.get("query", "")
    system_prompt = (
        "You are an intent classifier for a support ticket system. "
        "Classify the user query into exactly one route:\n"
        "- simple: straightforward FAQ-style questions (password reset, how-to, etc.)\n"
        "- tool: requires looking up data or performing a system action (order lookup, status check, etc.)\n"
        "- missing_info: query is too vague or missing critical details (pronouns without context, incomplete sentences)\n"
        "- risky: destructive or sensitive actions (refunds, deletions, sending on behalf of user, financial changes)\n"
        "- error: query indicates a system failure, timeout, or technical error\n\n"
        "Priority: risky > tool > missing_info > error > simple\n"
        "Set risk_level='high' for risky routes, 'low' for all others."
    )

    result: ClassificationResult = structured_llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": query},
    ])

    return {
        "route": result.route,
        "risk_level": result.risk_level,
        "events": [make_event("classify", "completed", f"route={result.route}, risk={result.risk_level}")],
    }


def tool_node(state: AgentState) -> dict:
    """Execute a mock tool call.

    Simulate transient failures for error-route scenarios to test retry loops.

    Requirements:
    - Read current attempt count from state
    - If route is "error" and attempt < 2: return error result (string containing "ERROR")
    - Otherwise: return a mock success result string
    - Append result to tool_results list

    Return: {"tool_results": [result_string], "events": [make_event(...)]}
    """
    route = state.get("route", "")
    attempt = state.get("attempt", 0)

    if route == "error" and attempt < 2:
        result = f"ERROR: transient failure on attempt {attempt} — retry advised"
        event_type = "error"
    else:
        result = f"SUCCESS: mock tool executed for route={route} (attempt={attempt})"
        event_type = "completed"

    return {
        "tool_results": [result],
        "events": [make_event("tool", event_type, result)],
    }


def evaluate_node(state: AgentState) -> dict:
    """Evaluate tool results — the retry-loop gate.

    Check whether the latest tool result is satisfactory or needs retry.

    Uses heuristic (checks for "ERROR" substring in latest tool result).
    LLM-as-judge is bonus — this heuristic is acceptable for base score.

    Requirements:
    - Read the latest entry from tool_results
    - Set evaluation_result to "needs_retry" or "success"
    - This field drives route_after_evaluate conditional edge
    """
    tool_results: list[str] = state.get("tool_results", [])
    if tool_results and "ERROR" in tool_results[-1]:
        evaluation = "needs_retry"
    else:
        evaluation = "success"

    return {
        "evaluation_result": evaluation,
        "events": [make_event("evaluate", "completed", f"eval={evaluation}")],
    }


def answer_node(state: AgentState) -> dict:
    """Generate a final response using an LLM.

    *** MUST use a real LLM call — hardcoded strings will lose points. ***

    The LLM should generate a helpful response grounded in available context:
    - tool_results (if any)
    - approval decision (if risky route)
    - original query
    """
    from .llm import get_llm

    llm = get_llm(temperature=0.3)

    query = state.get("query", "")
    route = state.get("route", "simple")
    tool_results: list[str] = state.get("tool_results", [])
    approval: dict = state.get("approval", {})

    context_parts = [f"User query: {query}", f"Classified route: {route}"]
    if tool_results:
        context_parts.append(f"Tool results: {'; '.join(tool_results)}")
    if approval:
        context_parts.append(
            f"Approval: approved={approval.get('approved')}, "
            f"reviewer={approval.get('reviewer', 'N/A')}, "
            f"comment={approval.get('comment', 'N/A')}"
        )

    system_prompt = (
        "You are a helpful support agent. Generate a clear, accurate response "
        "to the user's query based on the provided context. "
        "If the query was resolved via tools or approval, explain what was done. "
        "Be concise and professional."
    )

    response = llm.invoke([
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": "\n".join(context_parts)},
    ])

    final_answer = response.content if hasattr(response, "content") else str(response)
    return {
        "final_answer": final_answer,
        "events": [make_event("answer", "completed", f"answer={final_answer[:80]}...")],
    }


def ask_clarification_node(state: AgentState) -> dict:
    """Ask for missing information instead of hallucinating.

    Generate a specific clarification question based on the vague/incomplete query.
    """
    query = state.get("query", "")

    # Build a helpful clarification question
    question = (
        f"I'd be happy to help, but I need a bit more context. "
        f"Your request \"{query}\" is unclear — could you please provide "
        f"more specific details about what you'd like me to do?"
    )

    return {
        "pending_question": question,
        "final_answer": question,
        "events": [make_event("clarify", "completed", f"question={question[:80]}")],
    }


def risky_action_node(state: AgentState) -> dict:
    """Prepare a risky action for human approval.

    Describe the proposed action and why it requires approval.
    """
    query = state.get("query", "")

    proposed = (
        f"PROPOSED RISKY ACTION: Based on query \"{query}\", "
        f"this action involves a destructive or sensitive operation "
        f"(e.g., refund, deletion, financial change). "
        f"Human approval is required before proceeding."
    )

    return {
        "proposed_action": proposed,
        "events": [make_event("risky_action", "completed", proposed[:80])],
    }


def approval_node(state: AgentState) -> dict:
    """Human-in-the-loop approval step.

    Default behavior: mock approval (approved=True) so tests and CI run offline.
    Extension: if env LANGGRAPH_INTERRUPT=true, use langgraph.types.interrupt() for real HITL.
    """
    import os

    proposed = state.get("proposed_action", "")

    if os.getenv("LANGGRAPH_INTERRUPT", "").lower() == "true":
        from langgraph.types import interrupt  # type: ignore[import-untyped]

        decision = interrupt(
            f"Approve this action?\n{proposed}\n\nReply 'yes' or 'no':"
        )
        approved = str(decision).strip().lower() == "yes"
        reviewer = "human"
        comment = str(decision)
    else:
        approved = True
        reviewer = "mock-reviewer"
        comment = "auto-approved in mock mode"

    approval = {
        "approved": approved,
        "reviewer": reviewer,
        "comment": comment,
    }

    return {
        "approval": approval,
        "events": [make_event("approval", "completed", f"approved={approved}, reviewer={reviewer}")],
    }


def retry_or_fallback_node(state: AgentState) -> dict:
    """Record a retry attempt.

    Increment the attempt counter and log the transient failure.

    Requirements:
    - Read current attempt from state, increment by 1
    - Add an error message to errors list
    - Return updated attempt count

    Return: {"attempt": int, "errors": [str], "events": [make_event(...)]}
    """
    current_attempt = state.get("attempt", 0)
    new_attempt = current_attempt + 1
    max_attempts = state.get("max_attempts", 3)

    error_msg = (
        f"Transient failure on attempt {new_attempt}/{max_attempts}: "
        f"tool returned an error, scheduling retry"
    )

    return {
        "attempt": new_attempt,
        "errors": [error_msg],
        "events": [make_event("retry", "retry_scheduled", error_msg)],
    }


def dead_letter_node(state: AgentState) -> dict:
    """Handle unresolvable failures after max retries exceeded.

    This is the third layer: retry → fallback → dead letter.
    Log the failure and set a final_answer explaining that the request could not be completed.

    Return: {"final_answer": str, "events": [make_event(...)]}
    """
    query = state.get("query", "")
    attempt = state.get("attempt", 0)
    max_attempts = state.get("max_attempts", 3)

    msg = (
        f"Unable to process your request after {attempt} attempt(s) "
        f"(max retries: {max_attempts}). "
        f"This issue has been escalated to the engineering team. "
        f"Reference: dead-letter-{state.get('scenario_id', 'unknown')}"
    )

    # Note: we do NOT overwrite the route here.
    # The classified route (e.g. "error") stays intact so grading
    # metrics can match actual_route against expected_route.
    # The dead_letter path is visible in the audit events.
    return {
        "final_answer": msg,
        "events": [make_event("dead_letter", "escalated", msg[:80])],
    }


def finalize_node(state: AgentState) -> dict:
    """Emit a final audit event. All routes must pass through here before END.

    Return: {"events": [make_event("finalize", "completed", "workflow finished")]}
    """
    return {
        "events": [make_event("finalize", "completed", "workflow finished")],
    }
