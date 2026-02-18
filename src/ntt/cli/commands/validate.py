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

    if verbose:
        console.print(f"Validating {len(smd_files)} SMD(s)")

    parsed_smds = []
    parsed_amds = []

    for smd_file in smd_files:
        smd_filename = str(smd_file).replace(str(config.root), ".")
        if verbose:
            console.print(f" {smd_filename} ", end="")
        try:
            smd = parse_smd_file(smd_file)
            parsed_smds.append(smd)

            if verbose:
                console.print("[green]✓")
        except SMDParseError as e:
            if verbose:
                console.print(f"[red]x[/] - {len(e.errors)} error(s)")
            else:
                console.print(smd_filename)
                console.print(f"[red]Validation Error:[/] {len(e.errors)} error(s)")

            for error in e.errors:
                console.print(f"  - {error}")

            raise SystemExit(1)

    if verbose:
        console.print()
        console.print(f"Validating {len(amd_files)} AMD(s)")

    for amd_file in amd_files:
        amd_filename = str(amd_file).replace(str(config.root), ".")
        if verbose:
            console.print(f" {amd_filename} ", end="")
        try:
            amd = parse_amd_file(amd_file)
            parsed_amds.append(amd)

            if verbose:
                console.print("[green]✓")
        except AMDParseError as e:
            if verbose:
                console.print(f"[red]x[/] - {len(e.errors)} error(s)")
            else:
                console.print(smd_filename)
                console.print(f"[red]Validation Error:[/] {len(e.errors)} error(s)")

            for error in e.errors:
                console.print(f"  - {error}")

            raise SystemExit(1)

    if verbose:
        console.print()
        console.print("Cross-references: ", end="")

    # Cross reference parsed spec ids
    smd_spec_ids = [smd.spec_id for smd in parsed_smds]

    for smd in parsed_smds:
        for dep in smd.depends:
            if dep not in smd_spec_ids:
                console.print("[red]x")
                console.print(f" Spec {smd.spec_id} has dependency {dep} that doesn't exist.")
                raise SystemExit(1)

    console.print("[green]✓ all dependency spec IDs resolve")

    # Summary
    console.print(
        Panel(
            f"[green]✓[/green] Specs validated:\n"
            f"  • {len(parsed_smds)} SMD(s)\n"
            f"  • {len(parsed_amds)} AMD(s)",
            title="Summary",
            border_style="green",
        )
    )
