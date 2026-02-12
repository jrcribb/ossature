from ntt import __version__, __app_name__

import click
from rich.console import Console

from pathlib import Path


console = Console()


@click.group()
@click.version_option(version=__version__, prog_name=__app_name__)
@click.option(
    "--config",
    "-c",
    type=click.Path(exists=True, path_type=Path),
    help="Path to ntt.toml config file",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Enable verbose output",
)
@click.pass_context
def cli(ctx: click.Context, config: Path | None, verbose: bool) -> None:
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config
    ctx.obj["verbose"] = verbose
    ctx.obj["console"] = console
