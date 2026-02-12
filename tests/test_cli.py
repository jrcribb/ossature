from pathlib import Path

import pytest
from click.testing import CliRunner

from ntt.cli.main import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestInitCmd:
    def test_something(self, runner: CliRunner, temp_dir: Path):
        with runner.isolated_filesystem(temp_dir=temp_dir):
            result = runner.invoke(cli, ["init", "test-project"])

            assert result.exit_code == 0
            # assert (Path("my-project") / "ntt.toml").exists()
