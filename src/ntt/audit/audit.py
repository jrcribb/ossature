from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from pydantic_ai import Agent

from ntt.config.loader import NTTConfig
from ntt.models.audit import SpecAuditReport
from ntt.models.smd import SMDSpec
from ntt.renderer.smd import render_smd

SPEC_AUDIT_MODEL: Final[str] = "claude-opus-4-6"  # "claude-haiku-4-5-20251001"

SPEC_AUDIT_SYSTEM_PROMPT: Final[str] = (
    "You are a senior technical reviewer auditing a software specification "
    "for a {language} project.\n\n"
    "## What to Flag\n"
    "1. CONTRADICTION — requirements that conflict with each other\n"
    "2. AMBIGUITY — requirements where two reasonable interpretations would "
    "produce *incompatible* implementations\n"
    "3. CRITICAL GAPS — missing error handling that would cause crashes, data loss, "
    "or security issues\n"
    "4. INFEASIBILITY — things that cannot be built as described\n\n"
    "## What NOT to Flag\n"
    "- Implementation details the LLM can reasonably decide (algorithms, data structures, "
    "internal architecture)\n"
    "- Missing details that have standard or reasonably obvious solutions in {language}\n"
    "- Underspecification where any reasonable choice produces acceptable behavior\n"
    "- Behavior that can be inferred from the examples provided\n"
    "- Behavior that can be inferred from the other spec files\n"
    "- Stylistic preferences (naming, formatting, code organization)\n"
    "- Things that would be documented in an Architecture file, not a Spec\n\n"
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


def audit_specs(config: NTTConfig, parsed_smds: list[SMDSpec]) -> SpecAuditReport:
    agent = Agent(
        SPEC_AUDIT_MODEL,
        output_type=SpecAuditReport,
        system_prompt=SPEC_AUDIT_SYSTEM_PROMPT.format(language=config.output.language),
    )

    result = agent.run_sync(
        "\n---\n".join(render_smd(smd) for smd in parsed_smds),
    )

    return result.output


def save_audit_report(
    report: SpecAuditReport, name: str, spec_ids: list[str], filename: Path
) -> None:
    with open(filename, "w") as f:
        f.write(f"# Audit Report: {name} \n\n")

        current_time_utc = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        f.write(f"**Date:** {current_time_utc}\n")
        f.write(f"**Specs:** {', '.join(spec_ids)}\n\n")

        if not report.findings:
            f.write("No findings identified.\n")
            return

        for finding in report.findings:
            f.write(f"## {finding.severity.value.upper()}: {finding.location}\n\n")
            f.write(f"**Issue:** {finding.issue}\n\n")
            f.write(f"**Suggestion:** {finding.suggestion}\n\n")
