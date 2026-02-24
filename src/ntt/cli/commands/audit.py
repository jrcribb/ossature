from pathlib import Path

import questionary
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.status import Status
from rich.table import Table
from rich.text import Text

from ntt.audit.audit import (
    audit_cross_specs,
    audit_spec,
    load_cross_spec_audit_data,
    load_spec_audit_data,
    save_audit_report,
    save_cross_spec_audit_data,
    save_spec_audit_data,
)
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
        location = (
            finding.location if isinstance(finding, AuditFinding) else "-".join(finding.specs)
        )
        table.add_row(
            Text(label, style=f"bold {style}"),
            Text(location),
            Text(finding.issue),
            Text(finding.suggestion),
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
) -> list[str] | None:
    """Returns changed source keys, or None if manifest unchanged."""
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
                for source in mismatched:
                    console.log(f"  {source} has changed")
                write_manifest(new_manifest, filename=manifest_path)
                console.log("Manifest updated")
                return mismatched
            else:
                console.log("[green]Manifest unchanged")
                return None

    write_manifest(new_manifest, filename=manifest_path)
    console.log("[green]Manifest written")
    return list(new_manifest.sources.keys())


def get_changed_spec_ids(
    changed_files: list[str],
    smd_files: list[Path],
    amd_files: list[Path],
    parsed_smds: list[SMDSpec],
    parsed_amds: list[AMDSpec],
    config: NTTConfig,
) -> set[str]:
    """Maps changed manifest source keys to spec IDs."""
    if "ntt.toml" in changed_files:
        return {smd.spec_id for smd in parsed_smds}

    file_to_spec: dict[str, str] = {}

    for smd_file, smd in zip(smd_files, parsed_smds):
        key = str(smd_file).replace(str(config.root), ".")
        file_to_spec[key] = smd.spec_id

    for amd_file, amd in zip(amd_files, parsed_amds):
        key = str(amd_file).replace(str(config.root), ".")
        file_to_spec[key] = amd.spec_id

    return {file_to_spec[f] for f in changed_files if f in file_to_spec}


