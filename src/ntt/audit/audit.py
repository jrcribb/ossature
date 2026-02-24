from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from pydantic_ai import Agent

from ntt.config.loader import NTTConfig
from ntt.models.amd import AMDSpec
from ntt.models.audit import CrossSpecAuditReport, CrossSpecFinding, SpecAuditReport
from ntt.models.smd import SMDSpec
from ntt.renderer.amd import render_amd
from ntt.renderer.smd import render_smd

SPEC_AUDIT_MODEL: Final[str] = "claude-opus-4-6"

SPEC_AUDIT_SYSTEM_PROMPT: Final[str] = (
    "You are a senior technical reviewer auditing a software specification "
    "for a {language} project.\n\n"
    "You will receive one or more SMD (Spec Markdown) files, and optionally "
    "AMD (Architecture Markdown) files that provide structural detail for specs.\n\n"
    "## What to Flag\n"
    "1. CONTRADICTION — requirements that conflict with each other\n"
    "2. AMBIGUITY — requirements where two reasonable interpretations would "
    "produce *incompatible* implementations\n"
    "3. CRITICAL GAPS — missing error handling that would cause crashes, data loss, "
    "or security issues\n"
    "4. INFEASIBILITY — things that cannot be built as described\n"
    "5. SPEC-ARCH MISMATCH — if AMD is provided, flag cases where the architecture "
    "contradicts or fails to cover spec requirements\n\n"
    "## What NOT to Flag\n"
    "- Implementation details the LLM can reasonably decide (algorithms, data structures, "
    "internal architecture)\n"
    "- Missing details that have standard or reasonably obvious solutions in {language}\n"
    "- Underspecification where any reasonable choice produces acceptable behavior\n"
    "- Behavior that can be inferred from the examples provided\n"
    "- Behavior that can be inferred from the other spec files\n"
    "- Stylistic preferences (naming, formatting, code organization)\n"
    "- Things that would be documented in an Architecture file when no AMD is provided\n"
    "- Missing AMD — specs without architecture files are valid; "
    "the LLM will infer architecture\n\n"
    "## Severity Calibration\n"
    "- ERROR: Will cause *wrong* behavior — code won't match user intent\n"
    "- WARNING: Could cause wrong behavior depending on LLM interpretation\n"
    "- INFO: Worth clarifying but any reasonable implementation is acceptable\n\n"
    "## The Key Test\n"
    "Before flagging, ask: 'If two competent developers implemented this independently, "
    "would the ambiguity cause their implementations to be *incompatible* or produce "
    "*different user-visible behavior*?' If no, don't flag it.\n\n"
    "Don't invent findings. An empty array is a valid output for a well-written spec.\n\n"
    "For each finding, output JSON:\n"
    '{{"severity": "error"|"warning"|"info",'
    ' "location": "location in the markdown doc without header marks, just text, arrow separated, '
    'include headers text and number (if list)" and line number, '
    '"issue": "description", "suggestion": "how to fix"}}\n\n'
    "Output a JSON array of findings. Empty array if none found."
)

CROSS_SPEC_AUDIT_SYSTEM_PROMPT: Final[str] = (
    "You are a senior technical reviewer auditing the interfaces between "
    "interdependent specifications for a {language} project.\n\n"
    "You will receive:\n"
    "1. A spec dependency graph showing which specs depend on which\n"
    "2. Summarized specs (overview + requirements titles + key types)\n\n"
    "## What to Flag\n"
    "1. DEPENDENCY GAPS — Spec A depends on Spec B, but B doesn't provide "
    "something A's requirements clearly need\n"
    "2. CONTRACT MISMATCHES — Incompatible assumptions between specs about "
    "shared data types, error handling, or communication patterns\n"
    "3. CIRCULAR LOGIC — Requirements that create hidden circular dependencies "
    "not captured in @depends\n"
    "4. INTEGRATION AMBIGUITY — Unclear how specs connect at runtime where "
    "two implementations could be incompatible\n\n"
    "## What NOT to Flag\n"
    "- Internal spec issues (those are caught by per-spec audit)\n"
    "- Implementation details of how specs communicate\n"
    "- Missing details that have obvious integration patterns in {language}\n"
    "- Specs with no dependencies (nothing to check)\n\n"
    "## Severity Calibration\n"
    "- ERROR: Specs cannot be integrated as written — will fail at boundaries\n"
    "- WARNING: Integration could fail depending on implementation choices\n"
    "- INFO: Worth clarifying but reasonable implementations will interoperate\n\n"
    "## The Key Test\n"
    "Before flagging, ask: 'If two teams implemented these specs independently "
    "following only their own spec, would their code fail to integrate?' "
    "If no, don't flag it.\n\n"
    "Don't invent findings. An empty array is valid for well-designed spec boundaries.\n\n"
    "For each finding, output JSON:\n"
    '{{"severity": "error"|"warning"|"info",'
    ' "specs": ["SPEC_A", "SPEC_B"],'
    ' "issue": "description",'
    ' "suggestion": "how to fix"}}\n\n'
    "Output a JSON array of findings. Empty array if none found."
)


