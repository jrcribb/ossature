from ossature.cli.commands.retry import _collect_dependents
from ossature.models.plan import PlanTask, TaskStatus


def _make_task(id: str, depends_on: list[str] | None = None, status: str = "done") -> PlanTask:
    return PlanTask(
        id=id,
        spec="TEST",
        title=f"Task {id}",
        description="",
        outputs=[],
        depends_on=depends_on or [],
        spec_refs=[],
        arch_refs=[],
        status=TaskStatus(status),
        verify="",
    )


class TestCollectDependents:
    def test_no_dependents(self):
        tasks = [_make_task("001"), _make_task("002")]
        assert _collect_dependents("001", tasks) == set()

    def test_direct_dependent(self):
        tasks = [
            _make_task("001"),
            _make_task("002", depends_on=["001"]),
        ]
        assert _collect_dependents("001", tasks) == {"002"}

    def test_transitive_dependents(self):
        tasks = [
            _make_task("001"),
            _make_task("002", depends_on=["001"]),
            _make_task("003", depends_on=["002"]),
        ]
        assert _collect_dependents("001", tasks) == {"002", "003"}

    def test_diamond_dependents(self):
        tasks = [
            _make_task("001"),
            _make_task("002", depends_on=["001"]),
            _make_task("003", depends_on=["001"]),
            _make_task("004", depends_on=["002", "003"]),
        ]
        assert _collect_dependents("001", tasks) == {"002", "003", "004"}

    def test_middle_task(self):
        tasks = [
            _make_task("001"),
            _make_task("002", depends_on=["001"]),
            _make_task("003", depends_on=["002"]),
        ]
        assert _collect_dependents("002", tasks) == {"003"}

    def test_unrelated_tasks_not_included(self):
        tasks = [
            _make_task("001"),
            _make_task("002", depends_on=["001"]),
            _make_task("003"),  # independent
        ]
        assert _collect_dependents("001", tasks) == {"002"}
