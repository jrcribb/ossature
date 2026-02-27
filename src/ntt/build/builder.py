import json
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomli_w
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.usage import UsageLimits
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.status import Status
from rich.text import Text

from ntt.audit.planner import write_plan
from ntt.build.prompts import BUILD_MODEL, FIXER_SYSTEM_PROMPT, IMPLEMENTER_SYSTEM_PROMPT
from ntt.config.loader import NTTConfig
from ntt.models.amd import AMDSpec
from ntt.models.plan import Plan, PlanTask, TaskStatus
from ntt.models.smd import SMDSpec
from ntt.renderer.amd import render_component, render_data_model, render_dependency
from ntt.renderer.smd import render_example, render_requirement

# Build context & tools


@dataclass
class BuildContext:
    output_dir: Path
    console: Console
    status: Status
    written_files: list[str] = field(default_factory=list)
    total_lines: int = 0

    def __post_init__(self) -> None:
        self.output_dir = self.output_dir.resolve()


def _resolve_sandboxed(output_dir: Path, path: str, console: Console) -> Path:
    resolved = (output_dir / path).resolve()
    if not resolved.is_relative_to(output_dir):
        console.log(
            f"    [red] Access denied:[/red] [bold]{path}[/bold] "
            f"→ resolves to [dim]{resolved}[/dim] (outside [dim]{output_dir}[/dim])"
        )
        raise ModelRetry(
            f"Access denied: '{path}' resolves outside the output directory '{output_dir}'. "
            f"All file operations are sandboxed to the output directory. "
            f"Use a relative path within the project (no '..' or absolute paths)."
        )
    return resolved


_COMMAND_DENY_PATTERNS = re.compile(
    r"""
      \.\./              # directory traversal
    | /\.\.(?:/|$)       # traversal after absolute prefix
    | (?:^|[\s;&|])\s*/  # absolute path at start, after whitespace, or after shell separator
    """,
    re.VERBOSE,
)


def _validate_command(command: str, console: Console) -> None:
    if _COMMAND_DENY_PATTERNS.search(command):
        console.log(f"    [red] Command denied:[/red] [bold]{command}[/bold]")
        raise ModelRetry(
            f"Access denied: command '{command}' contains path traversal or absolute paths. "
            f"All commands run inside the output directory. "
            f"Use relative paths only — no '..' or '/'."
        )


def _apply_edits(content: str, edits_json: str) -> str:
    try:
        edits = json.loads(edits_json)
    except json.JSONDecodeError as e:
        raise ModelRetry(
            f"Could not parse edits JSON: {e}. "
            f"The `edits` parameter must be a valid JSON array of objects, e.g. "
            f'[{{"old": "old text", "new": "new text"}}]'
        )

    if not isinstance(edits, list):
        raise ModelRetry(
            f"Expected a JSON array of edits, got {type(edits).__name__}. "
            f'Use the format: [{{"old": "old text", "new": "new text"}}]'
        )

    if not edits:
        raise ModelRetry("Edits array is empty — provide at least one edit.")

    for i, edit in enumerate(edits):
        if not isinstance(edit, dict):
            raise ModelRetry(
                f"Edit #{i + 1} is not an object (got {type(edit).__name__}). "
                f'Each edit must be {{"old": "...", "new": "..."}}.'
            )
        if "old" not in edit or "new" not in edit:
            missing = [k for k in ("old", "new") if k not in edit]
            raise ModelRetry(
                f"Edit #{i + 1} is missing key(s): {', '.join(missing)}. "
                f'Each edit must have "old" and "new" keys.'
            )
        old, new = edit["old"], edit["new"]
        if not isinstance(old, str) or not isinstance(new, str):
            raise ModelRetry(f'Edit #{i + 1}: "old" and "new" must both be strings.')
        if old == new:
            raise ModelRetry(f"Edit #{i + 1}: old and new are identical — nothing to change.")

        count = content.count(old)
        if count == 0:
            # Show a short snippet of what's in the file to help the LLM
            raise ModelRetry(
                f"Edit #{i + 1} failed: the `old` text was not found in the file. "
                f"Make sure it matches the current file contents exactly "
                f"(including whitespace and indentation). "
                f"Use `read_file` or `grep_file` to check the current contents."
            )
        if count > 1:
            raise ModelRetry(
                f"Edit #{i + 1} failed: the `old` text matches {count} locations. "
                f"Include more surrounding context in `old` to make it unique."
            )

        content = content.replace(old, new, 1)

    return content


