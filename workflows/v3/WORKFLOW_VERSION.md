# Workflow V3

V3 is derived from `workflows/baseline`.

The first v3 change is limited to the LLM strategy layer:

- Analyzer is type-mismatch-first.
- Patcher consumes scorer-specific type mismatch repair plans.
- Deterministic stages, evaluator, slicer, comparator, and rule-script IO stay compatible with baseline.

The 12 risk categories are defined in `tools/risk_taxonomy.py`.
