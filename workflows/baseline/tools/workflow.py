from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowStep:
    key: str
    label: str
    candidate: bool = False


BASELINE_STEPS = [
    WorkflowStep("runner", "runner"),
    WorkflowStep("merger", "merger"),
    WorkflowStep("evaluator", "evaluator"),
    WorkflowStep("slicer", "slicer"),
    WorkflowStep("analyzer_llm", "analyzer_llm"),
]

PATCHER_STEPS = [
    WorkflowStep("patcher", "patcher"),
    WorkflowStep("candidate_runner", "candidate runner", candidate=True),
    WorkflowStep("candidate_merger", "candidate merger", candidate=True),
    WorkflowStep("candidate_evaluator", "candidate evaluator", candidate=True),
    WorkflowStep("candidate_slicer", "candidate slicer", candidate=True),
    WorkflowStep("comparator", "comparator", candidate=True),
]


def enabled_workflow_steps(enable_patcher: bool = True) -> list[WorkflowStep]:
    if enable_patcher:
        return [*BASELINE_STEPS, *PATCHER_STEPS]
    return list(BASELINE_STEPS)


def workflow_step_count(enable_patcher: bool = True) -> int:
    return len(enabled_workflow_steps(enable_patcher))