def _register_tools(agent: Agent[BuildContext, str]) -> None:
    @agent.tool
    def write_file(ctx: RunContext[BuildContext], path: str, content: str) -> str:
        full_path = _resolve_sandboxed(ctx.deps.output_dir, path, ctx.deps.console)
        try:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)
        except OSError as e:
            return f"Error writing {path}: {e}"
        is_new = path not in ctx.deps.written_files
        if is_new:
            ctx.deps.written_files.append(path)
        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        ctx.deps.total_lines += line_count
        action = "wrote" if is_new else "updated"
        ctx.deps.status.update(f"Writing {path}")
        ctx.deps.console.log(f"      {action} [bold]{path}[/bold] ({line_count} lines)")
        return f"Written: {path} ({len(content)} bytes, {line_count} lines)"

    @agent.tool
    def edit_file(ctx: RunContext[BuildContext], path: str, edits: str) -> str:
        full_path = _resolve_sandboxed(ctx.deps.output_dir, path, ctx.deps.console)
        try:
            if not full_path.exists():
                raise ModelRetry(
                    f"Cannot edit '{path}': file does not exist. "
                    f"Use `write_file` to create new files."
                )
            content = full_path.read_text()
        except OSError as e:
            return f"Error reading {path}: {e}"

        updated = _apply_edits(content, edits)
        try:
            full_path.write_text(updated)
        except OSError as e:
            return f"Error writing {path}: {e}"

        if path not in ctx.deps.written_files:
            ctx.deps.written_files.append(path)

        n_edits = len(json.loads(edits))
        ctx.deps.status.update(f"Editing {path}")
        ctx.deps.console.log(f"      edited [bold]{path}[/bold] ({n_edits} edit(s))")
        return f"Edited: {path} ({n_edits} edit(s) applied)"

    @agent.tool
    def read_file(ctx: RunContext[BuildContext], path: str) -> str:
        full_path = _resolve_sandboxed(ctx.deps.output_dir, path, ctx.deps.console)
        try:
            if not full_path.exists():
                return f"Error: {path} does not exist"
            ctx.deps.status.update(f"Reading {path}")
            return full_path.read_text()
        except OSError as e:
            return f"Error reading {path}: {e}"

    @agent.tool
    def read_lines(ctx: RunContext[BuildContext], path: str, start_line: int, end_line: int) -> str:
        full_path = _resolve_sandboxed(ctx.deps.output_dir, path, ctx.deps.console)
        try:
            if not full_path.exists():
                return f"Error: {path} does not exist"
            ctx.deps.status.update(f"Reading {path}:{start_line}-{end_line}")
            lines = full_path.read_text().splitlines()
            total = len(lines)
            start = max(1, start_line) - 1
            end = min(total, end_line)
            selected = lines[start:end]
            numbered = [f"{i + start + 1}: {line}" for i, line in enumerate(selected)]
            return f"Lines {start + 1}-{end} of {total}:\n" + "\n".join(numbered)
        except OSError as e:
            return f"Error reading {path}: {e}"

    @agent.tool
    def grep_file(ctx: RunContext[BuildContext], path: str, pattern: str) -> str:
        full_path = _resolve_sandboxed(ctx.deps.output_dir, path, ctx.deps.console)
        try:
            if not full_path.exists():
                return f"Error: {path} does not exist"
            ctx.deps.status.update(f"Searching {path}")
            lines = full_path.read_text().splitlines()
            compiled = re.compile(pattern, re.IGNORECASE)
            matches: list[str] = []
            for i, line in enumerate(lines):
                if compiled.search(line):
                    # Include 1 line of context above and below
                    start = max(0, i - 1)
                    end = min(len(lines), i + 2)
                    for j in range(start, end):
                        prefix = ">" if j == i else " "
                        entry = f"{prefix} {j + 1}: {lines[j]}"
                        if entry not in matches:
                            matches.append(entry)
                    matches.append("---")
            if not matches:
                return f"No matches for '{pattern}' in {path}"
            return f"Matches in {path}:\n" + "\n".join(matches[:200])
        except re.error as e:
            return f"Invalid pattern '{pattern}': {e}"
        except OSError as e:
            return f"Error reading {path}: {e}"

    @agent.tool
    def list_files(ctx: RunContext[BuildContext], directory: str) -> str:
        full_path = _resolve_sandboxed(ctx.deps.output_dir, directory, ctx.deps.console)
        try:
            if not full_path.is_dir():
                return f"Error: {directory} is not a directory"
            ctx.deps.status.update(f"Listing {directory}")
            max_entries = 200
            entries = sorted(full_path.iterdir())
            result: list[str] = []
            for entry in entries[:max_entries]:
                rel = entry.relative_to(ctx.deps.output_dir)
                if entry.is_dir():
                    result.append(f"  {rel}/")
                else:
                    size = entry.stat().st_size
                    result.append(f"  {rel} ({size} bytes)")
            if len(entries) > max_entries:
                result.append(f"  ... and {len(entries) - max_entries} more entries (truncated)")
            return "\n".join(result) if result else f"{directory} is empty"
        except OSError as e:
            return f"Error listing {directory}: {e}"

    @agent.tool
    def run_command(ctx: RunContext[BuildContext], command: str) -> str:
        _validate_command(command, ctx.deps.console)
        ctx.deps.status.update(f"Running: {command}")
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(ctx.deps.output_dir),
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            return "Error: command timed out after 120 seconds"
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            if output:
                output += "\n"
            output += result.stderr
        return f"Exit code: {result.returncode}\n{output}"


