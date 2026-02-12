from pathlib import Path

from rich.console import Console


def run_init(
    name: str,
    include_example: bool,
    console: Console,
) -> None:
    if name == ".":
        root = Path.cwd()
        project_name = root.name
    else:
        root = Path.cwd() / name
        project_name = name
        root.mkdir(parents=True, exist_ok=True)

    console.print(f"\n[bold]Initializing NTT project:[/] {project_name}\n")
