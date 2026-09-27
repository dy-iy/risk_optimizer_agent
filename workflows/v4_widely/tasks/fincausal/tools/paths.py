"""任务配置与路径：数据、静态资源、版本、报告。"""
import re
from dataclasses import asdict, dataclass, field as dataclass_field
from pathlib import Path

from .common import HERE, read_json

TASK_DIR = HERE.parent
ARTIFACTS = TASK_DIR / "artifacts"


@dataclass
class Task:
    name: str
    description: str
    kind: str
    labels: list[str]
    field: str = "label"
    language: str = "en"
    positive: str | None = None
    seed: int = 42
    examples_per_label: int = 3
    max_errors: int = 40
    max_changes: int = 6
    weights: dict = dataclass_field(default_factory=dict)
    max_drops: dict = dataclass_field(default_factory=dict)

    def __post_init__(self):
        if not self.weights:
            self.weights = ({"micro_f1": .5, "macro_f1": .5} if self.kind == "multilabel"
                            else {"macro_f1": 1.0})
        if not self.max_drops:
            self.max_drops = ({"micro_f1": .02, "macro_f1": .02} if self.kind == "multilabel"
                              else {"positive_f1": .02} if self.positive else {})

    @property
    def lf_names(self):
        return [f"lf_{i:02d}" for i in range(1, len(self.labels) + 1)]

    def to_dict(self):
        return asdict(self)


TASK_NAME = 'fincausal'
TASK_NAMES = (TASK_NAME,)


def task_directory(name=TASK_NAME):
    if name != TASK_NAME:
        raise ValueError(f"This workflow only handles {TASK_NAME}, not {name}")
    return TASK_DIR


def get_task(name=TASK_NAME, seed=42):
    task_directory(name)
    config = read_json(ARTIFACTS / "config.json")
    if config["name"] != TASK_NAME:
        raise ValueError("Task directory/config name mismatch")
    return Task(**{**config, "seed": seed})


def task_assets(task):
    if task.name != TASK_NAME:
        return {}
    return {name: (ARTIFACTS / name).read_text(encoding="utf-8") for name in
            ("annotation.md", "lf_design.md", "prompts/initialize.md", "prompts/analyze.md", "prompts/patch.md")}


def build_paths(version, directory=None):
    """Resolve one version's files without creating or overwriting anything."""
    if not re.fullmatch(r"v[1-9]\d*", version):
        raise ValueError("Version must be v1, v2, ...")
    directory = Path(directory) if directory is not None else TASK_DIR
    version_dir = directory / "versions" / version
    return {"directory": version_dir, "script": version_dir / "rules.py",
            "support": version_dir / "support.py", "reports": version_dir / "reports",
            "analysis": version_dir / "reports/analysis/analysis.json"}


def current_version():
    from .reporter import load_checkpoint
    return load_checkpoint(TASK_DIR)["state"]["current_version"]
