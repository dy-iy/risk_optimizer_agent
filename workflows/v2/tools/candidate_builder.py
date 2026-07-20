from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

try:
    from .common import ensure_parent_dir, write_json_file
except ImportError:
    from common import ensure_parent_dir, write_json_file


@dataclass(frozen=True)
class ExactEdit:
    old: str
    new: str
    expected_count: int = 1
    rationale: str = ""


@dataclass
class CandidateBuildResult:
    success: bool
    source_script: str
    candidate_script: str
    edit_count: int
    syntax_ok: bool
    edits: list[dict]
    error_message: str = ""


def build_candidate_from_exact_edits(
    source_script: str | Path,
    candidate_script: str | Path,
    edits: Iterable[ExactEdit],
    report_json: str | Path | None = None,
) -> CandidateBuildResult:
    """Build a minimal, auditable candidate without asking an LLM to rewrite a file.

    Every edit must match exactly the expected number of times.  The candidate is
    compiled before it is written, so stale hypotheses fail closed instead of
    silently changing an unintended code region.
    """
    source_path = Path(source_script).resolve()
    candidate_path = Path(candidate_script).resolve()
    edit_list = list(edits)

    def finish(result: CandidateBuildResult) -> CandidateBuildResult:
        if report_json:
            write_json_file(Path(report_json).resolve(), asdict(result))
        return result

    if not source_path.exists():
        return finish(
            CandidateBuildResult(
                success=False,
                source_script=str(source_path),
                candidate_script=str(candidate_path),
                edit_count=0,
                syntax_ok=False,
                edits=[],
                error_message=f"source script not found: {source_path}",
            )
        )

    source = source_path.read_text(encoding="utf-8-sig")
    candidate = source
    applied: list[dict] = []

    for edit in edit_list:
        actual_count = candidate.count(edit.old)
        if actual_count != edit.expected_count:
            return finish(
                CandidateBuildResult(
                    success=False,
                    source_script=str(source_path),
                    candidate_script=str(candidate_path),
                    edit_count=len(applied),
                    syntax_ok=False,
                    edits=applied,
                    error_message=(
                        "exact edit precondition failed: "
                        f"expected {edit.expected_count}, found {actual_count}; old={edit.old!r}"
                    ),
                )
            )
        candidate = candidate.replace(edit.old, edit.new, edit.expected_count)
        applied.append(
            {
                "old": edit.old,
                "new": edit.new,
                "expected_count": edit.expected_count,
                "rationale": edit.rationale,
            }
        )

    try:
        compile(candidate, str(candidate_path), "exec")
    except SyntaxError as exc:
        return finish(
            CandidateBuildResult(
                success=False,
                source_script=str(source_path),
                candidate_script=str(candidate_path),
                edit_count=len(applied),
                syntax_ok=False,
                edits=applied,
                error_message=f"candidate syntax error: {exc}",
            )
        )

    ensure_parent_dir(candidate_path)
    candidate_path.write_text(candidate, encoding="utf-8")
    return finish(
        CandidateBuildResult(
            success=True,
            source_script=str(source_path),
            candidate_script=str(candidate_path),
            edit_count=len(applied),
            syntax_ok=True,
            edits=applied,
        )
    )
