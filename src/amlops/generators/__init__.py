"""Model-to-text transformations (requirement iv: the DSL model is pivotal).

A resolved pipeline plus a placement is compiled into:
  * Infrastructure-as-Code: Terraform (native Scaleway resources, module
    stubs for other providers);
  * Platform-as-Code: Kubernetes / Argo Workflows manifests per location,
    Argo Events sensors for cross-location hand-offs, serving and monitoring
    deployments, drift-policy ConfigMaps;
  * CI/CD: a GitHub Actions workflow validating and applying the stack.
Every generated file is recorded in ``trace.json`` with the model elements
(steps, triggers, contracts) and SkeltyMLOps activities it realises.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from amlops.dsl.model import ResolvedPipeline
from amlops.placement.optimizer import Placement


@dataclass
class GeneratedFile:
    path: str
    content: str
    realises: list[str] = field(default_factory=list)      # model elements
    activities: list[str] = field(default_factory=list)    # SkeltyMLOps activity ids

    @property
    def lines(self) -> int:
        return sum(1 for ln in self.content.splitlines() if ln.strip() and not ln.strip().startswith(("#", "//")))


Generator = Callable[[ResolvedPipeline, Placement], list[GeneratedFile]]
_GENERATORS: dict[str, Generator] = {}


def register_generator(name: str) -> Callable[[Generator], Generator]:
    def deco(fn: Generator) -> Generator:
        _GENERATORS[name] = fn
        return fn
    return deco


def generators() -> dict[str, Generator]:
    return dict(_GENERATORS)


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def ident(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def dump_yaml_docs(docs: list[dict]) -> str:
    return "---\n".join(yaml.safe_dump(d, sort_keys=False) for d in docs)


def generate(rp: ResolvedPipeline, placement: Placement, outdir: str | Path | None = None,
             targets: list[str] | None = None) -> list[GeneratedFile]:
    from . import cicd, kubernetes, terraform  # noqa: F401  (registration side effects)

    files: list[GeneratedFile] = []
    for name in targets or sorted(_GENERATORS):
        files.extend(_GENERATORS[name](rp, placement))
    trace = {
        "pipeline": rp.name,
        "configuration": rp.configuration,
        "placement": placement.summary(),
        "files": [{"path": f.path, "lines": f.lines, "realises": f.realises, "activities": sorted(set(f.activities))}
                  for f in files],
    }
    files.append(GeneratedFile("trace.json", json.dumps(trace, indent=2)))
    if outdir is not None:
        out = Path(outdir)
        for f in files:
            p = out / f.path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f.content, encoding="utf-8")
    return files


def covered_activities(files: list[GeneratedFile]) -> set[str]:
    return {a for f in files for a in f.activities}