def _create_impl_agent(language: str) -> Agent[BuildContext, str]:
    agent: Agent[BuildContext, str] = Agent(
        BUILD_MODEL,
        system_prompt=IMPLEMENTER_SYSTEM_PROMPT.format(language=language),
        deps_type=BuildContext,
        retries=3,
        model_settings={"max_tokens": 16384},
    )
    _register_tools(agent)
    return agent


def _create_fix_agent(language: str) -> Agent[BuildContext, str]:
    agent: Agent[BuildContext, str] = Agent(
        BUILD_MODEL,
        system_prompt=FIXER_SYSTEM_PROMPT.format(language=language),
        deps_type=BuildContext,
        retries=3,
        model_settings={"max_tokens": 16384},
    )
    _register_tools(agent)
    return agent


# Rate-limit retry


def _run_with_retry(
    agent: Agent[BuildContext, str],
    prompt: str,
    deps: BuildContext,
    console: Console,
    max_retries: int = 5,
    base_delay: float = 30.0,
) -> Any:
    for attempt in range(max_retries):
        try:
            return agent.run_sync(prompt, deps=deps, usage_limits=UsageLimits(request_limit=200))
        except ModelHTTPError as e:
            if e.status_code != 429 or attempt >= max_retries - 1:
                raise
            delay = base_delay * (2**attempt)
            console.log(
                f"    [yellow] Rate limited, retrying in {delay:.0f}s "
                f"(attempt {attempt + 1}/{max_retries})[/yellow]"
            )
            time.sleep(delay)
    raise RuntimeError("Unreachable")


# Section filtering


def _render_spec_ref(smd: SMDSpec, section: str) -> str | None:
    s = section.lower()

    if s == "overview":
        return f"### Overview\n\n{smd.overview}"

    if s == "goals" and smd.goals:
        return "### Goals\n\n" + "\n".join(f"- {g}" for g in smd.goals)

    if s == "non-goals" and smd.non_goals:
        return "### Non-Goals\n\n" + "\n".join(f"- {g}" for g in smd.non_goals)

    if s == "constraints" and smd.constraints:
        return "### Constraints\n\n" + "\n".join(f"- {c}" for c in smd.constraints)

    if s == "acceptance criteria" and smd.acceptance_criteria:
        return "### Acceptance Criteria\n\n" + "\n".join(f"- {a}" for a in smd.acceptance_criteria)

    if s == "notes" and smd.notes:
        return f"### Notes\n\n{smd.notes}"

    if s == "requirements" and smd.requirements:
        rendered = "\n\n".join(render_requirement(r) for r in smd.requirements)
        return f"## Requirements\n\n{rendered}"

    if s == "examples" and smd.examples:
        rendered = "\n\n".join(render_example(e) for e in smd.examples)
        return f"## Examples\n\n{rendered}"

    # Match individual requirement by title
    for req in smd.requirements:
        if req.title.lower() == s:
            return render_requirement(req)

    # Match individual example by name
    for ex in smd.examples:
        if ex.name.lower() == s:
            return render_example(ex)

    return None


