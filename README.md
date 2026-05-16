# Risk Optimizer Agent

Project layout:

- `orchestrator.py`: full pipeline entrypoint.
- `analyzer_llm.py`: builds error-analysis payloads and asks the LLM for diagnosis.
- `patcher_llm_v2.py`: asks the LLM to generate a patched rule script and validates syntax.
- `tools/`: deterministic pipeline tools, IO helpers, version paths, metrics, slicing, merging, running scripts, and comparisons.
- `prompts/`: prompt templates and message builders for LLM-facing workflows.
- `versions/`: historical and generated rule-script versions (`v1`, `v2`, ...).
- `data/`: input and gold-label datasets.

Path convention:

- Rule scripts live at `versions/vN/scripts/risk_labeler_vN.py`.
- Reports live at `versions/vN/reports/...`.
- Shared data lives at `data/...`.
