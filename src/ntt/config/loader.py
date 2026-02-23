from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli


class ConfigError(Exception):
    pass


@dataclass
class OutputConfig:
    dir: str = "output"
    language: str = "python"
    framework: str | None = None


@dataclass
class TestConfig:
    runner: str = "pytest"
    coverage: bool = True
    coverage_threshold: float = 80.0


@dataclass
class NTTConfig:
    name: str = "ntt-project"
    version: str = "0.0.1"
    spec_dir: str = "specs"
    context_dir: str = "context"

    output: OutputConfig = field(default_factory=OutputConfig)
    test: TestConfig = field(default_factory=TestConfig)

    root: Path = field(default_factory=Path.cwd)

    @property
    def spec_path(self) -> Path:
        return self.root / self.spec_dir

    @property
    def context_path(self) -> Path:
        return self.root / self.context_dir

    @property
    def output_path(self) -> Path:
        return self.root / self.output.dir

    @property
    def metadata_path(self) -> Path:
        return self.root / ".ntt"

    @property
    def metadata_context_path(self) -> Path:
        return self.metadata_path / "context"

    @property
    def metadata_context_spec_briefs_path(self) -> Path:
        return self.metadata_context_path / "spec-briefs"

    @property
    def metadata_context_interfaces_path(self) -> Path:
        return self.metadata_context_path / "interfaces"

    @property
    def is_audited(self) -> bool:
        return self.metadata_path.exists()


def find_config(start_path: Path | None = None) -> Path | None:
    current = start_path or Path.cwd()
    current = current.resolve()

    while current != current.parent:
        config_path = current / "ntt.toml"
        if config_path.exists():
            return config_path
        current = current.parent

    config_path = current / "ntt.toml"
    if config_path.exists():
        return config_path

    return None


def _parse_output_config(data: dict[str, Any]) -> OutputConfig:
    return OutputConfig(
        dir=data.get("dir", "."),
        language=data.get("language", "python"),
    )


def _parse_test_config(data: dict[str, Any]) -> TestConfig:
    return TestConfig(
        runner=data.get("runner", "pytest"),
        coverage=data.get("coverage", True),
        coverage_threshold=float(data.get("coverage_threshold", 80.0)),
    )


def load_config(path: Path | None = None) -> NTTConfig:
    if path is None:
        path = find_config()
        if path is None:
            raise ConfigError("No ntt.toml found.")

    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    try:
        content = path.read_text(encoding="utf-8")
        data = tomli.loads(content)
    except Exception as e:
        raise ConfigError(f"Failed to parse {path}: {e}")

    project = data.get("project", {})

    config = NTTConfig(
        name=project.get("name", "ntt-project"),
        version=project.get("version", "0.0.1"),
        spec_dir=project.get("spec_dir", "specs"),
        context_dir=project.get("context_dir", "context"),
        output=_parse_output_config(data.get("output", {})),
        test=_parse_test_config(data.get("test", {})),
        root=path.parent,
    )

    return config
