from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import content_types
import tomli
import tomli_w
from pydantic_ai import Agent

from ntt.audit.graph import SpecGraph
from ntt.audit.prompts import PLAN_GENERATION_SYSTEM_PROMPT
from ntt.config.loader import NTTConfig
from ntt.models.amd import AMDSpec
from ntt.models.audit import SpecAuditReport
from ntt.models.plan import Plan, PlanMeta, PlanTask, SpecTaskPlan, TaskStatus
from ntt.models.smd import SMDSpec
from ntt.renderer.amd import render_amd
from ntt.renderer.smd import render_smd


def generate_spec_plan(
    config: NTTConfig,
    smd: SMDSpec,
    amds: list[AMDSpec] | None,
    audit_report: SpecAuditReport | None,
    context_inventory: list[str] | None = None,
) -> SpecTaskPlan:
    agent = Agent(
        config.llm.model_for("planner"),
        output_type=SpecTaskPlan,
        system_prompt=PLAN_GENERATION_SYSTEM_PROMPT.format(language=config.output.language),
    )

    sections: list[str] = []

    project_header = f"# Project: {config.name} v{config.version} ({config.output.language})"
    if config.output.framework:
        project_header += f" — Framework: {config.output.framework}"
    sections.append(project_header + "\n")
    sections.append("## Specification (SMD)\n")
    sections.append(render_smd(smd))

    if amds:
        sections.append("\n## Architecture Documents (AMD)\n")
        for amd in amds:
            sections.append(render_amd(amd))

    if audit_report and audit_report.findings:
        sections.append("\n## Audit Findings (avoid these issues in planning)\n")
        for finding in audit_report.findings:
            sections.append(
                f"- [{finding.severity.value.upper()}] {finding.location}: {finding.issue}"
            )

    if config.build.setup:
        sections.append(
            f"\n## Build Setup Command\n"
            f"The following setup command runs before the first task:\n"
            f"```\n{config.build.setup}\n```\n"
            f"Do not generate tasks that duplicate what this command does."
        )

    if context_inventory:
        file_lines = []
        for f in context_inventory:
            mime_type = content_types.get_content_type(f)
            file_lines.append(f"- `{f}` ({mime_type})")
        sections.append(
            "\n## Context Files\n\n"
            "The following files are available in the project's context directory. "
            "These are pre-existing assets (audio, images, reference code, documentation, etc.) "
            "that may be useful during implementation.\n\n" + "\n".join(file_lines) + "\n\n"
            "For each task, list any context files it needs in the `context_files` field. "
            "The build system will include text files in the prompt and provide tools "
            "for the implementer to copy binary assets to the appropriate location "
            "within the output directory (e.g. an `assets/` or `sounds/` subdirectory, "
            "wherever fits the project structure)."
        )

    result = agent.run_sync("\n".join(sections))

    return result.output


def merge_into_global_plan(
    spec_plans: dict[str, SpecTaskPlan],
    graph: SpecGraph,
    parsed_smds: list[SMDSpec],
) -> Plan:
    all_tasks: list[PlanTask] = []
    global_counter = 0

    spec_local_to_global: dict[str, dict[int, str]] = {}
    spec_last_task: dict[str, str] = {}

    smd_deps: dict[str, list[str]] = {smd.spec_id: list(smd.depends) for smd in parsed_smds}

    for level in graph.levels:
        for spec_id in level:
            if spec_id not in spec_plans:
                continue

            spec_plan = spec_plans[spec_id]
            local_to_global: dict[int, str] = {}

            for local_idx, planner_task in enumerate(spec_plan.tasks, start=1):
                global_counter += 1
                global_id = f"{global_counter:03d}"
                local_to_global[local_idx] = global_id

                depends_on: list[str] = []
                for local_dep in planner_task.depends_on:
                    if local_dep in local_to_global:
                        depends_on.append(local_to_global[local_dep])

                if local_idx == 1 and smd_deps.get(spec_id):
                    for dep_spec_id in smd_deps[spec_id]:
                        if dep_spec_id in spec_last_task:
                            dep_id = spec_last_task[dep_spec_id]
                            if dep_id not in depends_on:
                                depends_on.append(dep_id)

                cross_spec_interfaces: list[str] = []
                if smd_deps.get(spec_id):
                    cross_spec_interfaces = [
                        dep_id for dep_id in smd_deps[spec_id] if dep_id in spec_last_task
                    ]

                inject_files: list[str] = []
                for dep_global_id in depends_on:
                    for existing_task in all_tasks:
                        if existing_task.id == dep_global_id and existing_task.spec == spec_id:
                            inject_files.extend(existing_task.outputs)

                spec_refs = [f"{spec_id}:{ref}" for ref in planner_task.spec_refs]
                arch_refs = [f"{spec_id}:{ref}" for ref in planner_task.arch_refs]

                task = PlanTask(
                    id=global_id,
                    spec=spec_id,
                    title=planner_task.title,
                    description=planner_task.description,
                    outputs=planner_task.outputs,
                    depends_on=depends_on,
                    spec_refs=spec_refs,
                    arch_refs=arch_refs,
                    status=TaskStatus.PENDING,
                    verify=planner_task.verify,
                    inject_files=inject_files,
                    cross_spec_interfaces=cross_spec_interfaces,
                    context_files=list(planner_task.context_files),
                )
                all_tasks.append(task)

            spec_local_to_global[spec_id] = local_to_global

            if spec_plan.tasks:
                spec_last_task[spec_id] = local_to_global[len(spec_plan.tasks)]

    # Collect ordered spec IDs
    ordered_specs = [
        spec_id for level in graph.levels for spec_id in level if spec_id in spec_plans
    ]

    meta = PlanMeta(
        generated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        total_tasks=len(all_tasks),
        specs=ordered_specs,
    )

    return Plan(meta=meta, tasks=all_tasks)


