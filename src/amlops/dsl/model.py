"""Abstract syntax (metamodel) of the AdaptiveMLOps pipeline DSL.

Two layers:
  * ``PipelineSpec``  -- what the user wrote (possibly only a profile and objectives);
  * ``ResolvedPipeline`` -- the fully derived, platform-independent model (PIM)
    from which Platform/Infrastructure-as-Code is generated.

The step typing vocabulary is the set of 38 SkeltyMLOps activities, so a DSL
model can be traced back to the reference architecture of the consortium.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class Resources:
    vcpu: int = 4
    mem_gb: int = 16
    gpu: int = 0


@dataclass
class Step:
    id: str
    kind: str
    activity: str
    image: str = ""
    resources: Resources = field(default_factory=Resources)
    depends_on: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    continuous: bool = False            # long-running (serving, monitoring)
    hours_per_run: float = 0.25         # batch steps
    runs_per_month: float = 4.0
    replicas: int = 1
    output_gb: float = 0.1              # data handed to successors per run
    deferrable_hours: float = 0.0       # scheduling slack (carbon-aware)
    origin: str = "derived"             # derived | user
    variation_point: Optional[str] = None
    features: list[str] = field(default_factory=list)  # features that caused this step

    def monthly_hours(self) -> float:
        if self.continuous:
            return 730.0 * self.replicas
        return self.hours_per_run * self.runs_per_month * self.replicas


@dataclass
class Trigger:
    type: str                            # schedule | drift | performance | new_data | manual
    cron: Optional[str] = None
    detector: Optional[str] = None       # psi | ks
    threshold: Optional[float] = None
    metric: Optional[str] = None
    contract: Optional[str] = None


@dataclass
class Gate:
    id: str
    actor: str
    produces: list[str] = field(default_factory=list)
    approval: bool = False
    condition: Optional[str] = None
    automated_by: Optional[str] = None   # step id that produces the artefact automatically


@dataclass
class Contract:
    id: str
    ticket_type: str
    gates: list[Gate]
    version: str = "1"


@dataclass
class Objectives:
    cost: float = 1.0
    carbon: float = 0.0
    latency: float = 0.0

    def normalised(self) -> "Objectives":
        s = self.cost + self.carbon + self.latency
        if s <= 0:
            raise ValueError("At least one objective weight must be positive")
        return Objectives(self.cost / s, self.carbon / s, self.latency / s)


@dataclass
class PlacementConstraints:
    allowed_providers: list[str] = field(default_factory=list)   # empty = all
    required_labels: list[str] = field(default_factory=list)     # e.g. EU, SecNumCloud, HDS
    pin: dict[str, str] = field(default_factory=dict)            # step id -> provider/region
    max_latency_ms: Optional[float] = None
    colocate: list[list[str]] = field(default_factory=list)


@dataclass
class DataSpec:
    source: str = ""
    volume_gb: float = 10.0
    model_gb: float = 1.0


@dataclass
class PipelineSpec:
    """Concrete model as written by the user (low-code or expert)."""

    name: str
    dsl_version: str = "0.1"
    profile: Optional[str] = None
    select: list[str] = field(default_factory=list)
    deselect: list[str] = field(default_factory=list)
    data: DataSpec = field(default_factory=DataSpec)
    objectives: Objectives = field(default_factory=Objectives)
    placement: PlacementConstraints = field(default_factory=PlacementConstraints)
    steps: list[dict] = field(default_factory=list)      # partial step definitions
    triggers: list[dict] = field(default_factory=list)
    overrides: dict[str, Any] = field(default_factory=dict)
    apply_best_practices: bool = False
    source_lines: int = 0


@dataclass
class TraceLink:
    element: str
    reason: str


@dataclass
class ResolvedPipeline:
    name: str
    configuration: list[str]
    steps: list[Step]
    triggers: list[Trigger]
    contracts: list[Contract]
    objectives: Objectives
    placement: PlacementConstraints
    data: DataSpec
    findings: list[dict] = field(default_factory=list)
    trace: list[TraceLink] = field(default_factory=list)

    def step(self, sid: str) -> Step:
        for s in self.steps:
            if s.id == sid:
                return s
        raise KeyError(sid)

    def has(self, feature: str) -> bool:
        return feature in self.configuration

    def edges(self) -> list[tuple[str, str]]:
        return [(d, s.id) for s in self.steps for d in s.depends_on]

    def topological_order(self) -> list[str]:
        indeg = {s.id: 0 for s in self.steps}
        for _, t in self.edges():
            indeg[t] += 1
        ready = [s.id for s in self.steps if indeg[s.id] == 0]
        order: list[str] = []
        succ: dict[str, list[str]] = {s.id: [] for s in self.steps}
        for a, b in self.edges():
            succ[a].append(b)
        while ready:
            n = ready.pop(0)
            order.append(n)
            for m in succ[n]:
                indeg[m] -= 1
                if indeg[m] == 0:
                    ready.append(m)
        if len(order) != len(self.steps):
            raise ValueError("Pipeline step graph contains a cycle")
        return order

    def to_dict(self) -> dict:
        return asdict(self)
