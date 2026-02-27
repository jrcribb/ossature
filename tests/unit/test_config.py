import os
from pathlib import Path

import pytest

from ntt.config.loader import ConfigError, NTTConfig, load_config


class TestConfigLoader:
    def test_load_config_from_initialized_project(self, initialized_project: Path):

        original_cwd = os.getcwd()

        try:
            os.chdir(initialized_project)
            config = load_config()

            assert config.name == "test-project"
            assert config.root.resolve() == initialized_project.resolve()
        finally:
            os.chdir(original_cwd)

    def test_load_config_explicit_path(self, initialized_project: Path):
        config_path = initialized_project / "ntt.toml"
        config = load_config(config_path)

        assert config.name == "test-project"

    def test_load_config_not_found(self, temp_dir: Path):

        original_cwd = os.getcwd()
        try:
            os.chdir(temp_dir)
            with pytest.raises(ConfigError):
                load_config()
        finally:
            os.chdir(original_cwd)

    def test_config_paths(self, sample_config: NTTConfig):
        assert sample_config.spec_path == sample_config.root / "specs"
        assert sample_config.context_path == sample_config.root / "context"
        assert sample_config.output_path == sample_config.root / "output"

    def test_build_config_defaults(self, sample_config: NTTConfig):
        assert sample_config.build.max_fix_attempts == 3
        assert sample_config.build.setup is None
        assert sample_config.build.verify is None
        assert sample_config.build.test is None

    def test_load_config_with_build_section(self, temp_dir: Path):
        config_content = """
[project]
name = "test-project"
version = "0.0.1"

[output]
dir = "output"
language = "rust"

[build]
setup = "cargo init"
verify = "cargo check"
test = "cargo test"
max_fix_attempts = 5
"""
        (temp_dir / "ntt.toml").write_text(config_content)
        config = load_config(temp_dir / "ntt.toml")
        assert config.build.setup == "cargo init"
        assert config.build.verify == "cargo check"
        assert config.build.test == "cargo test"
        assert config.build.max_fix_attempts == 5