def generate_plan(
    config: NTTConfig,
    parsed_smds: list[SMDSpec],
    amd_by_spec: dict[str, list[AMDSpec]],
    graph: SpecGraph,
    spec_reports: dict[str, SpecAuditReport],
) -> Plan:
    spec_plans: dict[str, SpecTaskPlan] = {}

    context_inventory: list[str] = []
    if config.context_path.is_dir():
        for p in sorted(config.context_path.rglob("*")):
            if p.is_file():
                context_inventory.append(str(p.relative_to(config.context_path)))

    for level in graph.levels:
        for spec_id in level:
            smd = next((s for s in parsed_smds if s.spec_id == spec_id), None)
            if smd is None:
                continue

            amds = amd_by_spec.get(spec_id)
            audit_report = spec_reports.get(spec_id)

            spec_plan = generate_spec_plan(
                config,
                smd,
                amds,
                audit_report,
                context_inventory=context_inventory or None,
            )
            spec_plans[spec_id] = spec_plan

    return merge_into_global_plan(spec_plans, graph, parsed_smds)


def write_plan(plan: Plan, filepath: Path) -> None:
    data: dict[str, Any] = {
        "meta": {
            "generated_at": plan.meta.generated_at,
            "total_tasks": plan.meta.total_tasks,
            "specs": plan.meta.specs,
        },
        "task": [],
    }

    for task in plan.tasks:
        task_dict: dict[str, Any] = {
            "id": task.id,
            "spec": task.spec,
            "title": task.title,
            "description": task.description,
            "outputs": task.outputs,
            "depends_on": task.depends_on,
            "spec_refs": task.spec_refs,
            "arch_refs": task.arch_refs,
            "status": task.status.value,
            "verify": task.verify,
        }
        if task.inject_files:
            task_dict["inject_files"] = task.inject_files
        if task.cross_spec_interfaces:
            task_dict["cross_spec_interfaces"] = task.cross_spec_interfaces
        if task.context_files:
            task_dict["context_files"] = task.context_files
        if task.notes:
            task_dict["notes"] = task.notes
        data["task"].append(task_dict)

    content = tomli_w.dumps(data)

    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "w") as f:
        f.write("# .ntt/plan.toml — Generated by `ntt audit`, editable by architect\n")
        f.write("# Re-run `ntt audit --replan` to regenerate (discards manual edits)\n\n")
        f.write(content)


def load_plan(filepath: Path) -> Plan | None:
    if not filepath.exists():
        return None

    try:
        with open(filepath, "rb") as f:
            data = tomli.load(f)
    except tomli.TOMLDecodeError:
        return None

    meta = PlanMeta(**data["meta"])
    tasks = [
        PlanTask(
            id=t["id"],
            spec=t["spec"],
            title=t["title"],
            description=t["description"],
            outputs=t["outputs"],
            depends_on=t["depends_on"],
            spec_refs=t["spec_refs"],
            arch_refs=t["arch_refs"],
            status=TaskStatus(t["status"]),
            verify=t["verify"],
            inject_files=t.get("inject_files", []),
            cross_spec_interfaces=t.get("cross_spec_interfaces", []),
            context_files=t.get("context_files", []),
            notes=t.get("notes", ""),
        )
        for t in data.get("task", [])
    ]

    return Plan(meta=meta, tasks=tasks)


def write_task_definitions(plan: Plan, tasks_dir: Path) -> None:
    for task in plan.tasks:
        slug = task.title.lower().replace(" ", "-").replace(":", "").replace("/", "-")
        task_dir = tasks_dir / f"{task.id}-{slug}"
        task_dir.mkdir(parents=True, exist_ok=True)

        task_data = {
            "id": task.id,
            "spec": task.spec,
            "title": task.title,
            "description": task.description,
            "outputs": task.outputs,
            "depends_on": task.depends_on,
            "spec_refs": task.spec_refs,
            "arch_refs": task.arch_refs,
            "status": task.status.value,
            "verify": task.verify,
        }
        if task.inject_files:
            task_data["inject_files"] = task.inject_files
        if task.cross_spec_interfaces:
            task_data["cross_spec_interfaces"] = task.cross_spec_interfaces
        if task.context_files:
            task_data["context_files"] = task.context_files
        if task.notes:
            task_data["notes"] = task.notes

        task_filepath = task_dir / "task.toml"
        with open(task_filepath, "wb") as f:
            tomli_w.dump(task_data, f)
