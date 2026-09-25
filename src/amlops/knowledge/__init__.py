"""Packaged domain knowledge: feature model, activities, best practices, profiles, provider catalogue."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

KNOWLEDGE_DIR = Path(__file__).parent


def _load(name: str) -> dict:
    with open(KNOWLEDGE_DIR / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache(maxsize=None)
def feature_model():
    from amlops.variability import FeatureModel

    return FeatureModel.from_dict(_load("mlops_feature_model.yaml"))


@lru_cache(maxsize=None)
def activities() -> dict[str, str]:
    """Flat mapping activity id -> label (38 SkeltyMLOps activities)."""
    out: dict[str, str] = {}
    for _dim, acts in _load("activities.yaml").items():
        out.update(acts)
    return out


@lru_cache(maxsize=None)
def activity_dimensions() -> dict[str, str]:
    return {aid: dim for dim, acts in _load("activities.yaml").items() for aid in acts}


@lru_cache(maxsize=None)
def best_practices() -> list[dict]:
    return _load("best_practices.yaml")["rules"]


@lru_cache(maxsize=None)
def profiles() -> dict[str, dict]:
    return _load("profiles.yaml")["profiles"]


@lru_cache(maxsize=None)
def provider_catalog() -> dict:
    return _load("providers.yaml")