def _render_arch_ref(amds: list[AMDSpec], section: str) -> str | None:
    s = section.lower()

    if s == "overview":
        parts = [a.overview for a in amds if a.overview]
        return ("### Overview\n\n" + "\n\n".join(parts)) if parts else None

    if s == "dependencies":
        deps = [d for a in amds for d in a.dependencies]
        if not deps:
            return None
        return "### Dependencies\n\n" + "\n".join(render_dependency(d) for d in deps)

    if s == "flow":
        parts = [a.flow for a in amds if a.flow]
        if not parts:
            return None
        return "### Flow\n\n```\n" + "\n\n".join(parts) + "\n```"

    if s == "notes":
        parts = [a.notes for a in amds if a.notes]
        return ("### Notes\n\n" + "\n\n".join(parts)) if parts else None

    if s.startswith("components >"):
        name = section.split(">", 1)[1].strip()
        for amd in amds:
            for comp in amd.components:
                if comp.name.lower() == name.lower():
                    return render_component(comp)
        return None

    if s.startswith("data models >"):
        name = section.split(">", 1)[1].strip()
        for amd in amds:
            for dm in amd.data_models:
                if dm.name.lower() == name.lower():
                    return render_data_model(dm)
        return None

    return None


# Prompt assembly


def assemble_task_prompt(
    task: PlanTask,
    config: NTTConfig,
    smd_map: dict[str, SMDSpec],
    amd_by_spec: dict[str, list[AMDSpec]],
) -> str:
    sections: list[str] = []

    # Project brief
    brief_path = config.metadata_context_path / "project-brief.md"
    if brief_path.exists():
        sections.append(f"## Project Brief\n\n{brief_path.read_text().strip()}")

    # Spec brief
    spec_brief_path = config.metadata_context_spec_briefs_path / f"{task.spec}.md"
    if spec_brief_path.exists():
        sections.append(f"## Spec Brief: {task.spec}\n\n{spec_brief_path.read_text().strip()}")

    # Task description
    task_section = f"## Task: {task.title}\n\n{task.description}"
    if task.notes:
        task_section += f"\n\n**Notes:** {task.notes}"
    sections.append(task_section)

    # Files to produce
    if task.outputs:
        outputs_list = "\n".join(f"- `{o}`" for o in task.outputs)
        sections.append(f"## Files to Produce\n\n{outputs_list}")

    # Filtered spec sections (via spec_refs)
    smd = smd_map.get(task.spec)
    if smd and task.spec_refs:
        spec_parts: list[str] = []
        for ref in task.spec_refs:
            _, _, ref_section = ref.partition(":")
            if not ref_section:
                continue
            rendered = _render_spec_ref(smd, ref_section.strip())
            if rendered:
                spec_parts.append(rendered)
        if spec_parts:
            sections.append("## Specification Context\n\n" + "\n\n".join(spec_parts))

    # Filtered arch sections (via arch_refs)
    amds = amd_by_spec.get(task.spec)
    if amds and task.arch_refs:
        arch_parts: list[str] = []
        for ref in task.arch_refs:
            _, _, ref_section = ref.partition(":")
            if not ref_section:
                continue
            rendered = _render_arch_ref(amds, ref_section.strip())
            if rendered:
                arch_parts.append(rendered)
        if arch_parts:
            sections.append("## Architecture Context\n\n" + "\n\n".join(arch_parts))

    # Inject files — list available dependency files for tool-based exploration
    if task.inject_files:
        available: list[str] = []
        for filepath in task.inject_files:
            full_path = config.output_path / filepath
            if full_path.exists():
                line_count = len(full_path.read_text().splitlines())
                available.append(f"- `{filepath}` ({line_count} lines)")
        if available:
            sections.append(
                "## Dependency Files\n\n"
                "The following files from previous tasks are available. "
                "Use `grep_file` and `read_lines` to inspect the types, "
                "interfaces, and signatures you need.\n\n" + "\n".join(available)
            )

    # Cross-spec interfaces
    if task.cross_spec_interfaces:
        iface_sections: list[str] = []
        for spec_id in task.cross_spec_interfaces:
            iface_path = config.metadata_context_interfaces_path / f"{spec_id}.md"
            if iface_path.exists():
                iface_sections.append(
                    f"### {spec_id} Interface\n\n{iface_path.read_text().strip()}"
                )
        if iface_sections:
            sections.append("## Cross-Spec Interfaces\n\n" + "\n\n".join(iface_sections))

    return "\n\n---\n\n".join(sections)


