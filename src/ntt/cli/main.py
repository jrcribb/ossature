from pathlib import Path

import click
from rich.console import Console

from ntt import __app_name__, __version__

console = Console()


class NaturalOrderGroup(click.Group):
    def list_commands(self, ctx: click.Context) -> list[str]:
        return list(self.commands)


@click.group(cls=NaturalOrderGroup)
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
    """Intent - Specifiction and architecture driven code generation toolkit."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config
    ctx.obj["verbose"] = verbose
    ctx.obj["console"] = console


@cli.command()
@click.argument("name", default=".")
@click.pass_context
def init(ctx: click.Context, name: str) -> None:
    """Initialize a new Intent project."""
    from ntt.cli.commands.init import run_init

    run_init(
        name=name,
        console=ctx.obj["console"],
    )


@cli.command()
@click.argument("name")
@click.option(
    "--type",
    "-t",
    "spec_type",
    type=click.Choice(["smd", "amd"]),
    default="smd",
    help="Type of spec to create (defaults to smd)",
)
@click.option(
    "--interactive",
    "-i",
    is_flag=True,
    help="Create spec interactively",
)
@click.pass_context
def new(
    ctx: click.Context,
    name: str,
    spec_type: str,
    interactive: bool,
) -> None:
    """Create a new spec file."""
    from ntt.cli.commands.new import run_new

    run_new(
        name=name,
        spec_type=spec_type,
        interactive=interactive,
        config_path=ctx.obj["config_path"],
        console=ctx.obj["console"],
    )


@cli.command()
@click.pass_context
def validate(
    ctx: click.Context,
) -> None:
    """Validate config and spec files."""
    from ntt.cli.commands.validate import run_validate

    run_validate(
        config_path=ctx.obj["config_path"],
        verbose=ctx.obj["verbose"],
        console=ctx.obj["console"],
    )


@cli.command()
@click.option(
    "--replan",
    is_flag=True,
    help="Regenerate the build plan (discards manual edits to plan.toml)",
)
@click.pass_context
def audit(
    ctx: click.Context,
    replan: bool,
) -> None:
    """Semantically audit the specifications and generate plan metadata."""
    from ntt.cli.commands.audit import run_audit

    run_audit(
        config_path=ctx.obj["config_path"],
        verbose=ctx.obj["verbose"],
        console=ctx.obj["console"],
        replan=replan,
    )
