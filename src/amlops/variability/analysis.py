"""Automated analysis of the feature model (roadmap item B2) and UVL export (B1).

The feature model is translated into conjunctive normal form (CNF):

  * the root is selected;
  * every child implies its parent;
  * a mandatory child (outside any group) is implied by its parent;
  * an OR group requires at least one child when the parent is selected,
    an XOR group exactly one;
  * cross-tree constraints are encoded with a Tseitin transformation in which
    every auxiliary variable is *equivalent* to its sub-formula, so the
    auxiliary variables are functionally determined and the number of models
    of the CNF equals the number of valid configurations.

On top of this encoding the module offers an exact model counter (DPLL with
unit propagation, connected-component decomposition and component caching,
in the spirit of #SAT solvers such as sharpSAT) and the classical analysis
operations of Benavides et al. (2010): number of valid configurations, core
and dead features, false-optional features and per-feature commonality.
The count of every analysis is cross-checked against the three-valued
semantics of :class:`FeatureModel` by the test suite.
"""
from __future__ import annotations

import ast
import sys
import time
from dataclasses import dataclass
from typing import Iterable, Optional

from .feature_model import FeatureModel

Clause = tuple[int, ...]


# --------------------------------------------------------------------------
# CNF encoding
# --------------------------------------------------------------------------
@dataclass
class CNF:
    clauses: list[Clause]
    var: dict[str, int]          # feature name -> variable (1-based)
    n_vars: int                  # features + auxiliary variables

    @property
    def feature_vars(self) -> set[int]:
        return set(self.var.values())

    def lit(self, feature: str, value: bool = True) -> int:
        v = self.var[feature]
        return v if value else -v


def to_cnf(fm: FeatureModel) -> CNF:
    names = fm.preorder()
    var = {n: i + 1 for i, n in enumerate(names)}
    n_vars = len(names)
    clauses: list[Clause] = [(var[fm.root],)]

    for f in fm.features.values():
        v = var[f.name]
        if f.parent is not None:
            p = var[f.parent]
            clauses.append((-v, p))
            if fm.features[f.parent].group is None and not f.optional:
                clauses.append((-p, v))
        if f.children and f.group in ("or", "xor"):
            kids = [var[c] for c in f.children]
            clauses.append(tuple([-v] + kids))
            if f.group == "xor":
                for i in range(len(kids)):
                    for j in range(i + 1, len(kids)):
                        clauses.append((-kids[i], -kids[j]))

    def fresh() -> int:
        nonlocal n_vars
        n_vars += 1
        return n_vars

    const_true: Optional[int] = None

    def tseitin(node: ast.AST) -> int:
        nonlocal const_true
        if isinstance(node, ast.Name):
            return var[node.id]
        if isinstance(node, ast.Constant):
            if const_true is None:
                const_true = fresh()
                clauses.append((const_true,))
            return const_true if node.value else -const_true
        if isinstance(node, ast.UnaryOp):
            return -tseitin(node.operand)
        if isinstance(node, ast.BoolOp):
            xs = [tseitin(x) for x in node.values]
            g = fresh()
            if isinstance(node.op, ast.And):
                clauses.extend((-g, x) for x in xs)
                clauses.append(tuple([g] + [-x for x in xs]))
            else:
                clauses.extend((g, -x) for x in xs)
                clauses.append(tuple([-g] + xs))
            return g
        if isinstance(node, ast.Call):
            a, b = (tseitin(x) for x in node.args)
            g = fresh()
            if node.func.id == "implies":          # g <-> (not a or b)
                clauses.extend([(g, a), (g, -b), (-g, -a, b)])
            else:                                  # iff: g <-> (a <-> b)
                clauses.extend([(-g, -a, b), (-g, a, -b), (g, a, b), (g, -a, -b)])
            return g
        raise ValueError(f"unsupported node {ast.dump(node)}")

    for c in fm.constraints:
        clauses.append((tseitin(c.formula._tree),))
    return CNF(clauses, var, n_vars)


# --------------------------------------------------------------------------
# Exact model counting (#SAT)
# --------------------------------------------------------------------------
def _condition(clauses: list[Clause], lit: int) -> Optional[list[Clause]]:
    out: list[Clause] = []
    for c in clauses:
        if lit in c:
            continue
        if -lit in c:
            c = tuple(x for x in c if x != -lit)
            if not c:
                return None
        out.append(c)
    return out


def _propagate(clauses: list[Clause], free: set[int]) -> Optional[tuple[list[Clause], set[int]]]:
    free = set(free)
    while True:
        unit = next((c[0] for c in clauses if len(c) == 1), None)
        if unit is None:
            return clauses, free
        clauses = _condition(clauses, unit)
        if clauses is None:
            return None
        free.discard(abs(unit))


def _components(clauses: list[Clause]) -> list[tuple[list[Clause], set[int]]]:
    parent: dict[int, int] = {}

    def find(x: int) -> int:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for c in clauses:
        vs = [abs(x) for x in c]
        r = find(vs[0])
        for v in vs[1:]:
            parent[find(v)] = r
    groups: dict[int, tuple[list[Clause], set[int]]] = {}
    for c in clauses:
        r = find(abs(c[0]))
        g = groups.setdefault(r, ([], set()))
        g[0].append(c)
        g[1].update(abs(x) for x in c)
    return list(groups.values())


