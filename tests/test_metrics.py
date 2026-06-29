from langgraph_agent_lab.metrics import metric_from_state, summarize_metrics
from langgraph_agent_lab.state import make_event


def test_metric_from_state_success():
    state = {
        "scenario_id": "S",
        "route": "simple",
        "final_answer": "ok",
        "events": [make_event("intake", "completed", "ok"), make_event("answer", "completed", "ok")],
        "errors": [],
        "approval": None,
    }
    metric = metric_from_state(state, expected_route="simple", approval_required=False)
    assert metric.success is True
    assert metric.nodes_visited == 2


def test_metric_from_state_route_mismatch():
    state = {
        "scenario_id": "S",
        "route": "tool",
        "final_answer": "ok",
        "events": [],
        "errors": [],
        "approval": None,
    }
    metric = metric_from_state(state, expected_route="simple", approval_required=False)
    assert metric.success is False


def test_summarize_metrics():
    m1 = metric_from_state(
        {"scenario_id": "1", "route": "simple", "final_answer": "ok", "events": [], "errors": [], "approval": None},
        "simple",
        False,
    )
    m2 = metric_from_state(
        {"scenario_id": "2", "route": "tool", "final_answer": None, "events": [], "errors": [], "approval": None},
        "tool",
        False,
    )
    report = summarize_metrics([m1, m2])
    assert report.total_scenarios == 2
    assert 0 <= report.success_rate <= 1


def test_metric_from_state_approval_required_satisfied():
    """When approval is required and present, success should be True (route+answer OK)."""
    state = {
        "scenario_id": "S04",
        "route": "risky",
        "final_answer": "approved action completed",
        "events": [make_event("approval", "completed", "approved")],
        "errors": [],
        "approval": {"approved": True, "reviewer": "mock", "comment": "ok"},
    }
    metric = metric_from_state(state, expected_route="risky", approval_required=True)
    assert metric.success is True
    assert metric.approval_required is True
    assert metric.approval_observed is True


def test_metric_from_state_approval_required_missing():
    """When approval is required but missing, success should be False."""
    state = {
        "scenario_id": "S04",
        "route": "risky",
        "final_answer": "approved action completed",
        "events": [],
        "errors": [],
        "approval": None,
    }
    metric = metric_from_state(state, expected_route="risky", approval_required=True)
    assert metric.success is False
    assert metric.approval_required is True
    assert metric.approval_observed is False


def test_metric_from_state_retry_counting():
    """Verify retry_count and interrupt_count are counted from events."""
    state = {
        "scenario_id": "S05",
        "route": "error",
        "final_answer": "recovered after retries",
        "events": [
            make_event("intake", "completed", ""),
            make_event("classify", "completed", ""),
            make_event("retry", "retry_scheduled", ""),
            make_event("tool", "completed", ""),
            make_event("evaluate", "completed", ""),
            make_event("retry", "retry_scheduled", ""),
            make_event("tool", "completed", ""),
            make_event("evaluate", "completed", ""),
            make_event("answer", "completed", ""),
            make_event("finalize", "completed", ""),
        ],
        "errors": [],
        "approval": None,
    }
    metric = metric_from_state(state, expected_route="error", approval_required=False)
    assert metric.success is True
    assert metric.nodes_visited == 10
    assert metric.retry_count == 2
    assert metric.interrupt_count == 0
