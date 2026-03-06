from pathlib import Path

from ossature.templates.manager import TemplateManager


class TestTemplateManager:
    def test_init_project_creates_structure(self, temp_dir: Path):
        manager = TemplateManager(temp_dir)
        result = manager.init_project(name="test-project")

        assert result.success
        assert (temp_dir / ".gitignore").exists()
        assert (temp_dir / "ossature.toml").exists()
        assert (temp_dir / "specs").is_dir()
