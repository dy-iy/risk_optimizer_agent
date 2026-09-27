# Workflow V3

V3 is the type-mismatch-first workflow line.

- Active workflow code: `workflows/v3/orchestrator.py`, `workflows/v3/analyzer_llm.py`, `workflows/v3/patcher_llm_v2.py`
- Risk taxonomy: `workflows/v3/tools/risk_taxonomy.py`
- Deterministic run/evaluate stages: `workflows/v3/tools/pipeline_stages.py`
- Rule/script iterations: `workflows/v3/versions/vN/`
- Seed script: `workflows/v3/versions/v1/scripts/risk_labeler_v1.py`
- Experiment reports: `workflows/v3/experiments/reports/`
- Score-stage threshold: `TYPE_MISMATCH_SCORE_STAGE_THRESHOLD`, default `80`
- Quality-gate enforcement: `V3_ENFORCE_QUALITY_GATE`, default `0` (advisory only)
- Candidate retries when gate enforcement is enabled: `V3_MAX_CANDIDATE_ATTEMPTS`, default `2`
- Patcher analysis budget: `PATCHER_ANALYSIS_LIMIT`, default `120000`
- Patcher source budget: `PATCHER_SOURCE_CODE_LIMIT`, default `240000`

Analyzer strategy:

1. `type_mismatch` means every row whose gold and rule primary types differ, even when the risk labels also differ.
2. If `type_mismatch` rows are above the threshold, repair primary risk type confusion first.
3. High-confidence confusion patterns are selected by cumulative coverage, up to 12 changes per iteration.
4. Once `type_mismatch` rows are at or below the threshold, make score calibration the main repair target.
5. Score calibration uses only rows where `gold_primary_risk_type == rule_primary_risk_type` and the absolute score difference is at least 15.
6. Raw `score_xxx` values remain type evidence. Final severity is calibrated through target-specific `calibrate_score_xxx` functions after primary type selection, preventing score tuning from changing the selected type.
7. Every candidate is evaluated by a stage-aware quality gate, but the gate is advisory by default. Metric regressions are recorded and the candidate is still promoted so all requested iterations run to completion.
8. Set `V3_ENFORCE_QUALITY_GATE=1` to restore blocking behavior. In that mode, a rejected candidate is retried once inside the same iteration with metric feedback.

Prompt routing:

- Analyzer and patcher each have two independent system prompts, task instructions, and output schemas; there is no shared conditional prompt that lets the LLM choose a stage.
- `type_mismatch_repair`: requires `score_calibration_locked=true`, uses only type-mismatch counts, confusion pairs, FP/FN, primary-type accuracy, relevant scorer context and type evidence. MAE, RMSE, score-calibration groups and severity-score samples are removed before both LLM calls.
- `score_calibration`: requires `score_calibration_locked=false`, uses only score-error metrics, correctly typed calibration groups and target scorer/calibrator context. Type-confusion diagnostics and repair plans are removed before both LLM calls.
- An unknown stage or a stage/lock conflict raises an error before the LLM call.

The patcher prefers ordered exact-replacement operations instead of returning the whole script. This removes the full-script output bottleneck and allows rule code to grow when broader keyword sets, contextual guards, helpers, or calibrators are justified. An operation whose old and new code are identical is recorded and skipped without discarding other valid operations, but every successful iteration must contain at least one executable optimization change. An all-no-op, comment-only, formatting-only, or type-annotation-only batch is retried up to `PATCHER_GENERATION_ATTEMPTS` times, default `3`; if no real change is generated, no new version is produced.

Run the workflow with:

```powershell
py -3 -m workflows.v3.orchestrator
```

The orchestrator will ask for:

1. `current version`: enter `1` or `v1` to start from the seed script.
2. `loop count`: enter how many optimization rounds to run.

For example, current version `v1` and loop count `3` runs `v1->v2`, then `v2->v3`, then `v3->v4`.
