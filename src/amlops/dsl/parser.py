"""Concrete syntax: a YAML-based textual DSL.

Minimal (non-expert) model::

    amlops: "0.1"
    pipeline: churn
    profile: tabular-classification-continuous
    objectives: {cost: 0.6, carbon: 0.4}

Experts may add ``features``, ``steps``, ``triggers``, ``placement`` and
dotted-path ``overrides`` (see docs/DSL.md).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .model import DataSpec, Objectives, PipelineSpec, PlacementConstraints

SUPPORTED_VERSIONS = {"0.1"}
TOP_LEVEL = {
    "amlops", "pipeline", "profile", "features", "data", "objectives", "placement",
    "steps", "triggers", "overrides", "best_practices", "description",
}


class DSLError(ValueError):
    def __init__(self, path: str, message: str):
        super().__init__(f"{path}: {message}")
        self.path = path


def _expect(cond: bool, path: str, msg: str) -> None:
    if not cond:
        raise DSLError(path, msg)


def _known_keys(d: dict, allowed: set[str], path: str) -> None:
    extra = set(d) - allowed
    _expect(not extra, path, f"unknown key(s) {sorted(extra)}; allowed: {sorted(allowed)}")


def parse_dict(doc: dict[str, Any], source_lines: int = 0) -> PipelineSpec:
    _expect(isinstance(doc, dict), "$", "document must be a mapping")
    _known_keys(doc, TOP_LEVEL, "$")
    version = str(doc.get("amlops", "0.1"))
    _expect(version in SUPPORTED_VERSIONS, "$.amlops", f"unsupported DSL version {version}")
    _expect("pipeline" in doc, "$", "missing required key 'pipeline'")
    name = str(doc["pipeline"])
    _expect(name.replace("-", "").replace("_", "").isalnum(), "$.pipeline",
            "name must be alphanumeric with - or _")

    feats = doc.get("features", {}) or {}
    _known_keys(feats, {"select", "deselect"}, "$.features")

    data = doc.get("data", {}) or {}
    _known_keys(data, {"source", "volume_gb", "model_gb"}, "$.data")

    obj = doc.get("objectives", {}) or {}
    _known_keys(obj, {"cost", "carbon", "latency"}, "$.objectives")
    for k, v in obj.items():
        _expect(isinstance(v, (int, float)) and v >= 0, f"$.objectives.{k}", "must be a non-negative number")

    pl = doc.get("placement", {}) or {}
    _known_keys(pl, {"allowed_providers", "required_labels", "pin", "max_latency_ms", "colocate"}, "$.placement")

    steps = doc.get("steps", []) or []
    _expect(isinstance(steps, list), "$.steps", "must be a list")
    step_keys = {"id", "kind", "activity", "image", "resources", "after", "params", "hours",
                 "runs_per_month", "replicas", "output_gb", "deferrable_hours", "continuous", "remove"}
    for i, s in enumerate(steps):
        _expect(isinstance(s, dict) and "id" in s, f"$.steps[{i}]", "each step needs an 'id'")
        _known_keys(s, step_keys, f"$.steps[{i}]")

    triggers = doc.get("triggers", []) or []
    for i, t in enumerate(triggers):
        _expect(isinstance(t, dict) and "type" in t, f"$.triggers[{i}]", "each trigger needs a 'type'")
        _known_keys(t, {"type", "cron", "detector", "threshold", "metric", "contract"}, f"$.triggers[{i}]")
        _expect(t["type"] in {"schedule", "drift", "performance", "new_data", "manual"},
                f"$.triggers[{i}].type", f"unknown trigger type {t['type']}")

    return PipelineSpec(
        name=name,
        dsl_version=version,
        profile=doc.get("profile"),
        select=list(feats.get("select", []) or []),
        deselect=list(feats.get("deselect", []) or []),
        data=DataSpec(**data),
        objectives=Objectives(**obj) if obj else Objectives(),
        placement=PlacementConstraints(**pl),
        steps=steps,
        triggers=triggers,
        overrides=dict(doc.get("overrides", {}) or {}),
        apply_best_practices=bool(doc.get("best_practices", False)),
        source_lines=source_lines,
    )


def count_significant_lines(text: str) -> int:
    return sum(1 for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#"))


def parse_text(text: str) -> PipelineSpec:
    return parse_dict(yaml.safe_load(text), source_lines=count_significant_lines(text))


def parse_file(path: str | Path) -> PipelineSpec:
    return parse_text(Path(path).read_text(encoding="utf-8"))
