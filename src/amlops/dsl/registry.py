"""Extension points (requirement i: generic and extensible).

Step kinds and code generators are registered by name. Third parties add
their own with ``register_step_kind`` / ``amlops.generators.register_generator``
or through the ``amlops.step_kinds`` entry-point group.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any


@dataclass(frozen=True)
class StepKind:
    name: str
    activity: str
    image: str
    vcpu: int = 4
    mem_gb: int = 16
    gpu: int = 0
    continuous: bool = False
    hours_per_run: float = 0.25
    defaults: dict[str, Any] = field(default_factory=dict)


_KINDS: dict[str, StepKind] = {}


def register_step_kind(kind: StepKind, replace: bool = False) -> None:
    if kind.name in _KINDS and not replace:
        raise ValueError(f"Step kind {kind.name!r} already registered")
    _KINDS[kind.name] = kind


def step_kind(name: str) -> StepKind:
    if name not in _KINDS:
        raise KeyError(f"Unknown step kind {name!r}; known: {sorted(_KINDS)}")
    return _KINDS[name]


def step_kinds() -> dict[str, StepKind]:
    return dict(_KINDS)


_IMG = "registry.example.org/amlops"
for _k in [
    StepKind("ingest", "D2", f"{_IMG}/ingest:0.1", hours_per_run=0.25),
    StepKind("validate", "D4", f"{_IMG}/validate:0.1", hours_per_run=0.1),
    StepKind("impute", "D3", f"{_IMG}/preprocess:0.1", defaults={"strategy": "median"}),
    StepKind("remove_outliers", "D3", f"{_IMG}/preprocess:0.1", defaults={"method": "iqr"}),
    StepKind("normalize", "D3", f"{_IMG}/preprocess:0.1", defaults={"method": "standard"}),
    StepKind("select_features", "D5", f"{_IMG}/features:0.1", defaults={"k": "auto"}),
    StepKind("reduce_dims", "D5", f"{_IMG}/features:0.1", defaults={"method": "pca", "variance": 0.95}),
    StepKind("transform", "D5", f"{_IMG}/transform:0.1"),
    StepKind("tune", "M5", f"{_IMG}/train:0.1", vcpu=16, mem_gb=64, hours_per_run=2.0,
             defaults={"trials": 30}),
    StepKind("train", "M4", f"{_IMG}/train:0.1", vcpu=16, mem_gb=64, hours_per_run=1.0),
    StepKind("evaluate", "M7", f"{_IMG}/evaluate:0.1", hours_per_run=0.1),
    StepKind("explain", "M7", f"{_IMG}/explain:0.1", hours_per_run=0.2, defaults={"method": "shap"}),
    StepKind("register", "M9", f"{_IMG}/registry-client:0.1", hours_per_run=0.05),
    StepKind("approve", "M6", "", hours_per_run=0.0),
    StepKind("deploy_test", "O4", f"{_IMG}/deployer:0.1", hours_per_run=0.1),
    StepKind("deploy", "O5", f"{_IMG}/deployer:0.1", hours_per_run=0.1),
    StepKind("serve", "O6", f"{_IMG}/serve:0.1", continuous=True),
    StepKind("batch_score", "O6", f"{_IMG}/batch-score:0.1", hours_per_run=0.5),
    StepKind("monitor", "O7", f"{_IMG}/monitor:0.1", vcpu=4, mem_gb=16, continuous=True),
]:
    register_step_kind(_k)


def load_plugins() -> list[str]:
    """Load third-party step kinds declared under the ``amlops.step_kinds`` entry-point group."""
    loaded = []
    for ep in entry_points(group="amlops.step_kinds"):
        obj = ep.load()
        kinds = obj() if callable(obj) else obj
        for k in kinds:
            register_step_kind(k, replace=True)
            loaded.append(k.name)
    return loaded
