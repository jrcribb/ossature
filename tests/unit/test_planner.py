from pathlib import Path

from conftest import make_smd

from ossature.audit.graph import SpecGraph, SpecGraphEntry
from ossature.audit.planner import (
    load_plan,
    merge_into_global_plan,
    write_plan,
    write_task_definitions,
)
from ossature.models.plan import PlannerTask, SpecTaskPlan, TaskStatus


def _make_spec_plan(tasks: list[dict]) -> SpecTaskPlan:
    return SpecTaskPlan(
        tasks=[
            PlannerTask(
                title=t["title"],
                description=t.get("description", ""),
                outputs=t.get("outputs", []),
                depends_on=t.get("depends_on", []),
                spec_refs=t.get("spec_refs", []),
                arch_refs=t.get("arch_refs", []),
                verify=t.get("verify", "cargo check"),
            )
            for t in tasks
        ]
    )


class TestMergeIntoGlobalPlan:
    def test_single_spec_assigns_sequential_ids(self):
        smds = [make_smd("AUTH")]
        graph = SpecGraph(
            specs=[SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[])],
            levels=[["AUTH"]],
        )
        spec_plans = {
            "AUTH": _make_spec_plan(
                [
                    {"title": "Scaffold", "outputs": ["src/auth/mod.rs"]},
                    {"title": "Types", "outputs": ["src/auth/types.rs"], "depends_on": [1]},
                    {"title": "Tests", "outputs": ["tests/auth.rs"], "depends_on": [2]},
                ]
            )
        }

        plan = merge_into_global_plan(spec_plans, graph, smds)

        assert plan.meta.total_tasks == 3
        assert plan.meta.specs == ["AUTH"]
        assert [t.id for t in plan.tasks] == ["001", "002", "003"]

    def test_single_spec_remaps_local_depends_to_global(self):
        smds = [make_smd("AUTH")]
        graph = SpecGraph(
            specs=[SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[])],
            levels=[["AUTH"]],
        )
        spec_plans = {
            "AUTH": _make_spec_plan(
                [
                    {"title": "Scaffold", "outputs": ["src/mod.rs"]},
                    {"title": "Types", "depends_on": [1]},
                    {"title": "Service", "depends_on": [1, 2]},
                ]
            )
        }

        plan = merge_into_global_plan(spec_plans, graph, smds)

        assert plan.tasks[0].depends_on == []
        assert plan.tasks[1].depends_on == ["001"]
        assert plan.tasks[2].depends_on == ["001", "002"]

    def test_cross_spec_dependency_wiring(self):
        smds = [make_smd("AUTH"), make_smd("API", depends=["AUTH"])]
        graph = SpecGraph(
            specs=[
                SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[]),
                SpecGraphEntry(id="API", file="specs/api.smd", depends=["AUTH"]),
            ],
            levels=[["AUTH"], ["API"]],
        )
        spec_plans = {
            "AUTH": _make_spec_plan(
                [
                    {"title": "Auth Scaffold", "outputs": ["src/auth/mod.rs"]},
                    {"title": "Auth Tests", "outputs": ["tests/auth.rs"], "depends_on": [1]},
                ]
            ),
            "API": _make_spec_plan(
                [
                    {"title": "API Scaffold", "outputs": ["src/api/mod.rs"]},
                    {"title": "API Routes", "outputs": ["src/api/routes.rs"], "depends_on": [1]},
                ]
            ),
        }

        plan = merge_into_global_plan(spec_plans, graph, smds)

        assert len(plan.tasks) == 4
        # First API task should depend on last AUTH task
        api_scaffold = plan.tasks[2]
        assert api_scaffold.spec == "API"
        assert "002" in api_scaffold.depends_on  # last AUTH task

    def test_cross_spec_interfaces_set_on_all_dependent_tasks(self):
        smds = [make_smd("AUTH"), make_smd("DB"), make_smd("API", depends=["AUTH", "DB"])]
        graph = SpecGraph(
            specs=[
                SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[]),
                SpecGraphEntry(id="DB", file="specs/db.smd", depends=[]),
                SpecGraphEntry(id="API", file="specs/api.smd", depends=["AUTH", "DB"]),
            ],
            levels=[["AUTH", "DB"], ["API"]],
        )
        spec_plans = {
            "AUTH": _make_spec_plan([{"title": "Auth Scaffold"}]),
            "DB": _make_spec_plan([{"title": "DB Scaffold"}]),
            "API": _make_spec_plan(
                [
                    {"title": "API Scaffold"},
                    {"title": "API Routes", "depends_on": [1]},
                    {"title": "API Tests", "depends_on": [2]},
                ]
            ),
        }

        plan = merge_into_global_plan(spec_plans, graph, smds)

        api_tasks = [t for t in plan.tasks if t.spec == "API"]
        assert len(api_tasks) == 3
        for api_task in api_tasks:
            assert sorted(api_task.cross_spec_interfaces) == ["AUTH", "DB"]

    def test_spec_refs_prefixed_with_spec_id(self):
        smds = [make_smd("AUTH")]
        graph = SpecGraph(
            specs=[SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[])],
            levels=[["AUTH"]],
        )
        spec_plans = {
            "AUTH": _make_spec_plan(
                [
                    {
                        "title": "Scaffold",
                        "spec_refs": ["overview", "requirements"],
                        "arch_refs": ["dependencies"],
                    },
                ]
            )
        }

        plan = merge_into_global_plan(spec_plans, graph, smds)

        assert plan.tasks[0].spec_refs == ["AUTH:overview", "AUTH:requirements"]
        assert plan.tasks[0].arch_refs == ["AUTH:dependencies"]

    def test_inject_files_from_same_spec_dependencies(self):
        smds = [make_smd("AUTH")]
        graph = SpecGraph(
            specs=[SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[])],
            levels=[["AUTH"]],
        )
        spec_plans = {
            "AUTH": _make_spec_plan(
                [
                    {"title": "Scaffold", "outputs": ["src/auth/mod.rs"]},
                    {"title": "Types", "outputs": ["src/auth/types.rs"], "depends_on": [1]},
                    {
                        "title": "Service",
                        "outputs": ["src/auth/service.rs"],
                        "depends_on": [1, 2],
                    },
                ]
            )
        }

        plan = merge_into_global_plan(spec_plans, graph, smds)

        assert plan.tasks[1].inject_files == ["src/auth/mod.rs"]
        assert plan.tasks[2].inject_files == ["src/auth/mod.rs", "src/auth/types.rs"]

    def test_no_inject_files_across_spec_boundaries(self):
        smds = [make_smd("AUTH"), make_smd("API", depends=["AUTH"])]
        graph = SpecGraph(
            specs=[
                SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[]),
                SpecGraphEntry(id="API", file="specs/api.smd", depends=["AUTH"]),
            ],
            levels=[["AUTH"], ["API"]],
        )
        spec_plans = {
            "AUTH": _make_spec_plan(
                [
                    {"title": "Auth Scaffold", "outputs": ["src/auth/mod.rs"]},
                ]
            ),
            "API": _make_spec_plan(
                [
                    {"title": "API Scaffold", "outputs": ["src/api/mod.rs"]},
                ]
            ),
        }

        plan = merge_into_global_plan(spec_plans, graph, smds)

        api_task = next(t for t in plan.tasks if t.spec == "API")
        # Should NOT inject auth files — cross-spec uses interfaces, not inject_files
        assert api_task.inject_files == []

    def test_all_tasks_start_as_pending(self):
        smds = [make_smd("AUTH")]
        graph = SpecGraph(
            specs=[SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[])],
            levels=[["AUTH"]],
        )
        spec_plans = {
            "AUTH": _make_spec_plan(
                [
                    {"title": "Scaffold"},
                    {"title": "Types", "depends_on": [1]},
                ]
            )
        }

        plan = merge_into_global_plan(spec_plans, graph, smds)

        assert all(t.status == TaskStatus.PENDING for t in plan.tasks)

    def test_empty_spec_plan_skipped(self):
        smds = [make_smd("AUTH")]
        graph = SpecGraph(
            specs=[SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[])],
            levels=[["AUTH"]],
        )
        spec_plans = {"AUTH": _make_spec_plan([])}

        plan = merge_into_global_plan(spec_plans, graph, smds)

        assert plan.meta.total_tasks == 0
        assert plan.tasks == []


