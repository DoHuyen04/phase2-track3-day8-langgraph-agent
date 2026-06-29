# Day 08 Lab Report — LangGraph Agentic Orchestration

## 1. Team / Student

| Field | Detail |
|---|---|
| **Name** | Do Huyen |
| **Date** | 2026-06-30 |
| **LLM Provider** | OpenAI (gpt-4o-mini) |
| **Checkpointer** | Memory (MemorySaver) |
| **Scenarios Run** | 7 |
| **Success Rate** | 100.0% |

## 2. Architecture

### 2.1 Graph Design

The LangGraph StateGraph implements a support-ticket agent workflow with
**11 nodes** and **4 conditional routing functions**. The complete flow:

```text
START -> intake -> classify -> [route_after_classify]
  simple        -> answer -> finalize -> END
  tool          -> tool -> evaluate -> [route_after_evaluate]
                                        success     -> answer -> finalize -> END
                                        needs_retry -> retry -> [route_after_retry]
                                                                   attempt < max  -> tool (loop)
                                                                   attempt >= max -> dead_letter -> finalize -> END
  missing_info  -> clarify -> finalize -> END
  risky         -> risky_action -> approval -> [route_after_approval]
                                                  approved -> tool -> evaluate -> ...
                                                  rejected -> clarify -> finalize -> END
  error         -> retry -> [route_after_retry] -> ...
```

### 2.2 Node Descriptions

| # | Node | Type | Description |
|---|---:|---|---|
| 1 | intake | Transform | Normalize raw query, emit intake event |
| 2 | classify | **LLM (structured)** | Classify query into route + risk_level using structured output |
| 3 | answer | **LLM (grounded)** | Generate final response grounded in tool results, approval, query |
| 4 | tool | Mock | Execute mock tool; simulate transient errors for retry scenarios |
| 5 | evaluate | Heuristic | Check tool result for ERROR substring; gate the retry loop |
| 6 | retry | Logic | Increment attempt counter, log transient failure |
| 7 | dead_letter | Terminal | Escalate unresolvable failures after max retries |
| 8 | clarify | Response | Ask user for missing information |
| 9 | risky_action | Logic | Prepare destructive action description for human review |
| 10 | approval | HITL gate | Mock auto-approve (or real interrupt when LANGGRAPH_INTERRUPT=true) |
| 11 | finalize | Terminal | Emit final audit event; all routes converge here before END |

### 2.3 Routing Functions

| Function | Input | Decision Logic | Outputs |
|---|---|---|---|
| route_after_classify | route (string) | Dict mapping: simple->answer, tool->tool, missing_info->clarify, risky->risky_action, error->retry | answer | tool | clarify | risky_action | retry |
| route_after_evaluate | evaluation_result | needs_retry -> retry, else -> answer | answer | retry |
| route_after_retry | attempt, max_attempts | attempt < max -> tool, else -> dead_letter | tool | dead_letter |
| route_after_approval | approval.approved | approved=True -> tool, else -> clarify | tool | clarify |

## 3. State Schema

### 3.1 Overwrite Fields

| Field | Type | Default | Purpose |
|---|---|---|---|
| thread_id | str | thread-{id} | Unique thread identifier per scenario |
| scenario_id | str | from Scenario | Scenario ID for metrics tracking |
| query | str | from Scenario | User query string (normalized by intake) |
| route | str | "" | Classified intent: simple|tool|missing_info|risky|error |
| risk_level | str | "unknown" | "high" for risky actions, "low" otherwise |
| attempt | int | 0 | Current retry attempt counter |
| max_attempts | int | 3 | Maximum retries before dead_letter escalation |
| evaluation_result | str | "" | Retry-loop gate: needs_retry|success |
| pending_question | str | "" | Clarification question for missing_info route |
| proposed_action | str | "" | Description of proposed risky action |
| approval | dict | {} | HITL approval decision: {approved, reviewer, comment} |
| final_answer | str | None | None | Final response to user or escalation message |

### 3.2 Append-Only Fields (Reducer: `operator.add`)

| Field | Type | Purpose |
|---|---|---|
| messages | list[str] | Conversation audit trail (intake message, etc.) |
| tool_results | list[str] | Accumulated tool execution results across retries |
| errors | list[str] | Transient error messages from retries |
| events | list[dict] | Full audit log (LabEvent) for metrics & debugging |

## 4. Scenario Results

### 4.1 Summary

| Metric | Value |
|---|---|
| Total Scenarios | 7 |
| Success Rate | 100.0% |
| Avg Nodes Visited | 6.4 |
| Total Retries | 3 |
| Total Interrupts | 2 |
| Resume Success | False |

### 4.2 Per-Scenario Breakdown

| Scenario ID | Query | Expected | Actual | Ok | Nodes | Retries | Interrupts |
|---|---|---|---:|---:|---:|---:|
| S01_simple | How do I reset my password? | simple | simple | PASS | 4 | 0 | 0 |
| S02_tool | Please lookup order status for order 12345 | tool | tool | PASS | 6 | 0 | 0 |
| S03_missing | Can you fix it? | missing_info | missing_info | PASS | 4 | 0 | 0 |
| S04_risky | Refund this customer and send confirmation email | risky | risky | PASS | 8 | 0 | 1 |
| S05_error | Timeout failure while processing request | error | error | PASS | 10 | 2 | 0 |
| S06_delete | Delete customer account after support verification | risky | risky | PASS | 8 | 0 | 1 |
| S07_dead_letter | System failure cannot recover after multiple attempts | error | error | PASS | 5 | 1 | 0 |

### 4.3 Route Analysis

**S01_simple (4 nodes):** intake → classify → answer → finalize.
Direct FAQ route. No tools, no approval. Simplest path through the graph.

