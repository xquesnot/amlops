"""Executable semantics for SkeltyMLOps coordination contracts.

Daoud et al. (FGCS 2026, Sect. 5.8) define tickets, events and coordination
contracts: a ticket advances through the gates of a contract only when the
required artefacts and approvals are recorded; conditional gates route the
ticket; every hand-off is recorded for traceability. This module gives those
concepts an operational semantics so that the contracts generated from the
DSL can be executed by an orchestrator (MPO) and audited afterwards.
"""
from __future__ import annotations

import ast
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from amlops.dsl.model import Contract, Gate


class GateError(RuntimeError):
    pass


def _eval_condition(expr: str, ctx: dict[str, Any]) -> bool:
    """Evaluate ``a.b != c.d`` style comparisons on a nested context, safely."""
    tree = ast.parse(expr, mode="eval").body

    def val(node: ast.AST) -> Any:
        if isinstance(node, ast.Attribute):
            return val(node.value)[node.attr]
        if isinstance(node, ast.Name):
            return ctx[node.id]
        if isinstance(node, ast.Constant):
            return node.value
        raise ValueError(f"Unsupported condition element {ast.dump(node)}")

    if not (isinstance(tree, ast.Compare) and len(tree.ops) == 1):
        raise ValueError(f"Condition must be a single comparison: {expr}")
    left, right = val(tree.left), val(tree.comparators[0])
    op = tree.ops[0]
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.Lt):
        return left < right
    raise ValueError(f"Unsupported operator in {expr}")


@dataclass
class TraceEvent:
    t: float
    gate: str
    actor: str
    kind: str                      # handoff | skip | approval | open | close
    artefacts: dict[str, str] = field(default_factory=dict)
    note: str = ""


@dataclass
class Ticket:
    id: str
    contract: Contract
    context: dict[str, Any] = field(default_factory=dict)
    position: int = 0
    artefacts: dict[str, str] = field(default_factory=dict)
    trace: list[TraceEvent] = field(default_factory=list)
    closed: bool = False

    @classmethod
    def open(cls, tid: str, contract: Contract, context: Optional[dict] = None, clock=time.time) -> "Ticket":
        t = cls(tid, contract, dict(context or {}))
        t.trace.append(TraceEvent(clock(), "-", "MPO", "open", note=f"contract {contract.id} v{contract.version}"))
        t._skip_conditionals(clock)
        return t

    @property
    def current(self) -> Optional[Gate]:
        return None if self.closed else self.contract.gates[self.position]

    def _skip_conditionals(self, clock) -> None:
        while not self.closed:
            g = self.contract.gates[self.position]
            if g.condition and not _eval_condition(g.condition, self.context):
                self.trace.append(TraceEvent(clock(), g.id, g.actor, "skip", note=f"condition false: {g.condition}"))
                self._next(clock)
            else:
                return

    def _next(self, clock) -> None:
        self.position += 1
        if self.position >= len(self.contract.gates):
            self.closed = True
            self.trace.append(TraceEvent(clock(), "-", "MPO", "close"))

    def submit(self, actor: str, artefacts: dict[str, str], approved: bool = False, clock=time.time) -> None:
        g = self.current
        if g is None:
            raise GateError(f"Ticket {self.id} is closed")
        if actor != g.actor:
            raise GateError(f"Gate {g.id} is owned by {g.actor}, not {actor}")
        missing = [a for a in g.produces if a not in artefacts]
        if missing:
            raise GateError(f"Gate {g.id} requires artefacts {missing}")
        if g.approval and not approved:
            raise GateError(f"Gate {g.id} requires an explicit approval by {g.actor}")
        self.artefacts.update(artefacts)
        self.trace.append(TraceEvent(clock(), g.id, actor, "approval" if g.approval else "handoff", dict(artefacts)))
        self._next(clock)
        self._skip_conditionals(clock)

    def to_json(self) -> str:
        return json.dumps({
            "ticket": self.id, "contract": self.contract.id, "closed": self.closed,
            "artefacts": self.artefacts,
            "trace": [e.__dict__ for e in self.trace],
        }, indent=2)
