import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from ntt.config.loader import NTTConfig, OutputConfig
from ntt.templates.manager import TemplateManager


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
def sample_config(temp_dir: Path) -> NTTConfig:
    return NTTConfig(
        name="test-project",
        version="0.0.1",
        root=temp_dir,
        spec_dir="specs",
        output=OutputConfig(language="python"),
    )
