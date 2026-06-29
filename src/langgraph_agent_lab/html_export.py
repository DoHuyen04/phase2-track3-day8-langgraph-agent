"""Self-contained HTML dashboard generator.

Runs all scenarios and produces a single .html file with:
  - Tab 1: Dashboard (KPIs + results table)
  - Tab 2: Graph Explorer (SVG diagram with route highlighting)
  - Tab 3: Scenario Traces (per-scenario event timeline)

Usage:
    python -m langgraph_agent_lab.cli export-html --config configs/lab.yaml --output ui/dashboard.html
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .graph import build_graph
from .metrics import MetricsReport, metric_from_state, summarize_metrics
from .persistence import build_checkpointer
from .scenarios import load_scenarios, Scenario
from .state import initial_state


# ── Data Collector ───────────────────────────────────────────────────────────

def collect_traces(config_path: str | Path) -> dict[str, Any]:
    """Run all scenarios and collect complete trace data.

    Returns a dict with:
      - metrics: full MetricsReport
      - scenarios: list of {scenario, final_state, events, route_path}
      - graph: static graph structure (nodes, edges)
    """
    import yaml

    cfg = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    scenarios = load_scenarios(cfg["scenarios_path"])
    checkpointer = build_checkpointer(
        cfg.get("checkpointer", "memory"), cfg.get("database_url")
    )
    graph = build_graph(checkpointer=checkpointer)

    all_metrics: list[Any] = []
    scenario_traces: list[dict[str, Any]] = []

    for scenario in scenarios:
        state = initial_state(scenario)
        run_config = {"configurable": {"thread_id": state["thread_id"]}}
        final_state = graph.invoke(state, config=run_config)
        m = metric_from_state(
            final_state,
            expected_route=scenario.expected_route.value,
            approval_required=scenario.requires_approval,
        )
        all_metrics.append(m)
        scenario_traces.append({
            "scenario": scenario.model_dump(),
            "final_state": _serialize_state(final_state),
            "metric": m.model_dump(),
        })

    report = summarize_metrics(all_metrics)

    return {
        "metrics": report.model_dump(),
        "scenarios": scenario_traces,
        "graph": _graph_structure(),
    }


def _serialize_state(state: dict[str, Any]) -> dict[str, Any]:
    """Convert state to JSON-serializable dict (handle non-serializable values)."""
    out: dict[str, Any] = {}
    for key, value in state.items():
        if key == "approval" and isinstance(value, dict):
            out[key] = dict(value)
        elif isinstance(value, (str, int, float, bool, type(None), list, dict)):
            out[key] = value
        else:
            out[key] = str(value)
    return out


# ── Graph Structure (static, known from graph.py) ────────────────────────────

def _graph_structure() -> dict[str, Any]:
    """Return the static graph structure for the HTML renderer."""
    return {
        "nodes": [
            {"id": "__start__", "label": "START", "category": "terminal", "x": 20, "y": 280},
            {"id": "intake", "label": "intake", "category": "logic", "x": 145, "y": 280},
            {"id": "classify", "label": "classify", "category": "llm", "x": 270, "y": 280},
            # Row 2: branches from classify
            {"id": "answer", "label": "answer", "category": "llm", "x": 20, "y": 100},
            {"id": "tool", "label": "tool", "category": "tool", "x": 180, "y": 160},
            {"id": "clarify", "label": "clarify", "category": "terminal", "x": 340, "y": 100},
            {"id": "risky_action", "label": "risky_action", "category": "logic", "x": 465, "y": 100},
            {"id": "retry", "label": "retry", "category": "logic", "x": 590, "y": 160},
            # Row 3
            {"id": "evaluate", "label": "evaluate", "category": "llm", "x": 180, "y": 400},
            {"id": "approval", "label": "approval", "category": "logic", "x": 465, "y": 210},
            {"id": "dead_letter", "label": "dead_letter", "category": "terminal", "x": 590, "y": 400},
            # Row 4
            {"id": "finalize", "label": "finalize", "category": "terminal", "x": 300, "y": 500},
            {"id": "__end__", "label": "END", "category": "terminal", "x": 300, "y": 570},
        ],
        "edges": [
            # Fixed edges
            {"from": "__start__", "to": "intake", "type": "fixed"},
            {"from": "intake", "to": "classify", "type": "fixed"},
            {"from": "tool", "to": "evaluate", "type": "fixed"},
            {"from": "answer", "to": "finalize", "type": "fixed"},
            {"from": "clarify", "to": "finalize", "type": "fixed"},
            {"from": "risky_action", "to": "approval", "type": "fixed"},
            {"from": "dead_letter", "to": "finalize", "type": "fixed"},
            {"from": "finalize", "to": "__end__", "type": "fixed"},
            # Conditional edges
            {"from": "classify", "to": "answer", "type": "conditional", "condition": "simple"},
            {"from": "classify", "to": "tool", "type": "conditional", "condition": "tool"},
            {"from": "classify", "to": "clarify", "type": "conditional", "condition": "missing_info"},
            {"from": "classify", "to": "risky_action", "type": "conditional", "condition": "risky"},
            {"from": "classify", "to": "retry", "type": "conditional", "condition": "error"},
            {"from": "evaluate", "to": "answer", "type": "conditional", "condition": "success"},
            {"from": "evaluate", "to": "retry", "type": "conditional", "condition": "needs_retry"},
            {"from": "retry", "to": "tool", "type": "conditional", "condition": "attempt < max"},
            {"from": "retry", "to": "dead_letter", "type": "conditional", "condition": "attempt >= max"},
            {"from": "approval", "to": "tool", "type": "conditional", "condition": "approved"},
            {"from": "approval", "to": "clarify", "type": "conditional", "condition": "rejected"},
        ],
    }


# ── HTML Template ────────────────────────────────────────────────────────────

def _escape_json(obj: Any) -> str:
    """JSON-encode for embedding in <script> tag."""
    return json.dumps(obj, indent=2, ensure_ascii=False)


def generate_html(data: dict[str, Any]) -> str:
    """Generate a complete self-contained HTML dashboard from trace data."""
    json_data = _escape_json(data)
    return _HTML_TEMPLATE.replace("/* __DATA_PLACEHOLDER__ */", json_data)


# ── CLI Entry Point ──────────────────────────────────────────────────────────

def export_html(config_path: str | Path, output_path: str | Path) -> None:
    """Run scenarios and write a self-contained HTML dashboard."""
    data = collect_traces(str(config_path))
    html = generate_html(data)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"HTML dashboard written to {out}")


# ── HTML Template (generated once at module import) ──────────────────────────

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LangGraph Agent Lab - Trace Viewer</title>
<style>
:root {
  --bg: #f8fafc; --card: #fff; --text: #1e293b; --muted: #64748b;
  --border: #e2e8f0; --blue: #2563eb; --green: #16a34a; --amber: #d97706;
  --red: #dc2626; --purple: #9333ea; --gray: #6b7280;
  --llm: #dbeafe; --tool: #dcfce7; --logic: #fef3c7; --terminal: #f1f5f9;
  --llm-border: #93c5fd; --tool-border: #86efac;
  --logic-border: #fcd34d; --terminal-border: #cbd5e1;
  --shadow: 0 1px 3px rgba(0,0,0,.1);
  --radius: 8px;
}
* { box-sizing:border-box; margin:0; padding:0 }
body { font:14px/1.5 system-ui,sans-serif; background:var(--bg); color:var(--text) }

/* Nav tabs */
nav { background:var(--card); border-bottom:1px solid var(--border); padding:0 20px;
  display:flex; gap:0; align-items:stretch; box-shadow:var(--shadow); position:sticky; top:0; z-index:10 }
nav button { background:none; border:none; padding:14px 24px; cursor:pointer;
  font-size:14px; color:var(--muted); border-bottom:3px solid transparent;
  transition:all .15s; font-weight:500 }
nav button:hover { color:var(--text); background:#f8fafc }
nav button.active { color:var(--blue); border-bottom-color:var(--blue); font-weight:600 }
nav .brand { font-weight:700; font-size:16px; color:var(--text);
  display:flex; align-items:center; margin-right:auto; padding-right:40px }

/* Cards */
.card { background:var(--card); border:1px solid var(--border);
  border-radius:var(--radius); padding:20px; margin:16px 20px; box-shadow:var(--shadow) }
.card h2 { font-size:18px; margin-bottom:12px; color:var(--text) }
.card h3 { font-size:15px; margin:16px 0 8px; color:var(--text) }

/* KPIs */
.kpi-row { display:flex; gap:12px; flex-wrap:wrap; margin:0 20px 16px }
.kpi { flex:1; min-width:140px; background:var(--card); border:1px solid var(--border);
  border-radius:var(--radius); padding:16px 20px; box-shadow:var(--shadow) }
.kpi .label { font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.5px }
.kpi .value { font-size:28px; font-weight:700; margin-top:4px }
.kpi .value.green { color:var(--green) } .kpi .value.red { color:var(--red) }
.kpi .value.blue { color:var(--blue) } .kpi .value.amber { color:var(--amber) }

/* Tables */
table { width:100%; border-collapse:collapse; font-size:13px }
th { text-align:left; padding:8px 12px; border-bottom:2px solid var(--border);
  font-weight:600; color:var(--muted); font-size:12px; text-transform:uppercase }
td { padding:8px 12px; border-bottom:1px solid var(--border) }
tr:hover { background:#f8fafc }
.badge { display:inline-block; padding:2px 10px; border-radius:12px;
  font-size:11px; font-weight:600 }
.badge.pass { background:#dcfce7; color:#166534 }
.badge.fail { background:#fecaca; color:#991b1b }

/* Tabs content */
.tab-content { display:none }
.tab-content.active { display:block }

/* Graph SVG */
.graph-svg { width:100%; max-width:800px; display:block; margin:0 auto }
.legend { display:flex; gap:16px; flex-wrap:wrap; margin-top:12px }
.legend-item { display:flex; align-items:center; gap:6px; font-size:13px }
.legend-dot { width:12px; height:12px; border-radius:3px }

/* Trace timeline */
.trace-card { border-left:4px solid var(--border); padding:8px 16px; margin:4px 0;
  background:#fff; border-radius:0 var(--radius) var(--radius) 0 }
.trace-card.llm { border-left-color:var(--llm-border); background:var(--llm) }
.trace-card.tool { border-left-color:var(--tool-border); background:var(--tool) }
.trace-card.logic { border-left-color:var(--logic-border); background:var(--logic) }
.trace-card.terminal { border-left-color:var(--terminal-border); background:var(--terminal) }
.trace-card .trace-header { display:flex; justify-content:space-between; align-items:center }
.trace-card .trace-step { font-size:11px; color:var(--muted); min-width:30px }
.trace-card .trace-node { font-weight:600; font-size:13px }
.trace-card .trace-type { font-size:11px; color:var(--muted); text-transform:uppercase }
.trace-card .trace-msg { font-size:13px; color:var(--text); margin-top:4px }
.trace-card .trace-arrow { font-size:12px; color:var(--muted); margin-top:4px;
  padding-left:8px; border-left:2px dotted var(--border) }

/* Route badge path */
.route-path { display:flex; align-items:center; gap:4px; flex-wrap:wrap; margin:8px 0 }
.route-node { display:inline-block; padding:3px 10px; border-radius:12px;
  font-size:11px; font-weight:600; color:#fff }
.route-arrow { color:var(--muted); font-size:12px; margin:0 2px }
.route-node.llm { background:var(--blue) } .route-node.tool { background:var(--green) }
.route-node.logic { background:var(--amber); color:#78350f } .route-node.terminal { background:var(--gray) }

/* Button */
.btn { padding:8px 16px; border:1px solid var(--border); border-radius:6px;
  background:var(--card); cursor:pointer; font-size:13px; transition:all .15s }
.btn:hover { background:#f1f5f9 }
.btn.primary { background:var(--blue); color:#fff; border-color:var(--blue) }
.btn.primary:hover { opacity:.9 }

/* Filter bar */
.filter-bar { display:flex; gap:8px; margin-bottom:16px; flex-wrap:wrap; align-items:center }

/* Answer box */
.answer-box { background:#f8fafc; border:1px solid var(--border); border-radius:var(--radius);
  padding:12px 16px; margin:8px 0; font-size:14px; line-height:1.6 }
.answer-box.pending { border-left:4px solid var(--amber) }

/* Hidden utility */
.hidden { display:none }
</style>
</head>
<body>

<nav>
  <span class="brand">&#x1F917; LangGraph Agent Lab</span>
  <button class="active" onclick="switchTab('dashboard')">&#x1F4CA; Dashboard</button>
  <button onclick="switchTab('graph')">&#x1F578; Graph Explorer</button>
  <button onclick="switchTab('traces')">&#x1F50D; Scenario Traces</button>
</nav>

<div id="tab-dashboard" class="tab-content active"></div>
<div id="tab-graph" class="tab-content"></div>
<div id="tab-traces" class="tab-content"></div>

<script>
/* ── Embedded trace data ── */
var DATA = /* __DATA_PLACEHOLDER__ */;

/* ── Color mappings ── */
var CATEGORY = {
  intake:'logic', classify:'llm', answer:'llm', tool:'tool',
  evaluate:'llm', retry:'logic', dead_letter:'terminal', clarify:'terminal',
  risky_action:'logic', approval:'logic', finalize:'terminal',
  __start__:'terminal', __end__:'terminal'
};
var CAT_LABEL = {llm:'LLM', tool:'Tool', logic:'Logic', terminal:'Terminal'};
var CAT_COLOR = {llm:'#2563eb', tool:'#16a34a', logic:'#d97706', terminal:'#6b7280'};

/* ── Tab switching ── */
function switchTab(name) {
  document.querySelectorAll('.tab-content').forEach(function(el){ el.classList.remove('active') });
  document.querySelectorAll('nav button').forEach(function(b){ b.classList.remove('active') });
  document.getElementById('tab-' + name).classList.add('active');
  var btns = document.querySelectorAll('nav button');
  for (var i = 0; i < btns.length; i++) {
    if (btns[i].textContent.toLowerCase().indexOf(name) !== -1) btns[i].classList.add('active');
  }
}

/* ── Tab 1: Dashboard ── */
function renderDashboard() {
  var m = DATA.metrics;
  var html = '';
  html += '<div class="kpi-row">';
  html += kpi('Total Scenarios', m.total_scenarios, 'blue');
  html += kpi('Success Rate', (m.success_rate * 100).toFixed(0) + '%', m.success_rate >= 0.9 ? 'green' : 'amber');
  html += kpi('Avg Nodes', m.avg_nodes_visited.toFixed(1), 'blue');
  html += kpi('Total Retries', m.total_retries, m.total_retries > 0 ? 'amber' : 'green');
  html += kpi('Interrupts', m.total_interrupts, m.total_interrupts > 0 ? 'amber' : 'green');
  html += kpi('Resume', m.resume_success ? 'YES' : 'NO', m.resume_success ? 'green' : 'red');
  html += '</div>';

  html += '<div class="card"><h2>Scenario Results</h2><table>';
  html += '<thead><tr><th>ID</th><th>Query</th><th>Expected</th><th>Actual</th><th>Nodes</th><th>Retries</th><th>Interrupts</th><th>Outcome</th></tr></thead><tbody>';
  for (var i = 0; i < DATA.scenarios.length; i++) {
    var t = DATA.scenarios[i];
    var met = t.metric;
    var pass = met.success;
    html += '<tr>';
    html += '<td><strong>' + esc(met.scenario_id) + '</strong></td>';
    html += '<td>' + esc(t.scenario.query.substring(0, 60)) + '</td>';
    html += '<td>' + esc(met.expected_route) + '</td>';
    html += '<td>' + esc(met.actual_route || '?') + '</td>';
    html += '<td>' + met.nodes_visited + '</td>';
    html += '<td>' + met.retry_count + '</td>';
    html += '<td>' + met.interrupt_count + '</td>';
    html += '<td><span class="badge ' + (pass ? 'pass' : 'fail') + '">' + (pass ? 'PASS' : 'FAIL') + '</span></td>';
    html += '</tr>';
  }
  html += '</tbody></table></div>';

  /* Error summary */
  var errors = [];
  for (var i = 0; i < DATA.scenarios.length; i++) {
    var errs = DATA.scenarios[i].metric.errors || [];
    for (var j = 0; j < errs.length; j++) errors.push({sid: DATA.scenarios[i].scenario.id, msg: errs[j]});
  }
  if (errors.length > 0) {
    html += '<div class="card"><h2>Error Details (' + errors.length + ')</h2>';
    for (var k = 0; k < errors.length; k++) {
      html += '<p style="font-family:monospace;font-size:12px;margin:4px 0"><strong>' + esc(errors[k].sid) + ':</strong> ' + esc(errors[k].msg) + '</p>';
    }
    html += '</div>';
  }

  document.getElementById('tab-dashboard').innerHTML = html;
}

function kpi(label, value, color) {
  return '<div class="kpi"><div class="label">' + esc(label) + '</div><div class="value ' + color + '">' + value + '</div></div>';
}

/* ── Tab 2: Graph Explorer ── */
function renderGraph() {
  var graph = DATA.graph;
  var nodes = graph.nodes;
  var edges = graph.edges;
  var svgW = 800, svgH = 620;

  var html = '<div class="card"><h2>State Graph</h2>';
  html += '<div class="filter-bar">';
  html += '<span style="font-size:13px;color:var(--muted)">Highlight route:</span> ';
  var routes = ['All', 'simple', 'tool', 'missing_info', 'risky', 'error', 'dead_letter'];
  for (var r = 0; r < routes.length; r++) {
    html += '<button class="btn" onclick="highlightRoute(\'' + routes[r] + '\')">' + routes[r] + '</button>';
  }
  html += '</div>';

  /* SVG */
  html += '<svg class="graph-svg" viewBox="0 0 ' + svgW + ' ' + svgH + '" style="background:#fafbfc;border:1px solid var(--border);border-radius:8px">';

  /* Defs: arrow marker */
  html += '<defs><marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0,0 L10,5 L0,10 Z" fill="#94a3b8"/></marker></defs>';

  /* Edges */
  for (var e = 0; e < edges.length; e++) {
    var edge = edges[e];
    var from = findNode(nodes, edge.from);
    var to = findNode(nodes, edge.to);
    if (!from || !to) continue;
    var x1 = from.x, y1 = from.y, x2 = to.x, y2 = to.y;
    /* Offset slightly for parallel edges */
    var dx = x2 - x1, dy = y2 - y1;
    var dist = Math.sqrt(dx*dx + dy*dy);
    if (dist > 0) {
      var ox = -dy / dist * 4;
      var oy = dx / dist * 4;
      x1 += ox; y1 += oy; x2 += ox; y2 += oy;
    }
    var cls = edge.type === 'conditional' ? 'conditional' : 'fixed';
    var stroke = edge.type === 'conditional' ? '#94a3b8' : '#64748b';
    var dash = edge.type === 'conditional' ? '5,3' : '';
    html += '<line x1="' + x1 + '" y1="' + y1 + '" x2="' + x2 + '" y2="' + (y2 - 10) +
            '" stroke="' + stroke + '" stroke-width="1.5" stroke-dasharray="' + dash +
            '" marker-end="url(#arrow)" data-from="' + edge.from + '" data-to="' + edge.to + '" data-type="' + edge.type + '" data-condition="' + (edge.condition||'') + '"/>';
    /* Label */
    if (edge.condition) {
      html += '<text x="' + ((x1+x2)/2) + '" y="' + ((y1+y2)/2 - 6) +
              '" text-anchor="middle" font-size="9" fill="#94a3b8" font-family="monospace">' +
              esc(edge.condition.substring(0, 16)) + '</text>';
    }
  }

  /* Nodes */
  for (var n = 0; n < nodes.length; n++) {
    var node = nodes[n];
    var cat = CATEGORY[node.id] || 'terminal';
    var color = CAT_COLOR[cat];
    var w = node.id.length * 9 + 16;
    var h = 28;
    var rx = 6;
    html += '<rect x="' + (node.x - w/2) + '" y="' + (node.y - h/2) +
            '" width="' + w + '" height="' + h + '" rx="' + rx +
            '" fill="' + color + '" opacity="0.9" data-node="' + node.id + '" data-category="' + cat + '"/>';
    html += '<text x="' + node.x + '" y="' + (node.y + 5) +
            '" text-anchor="middle" font-size="11" fill="white" font-family="monospace" font-weight="600">' +
            esc(node.label) + '</text>';
  }

  html += '</svg>';

  /* Legend */
  html += '<div class="legend">';
  var cats = ['llm', 'tool', 'logic', 'terminal'];
  for (var c = 0; c < cats.length; c++) {
    html += '<div class="legend-item"><div class="legend-dot" style="background:' + CAT_COLOR[cats[c]] + '"></div> ' + CAT_LABEL[cats[c]] + '</div>';
  }
  html += '<div class="legend-item"><svg width="30" height="12"><line x1="0" y1="6" x2="28" y2="6" stroke="#94a3b8" stroke-dasharray="5,3"/></svg> Conditional</div>';
  html += '</div></div>';

  document.getElementById('tab-graph').innerHTML = html;
}

function findNode(nodes, id) {
  for (var i = 0; i < nodes.length; i++) { if (nodes[i].id === id) return nodes[i]; }
  return null;
}

var ROUTE_PATHS = {
  simple: ['intake','classify','answer','finalize'],
  tool: ['intake','classify','tool','evaluate','answer','finalize'],
  missing_info: ['intake','classify','clarify','finalize'],
  risky: ['intake','classify','risky_action','approval','tool','evaluate','answer','finalize'],
  error: ['intake','classify','retry','tool','evaluate','answer','finalize'],
  dead_letter: ['intake','classify','retry','tool','dead_letter','finalize']
};

function highlightRoute(route) {
  var path = ROUTE_PATHS[route] || [];
  var allRects = document.querySelectorAll('#tab-graph rect[data-node]');
  var allLines = document.querySelectorAll('#tab-graph line');

  for (var r = 0; r < allRects.length; r++) {
    var nid = allRects[r].getAttribute('data-node');
    var cat = allRects[r].getAttribute('data-category');
    var color = CAT_COLOR[cat] || '#6b7280';
    if (route === 'All' || path.indexOf(nid) !== -1) {
      allRects[r].setAttribute('fill', color);
      allRects[r].setAttribute('opacity', '0.9');
    } else {
      allRects[r].setAttribute('fill', '#cbd5e1');
      allRects[r].setAttribute('opacity', '0.4');
    }
  }

  for (var l = 0; l < allLines.length; l++) {
    var from = allLines[l].getAttribute('data-from');
    var to = allLines[l].getAttribute('data-to');
    if (route === 'All' || (path.indexOf(from) !== -1 && path.indexOf(to) !== -1)) {
      allLines[l].setAttribute('stroke', '#475569');
      allLines[l].setAttribute('opacity', '1');
    } else {
      allLines[l].setAttribute('stroke', '#cbd5e1');
      allLines[l].setAttribute('opacity', '0.25');
    }
  }
}

/* ── Tab 3: Scenario Traces ── */
var currentTraceIndex = -1;

function renderTraces() {
  var html = '<div class="card"><h2>Select Scenario</h2>';
  html += '<div class="filter-bar">';
  for (var i = 0; i < DATA.scenarios.length; i++) {
    var s = DATA.scenarios[i];
    var m = s.metric;
    var cls = m.success ? 'primary' : '';
    html += '<button class="btn ' + cls + '" onclick="showTrace(' + i + ')" id="trace-btn-' + i + '">' +
            esc(s.scenario.id) + ' <span style="font-size:10px">(' + esc(m.actual_route||'?') + ')</span></button>';
  }
  html += '</div>';
  html += '<div id="trace-detail"><p style="color:var(--muted)">Select a scenario above to view its execution trace.</p></div>';
  html += '</div>';
  document.getElementById('tab-traces').innerHTML = html;
  currentTraceIndex = -1;
}

function showTrace(index) {
  if (currentTraceIndex === index) return;
  currentTraceIndex = index;

  /* Update button states */
  var btns = document.querySelectorAll('#tab-traces .filter-bar button');
  for (var b = 0; b < btns.length; b++) btns[b].classList.remove('primary');
  var activeBtn = document.getElementById('trace-btn-' + index);
  if (activeBtn) activeBtn.classList.add('primary');

  var s = DATA.scenarios[index];
  var fs = s.final_state;
  var m = s.metric;
  var events = fs.events || [];

  var html = '';
  /* Header */
  var pass = m.success;
  html += '<div class="card" style="border-top:4px solid ' + (pass ? 'var(--green)' : 'var(--red)') + '">';
  html += '<h2>' + (pass ? '&#x2705;' : '&#x274C;') + ' ' + esc(s.scenario.id) + ': ' + esc(s.scenario.query) + '</h2>';

  /* Metric row */
  html += '<div class="kpi-row" style="margin:0">';
  html += kpi('Route', esc(m.actual_route||'?'), 'blue');
  html += kpi('Expected', esc(m.expected_route), 'blue');
  html += kpi('Nodes', m.nodes_visited, 'blue');
  html += kpi('Retries', m.retry_count, m.retry_count > 0 ? 'amber' : 'green');
  html += kpi('Interrupts', m.interrupt_count, m.interrupt_count > 0 ? 'amber' : 'green');
  html += kpi('Approval Seen', m.approval_observed ? 'Yes' : 'No', m.approval_observed ? 'green' : 'red');
  html += '</div>';

  /* Final answer */
  if (fs.final_answer) {
    html += '<h3>Final Output</h3><div class="answer-box">' + esc(fs.final_answer) + '</div>';
  }
  if (fs.pending_question) {
    html += '<h3>Pending Question</h3><div class="answer-box pending">' + esc(fs.pending_question) + '</div>';
  }

  /* Route path badge */
  html += '<h3>Route Path</h3><div class="route-path">';
  var ordered = uniqueNodes(events);
  for (var n = 0; n < ordered.length; n++) {
    if (n > 0) html += '<span class="route-arrow">&#x2192;</span>';
    var cat = CATEGORY[ordered[n]] || 'terminal';
    html += '<span class="route-node ' + cat + '">' + esc(ordered[n]) + '</span>';
  }
  html += '</div>';

  /* Event timeline */
  html += '<h3>Execution Trace (' + events.length + ' events)</h3>';
  if (events.length === 0) {
    html += '<p style="color:var(--muted)">No events recorded.</p>';
  } else {
    for (var e = 0; e < events.length; e++) {
      var ev = events[e];
      var node = ev.node || '?';
      var cat = CATEGORY[node] || 'terminal';
      var etype = ev.event_type || '';
      var msg = ev.message || '';
      html += '<div class="trace-card ' + cat + '">';
      html += '<div class="trace-header">';
      html += '<span><span class="trace-step">#' + (e + 1) + '</span> <span class="trace-node">' + esc(node) + '</span></span>';
      html += '<span class="trace-type">' + esc(etype) + '</span>';
      html += '</div>';
      html += '<div class="trace-msg">' + esc(msg) + '</div>';
      /* Show transition to next node */
      if (e < events.length - 1) {
        var nextNode = events[e+1].node || '';
        var cond = getTransition(node, nextNode);
        if (cond) {
          html += '<div class="trace-arrow">' + esc(cond) + '</div>';
        }
      }
      html += '</div>';
    }
  }

  /* Retry loop visualization */
  var cycles = findRetryCycles(events);
  if (cycles.length > 0) {
    html += '<h3>Retry Loop Visualization</h3>';
    for (var r = 0; r < cycles.length; r++) {
      var cycle = cycles[r];
      html += '<div style="background:#fef3c7;border:1px solid #fcd34d;border-radius:8px;padding:12px;margin:8px 0">';
      html += '<strong>Attempt ' + (r + 1) + ':</strong> ';
      html += cycle.map(function(ev){ return '<code>' + esc(ev.node) + '</code>'; }).join(' &#x2192; ');
      html += '</div>';
    }
  }

  /* Raw state */
  html += '<details style="margin-top:16px"><summary style="cursor:pointer;font-weight:600">State at Completion (JSON)</summary>';
  var stateCopy = {};
  var keys = Object.keys(fs);
  for (var k = 0; k < keys.length; k++) {
    if (keys[k] !== 'events') stateCopy[keys[k]] = fs[keys[k]];
  }
  html += '<pre style="background:#f8fafc;padding:12px;border-radius:6px;font-size:11px;overflow-x:auto;max-height:400px">' + esc(JSON.stringify(stateCopy, null, 2)) + '</pre>';
  html += '</details>';

  html += '</div>';
  document.getElementById('trace-detail').innerHTML = html;
}

function uniqueNodes(events) {
  var seen = [];
  for (var i = 0; i < events.length; i++) {
    var n = events[i].node || '';
    if (seen.length === 0 || seen[seen.length - 1] !== n) seen.push(n);
  }
  return seen;
}

var COND_MAP = {
  'classify|answer': 'simple', 'classify|tool': 'tool', 'classify|clarify': 'missing_info',
  'classify|risky_action': 'risky', 'classify|retry': 'error',
  'evaluate|answer': 'evaluation: success', 'evaluate|retry': 'evaluation: needs_retry',
  'retry|tool': 'attempt < max', 'retry|dead_letter': 'attempt >= max',
  'approval|tool': 'approved', 'approval|clarify': 'rejected'
};

function getTransition(from, to) {
  var key = from + '|' + to;
  return COND_MAP[key] || null;
}

function findRetryCycles(events) {
  var cycles = [];
  var current = null;
  for (var i = 0; i < events.length; i++) {
    var node = events[i].node || '';
    if (node === 'retry') {
      if (current) cycles.push(current);
      current = [events[i]];
    } else if (current && (node === 'tool' || node === 'evaluate')) {
      current.push(events[i]);
    } else if (current) {
      cycles.push(current);
      current = null;
    }
  }
  if (current) cycles.push(current);
  return cycles;
}

/* ── Utility ── */
function esc(s) {
  if (typeof s !== 'string') return s;
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

/* ── Init ── */
renderDashboard();
renderGraph();
renderTraces();
</script>
</body>
</html>"""
