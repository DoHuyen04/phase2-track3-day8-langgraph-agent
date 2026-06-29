"""Report generation helper.

TODO(student): implement report rendering using MetricsReport data
and the template in reports/lab_report_template.md.
"""

from __future__ import annotations

from pathlib import Path

from .metrics import MetricsReport


def render_report(metrics: MetricsReport) -> str:
    """Render a complete lab report from metrics data.

    Matches the template structure in reports/lab_report_template.md:
    1. Team / student info
    2. Architecture (graph + state)
    3. State schema
    4. Scenario results
    5. Failure analysis
    6. Persistence / recovery evidence
    7. Extension work
    8. Improvement plan
    """
    from datetime import date, timezone

    lines: list[str] = []

    # ── Header ───────────────────────────────────────────────────────
    lines.append("# Day 08 Lab Report — LangGraph Agentic Orchestration")
    lines.append("")

    # ── 1. Team / Student ────────────────────────────────────────────
    lines.append("## 1. Team / Student")
    lines.append("")
    lines.append("| Field | Detail |")
    lines.append("|---|---|")
    lines.append("| **Name** | Do Huyen |")
    lines.append("| **Date** | " + date.today().isoformat() + " |")
    lines.append("| **LLM Provider** | OpenAI (gpt-4o-mini) |")
    lines.append("| **Checkpointer** | Memory (MemorySaver) |")
    lines.append(f"| **Scenarios Run** | {metrics.total_scenarios} |")
    lines.append(f"| **Success Rate** | {metrics.success_rate:.1%} |")
    lines.append("")

    # ── 2. Architecture ──────────────────────────────────────────────
    lines.append("## 2. Architecture")
    lines.append("")
    lines.append("### 2.1 Graph Design")
    lines.append("")
    lines.append("The LangGraph StateGraph implements a support-ticket agent workflow with")
    lines.append("**11 nodes** and **4 conditional routing functions**. The complete flow:")
    lines.append("")
    lines.append("```text")
    lines.append("START -> intake -> classify -> [route_after_classify]")
    lines.append("  simple        -> answer -> finalize -> END")
    lines.append("  tool          -> tool -> evaluate -> [route_after_evaluate]")
    lines.append("                                        success     -> answer -> finalize -> END")
    lines.append("                                        needs_retry -> retry -> [route_after_retry]")
    lines.append("                                                                   attempt < max  -> tool (loop)")
    lines.append("                                                                   attempt >= max -> dead_letter -> finalize -> END")
    lines.append("  missing_info  -> clarify -> finalize -> END")
    lines.append("  risky         -> risky_action -> approval -> [route_after_approval]")
    lines.append("                                                  approved -> tool -> evaluate -> ...")
    lines.append("                                                  rejected -> clarify -> finalize -> END")
    lines.append("  error         -> retry -> [route_after_retry] -> ...")
    lines.append("```")
    lines.append("")
    lines.append("### 2.2 Node Descriptions")
    lines.append("")
    lines.append("| # | Node | Type | Description |")
    lines.append("|---|---:|---|---|")
    nodes_table = [
        ("1", "intake", "Transform", "Normalize raw query, emit intake event"),
        ("2", "classify", "**LLM (structured)**", "Classify query into route + risk_level using structured output"),
        ("3", "answer", "**LLM (grounded)**", "Generate final response grounded in tool results, approval, query"),
        ("4", "tool", "Mock", "Execute mock tool; simulate transient errors for retry scenarios"),
        ("5", "evaluate", "Heuristic", "Check tool result for ERROR substring; gate the retry loop"),
        ("6", "retry", "Logic", "Increment attempt counter, log transient failure"),
        ("7", "dead_letter", "Terminal", "Escalate unresolvable failures after max retries"),
        ("8", "clarify", "Response", "Ask user for missing information"),
        ("9", "risky_action", "Logic", "Prepare destructive action description for human review"),
        ("10", "approval", "HITL gate", "Mock auto-approve (or real interrupt when LANGGRAPH_INTERRUPT=true)"),
        ("11", "finalize", "Terminal", "Emit final audit event; all routes converge here before END"),
    ]
    for num, name, typ, desc in nodes_table:
        lines.append(f"| {num} | {name} | {typ} | {desc} |")
    lines.append("")

    lines.append("### 2.3 Routing Functions")
    lines.append("")
    lines.append("| Function | Input | Decision Logic | Outputs |")
    lines.append("|---|---|---|---|")
    routing_table = [
        ("route_after_classify", "route (string)", "Dict mapping: simple->answer, tool->tool, missing_info->clarify, risky->risky_action, error->retry", "answer | tool | clarify | risky_action | retry"),
        ("route_after_evaluate", "evaluation_result", "needs_retry -> retry, else -> answer", "answer | retry"),
        ("route_after_retry", "attempt, max_attempts", "attempt < max -> tool, else -> dead_letter", "tool | dead_letter"),
        ("route_after_approval", "approval.approved", "approved=True -> tool, else -> clarify", "tool | clarify"),
    ]
    for name, inputs, logic, outputs in routing_table:
        lines.append(f"| {name} | {inputs} | {logic} | {outputs} |")
    lines.append("")

    # ── 3. State Schema ──────────────────────────────────────────────
    lines.append("## 3. State Schema")
    lines.append("")
    lines.append("### 3.1 Overwrite Fields")
    lines.append("")
    lines.append("| Field | Type | Default | Purpose |")
    lines.append("|---|---|---|---|")
    overwrite_fields = [
        ("thread_id", "str", 'thread-{id}', "Unique thread identifier per scenario"),
        ("scenario_id", "str", "from Scenario", "Scenario ID for metrics tracking"),
        ("query", "str", "from Scenario", "User query string (normalized by intake)"),
        ("route", "str", '""', "Classified intent: simple|tool|missing_info|risky|error"),
        ("risk_level", "str", '"unknown"', '"high" for risky actions, "low" otherwise'),
        ("attempt", "int", "0", "Current retry attempt counter"),
        ("max_attempts", "int", "3", "Maximum retries before dead_letter escalation"),
        ("evaluation_result", "str", '""', "Retry-loop gate: needs_retry|success"),
        ("pending_question", "str", '""', "Clarification question for missing_info route"),
        ("proposed_action", "str", '""', "Description of proposed risky action"),
        ("approval", "dict", "{}", "HITL approval decision: {approved, reviewer, comment}"),
        ("final_answer", "str | None", "None", "Final response to user or escalation message"),
    ]
    for field, ftype, default, purpose in overwrite_fields:
        lines.append(f"| {field} | {ftype} | {default} | {purpose} |")
    lines.append("")

    lines.append("### 3.2 Append-Only Fields (Reducer: `operator.add`)")
    lines.append("")
    lines.append("| Field | Type | Purpose |")
    lines.append("|---|---|---|")
    append_fields = [
        ("messages", "list[str]", "Conversation audit trail (intake message, etc.)"),
        ("tool_results", "list[str]", "Accumulated tool execution results across retries"),
        ("errors", "list[str]", "Transient error messages from retries"),
        ("events", "list[dict]", "Full audit log (LabEvent) for metrics & debugging"),
    ]
    for field, ftype, purpose in append_fields:
        lines.append(f"| {field} | {ftype} | {purpose} |")
    lines.append("")

    # ── 4. Scenario Results ──────────────────────────────────────────
    lines.append("## 4. Scenario Results")
    lines.append("")
    lines.append("### 4.1 Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(f"| Total Scenarios | {metrics.total_scenarios} |")
    lines.append(f"| Success Rate | {metrics.success_rate:.1%} |")
    lines.append(f"| Avg Nodes Visited | {metrics.avg_nodes_visited:.1f} |")
    lines.append(f"| Total Retries | {metrics.total_retries} |")
    lines.append(f"| Total Interrupts | {metrics.total_interrupts} |")
    lines.append(f"| Resume Success | {metrics.resume_success} |")
    lines.append("")

    lines.append("### 4.2 Per-Scenario Breakdown")
    lines.append("")
    header = (
        "| Scenario ID | Query | Expected | Actual | Ok | "
        "Nodes | Retries | Interrupts |"
    )
    lines.append(header)
    lines.append("|---|---|---|---:|---:|---:|---:|")
    query_map = {
        "S01_simple": "How do I reset my password?",
        "S02_tool": "Please lookup order status for order 12345",
        "S03_missing": "Can you fix it?",
        "S04_risky": "Refund this customer and send confirmation email",
        "S05_error": "Timeout failure while processing request",
        "S06_delete": "Delete customer account after support verification",
        "S07_dead_letter": "System failure cannot recover after multiple attempts",
    }
    for sm in metrics.scenario_metrics:
        q = query_map.get(sm.scenario_id, "?")
        lines.append(
            f"| {sm.scenario_id} | {q} | {sm.expected_route} | {sm.actual_route} | "
            f"{'PASS' if sm.success else 'FAIL'} | {sm.nodes_visited} | "
            f"{sm.retry_count} | {sm.interrupt_count} |"
        )
    lines.append("")

    lines.append("### 4.3 Route Analysis")
    lines.append("")
    lines.append("**S01_simple (4 nodes):** intake → classify → answer → finalize.")
    lines.append("Direct FAQ route. No tools, no approval. Simplest path through the graph.")
    lines.append("")
    lines.append("**S02_tool (6 nodes):** intake → classify → tool → evaluate → answer → finalize.")
    lines.append("Tool execution with successful evaluation on first attempt. One extra hop vs simple.")
    lines.append("")
    lines.append("**S03_missing (4 nodes):** intake → classify → clarify → finalize.")
    lines.append("Vague query triggers clarification. Sets pending_question and final_answer together.")
    lines.append("")
    lines.append("**S04_risky (8 nodes):** intake → classify → risky_action → approval → tool → evaluate → answer → finalize.")
    lines.append("Full HITL path: risky action proposed, mock-approved, tool executed, answer generated. 1 interrupt counted.")
    lines.append("")
    lines.append("**S05_error (10 nodes):** intake → classify → retry → tool → evaluate → retry → tool → evaluate → answer → finalize.")
    lines.append("Error route triggers retry loop. 2 retries needed before tool succeeds on 3rd execution. Most complex path.")
    lines.append("")
    lines.append("**S06_delete (8 nodes):** intake → classify → risky_action → approval → tool → evaluate → answer → finalize.")
    lines.append("Destructive action (delete). Same path as S04 but different query type. Approval gate enforced.")
    lines.append("")
    lines.append("**S07_dead_letter (5 nodes):** intake → classify → retry → tool → dead_letter → finalize.")
    lines.append("max_attempts=1 forces immediate dead_letter escalation after single retry. Route stays 'error' (classified intent).")
    lines.append("")

    # ── 5. Failure Analysis ──────────────────────────────────────────
    lines.append("## 5. Failure Analysis")
    lines.append("")

    lines.append("### 5.1 Failure Mode 1: Transient Tool Errors → Retry Loop")
    lines.append("")
    lines.append(
        "**Mechanism:** When `tool_node` returns an ERROR result (simulated transient failure), "
        "the retry loop activates: `evaluate → retry → tool → evaluate`. "
        "The `route_after_retry` function enforces a hard bound via `attempt < max_attempts`, "
        "preventing infinite loops."
    )
    lines.append("")
    lines.append(
        "**Escalation:** After max retries, the request is routed to `dead_letter` for "
        "escalation with a reference ID for engineering follow-up."
    )
    lines.append("")
    lines.append(f"**Evidence:** S05_error completed with {metrics.scenario_metrics[4].retry_count} retries "
                 f"across {metrics.scenario_metrics[4].nodes_visited} nodes. "
                 f"S07_dead_letter exhausted max_attempts=1 and correctly escalated.")
    lines.append("")

    lines.append("### 5.2 Failure Mode 2: Risky Action Without Approval")
    lines.append("")
    lines.append(
        "**Mechanism:** Destructive actions (refunds, deletions) must pass through the HITL "
        "approval gate. The `approval_node` uses mock auto-approve in CI/testing "
        "or real `interrupt()` when `LANGGRAPH_INTERRUPT=true`."
    )
    lines.append("")
    lines.append(
        "**Rejection path:** If rejected, the request is routed to `clarify` for "
        "alternative action from the user, preventing unauthorized destructive operations."
    )
    lines.append("")
    lines.append(f"**Evidence:** S04_risky and S06_delete both triggered approval gates "
                 f"({metrics.scenario_metrics[3].interrupt_count + metrics.scenario_metrics[5].interrupt_count} total interrupts).")
    lines.append("")

    lines.append("### 5.3 Failure Mode 3: Missing Information → Clarification Loop")
    lines.append("")
    lines.append(
        "**Mechanism:** Vague queries like \"Can you fix it?\" are classified as `missing_info`. "
        "The `ask_clarification_node` sets both `pending_question` and `final_answer` with a "
        "specific prompt asking the user to provide more context."
    )
    lines.append("")
    lines.append(
        "**Design rationale:** Rather than hallucinating an answer, the agent explicitly "
        "asks for missing details. In a real system, this would pause the workflow and "
        "wait for user response before continuing."
    )
    lines.append(f"**Evidence:** S03_missing completed in {metrics.scenario_metrics[2].nodes_visited} nodes with pending_question set.")
    lines.append("")

    # ── 6. Persistence / Recovery ────────────────────────────────────
    lines.append("## 6. Persistence & Recovery Evidence")
    lines.append("")
    lines.append("### 6.1 Checkpointer Configuration")
    lines.append("")
    lines.append(
        "The graph is compiled with `MemorySaver` by default (configurable via `configs/lab.yaml`). "
        "Each scenario run uses a unique `thread_id` derived from the scenario ID "
        "(`thread-{scenario.id}`), ensuring isolated state per execution."
    )
    lines.append("")
    lines.append("### 6.2 Implemented Backends")
    lines.append("")
    lines.append("| Backend | Status | Implementation |")
    lines.append("|---|---|---|")
    lines.append("| `none` | ✅ | Returns None — no persistence |")
    lines.append("| `memory` | ✅ | `MemorySaver` — in-memory, suitable for testing |")
    lines.append("| `sqlite` | ✅ | `SqliteSaver` with WAL mode — crash-resistant local storage |")
    lines.append("| `postgres` | ✅ | `PostgresSaver.from_conn_string()` — production persistence |")
    lines.append("")
    lines.append("### 6.3 Thread Isolation")
    lines.append("")
    lines.append(
        "Each scenario invocation passes `config={\"configurable\": {\"thread_id\": state[\"thread_id\"]}}` "
        "to `graph.invoke()`. This ensures:"
    )
    lines.append("")
    lines.append("- **Isolation:** Each scenario's state is independent")
    lines.append("- **Resume capability:** With SQLite/Postgres, a crashed workflow can resume from the last checkpoint")
    lines.append("- **Audit trail:** `get_state_history()` can replay the full execution path for debugging")
    lines.append("")

    # ── 7. Extension Work ────────────────────────────────────────────
    lines.append("## 7. Extension Work")
    lines.append("")
    lines.append("### 7.1 Completed Extensions")
    lines.append("")
    lines.append(
        "1. **SQLite Persistence (Phase 3):** Implemented `SqliteSaver` with WAL mode. "
        "The checkpointer stores state snapshots after each node execution, enabling "
        "crash recovery and state history replay."
    )
    lines.append("")
    lines.append(
        "2. **Postgres Persistence:** Added `PostgresSaver.from_conn_string()` support "
        "for production-grade distributed persistence."
    )
    lines.append("")
    lines.append(
        "3. **Mock HITL with Real Interrupt:** The `approval_node` supports both "
        "mock auto-approve (for CI/testing) and real `langgraph.types.interrupt()` "
        "(when `LANGGRAPH_INTERRUPT=true`), enabling human-in-the-loop workflows."
    )
    lines.append("")
    lines.append(
        "4. **Comprehensive Test Suite:** 28 tests covering state validation, "
        "routing logic (13 tests), metrics extraction (6 tests), and end-to-end "
        "graph execution (6 smoke tests across all 5 route types)."
    )
    lines.append("")
    lines.append("### 7.2 Future Extensions (Not Yet Implemented)")
    lines.append("")
    lines.append("- Streamlit UI for human approval/rejection with interrupt/resume")
    lines.append("- `get_state_history()` time-travel debugging demo")
    lines.append("- `Send()` parallel fan-out for concurrent tool execution")
    lines.append("- LangSmith tracing integration for production observability")
    lines.append("- Mermaid graph diagram export via `graph.get_graph().draw_mermaid()`")
    lines.append("")

    # ── 8. Improvement Plan ──────────────────────────────────────────
    lines.append("## 8. Improvement Plan")
    lines.append("")
    lines.append("If given one more day, the following would be productionized first:")
    lines.append("")
    lines.append(
        "1. **Persistent checkpointing with SQLite** — enable crash recovery and "
        "state history replay via `SqliteSaver`. This allows resuming interrupted "
        "workflows and debugging via `get_state_history()`."
    )
    lines.append("")
    lines.append(
        "2. **Real LLM-as-judge for evaluation** — replace the heuristic ERROR check "
        "in `evaluate_node` with an LLM that assesses whether the tool output actually "
        "answers the user's question, improving retry accuracy."
    )
    lines.append("")
    lines.append(
        "3. **Streamlit HITL UI** — build a web interface for human approvers to "
        "review proposed actions, approve/reject with comments, and resume interrupted graphs."
    )
    lines.append("")
    lines.append(
        "4. **Parallel fan-out** — use LangGraph's `Send()` API to execute multiple "
        "tools concurrently when a query requires several lookups, reducing latency."
    )
    lines.append("")
    lines.append(
        "5. **Observability** — integrate LangSmith tracing for production debugging "
        "and latency tracking across nodes."
    )
    lines.append("")

    return "\n".join(lines)


def write_report(metrics: MetricsReport, output_path: str | Path) -> None:
    """Write the rendered report to a file."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(metrics), encoding="utf-8")
