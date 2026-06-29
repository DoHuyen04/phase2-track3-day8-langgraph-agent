from pathlib import Path

from langgraph_agent_lab.scenarios import load_scenarios
from langgraph_agent_lab.state import Route, Scenario, initial_state


def test_scenario_validation():
    scenario = Scenario(id="x", query="hello", expected_route=Route.SIMPLE)
    state = initial_state(scenario)
    assert state["thread_id"] == "thread-x"
    assert state["attempt"] == 0
    assert state["events"] == []


def test_initial_state_has_required_fields():
    """Verify initial_state includes all fields needed by the graph."""
    scenario = Scenario(id="test", query="test query", expected_route=Route.SIMPLE)
    state = initial_state(scenario)
    # Original base fields
    assert "query" in state
    assert "route" in state
    assert "attempt" in state
    assert "max_attempts" in state
    assert "messages" in state
    assert "tool_results" in state
    assert "errors" in state
    assert "events" in state
    # Student-added fields (required for routing, clarification, risky actions, HITL)
    assert "evaluation_result" in state
    assert "pending_question" in state
    assert "proposed_action" in state
    assert "approval" in state


def test_load_scenarios():
    # Use absolute path so test can run from any directory
    project_root = Path(__file__).resolve().parent.parent
    path = project_root / "data" / "sample" / "scenarios.jsonl"
    scenarios = load_scenarios(str(path))
    assert len(scenarios) >= 6
    assert {item.expected_route for item in scenarios} >= {Route.SIMPLE, Route.TOOL, Route.RISKY}
