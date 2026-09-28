import itertools
import random

import pytest

from amlops import knowledge
from amlops.variability import FeatureModel, analyse, count_configurations, to_uvl


def _random_model(rng: random.Random, n: int = 11) -> FeatureModel:
    names = [f"F{i}" for i in range(n)]
    nodes = {names[0]: {"name": names[0], "children": []}}
    for name in names[1:]:
        parent = nodes[rng.choice(list(nodes))]
        child = {"name": name, "children": []}
        if parent.get("group") is None and not parent["children"] and rng.random() < 0.4:
            parent["group"] = rng.choice(["or", "xor"])
        if parent.get("group") is None:
            child["optional"] = rng.random() < 0.6
        parent["children"].append(child)
        nodes[name] = child
    ops = [
        lambda a, b: f"implies({a}, {b})",
        lambda a, b: f"not ({a} and {b})",
        lambda a, b: f"{a} or not {b}",
        lambda a, b: f"iff({a}, {b})",
    ]
    constraints = [rng.choice(ops)(*rng.sample(names[1:], 2)) for _ in range(rng.randint(0, 3))]
    return FeatureModel.from_dict({"root": nodes[names[0]], "constraints": constraints})


def _brute_force(fm: FeatureModel) -> dict[str, int]:
    names = list(fm.features)
    counts = {n: 0 for n in names}
    total = 0
    for bits in itertools.product((False, True), repeat=len(names)):
        sel = [n for n, b in zip(names, bits) if b]
        if fm.is_valid(sel):
            total += 1
            for n in sel:
                counts[n] += 1
    counts["__total__"] = total
    return counts


@pytest.mark.parametrize("seed", range(25))
def test_model_counter_matches_brute_force(seed):
    fm = _random_model(random.Random(seed))
    bf = _brute_force(fm)
    assert count_configurations(fm) == bf["__total__"]
    a = analyse(fm)
    assert a.n_configurations == bf["__total__"]
    for f in fm.features:
        expected = bf[f] / bf["__total__"] if bf["__total__"] else 0.0
        assert a.commonality[f] == pytest.approx(expected)
    assert set(a.dead) == {f for f in fm.features if bf[f] == 0}


def test_reference_model_has_no_dead_or_false_optional_features():
    a = analyse(knowledge.feature_model())
    assert a.n_configurations > 10**9
    assert a.dead == [] and a.false_optional == []
    assert "MLOpsPipeline" in a.core


def test_core_features_are_in_every_completion():
    fm = knowledge.feature_model()
    core = set(analyse(fm).core)
    for profile in knowledge.profiles().values():
        cfg = fm.complete(select=profile.get("select", []))
        assert core <= cfg


def test_uvl_export_is_complete():
    fm = knowledge.feature_model()
    uvl = to_uvl(fm)
    assert uvl.startswith("namespace AdaptiveMLOps")
    for name in fm.features:
        assert f"\n{' ' * 4}" in uvl and name in uvl
    assert uvl.count("\n    ") >= len(fm.features)
    assert "ClassificationBaseline & DimReduction" in uvl
    assert "=>" in uvl


def test_detects_dead_and_false_optional_features():
    fm = FeatureModel.from_dict({
        "root": {"name": "R", "children": [
            {"name": "A", "optional": False},
            {"name": "B", "optional": True},
            {"name": "C", "optional": True},
        ]},
        "constraints": ["implies(A, B)", "not C"],
    })
    a = analyse(fm)
    assert a.dead == ["C"]
    assert "B" in a.core  # optional but forced by a cross-tree constraint
    assert a.n_configurations == 1
