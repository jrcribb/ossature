from dataclasses import dataclass, field
from enum import Enum

from ossature.models.shared import Status


class Priority(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class Requirement:
    title: str
    description: str
    accepts: str
    returns: str
    errors: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class Example:
    name: str
    input: str
    output: str


@dataclass
class SMDSpec:
    title: str
    spec_id: str
    status: Status
    priority: Priority
    overview: str
    depends: list[str] = field(default_factory=list)
    goals: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    requirements: list[Requirement] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    examples: list[Example] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    notes: str = ""
