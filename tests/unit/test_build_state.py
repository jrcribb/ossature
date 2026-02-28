from pathlib import Path

from conftest import make_config, make_task

from ntt.build.state import (
    BuildState,
    TaskState,
    compute_input_hash,
    compute_output_hash,
    get_task_written_files,
    load_state,
    write_state,
)


class TestComputeInputHash:
    def test_same_inputs_same_hash(self, temp_dir: Path):
        config = make_config(temp_dir)
        task = make_task("001", "AUTH")
        prompt = "Generate auth module"
        h1 = compute_input_hash(prompt, task, config)
        h2 = compute_input_hash(prompt, task, config)
        assert h1 == h2
        assert h1.startswith("sha256:")

    def test_different_prompt_different_hash(self, temp_dir: Path):
        config = make_config(temp_dir)
        task = make_task("001", "AUTH")
        h1 = compute_input_hash("prompt A", task, config)
        h2 = compute_input_hash("prompt B", task, config)
        assert h1 != h2

    def test_includes_inject_files_content(self, temp_dir: Path):
        output_dir = temp_dir / "output"
        (output_dir / "src").mkdir(parents=True)
        (output_dir / "src" / "dep.py").write_text("version 1")

        config = make_config(temp_dir)
        task = make_task("001", "AUTH")
        task.inject_files = ["src/dep.py"]
        prompt = "same prompt"

        h1 = compute_input_hash(prompt, task, config)

        (output_dir / "src" / "dep.py").write_text("version 2")
        h2 = compute_input_hash(prompt, task, config)

        assert h1 != h2

    def test_missing_inject_files_ignored(self, temp_dir: Path):
        config = make_config(temp_dir)
        task = make_task("001", "AUTH")
        task.inject_files = ["nonexistent.py"]
        prompt = "test"
        # Should not raise
        h = compute_input_hash(prompt, task, config)
        assert h.startswith("sha256:")

    def test_inject_files_order_independent(self, temp_dir: Path):
        output_dir = temp_dir / "output"
        (output_dir / "src").mkdir(parents=True)
        (output_dir / "src" / "a.py").write_text("aaa")
        (output_dir / "src" / "b.py").write_text("bbb")

        config = make_config(temp_dir)
        task1 = make_task("001", "AUTH")
        task1.inject_files = ["src/a.py", "src/b.py"]
        task2 = make_task("001", "AUTH")
        task2.inject_files = ["src/b.py", "src/a.py"]

        h1 = compute_input_hash("p", task1, config)
        h2 = compute_input_hash("p", task2, config)
        assert h1 == h2


class TestComputeOutputHash:
    def test_same_files_same_hash(self, temp_dir: Path):
        output_dir = temp_dir / "output"
        (output_dir / "src").mkdir(parents=True)
        (output_dir / "src" / "mod.py").write_text("content")

        config = make_config(temp_dir)
        h1 = compute_output_hash(["src/mod.py"], config)
        h2 = compute_output_hash(["src/mod.py"], config)
        assert h1 == h2
        assert h1.startswith("sha256:")

    def test_changed_content_different_hash(self, temp_dir: Path):
        output_dir = temp_dir / "output"
        (output_dir / "src").mkdir(parents=True)
        (output_dir / "src" / "mod.py").write_text("v1")

        config = make_config(temp_dir)
        h1 = compute_output_hash(["src/mod.py"], config)

        (output_dir / "src" / "mod.py").write_text("v2")
        h2 = compute_output_hash(["src/mod.py"], config)
        assert h1 != h2

    def test_missing_files_ignored(self, temp_dir: Path):
        config = make_config(temp_dir)
        h = compute_output_hash(["nonexistent.py"], config)
        assert h.startswith("sha256:")


class TestLoadWriteState:
    def test_roundtrip(self, temp_dir: Path):
        filepath = temp_dir / ".ntt" / "state.toml"
        state = BuildState()
        state.set("001", TaskState("sha256:aaa", "sha256:bbb", ["src/mod.py"]))
        state.set("002", TaskState("sha256:ccc", "sha256:ddd", ["src/types.py"]))
        write_state(state, filepath)

        loaded = load_state(filepath)
        assert loaded.get("001") is not None
        assert loaded.get("001").input_hash == "sha256:aaa"
        assert loaded.get("001").output_hash == "sha256:bbb"
        assert loaded.get("001").written_files == ["src/mod.py"]
        assert loaded.get("002").input_hash == "sha256:ccc"

    def test_load_nonexistent_returns_empty(self, temp_dir: Path):
        state = load_state(temp_dir / "nonexistent.toml")
        assert state.tasks == {}

    def test_load_malformed_returns_empty(self, temp_dir: Path):
        filepath = temp_dir / "bad.toml"
        filepath.write_text("this is not valid toml [[[")
        state = load_state(filepath)
        assert state.tasks == {}

    def test_get_missing_returns_none(self):
        state = BuildState()
        assert state.get("999") is None

    def test_set_overwrites(self):
        state = BuildState()
        state.set("001", TaskState("h1", "h2", ["a.py"]))
        state.set("001", TaskState("h3", "h4", ["b.py"]))
        assert state.get("001").input_hash == "h3"
        assert state.get("001").written_files == ["b.py"]

    def test_write_creates_parent_dirs(self, temp_dir: Path):
        filepath = temp_dir / "deep" / "nested" / "state.toml"
        state = BuildState()
        state.set("001", TaskState("h1", "h2", []))
        write_state(state, filepath)
        assert filepath.exists()


class TestGetTaskWrittenFiles:
    def test_reads_from_output_toml(self, temp_dir: Path):
        tasks_dir = temp_dir / ".ntt" / "tasks"
        task = make_task("001", "AUTH", outputs=["declared.py"])
        slug = "auth-task-001"

        task_dir = tasks_dir / f"001-{slug}"
        task_dir.mkdir(parents=True)

        import tomli_w

        with open(task_dir / "output.toml", "wb") as f:
            tomli_w.dump({"files": ["actual_a.py", "actual_b.py"], "success": True}, f)

        result = get_task_written_files(task, tasks_dir)
        assert result == ["actual_a.py", "actual_b.py"]

    def test_falls_back_to_task_outputs(self, temp_dir: Path):
        tasks_dir = temp_dir / ".ntt" / "tasks"
        task = make_task("001", "AUTH", outputs=["declared.py"])

        result = get_task_written_files(task, tasks_dir)
        assert result == ["declared.py"]
