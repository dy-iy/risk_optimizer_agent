# Workflow and Experiment Guide

This project now separates three concerns:

- Workflow orchestration: `workflows/baseline/orchestrator.py`
- Shared workflow metadata: `workflows/baseline/tools/workflow.py`
- Version metrics and experiment comparison: `workflows/baseline/tools/metrics.py`, `workflows/baseline/tools/experiment_report.py`
- Workflow-specific iterations: `workflows/<workflow>/versions/`

## Change the Workflow

Use `workflows/baseline/tools/workflow.py` as the first place to describe stage order and enabled stages.

The current baseline stages are:

1. `runner`
2. `merger`
3. `evaluator`
4. `slicer`
5. `analyzer_llm`

When patching is enabled, candidate stages are appended:

1. `patcher`
2. `candidate_runner`
3. `candidate_merger`
4. `candidate_evaluator`
5. `candidate_slicer`
6. `comparator`

`workflows/baseline/orchestrator.py` still contains the actual execution logic. When adding a new stage, add the stage metadata in `workflows/baseline/tools/workflow.py`, then add the execution block in the workflow orchestrator.

## Workflow Lines

Current layout:

- `workflows/baseline/`: current main workflow line.
- `workflows/baseline/versions/`: baseline rule-script iterations.
- `workflows/v2/`: previous workflow experiment formerly stored as a backup.

For a new workflow experiment, create a sibling workflow directory, for example:

```text
workflows/v3/
  versions/
  experiments/
```

Then run workflow-aware tools with `--workflow v3` or set:

```powershell
$env:RISK_WORKFLOW = "v3"
```

## Compare Many Versions

Generate a multi-version experiment report:

```powershell
py -3 -m workflows.baseline.tools.experiment_report --versions v20 v21 v22 v23
```

By default, this writes:

- `workflows/baseline/experiments/reports/latest/version_metrics.json`
- `workflows/baseline/experiments/reports/latest/version_metrics.csv`
- `workflows/baseline/experiments/reports/latest/version_metrics.md`

Scan all `workflows/baseline/versions/vN` directories:

```powershell
py -3 -m workflows.baseline.tools.experiment_report
```

Write to a custom directory:

```powershell
py -3 -m workflows.baseline.tools.experiment_report --versions v20 v23 --output-dir reports/v20_vs_v23
```

Compare a non-baseline workflow:

```powershell
py -3 -m workflows.baseline.tools.experiment_report --workflow v3
```

## Metric Source of Truth

`workflows/baseline/tools/metrics.py` reads the existing per-version artifacts:

- `workflows/<workflow>/versions/vN/reports/evals/risk_labeler_vN_eval.json`
- `workflows/<workflow>/versions/vN/reports/errors/risk_labeler_vN_slice_log.json`

Under the default baseline workflow, those resolve to:

- `workflows/baseline/versions/vN/reports/evals/risk_labeler_vN_eval.json`
- `workflows/baseline/versions/vN/reports/errors/risk_labeler_vN_slice_log.json`

The same metric helpers are used by:

- `workflows/baseline/tools/comparator.py`
- `workflows/baseline/orchestrator.py` analyzer context
- `workflows/baseline/tools/experiment_report.py`

This keeps pairwise comparison, analyzer history, and experiment summaries on one metric definition.

For fair multi-version comparison, make sure the compared `eval.json` files were generated with the same input CSV and gold-label CSV.
