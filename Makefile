.PHONY: install test lint typecheck run-scenarios grade-local clean

install:
	pip install -e '.[dev]'

test:
	pytest

lint:
	ruff check src tests

typecheck:
	mypy src

run-scenarios:
	python -m langgraph_agent_lab.cli run-scenarios --config configs/lab.yaml --output outputs/metrics.json

grade-local:
	python -m langgraph_agent_lab.cli validate-metrics --metrics outputs/metrics.json

grade-rag:
	python -m langgraph_agent_lab.cli grade-rag --questions data/sample/grading_questions.json --output outputs/grading_report.json -v

export-html:
	python -m langgraph_agent_lab.cli export-html --config configs/lab.yaml --output ui/dashboard.html

clean:
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov dist build *.egg-info outputs/*.json
