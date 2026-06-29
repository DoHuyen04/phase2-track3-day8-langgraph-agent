"""RAG QA grading module.

Loads grading questions, uses LLM to answer them, and evaluates
answers against must_contain_any / must_not_contain criteria.

Usage:
    from langgraph_agent_lab.rag_grader import load_grading_questions, run_grading
    questions = load_grading_questions("data/sample/grading_questions.json")
    from langgraph_agent_lab.llm import get_llm
    report = run_grading(questions, get_llm())
    print(report.model_dump_json(indent=2))
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


# ── Data Models ──────────────────────────────────────────────────────────────

class GradingQuestion(BaseModel):
    """A single grading question from the JSON file."""
    id: str
    question: str
    must_contain_any: list[str] = Field(default_factory=list)
    must_not_contain: list[str] = Field(default_factory=list)
    expect_top1_doc_id: str = ""
    grading_criteria: list[str] = Field(default_factory=list)


class GradingResult(BaseModel):
    """Result for a single question after LLM answer + grading."""
    question_id: str
    question: str
    llm_answer: str
    score: float  # 0.0 - 1.0
    checks: list[dict[str, Any]]  # detail per check
    expected_doc_id: str
    passed: bool


class GradingReport(BaseModel):
    """Aggregate grading report."""
    total_questions: int
    passed_count: int
    failed_count: int
    overall_score: float  # 0.0 - 1.0
    results: list[GradingResult]


# ── Loader ───────────────────────────────────────────────────────────────────

def load_grading_questions(path: str | Path) -> list[GradingQuestion]:
    """Load grading questions from a JSON file."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    questions = [GradingQuestion.model_validate(item) for item in raw]
    if len(questions) < 1:
        raise ValueError("Grading file must have at least 1 question")
    # Validate unique IDs
    ids = [q.id for q in questions]
    duplicates = [i for i in ids if ids.count(i) > 1]
    if duplicates:
        raise ValueError(f"Duplicate question IDs found: {list(set(duplicates))}")
    return questions


# ── Grader Logic ─────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """Normalize text for comparison: lowercase, strip accents, collapse whitespace."""
    import re
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _check_contains(answer: str, phrases: list[str]) -> dict[str, Any]:
    """Check if answer contains at least one of the required phrases."""
    norm_answer = _normalize(answer)
    hits: list[str] = []
    misses: list[str] = []
    for phrase in phrases:
        if _normalize(phrase) in norm_answer:
            hits.append(phrase)
        else:
            misses.append(phrase)
    passed = len(hits) > 0 if phrases else True
    return {
        "type": "must_contain_any",
        "passed": passed,
        "hits": hits,
        "misses": misses,
        "required": phrases,
    }


def _check_not_contains(answer: str, phrases: list[str]) -> dict[str, Any]:
    """Check that answer does NOT contain any forbidden phrases."""
    norm_answer = _normalize(answer)
    violations: list[str] = []
    for phrase in phrases:
        if _normalize(phrase) in norm_answer:
            violations.append(phrase)
    passed = len(violations) == 0
    return {
        "type": "must_not_contain",
        "passed": passed,
        "violations": violations,
        "forbidden": phrases,
    }


def grade_answer(question: GradingQuestion, llm_answer: str) -> GradingResult:
    """Grade a single LLM answer against a grading question's criteria.

    Checks:
    1. must_contain_any: at least one of the required phrases appears
    2. must_not_contain: none of the forbidden phrases appear

    Score = (passed checks) / (total checks)
    """
    checks: list[dict[str, Any]] = []

    # Check 1: must contain
    check1 = _check_contains(llm_answer, question.must_contain_any)
    checks.append(check1)

    # Check 2: must not contain
    check2 = _check_not_contains(llm_answer, question.must_not_contain)
    checks.append(check2)

    # Calculate score
    total_checks = len(checks)
    passed_checks = sum(1 for c in checks if c["passed"])
    score = passed_checks / total_checks if total_checks > 0 else 0.0
    passed = score == 1.0

    return GradingResult(
        question_id=question.id,
        question=question.question,
        llm_answer=llm_answer,
        score=score,
        checks=checks,
        expected_doc_id=question.expect_top1_doc_id,
        passed=passed,
    )


# ── Runner ───────────────────────────────────────────────────────────────────

def run_grading(
    questions: list[GradingQuestion],
    llm: Any,
    system_prompt: str | None = None,
) -> GradingReport:
    """Run all grading questions through an LLM and grade the answers.

    Args:
        questions: List of grading questions to evaluate.
        llm: An LLM instance (from get_llm()).
        system_prompt: Optional custom system prompt for the LLM.

    Returns:
        GradingReport with per-question results and aggregate scores.
    """
    if system_prompt is None:
        system_prompt = (
            "Bạn là trợ lý hỗ trợ khách hàng chuyên nghiệp. "
            "Trả lời câu hỏi bằng tiếng Việt một cách chính xác, ngắn gọn, "
            "dựa trên kiến thức về chính sách công ty. "
            "Chỉ trả lời câu hỏi, không thêm giải thích dài dòng."
        )

    results: list[GradingResult] = []
    total = len(questions)

    for i, q in enumerate(questions):
        print(f"[{i + 1}/{total}] Processing {q.id}: {q.question[:60]}...")

        # Generate answer with LLM
        response = llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": q.question},
        ])
        llm_answer = response.content if hasattr(response, "content") else str(response)

        # Grade the answer
        result = grade_answer(q, llm_answer)
        results.append(result)

        # Progress indicator
        status = "PASS" if result.passed else f"FAIL ({result.score:.0%})"
        print(f"       -> {status}")

    passed_count = sum(1 for r in results if r.passed)
    failed_count = total - passed_count
    overall = passed_count / total if total > 0 else 0.0

    return GradingReport(
        total_questions=total,
        passed_count=passed_count,
        failed_count=failed_count,
        overall_score=overall,
        results=results,
    )


def write_report(report: GradingReport, output_path: str | Path) -> None:
    """Write grading report to a JSON file."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def print_report(report: GradingReport) -> None:
    """Print a human-readable grading report to stdout."""
    print()
    print("=" * 70)
    print("  RAG QA GRADING REPORT")
    print("=" * 70)
    print(f"  Total Questions : {report.total_questions}")
    print(f"  Passed          : {report.passed_count}")
    print(f"  Failed          : {report.failed_count}")
    print(f"  Overall Score   : {report.overall_score:.1%}")
    print("-" * 70)

    for r in report.results:
        status = "PASS" if r.passed else "FAIL"
        print(f"  [{status}] {r.question_id}")
        print(f"   Q: {r.question[:80]}")
        print(f"   A: {r.llm_answer[:120]}...")
        print(f"   Expected doc: {r.expected_doc_id}")
        for check in r.checks:
            if check["type"] == "must_contain_any":
                if check["hits"]:
                    print(f"   + Contains: {', '.join(check['hits'])}")
                if check["misses"]:
                    print(f"   - Missing:  {', '.join(check['misses'])}")
            elif check["type"] == "must_not_contain":
                if check["violations"]:
                    print(f"   ! Forbidden: {', '.join(check['violations'])}")
        print()

    print("=" * 70)