class TestPlanTomlRoundtrip:
    def test_write_and_load(self, temp_dir: Path):
        smds = [make_smd("AUTH")]
        graph = SpecGraph(
            specs=[SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[])],
            levels=[["AUTH"]],
        )
        spec_plans = {
            "AUTH": _make_spec_plan(
                [
                    {"title": "Scaffold", "outputs": ["src/mod.rs"], "verify": "cargo check"},
                    {
                        "title": "Types",
                        "outputs": ["src/types.rs"],
                        "depends_on": [1],
                        "spec_refs": ["overview"],
                        "verify": "cargo check",
                    },
                ]
            )
        }

        plan = merge_into_global_plan(spec_plans, graph, smds)
        filepath = temp_dir / "plan.toml"

        write_plan(plan, filepath)

        assert filepath.exists()
        content = filepath.read_text()
        assert "Generated by `ossature audit`" in content

        loaded = load_plan(filepath)

        assert loaded is not None
        assert loaded.meta.total_tasks == plan.meta.total_tasks
        assert loaded.meta.specs == plan.meta.specs
        assert len(loaded.tasks) == len(plan.tasks)

        for orig, loaded_task in zip(plan.tasks, loaded.tasks):
            assert loaded_task.id == orig.id
            assert loaded_task.spec == orig.spec
            assert loaded_task.title == orig.title
            assert loaded_task.outputs == orig.outputs
            assert loaded_task.depends_on == orig.depends_on
            assert loaded_task.spec_refs == orig.spec_refs
            assert loaded_task.status == TaskStatus.PENDING
            assert loaded_task.verify == orig.verify

    def test_load_nonexistent_returns_none(self, temp_dir: Path):
        assert load_plan(temp_dir / "nonexistent.toml") is None

    def test_load_malformed_returns_none(self, temp_dir: Path):
        filepath = temp_dir / "bad.toml"
        filepath.write_text("not valid { toml [[[")
        assert load_plan(filepath) is None


class TestWriteTaskDefinitions:
    def test_creates_task_directories(self, temp_dir: Path):
        smds = [make_smd("AUTH")]
        graph = SpecGraph(
            specs=[SpecGraphEntry(id="AUTH", file="specs/auth.smd", depends=[])],
            levels=[["AUTH"]],
        )
        spec_plans = {
            "AUTH": _make_spec_plan(
                [
                    {"title": "Scaffold", "outputs": ["src/mod.rs"]},
                    {"title": "Types", "outputs": ["src/types.rs"], "depends_on": [1]},
                ]
            )
        }

        plan = merge_into_global_plan(spec_plans, graph, smds)
        tasks_dir = temp_dir / "tasks"

        write_task_definitions(plan, tasks_dir)

        task_dirs = sorted(tasks_dir.iterdir())
        assert len(task_dirs) == 2
        assert (task_dirs[0] / "task.toml").exists()
        assert (task_dirs[1] / "task.toml").exists()