def assemble_fix_prompt(task: PlanTask, error_output: str, config: NTTConfig) -> str:
    sections = [f"## Error Output\n\n```\n{error_output}\n```"]

    for filepath in task.outputs:
        full_path = config.output_path / filepath
        if full_path.exists():
            content = full_path.read_text()
            sections.append(f"## Current File: {filepath}\n\n```\n{content}\n```")

    sections.append(f"## Original Task\n\n**{task.title}**: {task.description}")

    return "\n\n---\n\n".join(sections)


# Verification


def run_verify(command: str, cwd: Path) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=str(cwd),
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False, "Verify command timed out after 120 seconds"
    output = ""
    if result.stdout:
        output += result.stdout
    if result.stderr:
        if output:
            output += "\n"
        output += result.stderr
    return result.returncode == 0, output.strip()


# Task building


def make_task_slug(task: PlanTask) -> str:
    slug = task.title.lower().replace(" ", "-").replace(":", "").replace("/", "-")
    return slug.strip("-")


def save_task_output(
    task_dir: Path, written_files: list[str], success: bool, verify_output: str
) -> None:
    data: dict[str, Any] = {
        "files": written_files,
        "success": success,
        "verify_output": verify_output,
    }
    with open(task_dir / "output.toml", "wb") as f:
        tomli_w.dump(data, f)


def _truncate_output(text: str, max_lines: int = 30) -> str:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text
    kept = lines[:10] + [f"  ... ({len(lines) - 20} lines omitted) ..."] + lines[-10:]
    return "\n".join(kept)


def _print_verify_errors(console: Console, verify_output: str) -> None:
    truncated = _truncate_output(verify_output)
    console.print()
    console.print(
        Panel(
            truncated,
            title="[bold red]Errors[/bold red]",
            border_style="red",
            expand=True,
            padding=(0, 1),
        )
    )


def build_task(
    task: PlanTask,
    config: NTTConfig,
    smd_map: dict[str, SMDSpec],
    amd_by_spec: dict[str, list[AMDSpec]],
    console: Console,
    status: Status,
) -> bool:
    language = config.output.language
    impl_agent = _create_impl_agent(language)
    fix_agent = _create_fix_agent(language)

    slug = make_task_slug(task)
    task_dir = config.metadata_path / "tasks" / f"{task.id}-{slug}"
    task_dir.mkdir(parents=True, exist_ok=True)

    prompt = assemble_task_prompt(task, config, smd_map, amd_by_spec)
    (task_dir / "prompt.md").write_text(prompt)

    build_ctx = BuildContext(
        output_dir=config.output_path,
        console=console,
        status=status,
    )

    # Implementation
    status.update(f"Generating code for {task.title}")
    result = _run_with_retry(impl_agent, prompt, build_ctx, console)
    (task_dir / "response.md").write_text(result.output)

    if not task.verify:
        save_task_output(task_dir, build_ctx.written_files, True, "")
        return True

    # Verification
    status.update(f"Verifying: {task.verify}")
    passed, verify_output = run_verify(task.verify, config.output_path)

    if passed:
        console.log(f"    [green]✓[/green] {task.verify}")
        save_task_output(task_dir, build_ctx.written_files, True, verify_output)
        return True

    console.log(f"    [red]✗[/red] {task.verify}")
    _print_verify_errors(console, verify_output)

    # Fix loop — fresh agent per attempt to avoid accumulating fix history
    for attempt in range(config.build.max_fix_attempts):
        console.log(
            f"    [yellow]↻[/yellow] Fix attempt {attempt + 1}/{config.build.max_fix_attempts}"
        )
        fix_prompt = assemble_fix_prompt(task, verify_output, config)
        (task_dir / f"fix-{attempt + 1}-prompt.md").write_text(fix_prompt)

        status.update(f"Fix attempt {attempt + 1}/{config.build.max_fix_attempts} for {task.title}")
        fix_agent = _create_fix_agent(language)
        fix_result = _run_with_retry(fix_agent, fix_prompt, build_ctx, console)
        (task_dir / f"fix-{attempt + 1}-response.md").write_text(fix_result.output)

        status.update(f"Re-verifying: {task.verify}")
        passed, verify_output = run_verify(task.verify, config.output_path)
        if passed:
            console.log(f"    [green]✓[/green] {task.verify} (fixed on attempt {attempt + 1})")
            save_task_output(task_dir, build_ctx.written_files, True, verify_output)
            return True

        console.log(
            f"    [red]✗[/red] still failing ({attempt + 1}/{config.build.max_fix_attempts})"
        )

    _print_verify_errors(console, verify_output)
    save_task_output(task_dir, build_ctx.written_files, False, verify_output)
    return False


