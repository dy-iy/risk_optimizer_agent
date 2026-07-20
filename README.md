# Risk Optimizer Agent

This project is an iterative optimization pipeline for a crypto-news risk labeling rule system. It runs a versioned rule script, evaluates it against labeled data, slices error cases, asks an LLM to diagnose failure patterns, and can generate the next patched rule-script version.

## Pipeline

1. Run a versioned rule script on input news CSV.
2. Merge rule predictions with the gold-label CSV.
3. Evaluate score, label, and primary-risk-type quality.
4. Slice representative error buckets: false positive, false negative, type mismatch, and score-diff cases.
5. Use `workflows/baseline/analyzer_llm.py` to summarize error patterns from statistics and samples.
6. Use `workflows/baseline/patcher_llm_v2.py` to generate the next versioned rule script.
7. Run the generated candidate version and use `workflows/baseline/tools/comparator.py` to compare current vs next metrics.

The main orchestration entrypoint is:

```powershell
py -3 -m workflows.baseline.orchestrator
```

## Layout

- `workflows/baseline/`: current main workflow line and its rule-script iterations.
- `workflows/baseline/orchestrator.py`: baseline workflow entrypoint.
- `workflows/baseline/analyzer_llm.py`: baseline analyzer.
- `workflows/baseline/patcher_llm_v2.py`: baseline patcher.
- `workflows/baseline/tools/`: deterministic baseline utilities for running scripts, merging, evaluation, slicing, comparison, IO, path handling, and experiment reports.
- `workflows/baseline/prompts/`: baseline LLM prompt templates and message builders.
- `workflows/baseline/versions/`: historical and generated rule-script versions for the baseline workflow.
- `workflows/v2/`: previous workflow experiment preserved for reference.
- `data/`: shared input and gold-label datasets.
- `docs/EXPERIMENTS.md`: workflow extension and experiment comparison guide.

## Version Convention

- Rule scripts: `workflows/baseline/versions/vN/scripts/risk_labeler_vN.py`
- Reports: `workflows/baseline/versions/vN/reports/...`
- Shared input data: `data/input/...`
- Gold labels: `data/gold/...`

Each optimization round uses a current version, for example `v16`, and writes patched output for the next version, for example `v17`.
The orchestrator creates the required `scripts/` and `reports/` subdirectories for the current and next versions automatically.

## Experiment Comparison

Build a multi-version metrics report:

```powershell
py -3 -m workflows.baseline.tools.experiment_report --versions v20 v21 v22 v23
```

This writes JSON, CSV, and Markdown summaries under `workflows/baseline/experiments/reports/latest/` by default.
The report uses balanced metric voting across MAE/RMSE, label accuracy, primary-type accuracy, and sliced error counts.
Compare versions only when their eval artifacts use the same gold-label CSV.
See `docs/EXPERIMENTS.md` for workflow extension notes and custom report commands.

To compare another workflow line, pass `--workflow`, for example:

```powershell
py -3 -m workflows.baseline.tools.experiment_report --workflow v3
```

## Configuration

LLM calls use environment variables, usually from `.env`:

- `DEEPSEEK_API_KEY` or `OPENAI_API_KEY`
- `DEEPSEEK_BASE_URL`
- `DEEPSEEK_MODEL`

Other tuning variables include sample size, prompt text limits, patcher temperature, and retry limits. Defaults are defined in `workflows/baseline/analyzer_llm.py` and `workflows/baseline/patcher_llm_v2.py`.

## Notes

Generated reports and local data are ignored by Git where possible. The source of truth for baseline rule scripts is under `workflows/baseline/versions/`; avoid adding new root-level `vN` folders.