def generate_and_write_briefs(
    console: Console,
    status: Status,
    config: NTTConfig,
    parsed_smds: list[SMDSpec],
    changed_spec_ids: set[str] | None = None,
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
    smds_to_brief = (
        [s for s in parsed_smds if s.spec_id in changed_spec_ids]
        if changed_spec_ids is not None
        else parsed_smds
    )
    spec_brefs = generate_spec_briefs(config=config, parsed_smds=smds_to_brief)
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

    with Status("Spec validation", console=console) as status:
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

        # Check manifest for changes
        changed_files = check_and_update_manifest(console, config, smd_files, amd_files)

        if changed_files is None:
            status.stop()
            if questionary.confirm(
                "Re-audit is not required. Re-audit anyway?", default=False
            ).ask():
                specs_to_audit = {smd.spec_id for smd in parsed_smds}
            else:
                specs_to_audit = set()
            status.start()
        else:
            specs_to_audit = get_changed_spec_ids(
                changed_files, smd_files, amd_files, parsed_smds, parsed_amds, config
            )

        # Group AMDs by spec
        amd_by_spec: dict[str, list[AMDSpec]] = {}
        for amd in parsed_amds:
            amd_by_spec.setdefault(amd.spec_id, []).append(amd)

        # Check for missing cached files — force re-audit/re-brief if absent
        audit_data_dir = config.metadata_path / "audits"
        specs_missing_audit: set[str] = set()
        specs_missing_briefs: set[str] = set()

        for smd in parsed_smds:
            if smd.spec_id not in specs_to_audit:
                audit_json = audit_data_dir / f"{smd.spec_id}.json"
                if not audit_json.exists():
                    specs_missing_audit.add(smd.spec_id)

            brief_file = config.metadata_context_spec_briefs_path / f"{smd.spec_id}.md"
            if not brief_file.exists():
                specs_missing_briefs.add(smd.spec_id)

        if specs_missing_audit:
            console.log(
                f"[yellow]Missing audit data for: {', '.join(sorted(specs_missing_audit))}. "
                "Will re-audit."
            )
            specs_to_audit |= specs_missing_audit

        project_brief_file = config.metadata_context_path / "project-brief.md"
        if not project_brief_file.exists():
            specs_missing_briefs |= {smd.spec_id for smd in parsed_smds}

        if specs_missing_briefs - specs_to_audit:
            console.log(
                f"[yellow]Missing briefs for: "
                f"{', '.join(sorted(specs_missing_briefs - specs_to_audit))}. "
                "Will regenerate."
            )

        # - PER-SPEC AUDIT
        spec_reports: dict[str, SpecAuditReport] = {}
        audited_spec_ids: set[str] = set()

        for smd in parsed_smds:
            spec_amds = amd_by_spec.get(smd.spec_id)

            if smd.spec_id in specs_to_audit:
                status.update(f"Auditing {smd.spec_id} - {smd.title}")
                report = audit_spec(config, smd, spec_amds)
                save_spec_audit_data(report, smd.spec_id, audit_data_dir)
                spec_reports[smd.spec_id] = report
                audited_spec_ids.add(smd.spec_id)

                counts = {s: 0 for s in Severity}
                for finding in report.findings:
                    counts[finding.severity] += 1
                summary = ", ".join(f"{v} {k.value}(s)" for k, v in counts.items() if v > 0)
                console.log(f"  {smd.spec_id}: {summary or 'no findings'}")
            else:
                cached = load_spec_audit_data(smd.spec_id, audit_data_dir)
                if cached:
                    spec_reports[smd.spec_id] = cached
                    console.log(f"  {smd.spec_id} - {smd.title}: [dim](cached)[/dim]")

        # Present findings for freshly audited specs
        if audited_spec_ids:
            fresh_findings = SpecAuditReport(
                findings=[
                    f
                    for sid in audited_spec_ids
                    if sid in spec_reports
                    for f in spec_reports[sid].findings
                ]
            )

            print_audit_summary(
                console,
                report=fresh_findings,
                title=f"{config.name} v{config.version} - Spec Audit",
            )

            present_findings_and_confirm(console, status, fresh_findings)

        # - CROSS-SPEC AUDIT
        cross_spec_report: CrossSpecAuditReport | None = None

        if len(parsed_smds) > 1:
            if audited_spec_ids:
                status.update(f"Cross-spec audit - {config.name} v{config.version}")
                cross_spec_report = audit_cross_specs(config, parsed_smds, parsed_amds)
                save_cross_spec_audit_data(cross_spec_report, audit_data_dir)

                print_audit_summary(
                    console,
                    report=cross_spec_report,
                    title=f"{config.name} v{config.version} - Cross-Spec Audit",
                )

                present_findings_and_confirm(console, status, cross_spec_report)
            else:
                cross_spec_report = load_cross_spec_audit_data(audit_data_dir)

        # - WRITE UNIFIED AUDIT REPORT
        if spec_reports:
            audit_report_filepath = config.metadata_path / "audit-report.md"
            save_audit_report(
                spec_reports=spec_reports,
                cross_spec_report=cross_spec_report,
                name=f"{config.name} v{config.version}",
                filename=audit_report_filepath,
            )
            console.log(f"Audit report saved to [bold]{audit_report_filepath}")

        # - BRIEFS GENERATION
        specs_needing_briefs = audited_spec_ids | specs_missing_briefs
        if specs_needing_briefs:
            generate_and_write_briefs(
                console, status, config, parsed_smds, changed_spec_ids=specs_needing_briefs
            )
        else:
            console.log("[yellow]Project and spec brief regeneration not required")

        # - TODO: Generate Interfaces
        # generate_interfaces(config, parsed_smds, parsed_amds)

        # - TODO: GENERATE OR UPDATE PLAN
        # plan = generate_plan(config, parsed_smds, parsed_amds, graph)
        # plan_filepath = config.metadata_path / "plan.toml"
        # write_plan(plan, plan_filepath)
