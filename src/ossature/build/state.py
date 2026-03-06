import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli
import tomli_w

from ossature.config.loader import OssatureConfig
from ossature.models.plan import PlanTask


@dataclass
class TaskState:
    input_hash: str
    output_hash: str
    written_files: list[str]


@dataclass
class BuildState:
    tasks: dict[str, TaskState] = field(default_factory=dict)

    def get(self, task_id: str) -> TaskState | None:
        return self.tasks.get(task_id)

    def set(self, task_id: str, state: TaskState) -> None:
        self.tasks[task_id] = state


def compute_input_hash(prompt: str, task: PlanTask, config: OssatureConfig) -> str:
    """Hash the assembled prompt + inject_files content.

    The prompt already includes: project brief, spec brief, task definition,
    spec_refs content, arch_refs content, and cross_spec_interfaces content.
    inject_files are listed by name in the prompt but read via tool calls,
    so their content must be hashed separately.
    """
    hasher = hashlib.sha256()
    hasher.update(prompt.encode())
    for filepath in sorted(task.inject_files):
        full_path = config.output_path / filepath
        if full_path.exists():
            hasher.update(filepath.encode())
            hasher.update(full_path.read_bytes())
    for filepath in sorted(task.context_files):
        full_path = config.context_path / filepath
        if full_path.exists():
            hasher.update(f"context:{filepath}".encode())
            hasher.update(full_path.read_bytes())
    return f"sha256:{hasher.hexdigest()}"


def compute_output_hash(written_files: list[str], config: OssatureConfig) -> str:
    """Hash all files written by a task."""
    hasher = hashlib.sha256()
    for filepath in sorted(written_files):
        full_path = config.output_path / filepath
        if full_path.exists():
            hasher.update(filepath.encode())
            hasher.update(full_path.read_bytes())
    return f"sha256:{hasher.hexdigest()}"


def get_task_written_files(task: PlanTask, tasks_dir: Path) -> list[str]:
    """Get written files for a DONE task from output.toml, falling back to task.outputs."""
    from ossature.build.builder import make_task_slug

    slug = make_task_slug(task)
    output_file = tasks_dir / f"{task.id}-{slug}" / "output.toml"
    if output_file.exists():
        try:
            with open(output_file, "rb") as f:
                data = tomli.load(f)
            files = data.get("files")
            if isinstance(files, list):
                return files
        except tomli.TOMLDecodeError, OSError:
            pass
    return list(task.outputs)


def load_state(filepath: Path) -> BuildState:
    if not filepath.exists():
        return BuildState()
    try:
        with open(filepath, "rb") as f:
            data = tomli.load(f)
    except tomli.TOMLDecodeError, OSError:
        return BuildState()

    tasks: dict[str, TaskState] = {}
    for task_id, task_data in data.get("tasks", {}).items():
        if not isinstance(task_data, dict):
            continue
        tasks[task_id] = TaskState(
            input_hash=task_data.get("input_hash", ""),
            output_hash=task_data.get("output_hash", ""),
            written_files=task_data.get("written_files", []),
        )
    return BuildState(tasks=tasks)


def write_state(state: BuildState, filepath: Path) -> None:
    data: dict[str, Any] = {"tasks": {}}
    for task_id in sorted(state.tasks):
        ts = state.tasks[task_id]
        data["tasks"][task_id] = {
            "input_hash": ts.input_hash,
            "output_hash": ts.output_hash,
            "written_files": ts.written_files,
        }

    filepath.parent.mkdir(parents=True, exist_ok=True)
    content = tomli_w.dumps(data)
    with open(filepath, "w") as f:
        f.write("# .ossature/state.toml — Build state (auto-generated, do not edit)\n\n")
        f.write(content)
