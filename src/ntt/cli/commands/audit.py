from pathlib import Path

import questionary
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.status import Status
from rich.table import Table
from rich.text import Text

from ntt.audit.audit import audit_specs, save_audit_report
from ntt.audit.context import generate_project_brief, generate_spec_briefs
from ntt.audit.manifest import create_manifest, read_manifest, write_manifest
from ntt.config.loader import ConfigError, load_config
from ntt.models.amd import AMDSpec
from ntt.models.audit import Severity, SpecAuditReport
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
    console: Console, report: SpecAuditReport, title: str = "Spec Audit Report"
) -> None:
    counts = {s: 0 for s in Severity}
    for finding in report.findings:
        counts[finding.severity] += 1

    summary = Text()
    for severity, (style, label) in SEVERITY_STYLES.items():
        summary.append(f"  {label}: {counts[severity]}  ", style=f"bold {style}")

    console.print()
    console.print(Panel(summary, title=f"[bold]{title}[/bold]", expand=False, box=box.ROUNDED))


def print_audit_findings_table(console: Console, report: SpecAuditReport) -> None:
    table = Table(
        box=box.SIMPLE_HEAD,
        show_lines=True,
        expand=True,
        header_style="bold white",
    )

    table.add_column("Severity", style="bold", width=10, no_wrap=True)
    table.add_column("Location", style="dim", width=20)
    table.add_column("Issue", ratio=2)
    table.add_column("Suggestion", style="italic", ratio=3)

    severity_order = {Severity.INFO: 0, Severity.WARNING: 1, Severity.ERROR: 2}
    sorted_findings = sorted(report.findings, key=lambda x: severity_order[x.severity])

    for finding in sorted_findings:
        style, label = SEVERITY_STYLES[finding.severity]
        table.add_row(
            Text(label, style=f"bold {style}"),
            finding.location,
            finding.issue,
            finding.suggestion,
        )

    console.print(table)
    console.print()


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
            console.print("[red] Specs invalid. Run: `ntt validate` to check errors.")
            raise SystemExit(1)

        console.log("[green]✓ specs valid")

        # Audit specs and generate audit report
        config.metadata_path.mkdir(parents=True, exist_ok=True)
        manifest_path = config.metadata_path / "manifest.toml"
        new_manifest = create_manifest(config=config, smd_files=smd_files, amd_files=amd_files)

        specs_require_audit = True
        briefs_generation_required = True

        # if the manifest exists, check for mismatch
        # then update it. Mismatched checksums require re-audit

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
                    specs_require_audit = False
                    briefs_generation_required = False
                    console.log("[green]Manifest unchanged")

        else:
            write_manifest(new_manifest, filename=manifest_path)
            console.log("[green]Manifest written")

            manifest = new_manifest

        # - SPEC AUDIT
        if not specs_require_audit:
            status.stop()

            if questionary.confirm(
                "Re-audit is not required. Re-audit anyway?", default=False
            ).ask():
                specs_require_audit = True

            status.start()

        if specs_require_audit:
            status.update(f"Auditing {config.name} v{config.version} specs")
            spec_audit_report = audit_specs(config, parsed_smds)

            counts = {s: 0 for s in Severity}
            for finding in spec_audit_report.findings:
                counts[finding.severity] += 1

            print_audit_summary(
                console,
                report=spec_audit_report,
                title=f"{config.name} v{config.version} - Spec Audit",
            )

            save_audit_report(
                report=spec_audit_report,
                name=f"{config.name} v{config.version}",
                spec_ids=[smd.spec_id for smd in parsed_smds],
                filename=config.metadata_path / "audit-report.md",
            )

            console.log(f"Report saved to [bold]{config.metadata_path / 'audit-report.md'}")

            if any(v > 0 for _, v in counts.items()):
                status.stop()
                if questionary.confirm("Print full report?").ask():
                    print_audit_findings_table(console, report=spec_audit_report)

                severity_counts = ", ".join(f"{v} {k.value}(s)" for k, v in counts.items() if v > 0)

                confirm_default = counts[Severity.ERROR] == 0

                if not questionary.confirm(
                    f"Audit found {severity_counts}. Continue?", default=confirm_default
                ).ask():
                    raise SystemExit(1)

                status.start()

        # - BRIEFS GENERATION
        if briefs_generation_required:
            # -- Create context files: Project brief
            status.update("Generating project brief")
            project_brief = generate_project_brief(config=config, parsed_smds=parsed_smds)

            config.metadata_context_path.mkdir(parents=True, exist_ok=True)

            project_brief_filepath = config.metadata_context_path / "project-brief.md"
            with open(project_brief_filepath, "w") as f:
                f.write(project_brief.brief)
                f.flush()

            console.log(f"Project brief written to [bold]{project_brief_filepath}")

            # - Create context files: Spec briefs
            status.update("Generating spec briefs")

            spec_brefs = generate_spec_briefs(config=config, parsed_smds=parsed_smds)
            config.metadata_context_spec_briefs_path.mkdir(parents=True, exist_ok=True)

            for spec_id, brief in spec_brefs.items():
                spec_brief_filepath = config.metadata_context_spec_briefs_path / f"{spec_id}.md"

                with open(spec_brief_filepath, "w") as f:
                    f.write(brief.brief)
                    f.flush()

                console.log(f"Project brief written to [bold]{spec_brief_filepath}")

            status.stop()
        else:
            console.log("Project brief regeneration not required")

        # - GENERATE OR UPDATE PLAN

        ...

    ...
