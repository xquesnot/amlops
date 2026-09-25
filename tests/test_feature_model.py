import pytest

from amlops import knowledge
from amlops.variability import FeatureModel, Formula, advise, apply_fixes

SMALL = {
    "name": "t",
    "root": {"name": "R", "children": [
        {"name": "A", "optional": False, "group": "xor", "children": ["A1", "A2"]},
        {"name": "B", "group": "or", "children": ["B1", "B2"]},
        {"name": "C"},
    ]},
    "constraints": ["implies(A2, C)", "not (B1 and A1)"],
}


def test_three_valued_logic():
    f = Formula("implies(X, Y or Z)")
    assert f.evaluate({"X": False}) is True
    assert f.evaluate({"X": True, "Y": None, "Z": False}) is None
    assert f.evaluate({"X": True, "Y": False, "Z": False}) is False


def test_rejects_unsafe_formula():
    with pytest.raises(ValueError):
        Formula("__import__('os')")


def test_structural_semantics():
    fm = FeatureModel.from_dict(SMALL)
    assert fm.is_valid({"R", "A", "A1"})
    assert not fm.is_valid({"R", "A", "A1", "A2"})          # xor
    assert not fm.is_valid({"R"})                            # mandatory A
    assert not fm.is_valid({"R", "A", "A1", "B"})            # or group empty
    assert not fm.is_valid({"R", "A", "A2"})                 # constraint implies(A2, C)
    assert not fm.is_valid({"R", "A", "A1", "B", "B1"})      # cross-tree exclusion


def test_completion_is_valid_and_minimal():
    fm = FeatureModel.from_dict(SMALL)
    cfg = fm.complete(select=["A2"])
    assert fm.is_valid(cfg) and {"A2", "C"} <= cfg and "B" not in cfg


def test_completion_detects_unsat():
    fm = FeatureModel.from_dict(SMALL)
    with pytest.raises(ValueError):
        fm.complete(select=["A1", "B1"])


def test_reference_model_loads_with_six_categories():
    fm = knowledge.feature_model()
    assert set(fm.categories()) == {"Functional", "Behavioral", "Technical", "DomainSpecific",
                                    "NonFunctional", "Organizational"}


def test_poster_constraint_is_enforced():
    fm = knowledge.feature_model()
    with pytest.raises(ValueError):
        fm.complete(select=["ClassificationBaseline", "DimReduction"])


def test_every_profile_is_satisfiable():
    fm = knowledge.feature_model()
    for name, p in knowledge.profiles().items():
        assert fm.is_valid(fm.complete(select=p["select"])), name


def test_advisor_fixes_are_valid_and_remove_warnings():
    fm = knowledge.feature_model()
    cfg = fm.complete(select=knowledge.profiles()["tabular-classification-continuous"]["select"])
    findings = advise(fm, cfg, knowledge.best_practices())
    assert {f.rule_id for f in findings} >= {"BP01", "BP02"}
    fixed = apply_fixes(fm, cfg, findings)
    assert fm.is_valid(fixed)
    left = {f.rule_id for f in advise(fm, fixed, knowledge.best_practices()) if f.severity == "warning"}
    assert not left
