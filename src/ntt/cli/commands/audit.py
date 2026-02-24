from pathlib import Path

import questionary
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.status import Status
from rich.table import Table
from rich.text import Text

from ntt.audit.audit import audit_cross_specs, audit_specs, save_audit_report
from ntt.audit.context import generate_project_brief, generate_spec_briefs
from ntt.audit.manifest import create_manifest, read_manifest, write_manifest
from ntt.config.loader import ConfigError, NTTConfig, load_config
from ntt.models.amd import AMDSpec
from ntt.models.audit import (
    AuditFinding,
    CrossSpecAuditReport,
    CrossSpecFinding,
    Severity,
    SpecAuditReport,
)
from ntt.models.smd import SMDSpec
from ntt.parsers.amd import AMDParseError, parse_amd_file
from ntt.parsers.smd import SMDParseError, parse_smd_file


class ValidationError(Exception): ...


SEVERITY_STYLES: dict[Severity, tuple[str, str]] = {
    Severity.ERROR: ("red", "ERROR"),
    Severity.WARNING: ("yellow", "WARNING"),
    Severity.INFO: ("cyan", "INFO"),
}


def print_audit_summary(
    console: Console,
    report: SpecAuditReport | CrossSpecAuditReport,
    title: str = "Spec Audit Report",
) -> None:
    counts = {s: 0 for s in Severity}
    for finding in report.findings:
        counts[finding.severity] += 1

    summary = Text()
    for severity, (style, label) in SEVERITY_STYLES.items():
        summary.append(f"  {label}: {counts[severity]}  ", style=f"bold {style}")

    console.print()
    console.print(Panel(summary, title=f"[bold]{title}[/bold]", expand=False, box=box.ROUNDED))


def print_audit_findings_table(
    console: Console, report: SpecAuditReport | CrossSpecAuditReport
) -> None:
    table = Table(
        box=box.SIMPLE_HEAD,
        show_lines=True,
        expand=True,
        header_style="bold white",
    )

    table.add_column("Severity", style="bold", width=10, no_wrap=True)

    if isinstance(report, SpecAuditReport):
        table.add_column("Location", style="dim", width=20)
    else:
        table.add_column("Specs", style="dim", width=20)

    table.add_column("Issue", ratio=2)
    table.add_column("Suggestion", style="italic", ratio=3)

    severity_order = {Severity.INFO: 0, Severity.WARNING: 1, Severity.ERROR: 2}
    sorted_findings: list[AuditFinding | CrossSpecFinding] = sorted(
        report.findings, key=lambda x: severity_order[x.severity]
    )

    for finding in sorted_findings:
        style, label = SEVERITY_STYLES[finding.severity]
        table.add_row(
            Text(label, style=f"bold {style}"),
            finding.location if isinstance(finding, AuditFinding) else "-".join(finding.specs),
            finding.issue,
            finding.suggestion,
        )

    console.print(table)
    console.print()


def present_findings_and_confirm(
    console: Console,
    status: Status,
    report: SpecAuditReport | CrossSpecAuditReport,
) -> None:
    counts = {s: 0 for s in Severity}
    for finding in report.findings:
        counts[finding.severity] += 1

    if not any(v > 0 for v in counts.values()):
        return

    status.stop()

    if questionary.confirm("Print full report?").ask():
        print_audit_findings_table(console, report=report)

    severity_counts = ", ".join(f"{v} {k.value}(s)" for k, v in counts.items() if v > 0)
    confirm_default = counts[Severity.ERROR] == 0

    if not questionary.confirm(
        f"Audit found {severity_counts}. Continue?", default=confirm_default
    ).ask():
        raise SystemExit(1)

    status.start()


def check_and_update_manifest(
    console: Console,
    config: NTTConfig,
    smd_files: list[Path],
    amd_files: list[Path],
) -> tuple[bool, bool]:
    """Returns (specs_require_audit, briefs_generation_required)."""
    config.metadata_path.mkdir(parents=True, exist_ok=True)
    manifest_path = config.metadata_path / "manifest.toml"
    new_manifest = create_manifest(config=config, smd_files=smd_files, amd_files=amd_files)

    if manifest_path.exists():
        console.log("Reading existing manifest")
        manifest = read_manifest(manifest_path)

        if not manifest:
            console.log("Malformed manifest. Disregarding.")
        else:
            mismatched = new_manifest.diff(other=manifest)

            if mismatched:
                console.log("[red]Manifest changed")
                for spec in mismatched:
                    console.log(f"Spec {spec} has changed")
                write_manifest(new_manifest, filename=manifest_path)
                console.log("Manifest updated")
            else:
                console.log("[green]Manifest unchanged")
                return False, False
    else:
        write_manifest(new_manifest, filename=manifest_path)
        console.log("[green]Manifest written")

    return True, True