# Console output helpers


def _print_task_header(console: Console, task: PlanTask, total: int) -> None:
    console.print()
    header = Text()
    header.append(f"  [{task.id}/{total:03d}] ", style="bold cyan")
    header.append(task.title, style="bold")
    console.print(header)
    console.print(f"    [dim]{task.description}[/dim]")
    if task.outputs:
        console.print(f"    [dim]→ {', '.join(task.outputs)}[/dim]")


# Main build loop


def execute_build(
    config: NTTConfig,
    plan: Plan,
    smd_map: dict[str, SMDSpec],
    amd_by_spec: dict[str, list[AMDSpec]],
    console: Console,
    plan_filepath: Path,
) -> None:
    config.output_path.mkdir(parents=True, exist_ok=True)

    total = plan.meta.total_tasks
    completed_before = sum(1 for t in plan.tasks if t.status == TaskStatus.DONE)

    with Status("", console=console) as status:
        for task in plan.tasks:
            if task.status in (TaskStatus.DONE, TaskStatus.SKIPPED):
                console.log(
                    f"  [dim][{task.id}/{total:03d}] {task.title} ({task.status.value})[/dim]"
                )
                continue

            if task.status == TaskStatus.MANUAL:
                console.log(
                    f"  [yellow][{task.id}/{total:03d}] {task.title} — MANUAL (skipping)[/yellow]"
                )
                continue

            # Check dependencies
            task_status_map = {t.id: t.status for t in plan.tasks}
            deps_ok = all(
                task_status_map.get(dep_id) == TaskStatus.DONE for dep_id in task.depends_on
            )
            if not deps_ok:
                unmet = [
                    dep_id
                    for dep_id in task.depends_on
                    if task_status_map.get(dep_id) != TaskStatus.DONE
                ]
                console.print()
                console.log(f"  [red]✗ [{task.id}/{total:03d}] {task.title}[/red]")
                console.log(f"    [red]Dependencies not met: {', '.join(unmet)}[/red]")
                task.status = TaskStatus.FAILED
                write_plan(plan, plan_filepath)
                break

            _print_task_header(console, task, total)

            success = build_task(
                task,
                config,
                smd_map,
                amd_by_spec,
                console,
                status,
            )

            if success:
                task.status = TaskStatus.DONE
                console.log(f"  [green]✓ [{task.id}/{total:03d}] {task.title}[/green]")
            else:
                task.status = TaskStatus.FAILED

            write_plan(plan, plan_filepath)

            if not success:
                console.print()
                console.print(
                    Panel(
                        f"Task [bold]{task.id}[/bold] failed after "
                        f"{config.build.max_fix_attempts} fix attempts.\n"
                        f"Review: [cyan].ntt/tasks/{task.id}-*/[/cyan]\n"
                        f"Resume: [cyan]ntt build[/cyan]",
                        title="[bold red]Build Stopped[/bold red]",
                        border_style="red",
                        expand=False,
                        box=box.ROUNDED,
                    )
                )
                return

    # Final summary
    done = sum(1 for t in plan.tasks if t.status == TaskStatus.DONE)
    built_this_run = done - completed_before
    failed = sum(1 for t in plan.tasks if t.status == TaskStatus.FAILED)
    skipped = sum(1 for t in plan.tasks if t.status == TaskStatus.SKIPPED)

    summary = Text()
    summary.append(f"  Done: {done}/{total}", style="bold green")
    if built_this_run > 0:
        summary.append(f"  (built {built_this_run} this run)", style="dim")
    if failed:
        summary.append(f"  Failed: {failed}", style="bold red")
    if skipped:
        summary.append(f"  Skipped: {skipped}", style="dim")

    console.print()
    console.print(
        Panel(
            summary,
            title=f"[bold]{config.name} v{config.version} — Build Complete[/bold]",
            expand=False,
            box=box.ROUNDED,
        )
    )