def audit_specs(
    config: NTTConfig,
    parsed_smds: list[SMDSpec],
    parsed_amds: list[AMDSpec] | None = None,
) -> SpecAuditReport:
    agent = Agent(
        SPEC_AUDIT_MODEL,
        output_type=SpecAuditReport,
        system_prompt=SPEC_AUDIT_SYSTEM_PROMPT.format(language=config.output.language),
    )

    # Build the audit input: all SMDs, then all AMDs grouped by their parent spec
    sections: list[str] = []

    # Add all SMD content
    sections.append("# Specifications (SMD)\n")
    for smd in parsed_smds:
        sections.append(render_smd(smd))

    # Add AMD content if present, grouped by spec
    if parsed_amds:
        sections.append("\n# Architecture Documents (AMD)\n")

        # Group AMDs by their spec_id
        amd_by_spec: dict[str, list[AMDSpec]] = {}
        for amd in parsed_amds:
            amd_by_spec.setdefault(amd.spec_id, []).append(amd)

        for spec_id, amds in amd_by_spec.items():
            sections.append(f"\n## Architecture for {spec_id}\n")

            for amd in amds:
                sections.append(render_amd(amd))

    result = agent.run_sync("\n---\n".join(sections))

    return result.output


def audit_cross_specs(
    config: NTTConfig,
    parsed_smds: list[SMDSpec],
    parsed_amds: list[AMDSpec] | None = None,
) -> CrossSpecAuditReport:
    """
    Audit interfaces between interdependent specs.
    Only meaningful when there are multiple specs with dependencies.
    """
    agent = Agent(
        SPEC_AUDIT_MODEL,
        output_type=CrossSpecAuditReport,
        system_prompt=CROSS_SPEC_AUDIT_SYSTEM_PROMPT.format(language=config.output.language),
    )

    # Build dependency graph representation
    graph_lines = ["## Spec Dependency Graph\n"]
    for smd in parsed_smds:
        deps = ", ".join(smd.depends) if smd.depends else "(none)"
        graph_lines.append(f"- {smd.spec_id}: depends on [{deps}]")

    # Group AMDs by spec_id for lookup
    amd_by_spec: dict[str, list[AMDSpec]] = {}
    if parsed_amds:
        for amd in parsed_amds:
            amd_by_spec.setdefault(amd.spec_id, []).append(amd)

    # Build condensed spec summaries
    summary_lines = ["\n## Spec Summaries\n"]
    for smd in parsed_smds:
        summary_lines.append(f"### {smd.spec_id}: {smd.title}\n")
        summary_lines.append(f"**Overview:** {smd.overview}\n")

        if smd.requirements:
            summary_lines.append("**Requirements:**")
            for req in smd.requirements:
                summary_lines.append(f"- {req.title}")
            summary_lines.append("")

        # Include AMD details if available
        spec_amds = amd_by_spec.get(smd.spec_id, [])
        if spec_amds:
            # Components with interfaces
            all_components = [comp for amd in spec_amds for comp in amd.components]
            if all_components:
                summary_lines.append("**Components:**")
                for comp in all_components:
                    deps_str = (
                        f" [depends: {', '.join(comp.depends_on)}]" if comp.depends_on else ""
                    )
                    summary_lines.append(f"- {comp.name}: {comp.description}{deps_str}")
                    if comp.interface:
                        summary_lines.append(f"  ```{comp.interface_language}")
                        summary_lines.append(f"  {comp.interface}")
                        summary_lines.append("  ```")
                summary_lines.append("")

            # Data models (critical for cross-spec contracts)
            all_data_models = [dm for amd in spec_amds for dm in amd.data_models]
            if all_data_models:
                summary_lines.append("**Data Models:**")
                for dm in all_data_models:
                    summary_lines.append(f"- {dm.name}")
                    if dm.definition:
                        summary_lines.append(f"  ```{dm.definition_language}")
                        summary_lines.append("  {dm.definition}")
                        summary_lines.append("  ```")
                summary_lines.append("")

            # External dependencies
            all_deps = [dep for amd in spec_amds for dep in amd.dependencies]
            if all_deps:
                summary_lines.append("**External Dependencies:**")
                for dep in all_deps:
                    summary_lines.append(f"- {dep.name}: {dep.purpose}")
                summary_lines.append("")

    audit_input = "\n".join(graph_lines) + "\n" + "\n".join(summary_lines)

    result = agent.run_sync(audit_input)

    return result.output


def save_audit_report(
    report: SpecAuditReport | CrossSpecAuditReport,
    name: str,
    spec_ids: list[str],
    filename: Path,
) -> None:
    with open(filename, "w") as f:
        title = (
            "Cross-Spec Audit Report"
            if isinstance(report, CrossSpecAuditReport)
            else "Audit Report"
        )

        f.write(f"# {title}: {name}\n\n")

        current_time_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        f.write(f"**Date:** {current_time_utc}\n")
        f.write(f"**Specs:** {', '.join(spec_ids)}\n\n")

        if not report.findings:
            f.write("No findings identified.\n")
            return

        for finding in report.findings:
            if isinstance(finding, CrossSpecFinding):
                location = " <-> ".join(finding.specs)
            else:
                location = finding.location

            f.write(f"## {finding.severity.value.upper()}: {location}\n\n")
            f.write(f"**Issue:** {finding.issue}\n\n")
            f.write(f"**Suggestion:** {finding.suggestion}\n\n")
