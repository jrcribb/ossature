from pathlib import Path

from rich.console import Console

from ntt.cli.decorators import requires_llm
from ntt.config.loader import ConfigError, load_config
from ntt.models.amd import AMDSpec
from ntt.parsers.amd import parse_amd_file
from ntt.parsers.smd import parse_smd_file


@requires_llm
def run_build(
    config_path: Path | None,
    verbose: bool,
    console: Console,
) -> None:
    try:
        config = load_config(config_path)
    except ConfigError as e:
        console.print(f"[red]Error:[/] {e}")
        raise SystemExit(1)

    plan_filepath = config.metadata_path / "plan.toml"

    from ntt.audit.planner import load_plan

    plan = load_plan(plan_filepath)
    if not plan:
        console.print("[red]No plan found. Run `ntt audit` first.[/red]")
        raise SystemExit(1)

    pending = sum(1 for t in plan.tasks if t.status.value == "pending")
    if pending == 0:
        console.print("[green]All tasks already completed.[/green]")
        return

    console.print(
        f"[bold]{config.name} v{config.version}[/bold] — "
        f"{plan.meta.total_tasks} tasks, {pending} pending\n"
    )

    # Parse specs for context assembly
    smd_files = list(config.spec_path.glob("**/*.smd"))
    amd_files = list(config.spec_path.glob("**/*.amd"))

    parsed_smds = [parse_smd_file(f) for f in smd_files]
    parsed_amds = [parse_amd_file(f) for f in amd_files]

    smd_map = {smd.spec_id: smd for smd in parsed_smds}
    amd_by_spec: dict[str, list[AMDSpec]] = {}
    for amd in parsed_amds:
        amd_by_spec.setdefault(amd.spec_id, []).append(amd)

    from ntt.build.builder import execute_build

    execute_build(config, plan, smd_map, amd_by_spec, console, plan_filepath)
