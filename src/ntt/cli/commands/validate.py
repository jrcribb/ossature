from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from ntt.config.loader import ConfigError, load_config
from ntt.parsers.amd import AMDParseError, parse_amd_file
from ntt.parsers.smd import SMDParseError, parse_smd_file


def run_validate(
    config_path: Path,
    verbose: bool,
    console: Console,
) -> None:
    try:
        config = load_config(config_path)
    except ConfigError as e:
        console.print(f"[red]Error:[/] {e}")
        console.print("Run [cyan]ntt init[/] first to create a project.")
        raise SystemExit(1)

    _conf_file = config.root / "ntt.toml"
    smd_files = list(config.spec_path.glob("**/*.smd"))
    amd_files = list(config.spec_path.glob("**/*.amd"))

    if not smd_files:
        console.print("[yellow]No spec files found.[/]")
        return

    for smd_file in smd_files:
        try:
            parse_smd_file(smd_file)
        except SMDParseError as e:
            console.print("[red]Validation error[/]")
            console.print(f"  File: {str(smd_file).replace(str(config.root), '.')}")
            console.print(f"  {len(e.errors)} error(s)")
            for error in e.errors:
                console.print(f"  - {error}")
            raise SystemExit(1)

    for amd_file in amd_files:
        try:
            parse_amd_file(amd_file)
        except AMDParseError as e:
            console.print("[red]Validation error[/]")
            console.print(f"  File: {str(amd_file).replace(str(config.root), '.')}")
            console.print(f"  {len(e.errors)} error(s)")
            for error in e.errors:
                console.print(f"  - {error}")
            raise SystemExit(1)

    console.print(
        Panel(
            f"[green]✓[/green] Specs validated:\n"
            f"  • {len(smd_files)} SMD(s)\n"
            f"  • {len(amd_files)} AMD(s)",
            title="Summary",
            border_style="green",
        )
    )
