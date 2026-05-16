# Risk Optimizer Agent

This project is an iterative optimization pipeline for a crypto-news risk labeling rule system. It runs a versioned rule script, evaluates it against labeled data, slices error cases, asks an LLM to diagnose failure patterns, and can generate the next patched rule-script version.

## Pipeline

1. Run a versioned rule script on input news CSV.
2. Merge rule predictions with the gold-label CSV.
3. Evaluate score, label, and primary-risk-type quality.
4. Slice representative error buckets: false positive, false negative, type mismatch, and score-diff cases.
5. Use `analyzer_llm.py` to summarize error patterns from statistics and samples.
6. Use `patcher_llm_v2.py` to generate the next versioned rule script.
7. Run the generated candidate version and use `tools/comparator.py` to compare current vs next metrics.

The main orchestration entrypoint is:

```powershell
py -3 orchestrator.py
```

## Layout

- `orchestrator.py`: full pipeline entrypoint.
- `analyzer_llm.py`: builds the analysis payload and asks the LLM for structured diagnosis.
- `patcher_llm_v2.py`: asks the LLM to produce a patched Python rule script and validates syntax.
- `prompts/`: LLM prompt templates and message builders.
- `tools/`: deterministic utilities for running scripts, merging, evaluation, slicing, comparison, IO, and path handling.
- `versions/`: historical and generated rule-script versions.
- `data/`: shared input and gold-label datasets.

## Version Convention

- Rule scripts: `versions/vN/scripts/risk_labeler_vN.py`
- Reports: `versions/vN/reports/...`
- Shared input data: `data/input/...`
- Gold labels: `data/gold/...`

Each optimization round uses a current version, for example `v16`, and writes patched output for the next version, for example `v17`.
The orchestrator creates the required `scripts/` and `reports/` subdirectories for the current and next versions automatically.

## Configuration

LLM calls use environment variables, usually from `.env`:

- `DEEPSEEK_API_KEY` or `OPENAI_API_KEY`
- `DEEPSEEK_BASE_URL`
- `DEEPSEEK_MODEL`

Other tuning variables include sample size, prompt text limits, patcher temperature, and retry limits. Defaults are defined in `analyzer_llm.py` and `patcher_llm_v2.py`.

## Notes

Generated reports and local data are ignored by Git where possible. The source of truth for versioned rule scripts is under `versions/`; avoid adding new root-level `vN` folders.
