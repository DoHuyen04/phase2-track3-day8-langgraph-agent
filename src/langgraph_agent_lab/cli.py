"""CLI for the lab."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer
import yaml
from dotenv import load_dotenv

load_dotenv()

from .graph import build_graph
from .html_export import export_html
from .llm import get_llm
from .metrics import MetricsReport, metric_from_state, summarize_metrics, write_metrics
from .persistence import build_checkpointer
from .rag_grader import (
    GradingReport,
    load_grading_questions,
    print_report,
    run_grading,
    write_report as write_grading_report,
)
from .report import write_report
from .scenarios import load_scenarios
from .state import initial_state

app = typer.Typer(no_args_is_help=True)


@app.command("run-scenarios")
def run_scenarios(
    config: Annotated[Path, typer.Option("--config")],
    output: Annotated[Path, typer.Option("--output")],
) -> None:
    """Run all grading scenarios and write metrics JSON."""
    cfg = yaml.safe_load(config.read_text(encoding="utf-8"))
    scenarios = load_scenarios(cfg["scenarios_path"])
    checkpointer = build_checkpointer(cfg.get("checkpointer", "memory"), cfg.get("database_url"))
    graph = build_graph(checkpointer=checkpointer)
    metrics = []
    for scenario in scenarios:
        state = initial_state(scenario)
        run_config = {"configurable": {"thread_id": state["thread_id"]}}
        final_state = graph.invoke(state, config=run_config)
        metrics.append(metric_from_state(final_state, scenario.expected_route.value, scenario.requires_approval))
    report = summarize_metrics(metrics)
    write_metrics(report, output)
    if cfg.get("report_path"):
        write_report(report, cfg["report_path"])
    typer.echo(f"Wrote metrics to {output}")


@app.command("validate-metrics")
def validate_metrics(metrics: Annotated[Path, typer.Option("--metrics")]) -> None:
    """Validate metrics JSON schema for grading."""
    payload = json.loads(metrics.read_text(encoding="utf-8"))
    report = MetricsReport.model_validate(payload)
    if report.total_scenarios < 6:
        raise typer.BadParameter("Expected at least 6 scenarios")
    typer.echo(f"Metrics valid. success_rate={report.success_rate:.2%}")


@app.command("grade-rag")
def grade_rag(
    questions: Annotated[Path, typer.Option("--questions")],
    output: Annotated[Path, typer.Option("--output")],
    verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False,
) -> None:
    """Grade LLM answers against RAG QA grading questions.

    Loads a grading_questions.json file, runs each question through the
    configured LLM, and evaluates answers against must_contain_any /
    must_not_contain criteria.

    Example:
        agent-lab grade-rag --questions data/sample/grading_questions.json --output outputs/grading_report.json
    """
    grading_questions = load_grading_questions(questions)
    llm = get_llm(temperature=0.0)
    report = run_grading(grading_questions, llm)
    write_grading_report(report, output)

    if verbose:
        print_report(report)

    typer.echo(
        f"Grading complete: {report.passed_count}/{report.total_questions} passed "
        f"({report.overall_score:.1%})"
    )
    typer.echo(f"Wrote report to {output}")


@app.command("export-html")
def export_html_cmd(
    config: Annotated[Path, typer.Option("--config")],
    output: Annotated[Path, typer.Option("--output")],
) -> None:
    """Generate a standalone HTML dashboard with trace viewer.

    Runs all scenarios and produces a self-contained .html file with
    Dashboard, Graph Explorer, and Scenario Trace tabs.

    Example:
        agent-lab export-html --config configs/lab.yaml --output ui/dashboard.html
    """
    export_html(config, output)
    typer.echo(f"HTML dashboard written to {output}")


if __name__ == "__main__":
    app()
