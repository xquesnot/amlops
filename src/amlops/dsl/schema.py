"""JSON Schema of the DSL (roadmap item F1).

The schema mirrors the checks of :mod:`amlops.dsl.parser` and enumerates the
features of the packaged feature model and the step kinds of the registry,
so editors (VS Code with the YAML extension, JetBrains IDEs) can offer
completion and validation while a model is written.
"""
from __future__ import annotations

from amlops import knowledge

from .registry import step_kinds

SCHEMA_ID = "https://github.com/xquesnot/amlops/schema/amlops-0.1.json"


def dsl_schema() -> dict:
    fm = knowledge.feature_model()
    features = sorted(fm.features)
    providers = sorted(knowledge.provider_catalog()["providers"])
    activities = sorted(knowledge.activities())
    weight = {"type": "number", "minimum": 0}
    step = {
        "type": "object",
        "required": ["id"],
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string"},
            "kind": {"enum": sorted(step_kinds())},
            "activity": {"enum": activities, "description": "SkeltyMLOps activity id"},
            "image": {"type": "string"},
            "resources": {
                "type": "object", "additionalProperties": False,
                "properties": {"vcpu": weight, "mem_gb": weight, "gpu": {"type": "integer", "minimum": 0}},
            },
            "after": {"type": "string"},
            "params": {"type": "object"},
            "hours": weight,
            "runs_per_month": weight,
            "replicas": {"type": "integer", "minimum": 1},
            "output_gb": weight,
            "deferrable_hours": weight,
            "continuous": {"type": "boolean"},
            "remove": {"type": "boolean"},
        },
    }
    trigger = {
        "type": "object",
        "required": ["type"],
        "additionalProperties": False,
        "properties": {
            "type": {"enum": ["schedule", "drift", "performance", "new_data", "manual"]},
            "cron": {"type": "string"},
            "detector": {"enum": ["psi", "ks"]},
            "threshold": {"type": "number"},
            "metric": {"type": "string"},
            "contract": {"type": "string"},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": "amlops DSL model",
        "type": "object",
        "required": ["pipeline"],
        "additionalProperties": False,
        "properties": {
            "amlops": {"enum": ["0.1"]},
            "pipeline": {"type": "string", "pattern": "^[A-Za-z0-9_-]+$"},
            "description": {"type": "string"},
            "profile": {"enum": sorted(knowledge.profiles())},
            "best_practices": {"type": "boolean"},
            "features": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "select": {"type": "array", "items": {"enum": features}},
                    "deselect": {"type": "array", "items": {"enum": features}},
                },
            },
            "data": {
                "type": "object", "additionalProperties": False,
                "properties": {"source": {"type": "string"}, "volume_gb": weight, "model_gb": weight},
            },
            "objectives": {
                "type": "object", "additionalProperties": False,
                "properties": {"cost": weight, "carbon": weight, "latency": weight},
            },
            "placement": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "allowed_providers": {"type": "array", "items": {"enum": providers}},
                    "required_labels": {"type": "array", "items": {"type": "string"}},
                    "pin": {"type": "object", "additionalProperties": {"type": "string"}},
                    "max_latency_ms": weight,
                    "colocate": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                },
            },
            "steps": {"type": "array", "items": step},
            "triggers": {"type": "array", "items": trigger},
            "overrides": {"type": "object"},
        },
    }
