"""Best-practice advisor: evaluates documented know-how on a configuration.

This is how the formalised domain knowledge "guides and assists" the design
of new pipelines: every fired rule carries its rationale, its literature or
industrial source and, when possible, an automatic fix (a feature delta that
is re-validated against the feature model before being proposed).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from .feature_model import FeatureModel, Formula


@dataclass
class Finding:
    rule_id: str
    severity: str
    advice: str
    source: str
    fix: dict = field(default_factory=dict)
    fix_is_valid: Optional[bool] = None

    def as_dict(self) -> dict:
        return {
            "rule": self.rule_id,
            "severity": self.severity,
            "advice": self.advice,
            "source": self.source,
            "fix": self.fix,
            "fix_is_valid": self.fix_is_valid,
        }


def advise(fm: FeatureModel, selected: Iterable[str], rules: list[dict]) -> list[Finding]:
    sel = set(selected)
    assignment = fm.assignment(sel)
    findings: list[Finding] = []
    for r in rules:
        formula = Formula(r["when"])
        if formula.evaluate(assignment) is True:
            fix = r.get("fix", {}) or {}
            valid = None
            if fix:
                candidate = (sel | set(fix.get("select", []))) - set(fix.get("deselect", []))
                try:
                    fm.complete(select=candidate, deselect=fix.get("deselect", []))
                    valid = True
                except ValueError:
                    valid = False
            findings.append(Finding(r["id"], r["severity"], r["advice"], r.get("source", ""), fix, valid))
    return findings


def apply_fixes(fm: FeatureModel, selected: Iterable[str], findings: list[Finding]) -> set[str]:
    """Apply all valid automatic fixes and return a new complete configuration."""
    sel = set(selected)
    desel: set[str] = set()
    for f in findings:
        if f.fix and f.fix_is_valid:
            sel |= set(f.fix.get("select", []))
            desel |= set(f.fix.get("deselect", []))
    sel -= desel
    # keep only user-meaningful (leaf or concrete) decisions and re-complete
    return fm.complete(select=sel, deselect=desel)
