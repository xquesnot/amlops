"""Feature models for MLOps variability.

Implements a cardinality-free FODA-style feature model with:
  * mandatory / optional children,
  * XOR (alternative) and OR groups,
  * cross-tree constraints written as propositional formulas
    (``and``, ``or``, ``not``, ``implies(a, b)``) over feature names,
  * three-valued partial evaluation used to prune a backtracking
    configuration-completion search (requirement R8, "configuration resolution").

The six top-level variability categories come from the AdaptiveMLOps
variability mapping study (El Hatimi et al., GdR GPL 2025): Functional,
Behavioral, Technical, Domain-Specific, Non-Functional, Organizational.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import yaml

TRUE, FALSE, UNKNOWN = True, False, None


# --------------------------------------------------------------------------
# Propositional formulas
# --------------------------------------------------------------------------
class Formula:
    """A propositional formula over feature names, parsed with ``ast``.

    Only a safe subset of Python syntax is accepted:
    ``A and B``, ``A or B``, ``not A``, ``implies(A, B)``, ``iff(A, B)``.
    """

    _ALLOWED_CALLS = {"implies", "iff"}

    def __init__(self, text: str):
        self.text = text.strip()
        try:
            self._tree = ast.parse(self.text, mode="eval").body
        except SyntaxError as exc:  # pragma: no cover - defensive
            raise ValueError(f"Invalid formula {text!r}: {exc}") from exc
        self.variables: set[str] = set()
        self._check(self._tree)

    def _check(self, node: ast.AST) -> None:
        if isinstance(node, ast.BoolOp):
            for v in node.values:
                self._check(v)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            self._check(node.operand)
        elif isinstance(node, ast.Name):
            self.variables.add(node.id)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in self._ALLOWED_CALLS
            and len(node.args) == 2
        ):
            for a in node.args:
                self._check(a)
        elif isinstance(node, ast.Constant) and isinstance(node.value, bool):
            pass
        else:
            raise ValueError(f"Unsupported construct in formula {self.text!r}: {ast.dump(node)}")

    # Kleene three-valued logic -------------------------------------------
    def evaluate(self, assignment: dict[str, Optional[bool]]) -> Optional[bool]:
        return self._eval(self._tree, assignment)

    def _eval(self, node: ast.AST, a: dict[str, Optional[bool]]) -> Optional[bool]:
        if isinstance(node, ast.Name):
            return a.get(node.id, UNKNOWN)
        if isinstance(node, ast.Constant):
            return bool(node.value)
        if isinstance(node, ast.UnaryOp):
            v = self._eval(node.operand, a)
            return None if v is None else (not v)
        if isinstance(node, ast.BoolOp):
            vals = [self._eval(v, a) for v in node.values]
            if isinstance(node.op, ast.And):
                if any(v is False for v in vals):
                    return False
                return True if all(v is True for v in vals) else None
            if any(v is True for v in vals):
                return True
            return False if all(v is False for v in vals) else None
        if isinstance(node, ast.Call):
            x, y = (self._eval(arg, a) for arg in node.args)
            if node.func.id == "implies":
                if x is False or y is True:
                    return True
                if x is True and y is False:
                    return False
                return None
            # iff
            if x is None or y is None:
                return None
            return x == y
        raise AssertionError("unreachable")

    def __repr__(self) -> str:
        return f"Formula({self.text!r})"


# --------------------------------------------------------------------------
# Feature model
# --------------------------------------------------------------------------
@dataclass
class Feature:
    name: str
    parent: Optional[str] = None
    optional: bool = True
    group: Optional[str] = None  # 'xor' | 'or' | None  (applies to children)
    children: list[str] = field(default_factory=list)
    category: Optional[str] = None
    description: str = ""
    default: bool = False
    abstract: bool = False


@dataclass
class Constraint:
    formula: Formula
    id: str = ""
    rationale: str = ""


class FeatureModel:
    def __init__(self, name: str):
        self.name = name
        self.root: Optional[str] = None
        self.features: dict[str, Feature] = {}
        self.constraints: list[Constraint] = []

    # -- construction ------------------------------------------------------
    @classmethod
    def from_yaml(cls, path: str | Path) -> "FeatureModel":
        with open(path, encoding="utf-8") as fh:
            return cls.from_dict(yaml.safe_load(fh))

    @classmethod
    def from_dict(cls, data: dict) -> "FeatureModel":
        fm = cls(data.get("name", "feature-model"))
        fm._add(data["root"], parent=None, category=None, optional=False)
        for i, c in enumerate(data.get("constraints", [])):
            if isinstance(c, str):
                c = {"formula": c}
            fm.constraints.append(
                Constraint(Formula(c["formula"]), c.get("id", f"C{i + 1}"), c.get("rationale", ""))
            )
        unknown = {v for c in fm.constraints for v in c.formula.variables} - set(fm.features)
        if unknown:
            raise ValueError(f"Constraints reference unknown features: {sorted(unknown)}")
        return fm

    def _add(self, node: dict | str, parent: Optional[str], category: Optional[str], optional: bool) -> str:
        if isinstance(node, str):
            node = {"name": node}
        name = node["name"]
        if name in self.features:
            raise ValueError(f"Duplicate feature {name}")
        cat = node.get("category", category)
        feat = Feature(
            name=name,
            parent=parent,
            optional=node.get("optional", optional),
            group=node.get("group"),
            category=cat,
            description=node.get("description", ""),
            default=node.get("default", False),
            abstract=node.get("abstract", False),
        )
        self.features[name] = feat
        if parent is None:
            self.root = name
        in_group = feat.group is not None
        for child in node.get("children", []):
            child_opt = True if in_group else (child.get("optional", True) if isinstance(child, dict) else True)
            cname = self._add(child, parent=name, category=cat, optional=child_opt)
            feat.children.append(cname)
        return name

    # -- queries -----------------------------------------------------------
    def categories(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for f in self.features.values():
            if f.category:
                out.setdefault(f.category, []).append(f.name)
        return out

    def leaves(self) -> list[str]:
        return [f.name for f in self.features.values() if not f.children]

    def preorder(self) -> list[str]:
        order: list[str] = []

        def walk(n: str) -> None:
            order.append(n)
            for c in self.features[n].children:
                walk(c)

        assert self.root is not None
        walk(self.root)
        return order

    # -- semantics ---------------------------------------------------------
    def _structural_rules(self, a: dict[str, Optional[bool]]) -> list[tuple[str, Optional[bool]]]:
        """Return (description, truth value) for every structural rule."""
        rules: list[tuple[str, Optional[bool]]] = []
        rules.append((f"root {self.root} selected", a.get(self.root)))
        for f in self.features.values():
            p = a.get(f.name)
            if f.parent is not None:
                # child => parent
                rules.append((f"{f.name} requires parent {f.parent}",
                              Formula(f"implies({f.name}, {f.parent})").evaluate(a)))
                parent = self.features[f.parent]
                if parent.group is None and not f.optional:
                    rules.append((f"mandatory {f.name} under {f.parent}",
                                  Formula(f"implies({f.parent}, {f.name})").evaluate(a)))
            if f.children and f.group in ("xor", "or"):
                vals = [a.get(c) for c in f.children]
                n_true = sum(v is True for v in vals)
                n_unknown = sum(v is None for v in vals)
                if p is False:
                    val: Optional[bool] = True
                elif f.group == "xor":
                    if n_true > 1:
                        val = False
                    elif p is True and n_true == 1:
                        val = True
                    elif p is True and n_true == 0 and n_unknown == 0:
                        val = False
                    else:
                        val = None
                else:  # or
                    if p is True and n_true >= 1:
                        val = True
                    elif p is True and n_unknown == 0:
                        val = False
                    else:
                        val = None
                rules.append((f"{f.group.upper()} group under {f.name}", val))
        for c in self.constraints:
            rules.append((f"constraint {c.id}: {c.formula.text}", c.formula.evaluate(a)))
        return rules

    def assignment(self, selected: Iterable[str]) -> dict[str, bool]:
        sel = set(selected)
        unknown = sel - set(self.features)
        if unknown:
            raise ValueError(f"Unknown features: {sorted(unknown)}")
        return {n: (n in sel) for n in self.features}

    def violations(self, selected: Iterable[str]) -> list[str]:
        a = self.assignment(selected)
        return [d for d, v in self._structural_rules(a) if v is False]

    def is_valid(self, selected: Iterable[str]) -> bool:
        return not self.violations(selected)

    # -- configuration completion (R8) -------------------------------------
    def complete(
        self,
        select: Iterable[str] = (),
        deselect: Iterable[str] = (),
        prefer_defaults: bool = True,
        max_nodes: int = 200_000,
    ) -> set[str]:
        """Complete a partial configuration into a valid full one.

        Undecided features are explored in pre-order; ``False`` is tried
        first unless the feature is flagged ``default: true`` (minimal
        completion). Raises ``ValueError`` if no valid completion exists.
        """
        a: dict[str, Optional[bool]] = {n: None for n in self.features}
        for n in select:
            if n not in self.features:
                raise ValueError(f"Unknown feature {n}")
            a[n] = True
        for n in deselect:
            if n not in self.features:
                raise ValueError(f"Unknown feature {n}")
            if a[n] is True:
                raise ValueError(f"Feature {n} both selected and deselected")
            a[n] = False
        # propagate: selected => ancestors selected
        for n in list(a):
            if a[n] is True:
                p = self.features[n].parent
                while p is not None:
                    a[p] = True
                    p = self.features[p].parent
        assert self.root is not None
        a[self.root] = True

        order = [n for n in self.preorder() if a[n] is None]
        nodes = 0

        def consistent() -> bool:
            return all(v is not False for _, v in self._structural_rules(a))

        def search(i: int) -> bool:
            nonlocal nodes
            nodes += 1
            if nodes > max_nodes:
                raise RuntimeError("Configuration search exceeded node budget")
            if not consistent():
                return False
            if i == len(order):
                return True
            n = order[i]
            feat = self.features[n]
            first = True if (prefer_defaults and feat.default) else False
            parent_sel = feat.parent is None or a[feat.parent] is True
            values = (first, not first) if parent_sel else (False,)
            for v in values:
                a[n] = v
                if search(i + 1):
                    return True
            a[n] = None
            return False

        if not search(0):
            raise ValueError(
                "No valid configuration extends the partial selection; violated: "
                + "; ".join(self.violations([k for k, v in a.items() if v]))
            )
        return {n for n, v in a.items() if v}

    def explain(self, selected: Iterable[str]) -> dict:
        sel = set(selected)
        return {
            "valid": self.is_valid(sel),
            "violations": self.violations(sel),
            "selected_by_category": {
                cat: sorted(f for f in feats if f in sel and not self.features[f].abstract)
                for cat, feats in self.categories().items()
            },
        }