class ModelCounter:
    """Exact #SAT by DPLL with component decomposition and caching."""

    def __init__(self) -> None:
        self.cache: dict[frozenset, int] = {}
        self.calls = 0

    def count(self, clauses: Iterable[Clause], variables: set[int]) -> int:
        old = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old, 20000))
        try:
            return self._count([tuple(c) for c in clauses], set(variables))
        finally:
            sys.setrecursionlimit(old)

    def _count(self, clauses: list[Clause], free: set[int]) -> int:
        self.calls += 1
        res = _propagate(clauses, free)
        if res is None:
            return 0
        clauses, free = res
        if not clauses:
            return 2 ** len(free)
        total = 1
        used: set[int] = set()
        for comp, cvars in _components(clauses):
            used |= cvars
            key = frozenset(frozenset(c) for c in comp)
            n = self.cache.get(key)
            if n is None:
                n = self._branch(comp, cvars)
                self.cache[key] = n
            if n == 0:
                return 0
            total *= n
        return total * 2 ** len(free - used)

    def _branch(self, clauses: list[Clause], cvars: set[int]) -> int:
        occ: dict[int, int] = {}
        for c in clauses:
            for x in c:
                occ[abs(x)] = occ.get(abs(x), 0) + 1
        v = max(occ, key=occ.get)
        rest = cvars - {v}
        n = 0
        for lit in (v, -v):
            sub = _condition(clauses, lit)
            if sub is not None:
                n += self._count(sub, rest)
        return n


def satisfiable(clauses: Iterable[Clause], variables: set[int]) -> bool:
    return ModelCounter().count(clauses, variables) > 0


# --------------------------------------------------------------------------
# Analysis operations
# --------------------------------------------------------------------------
@dataclass
class Analysis:
    n_configurations: int
    core: list[str]
    dead: list[str]
    false_optional: list[str]
    commonality: dict[str, float]
    n_clauses: int
    n_aux_vars: int
    seconds: float

    def as_dict(self) -> dict:
        return {
            "n_configurations": self.n_configurations,
            "core": self.core,
            "dead": self.dead,
            "false_optional": self.false_optional,
            "commonality": {k: round(v, 6) for k, v in self.commonality.items()},
            "n_clauses": self.n_clauses,
            "n_aux_vars": self.n_aux_vars,
            "seconds": round(self.seconds, 3),
        }


def count_configurations(fm: FeatureModel, assume: Iterable[tuple[str, bool]] = ()) -> int:
    cnf = to_cnf(fm)
    clauses = list(cnf.clauses) + [(cnf.lit(f, v),) for f, v in assume]
    return ModelCounter().count(clauses, set(range(1, cnf.n_vars + 1)))


def analyse(fm: FeatureModel) -> Analysis:
    t0 = time.perf_counter()
    cnf = to_cnf(fm)
    allv = set(range(1, cnf.n_vars + 1))
    mc = ModelCounter()
    total = mc.count(cnf.clauses, allv)
    per: dict[str, int] = {}
    for f in fm.preorder():
        per[f] = mc.count(cnf.clauses + [(cnf.lit(f),)], allv)
    core = [f for f, n in per.items() if total and n == total]
    dead = [f for f, n in per.items() if n == 0]
    false_opt = []
    for f in fm.preorder():
        feat = fm.features[f]
        if feat.parent is None or f in core or f in dead:
            continue
        optional = fm.features[feat.parent].group is not None or feat.optional
        if optional and per[f] == per[feat.parent]:
            false_opt.append(f)
    return Analysis(
        n_configurations=total,
        core=core,
        dead=dead,
        false_optional=false_opt,
        commonality={f: (per[f] / total if total else 0.0) for f in fm.preorder()},
        n_clauses=len(cnf.clauses),
        n_aux_vars=cnf.n_vars - len(cnf.var),
        seconds=time.perf_counter() - t0,
    )


# --------------------------------------------------------------------------
# UVL export (Universal Variability Language)
# --------------------------------------------------------------------------
def _uvl_formula(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant):
        return "true" if node.value else "false"
    if isinstance(node, ast.UnaryOp):
        return "!" + _uvl_atom(node.operand)
    if isinstance(node, ast.BoolOp):
        op = " & " if isinstance(node.op, ast.And) else " | "
        return op.join(_uvl_atom(v) for v in node.values)
    if isinstance(node, ast.Call):
        a, b = (_uvl_atom(x) for x in node.args)
        return f"{a} {'=>' if node.func.id == 'implies' else '<=>'} {b}"
    raise ValueError(ast.dump(node))


def _uvl_atom(node: ast.AST) -> str:
    s = _uvl_formula(node)
    return s if isinstance(node, (ast.Name, ast.Constant, ast.UnaryOp)) else f"({s})"


def to_uvl(fm: FeatureModel, namespace: str = "AdaptiveMLOps") -> str:
    """Serialise the feature model in UVL (Sundermann et al.), readable by
    FeatureIDE, flamapy and the UVL tooling."""
    lines = [f"namespace {namespace}", "", "features"]

    def emit(name: str, depth: int) -> None:
        f = fm.features[name]
        attrs = " {abstract}" if f.abstract else ""
        lines.append("    " * depth + name + attrs)
        if not f.children:
            return
        if f.group in ("xor", "or"):
            lines.append("    " * (depth + 1) + ("alternative" if f.group == "xor" else "or"))
            for c in f.children:
                emit(c, depth + 2)
            return
        mand = [c for c in f.children if not fm.features[c].optional]
        opt = [c for c in f.children if fm.features[c].optional]
        for kw, kids in (("mandatory", mand), ("optional", opt)):
            if kids:
                lines.append("    " * (depth + 1) + kw)
                for c in kids:
                    emit(c, depth + 2)

    emit(fm.root, 1)
    if fm.constraints:
        lines += ["", "constraints"]
        for c in fm.constraints:
            lines.append("    " + _uvl_formula(c.formula._tree))
    return "\n".join(lines) + "\n"
