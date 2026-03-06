import json
from pathlib import Path

import pytest
from pydantic_ai import ModelRetry
from rich.console import Console

from ossature.build.builder import _resolve_sandboxed, _validate_command
from ossature.shared import apply_edits


@pytest.fixture()
def quiet_console() -> Console:
    return Console(quiet=True)


class TestResolveSandboxed:
    def test_simple_relative_path(self, tmp_path: Path, quiet_console: Console) -> None:
        result = _resolve_sandboxed(tmp_path, "src/main.rs", quiet_console)
        assert result == tmp_path / "src" / "main.rs"

    def test_nested_relative_path(self, tmp_path: Path, quiet_console: Console) -> None:
        result = _resolve_sandboxed(tmp_path, "src/auth/mod.rs", quiet_console)
        assert result == tmp_path / "src" / "auth" / "mod.rs"

    def test_dot_in_filename(self, tmp_path: Path, quiet_console: Console) -> None:
        result = _resolve_sandboxed(tmp_path, "Cargo.toml", quiet_console)
        assert result == tmp_path / "Cargo.toml"

    def test_rejects_parent_traversal(self, tmp_path: Path, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _resolve_sandboxed(tmp_path, "../etc/passwd", quiet_console)

    def test_rejects_deep_traversal(self, tmp_path: Path, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _resolve_sandboxed(tmp_path, "src/../../etc/shadow", quiet_console)

    def test_rejects_absolute_path(self, tmp_path: Path, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _resolve_sandboxed(tmp_path, "/etc/passwd", quiet_console)

    def test_rejects_absolute_path_to_different_dir(
        self, tmp_path: Path, quiet_console: Console
    ) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _resolve_sandboxed(tmp_path, "/tmp/evil", quiet_console)

    def test_allows_current_dir(self, tmp_path: Path, quiet_console: Console) -> None:
        result = _resolve_sandboxed(tmp_path, ".", quiet_console)
        assert result == tmp_path

    def test_allows_path_with_dot_segments_staying_inside(
        self, tmp_path: Path, quiet_console: Console
    ) -> None:
        result = _resolve_sandboxed(tmp_path, "src/../src/main.rs", quiet_console)
        assert result == tmp_path / "src" / "main.rs"

    def test_rejects_traversal_via_dot_segments(
        self, tmp_path: Path, quiet_console: Console
    ) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _resolve_sandboxed(tmp_path, "src/../../outside", quiet_console)


class TestValidateCommand:
    def test_allows_simple_command(self, quiet_console: Console) -> None:
        _validate_command("cargo check", quiet_console)

    def test_allows_make(self, quiet_console: Console) -> None:
        _validate_command("make build", quiet_console)

    def test_allows_relative_path(self, quiet_console: Console) -> None:
        _validate_command("./build.sh", quiet_console)

    def test_rejects_traversal(self, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _validate_command("cat ../secret", quiet_console)

    def test_rejects_absolute_path(self, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _validate_command("/bin/rm -rf /", quiet_console)

    def test_rejects_chained_absolute(self, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _validate_command("echo hello; /usr/bin/evil", quiet_console)

    def test_rejects_pipe_to_absolute(self, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _validate_command("echo hello | /usr/bin/evil", quiet_console)

    def test_rejects_and_absolute(self, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _validate_command("true && /usr/bin/evil", quiet_console)

    def test_rejects_traversal_in_middle(self, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _validate_command("cat src/../../etc/passwd", quiet_console)

    def test_allows_pytest(self, quiet_console: Console) -> None:
        _validate_command("python -m pytest tests/", quiet_console)

    def test_allows_cargo_test(self, quiet_console: Console) -> None:
        _validate_command("cargo test --release", quiet_console)

    def test_rejects_ls_root(self, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _validate_command("ls /", quiet_console)

    def test_rejects_ls_absolute_dir(self, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _validate_command("ls /Users", quiet_console)

    def test_rejects_cat_absolute_arg(self, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _validate_command("cat /etc/passwd", quiet_console)

    def test_rejects_cp_absolute_dest(self, quiet_console: Console) -> None:
        with pytest.raises(ModelRetry, match="Access denied"):
            _validate_command("cp foo.txt /tmp/foo.txt", quiet_console)


class TestApplyEdits:
    SAMPLE = 'fn main() {\n    println!("hello");\n}\n'

    def test_single_edit(self) -> None:
        edits = json.dumps([{"old": "hello", "new": "world"}])
        result = apply_edits(self.SAMPLE, edits)
        assert "world" in result
        assert "hello" not in result

    def test_multiple_edits(self) -> None:
        content = "aaa\nbbb\nccc\n"
        edits = json.dumps(
            [
                {"old": "aaa", "new": "AAA"},
                {"old": "ccc", "new": "CCC"},
            ]
        )
        result = apply_edits(content, edits)
        assert result == "AAA\nbbb\nCCC\n"

    def test_sequential_edits_see_previous_changes(self) -> None:
        content = "foo bar"
        edits = json.dumps(
            [
                {"old": "foo", "new": "baz"},
                {"old": "baz bar", "new": "done"},
            ]
        )
        result = apply_edits(content, edits)
        assert result == "done"

    def test_multiline_old_and_new(self) -> None:
        content = "start\n    if x > 0 {\n        return x;\n    }\nend\n"
        edits = json.dumps(
            [
                {
                    "old": "    if x > 0 {\n        return x;\n    }",
                    "new": "    if x > 0 {\n        return x * 2;\n    }",
                }
            ]
        )
        result = apply_edits(content, edits)
        assert "return x * 2;" in result

    def test_rejects_invalid_json(self) -> None:
        with pytest.raises(ModelRetry, match="Could not parse edits JSON"):
            apply_edits("content", "not json")

    def test_rejects_non_array(self) -> None:
        with pytest.raises(ModelRetry, match="Expected a JSON array"):
            apply_edits("content", '{"old": "a", "new": "b"}')

    def test_rejects_empty_array(self) -> None:
        with pytest.raises(ModelRetry, match="empty"):
            apply_edits("content", "[]")

    def test_rejects_non_object_entry(self) -> None:
        with pytest.raises(ModelRetry, match="not an object"):
            apply_edits("content", '["not an object"]')

    def test_rejects_missing_old_key(self) -> None:
        with pytest.raises(ModelRetry, match="missing key.*old"):
            apply_edits("content", json.dumps([{"new": "b"}]))

    def test_rejects_missing_new_key(self) -> None:
        with pytest.raises(ModelRetry, match="missing key.*new"):
            apply_edits("content", json.dumps([{"old": "a"}]))

    def test_rejects_non_string_values(self) -> None:
        with pytest.raises(ModelRetry, match="must both be strings"):
            apply_edits("content", json.dumps([{"old": 123, "new": "b"}]))

    def test_rejects_identical_old_new(self) -> None:
        with pytest.raises(ModelRetry, match="identical"):
            apply_edits("content", json.dumps([{"old": "x", "new": "x"}]))

    def test_rejects_old_not_found(self) -> None:
        with pytest.raises(ModelRetry, match="not found"):
            apply_edits("hello world", json.dumps([{"old": "missing", "new": "x"}]))

    def test_rejects_ambiguous_match(self) -> None:
        with pytest.raises(ModelRetry, match="matches 2 locations"):
            apply_edits("aaa bbb aaa", json.dumps([{"old": "aaa", "new": "x"}]))
