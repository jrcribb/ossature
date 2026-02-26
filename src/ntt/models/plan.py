from enum import Enum

from pydantic import BaseModel


class TaskStatus(Enum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    MANUAL = "manual"


class PlannerTask(BaseModel):
    title: str
    description: str
    outputs: list[str]
    depends_on: list[int]
    spec_refs: list[str]
    arch_refs: list[str]
    verify: str


class SpecTaskPlan(BaseModel):
    tasks: list[PlannerTask]


class PlanTask(BaseModel):
    id: str
    spec: str
    title: str
    description: str
    outputs: list[str]
    depends_on: list[str]
    spec_refs: list[str]
    arch_refs: list[str]
    status: TaskStatus = TaskStatus.PENDING
    verify: str
    inject_files: list[str] = []
    cross_spec_interfaces: list[str] = []
    notes: str = ""


class PlanMeta(BaseModel):
    generated_at: str
    total_tasks: int
    specs: list[str]


class Plan(BaseModel):
    meta: PlanMeta
    tasks: list[PlanTask]
