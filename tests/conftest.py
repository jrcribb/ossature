import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from ossature.config.loader import BuildConfig, OssatureConfig, OutputConfig
from ossature.models.plan import Plan, PlanMeta, PlanTask, TaskStatus
from ossature.models.shared import Status
from ossature.models.smd import Priority, SMDSpec
from ossature.templates.manager import TemplateManager


@pytest.fixture
def temp_dir() -> Generator[Path]:
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def initialized_project(temp_dir: Path) -> Path:
    manager = TemplateManager(temp_dir)
    manager.init_project(name="test-project")
    return temp_dir


@pytest.fixture
def sample_config(temp_dir: Path) -> OssatureConfig:
    return OssatureConfig(
        name="test-project",
        version="0.0.1",
        root=temp_dir,
        spec_dir="specs",
        output=OutputConfig(language="python"),
    )


def make_config(
    root: Path,
    language: str = "python",
    output_dir: str = "output",
    setup: str | None = None,
    verify: str | None = None,
    test: str | None = None,
) -> OssatureConfig:
    return OssatureConfig(
        name="test",
        version="0.0.1",
        root=root,
        output=OutputConfig(dir=output_dir, language=language),
        build=BuildConfig(setup=setup, verify=verify, test=test),
    )


def make_smd(spec_id: str, depends: list[str] | None = None) -> SMDSpec:
    return SMDSpec(
        title=f"{spec_id} Module",
        spec_id=spec_id,
        status=Status.DRAFT,
        priority=Priority.HIGH,
        overview=f"Overview of {spec_id}",
        depends=depends or [],
    )


def make_task(
    id: str,
    spec: str,
    outputs: list[str] | None = None,
    depends_on: list[str] | None = None,
    status: TaskStatus = TaskStatus.PENDING,
    verify: str = "",
) -> PlanTask:
    return PlanTask(
        id=id,
        spec=spec,
        title=f"{spec} task {id}",
        description="",
        outputs=outputs or [],
        depends_on=depends_on or [],
        spec_refs=[],
        arch_refs=[],
        status=status,
        verify=verify,
    )


def make_plan(tasks: list[PlanTask]) -> Plan:
    specs = sorted({t.spec for t in tasks})
    return Plan(
        meta=PlanMeta(
            generated_at="2026-01-01T00:00:00Z",
            total_tasks=len(tasks),
            specs=specs,
        ),
        tasks=tasks,
    )
