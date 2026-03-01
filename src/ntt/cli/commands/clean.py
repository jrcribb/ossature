import shutil
from pathlib import Path

import questionary
from rich.console import Console

from ntt.config.loader import ConfigError, load_config


def run_clean(
    config_path: Path | None,
    console: Console,
) -> None:
    try:
        config = load_config(config_path)
    except ConfigError as e:
        console.print(f"[red]Error:[/] {e}")
        raise SystemExit(1)

    ntt_dir = config.metadata_path

    if not ntt_dir.exists():
        console.print("[yellow]Nothing to clean.[/] No .ntt/ directory found.")
        return

    if not questionary.confirm(
        "This will delete any previously generated audits, plans, or state files. "
        "Are you sure you want to continue?",
        default=False,
    ).ask():
        raise SystemExit(0)

    shutil.rmtree(ntt_dir)
    console.print("[green]✓[/] Removed .ntt/ — full reset complete.")