def generate_and_write_briefs(
    console: Console,
    status: Status,
    config: NTTConfig,
    parsed_smds: list[SMDSpec],
) -> None:
    status.update("Generating project brief")
    project_brief = generate_project_brief(config=config, parsed_smds=parsed_smds)

    config.metadata_context_path.mkdir(parents=True, exist_ok=True)
    project_brief_filepath = config.metadata_context_path / "project-brief.md"
    with open(project_brief_filepath, "w") as f:
        f.write(project_brief.brief)
        f.flush()

    console.log(f"Project brief written to [bold]{project_brief_filepath}")

    status.update("Generating spec briefs")
    spec_brefs = generate_spec_briefs(config=config, parsed_smds=parsed_smds)
    config.metadata_context_spec_briefs_path.mkdir(parents=True, exist_ok=True)

    for spec_id, brief in spec_brefs.items():
        spec_brief_filepath = config.metadata_context_spec_briefs_path / f"{spec_id}.md"
        with open(spec_brief_filepath, "w") as f:
            f.write(brief.brief)
            f.flush()
        console.log(f"Spec brief written to [bold]{spec_brief_filepath}")

    status.stop()


def quick_validate(
    smd_files: list[Path], amd_files: list[Path]
) -> tuple[list[SMDSpec], list[AMDSpec]]:
    parsed_smds: list[SMDSpec] = []
    parsed_amds: list[AMDSpec] = []

    for smd_file in smd_files:
        parsed_smds.append(parse_smd_file(smd_file))

    for amd_file in amd_files:
        parsed_amds.append(parse_amd_file(amd_file))

    smd_spec_ids = [smd.spec_id for smd in parsed_smds]

    for smd in parsed_smds:
        for dep in smd.depends:
            if dep not in smd_spec_ids:
                raise ValidationError

    if parsed_amds:
        for amd in parsed_amds:
            if amd.spec_id not in smd_spec_ids:
                raise ValidationError

    return parsed_smds, parsed_amds


def run_audit(
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

    with Status("Spec validation") as status:
        # Quick validation
        smd_files = list(config.spec_path.glob("**/*.smd"))
        amd_files = list(config.spec_path.glob("**/*.amd"))

        if not smd_files:
            console.print("[yellow]No spec files found.[/]")
            return

        try:
            parsed_smds, parsed_amds = quick_validate(smd_files=smd_files, amd_files=amd_files)
        except SMDParseError, AMDParseError, ValidationError:
            console.log("[red] Specs invalid. Run: `ntt validate` to check errors.")
            raise SystemExit(1)

        console.log("[green]✓ specs valid")

        # TODO: Generate graph.toml
        # graph = build_spec_graph(parsed_smds, parsed_amds)
        # spec_graph_filepath = config.metadata_path / "graph.toml"
        # write_spec_graph(
        #     graph,
        #     spec_graph_filepath
        # )

        # Audit specs and generate audit report
        specs_require_audit, briefs_generation_required = check_and_update_manifest(
            console, config, smd_files, amd_files
        )

        # - SPEC AUDIT
        if not specs_require_audit:
            status.stop()

            if questionary.confirm(
                "Re-audit is not required. Re-audit anyway?", default=False
            ).ask():
                specs_require_audit = True

            status.start()

        if specs_require_audit:
            status.update(f"Spec audit - {config.name} v{config.version} specs")

            # Spec and Architecture audit
            spec_audit_report = audit_specs(config, parsed_smds, parsed_amds)

            print_audit_summary(
                console,
                report=spec_audit_report,
                title=f"{config.name} v{config.version} - Spec Audit",
            )

            audit_report_filepath = config.metadata_path / "audit-report.md"

            save_audit_report(
                report=spec_audit_report,
                name=f"{config.name} v{config.version}",
                spec_ids=[smd.spec_id for smd in parsed_smds],
                filename=audit_report_filepath,
            )

            console.log(f"Spec audit report saved to [bold]{audit_report_filepath}")

            present_findings_and_confirm(console, status, spec_audit_report)

            # Cross spec audit
            if len(parsed_smds) > 1:
                status.update(f"Cross-spec audit - {config.name} v{config.version} specs")
                cross_spec_audit_report = audit_cross_specs(config, parsed_smds, parsed_amds)

                print_audit_summary(
                    console,
                    report=cross_spec_audit_report,
                    title=f"{config.name} v{config.version} - Spec Audit",
                )

                cross_audit_report_filepath = config.metadata_path / "cross-audit-report.md"

                save_audit_report(
                    report=cross_spec_audit_report,
                    name=f"{config.name} v{config.version}",
                    spec_ids=[smd.spec_id for smd in parsed_smds],
                    filename=cross_audit_report_filepath,
                )

                console.log(f"Cross spec audit report saved to [bold]{cross_audit_report_filepath}")

                present_findings_and_confirm(console, status, cross_spec_audit_report)

        # - BRIEFS GENERATION
        if briefs_generation_required:
            generate_and_write_briefs(console, status, config, parsed_smds)
        else:
            console.log("[yellow]Project and spec brief regeneration not required")

        # - TODO: Generate Interfaces
        # generate_interfaces(config, parsed_smds, parsed_amds)

        # - TODO: GENERATE OR UPDATE PLAN
        # plan = generate_plan(config, parsed_smds, parsed_amds, graph)
        # plan_filepath = config.metadata_path / "plan.toml"
        # write_plan(plan, plan_filepath)