**S02_tool (6 nodes):** intake → classify → tool → evaluate → answer → finalize.
Tool execution with successful evaluation on first attempt. One extra hop vs simple.

**S03_missing (4 nodes):** intake → classify → clarify → finalize.
Vague query triggers clarification. Sets pending_question and final_answer together.

**S04_risky (8 nodes):** intake → classify → risky_action → approval → tool → evaluate → answer → finalize.
Full HITL path: risky action proposed, mock-approved, tool executed, answer generated. 1 interrupt counted.

**S05_error (10 nodes):** intake → classify → retry → tool → evaluate → retry → tool → evaluate → answer → finalize.
Error route triggers retry loop. 2 retries needed before tool succeeds on 3rd execution. Most complex path.

**S06_delete (8 nodes):** intake → classify → risky_action → approval → tool → evaluate → answer → finalize.
Destructive action (delete). Same path as S04 but different query type. Approval gate enforced.

**S07_dead_letter (5 nodes):** intake → classify → retry → tool → dead_letter → finalize.
max_attempts=1 forces immediate dead_letter escalation after single retry. Route stays 'error' (classified intent).

## 5. Failure Analysis

### 5.1 Failure Mode 1: Transient Tool Errors → Retry Loop

**Mechanism:** When `tool_node` returns an ERROR result (simulated transient failure), the retry loop activates: `evaluate → retry → tool → evaluate`. The `route_after_retry` function enforces a hard bound via `attempt < max_attempts`, preventing infinite loops.

**Escalation:** After max retries, the request is routed to `dead_letter` for escalation with a reference ID for engineering follow-up.

**Evidence:** S05_error completed with 2 retries across 10 nodes. S07_dead_letter exhausted max_attempts=1 and correctly escalated.

### 5.2 Failure Mode 2: Risky Action Without Approval

**Mechanism:** Destructive actions (refunds, deletions) must pass through the HITL approval gate. The `approval_node` uses mock auto-approve in CI/testing or real `interrupt()` when `LANGGRAPH_INTERRUPT=true`.

**Rejection path:** If rejected, the request is routed to `clarify` for alternative action from the user, preventing unauthorized destructive operations.

**Evidence:** S04_risky and S06_delete both triggered approval gates (2 total interrupts).

### 5.3 Failure Mode 3: Missing Information → Clarification Loop

**Mechanism:** Vague queries like "Can you fix it?" are classified as `missing_info`. The `ask_clarification_node` sets both `pending_question` and `final_answer` with a specific prompt asking the user to provide more context.

**Design rationale:** Rather than hallucinating an answer, the agent explicitly asks for missing details. In a real system, this would pause the workflow and wait for user response before continuing.
**Evidence:** S03_missing completed in 4 nodes with pending_question set.

## 6. Persistence & Recovery Evidence

### 6.1 Checkpointer Configuration

The graph is compiled with `MemorySaver` by default (configurable via `configs/lab.yaml`). Each scenario run uses a unique `thread_id` derived from the scenario ID (`thread-{scenario.id}`), ensuring isolated state per execution.

### 6.2 Implemented Backends

| Backend | Status | Implementation |
|---|---|---|
| `none` | ✅ | Returns None — no persistence |
| `memory` | ✅ | `MemorySaver` — in-memory, suitable for testing |
| `sqlite` | ✅ | `SqliteSaver` with WAL mode — crash-resistant local storage |
| `postgres` | ✅ | `PostgresSaver.from_conn_string()` — production persistence |

### 6.3 Thread Isolation

Each scenario invocation passes `config={"configurable": {"thread_id": state["thread_id"]}}` to `graph.invoke()`. This ensures:

- **Isolation:** Each scenario's state is independent
- **Resume capability:** With SQLite/Postgres, a crashed workflow can resume from the last checkpoint
- **Audit trail:** `get_state_history()` can replay the full execution path for debugging

## 7. Extension Work

### 7.1 Completed Extensions

1. **SQLite Persistence (Phase 3):** Implemented `SqliteSaver` with WAL mode. The checkpointer stores state snapshots after each node execution, enabling crash recovery and state history replay.

2. **Postgres Persistence:** Added `PostgresSaver.from_conn_string()` support for production-grade distributed persistence.

3. **Mock HITL with Real Interrupt:** The `approval_node` supports both mock auto-approve (for CI/testing) and real `langgraph.types.interrupt()` (when `LANGGRAPH_INTERRUPT=true`), enabling human-in-the-loop workflows.

4. **Comprehensive Test Suite:** 28 tests covering state validation, routing logic (13 tests), metrics extraction (6 tests), and end-to-end graph execution (6 smoke tests across all 5 route types).

### 7.2 Future Extensions (Not Yet Implemented)

- Streamlit UI for human approval/rejection with interrupt/resume
- `get_state_history()` time-travel debugging demo
- `Send()` parallel fan-out for concurrent tool execution
- LangSmith tracing integration for production observability
- Mermaid graph diagram export via `graph.get_graph().draw_mermaid()`

## 8. Improvement Plan

If given one more day, the following would be productionized first:

1. **Persistent checkpointing with SQLite** — enable crash recovery and state history replay via `SqliteSaver`. This allows resuming interrupted workflows and debugging via `get_state_history()`.

2. **Real LLM-as-judge for evaluation** — replace the heuristic ERROR check in `evaluate_node` with an LLM that assesses whether the tool output actually answers the user's question, improving retry accuracy.

3. **Streamlit HITL UI** — build a web interface for human approvers to review proposed actions, approve/reject with comments, and resume interrupted graphs.

4. **Parallel fan-out** — use LangGraph's `Send()` API to execute multiple tools concurrently when a query requires several lookups, reducing latency.

5. **Observability** — integrate LangSmith tracing for production debugging and latency tracking across nodes.
