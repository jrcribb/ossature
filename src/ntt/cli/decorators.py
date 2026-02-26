import os
from collections.abc import Callable
from functools import wraps
from typing import Any

from rich.console import Console


def requires_llm(fn: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not os.environ.get("ANTHROPIC_API_KEY"):
            console = kwargs.get("console") or Console()
            console.print(
                "[red]Error:[/] ANTHROPIC_API_KEY environment variable is not set.\n"
                "This command requires LLM access. Set it with:\n\n"
                "  [cyan]export ANTHROPIC_API_KEY=your-key-here[/cyan]\n"
            )
            raise SystemExit(1)
        return fn(*args, **kwargs)

    return wrapper
