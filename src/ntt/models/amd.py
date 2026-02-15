from dataclasses import dataclass, field

from ntt.models.shared import Status


@dataclass
class Component:
    name: str
    path: str
    description: str
    interface: str
    interface_language: str = ""
    depends_on: list[str] = field(default_factory=list)


@dataclass
class DataModel:
    name: str
    definition: str
    definition_language: str = ""


@dataclass
class Dependency:
    name: str
    purpose: str


@dataclass
class AMDSpec:
    title: str
    spec_id: str
    status: Status
    overview: str
    components: list[Component] = field(default_factory=list)
    data_models: list[DataModel] = field(default_factory=list)
    flow: str = ""
    dependencies: list[Dependency] = field(default_factory=list)
    notes: str = ""
