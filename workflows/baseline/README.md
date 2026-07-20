# Workflow Baseline

This is the current main workflow line.

- Active workflow code: `workflows/baseline/orchestrator.py`, `workflows/baseline/analyzer_llm.py`, `workflows/baseline/patcher_llm_v2.py`, `workflows/baseline/tools/`, `workflows/baseline/prompts/`
- Rule/script iterations: `workflows/baseline/versions/vN/`
- Experiment reports: `workflows/baseline/experiments/reports/`

Use this workflow as the stable base for future workflow experiments such as `workflows/v3`.

Use `py -3 -m workflows.baseline.orchestrator` to run this workflow.
