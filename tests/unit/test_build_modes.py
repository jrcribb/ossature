from unittest.mock import MagicMock

import pytest

from ntt.build.builder import BuildMode
from ntt.cli.commands.build import _resolve_spec_filter


class TestResolveSpecFilter:
    def test_resolves_single_spec_no_deps(self):
        console = MagicMock()
        result = _resolve_spec_filter("AUTH", ["AUTH", "API"], {}, console)
        assert result == {"AUTH"}

    def test_resolves_case_insensitive(self):
        console = MagicMock()
        result = _resolve_spec_filter("auth", ["AUTH", "API"], {}, console)
        assert result == {"AUTH"}

    def test_resolves_with_transitive_deps(self):
        console = MagicMock()
        smd_deps = {
            "AUTH": [],
            "DATABASE": [],
            "API": ["AUTH", "DATABASE"],
            "FRONTEND": ["API"],
        }
        result = _resolve_spec_filter(
            "FRONTEND", ["AUTH", "DATABASE", "API", "FRONTEND"], smd_deps, console
        )
        assert result == {"AUTH", "DATABASE", "API", "FRONTEND"}

    def test_resolves_direct_deps_only(self):
        console = MagicMock()
        smd_deps = {
            "AUTH": [],
            "DATABASE": [],
            "API": ["AUTH", "DATABASE"],
            "FRONTEND": ["API"],
        }
        result = _resolve_spec_filter(
            "API", ["AUTH", "DATABASE", "API", "FRONTEND"], smd_deps, console
        )
        assert result == {"AUTH", "DATABASE", "API"}
        assert "FRONTEND" not in result

    def test_unknown_spec_exits(self):
        console = MagicMock()
        with pytest.raises(SystemExit):
            _resolve_spec_filter("NONEXISTENT", ["AUTH", "API"], {}, console)

    def test_no_circular_deps(self):
        console = MagicMock()
        # Even with a cycle in deps, we don't infinite loop (visited set)
        smd_deps = {"A": ["B"], "B": ["A"]}
        result = _resolve_spec_filter("A", ["A", "B"], smd_deps, console)
        assert result == {"A", "B"}


class TestBuildMode:
    def test_mode_values(self):
        assert BuildMode.DEFAULT.value == "default"
        assert BuildMode.STEP.value == "step"
        assert BuildMode.AUTO.value == "auto"
        assert BuildMode.AUTO_SKIP.value == "auto_skip"
