from pathlib import Path

import click
from rich.console import Console

from ntt import __app_name__, __version__

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


@cli.command()
@click.argument("name", default=".")
@click.option(
    "--no-example",
    is_flag=True,
    help="Don't create example spec files",
)
@click.pass_context
def init(ctx: click.Context, name: str, no_example: bool) -> None:
    from ntt.cli.commands.init import run_init

    run_init(
        name=name,
        include_example=not no_example,
        console=ctx.obj["console"],
    )
