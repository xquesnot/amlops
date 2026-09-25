"""MAPE-K adaptation loop tying monitoring, contracts and re-placement.

Monitor  : drift detectors on production batches (O7);
Analyse  : decide whether a trigger of the resolved pipeline fires;
Plan     : open a ticket under the trigger's coordination contract and, if
           the environment changed, compute a new placement;
Execute  : regenerate IaC/PaC for the new placement (diff-able artefacts);
Knowledge: the resolved pipeline, its contracts, the provider catalogue.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from amlops.dsl.model import ResolvedPipeline
from amlops.placement.optimizer import Offer, Placement, evaluate_assignment, optimise

from .contracts import Ticket
from .drift import DriftDetector, DriftReport


@dataclass
class LoopDecision:
    drift: list[DriftReport]
    ticket: Optional[Ticket] = None
    new_placement: Optional[Placement] = None
    replacement_gain: float = 0.0
    notes: list[str] = field(default_factory=list)


class AdaptationLoop:
    _ids = itertools.count(1)

    def __init__(self, rp: ResolvedPipeline, placement: Placement, migration_margin: float = 0.05):
        self.rp, self.placement, self.margin = rp, placement, migration_margin
        drift_triggers = [t for t in rp.triggers if t.type == "drift"]
        t = drift_triggers[0] if drift_triggers else None
        self.trigger = t
        self.detector = DriftDetector(t.detector or "psi", t.threshold or 0.2) if t else None
        self.contracts = {c.id: c for c in rp.contracts}

    def step(self, reference: dict[str, np.ndarray], batch: dict[str, np.ndarray],
             offers_now: Optional[list[Offer]] = None, context: Optional[dict] = None) -> LoopDecision:
        reports = self.detector.check(reference, batch) if self.detector else []
        dec = LoopDecision(reports)
        if any(r.drift for r in reports) and self.trigger is not None:
            contract = self.contracts[self.trigger.contract or "drift-response"]
            dec.ticket = Ticket.open(f"T{next(self._ids)}", contract, context or {})
            dec.notes.append(f"drift on {[r.feature for r in reports if r.drift]} -> ticket under {contract.id}")
        if offers_now is not None:
            w = (self.rp.objectives.cost, self.rp.objectives.carbon, self.rp.objectives.latency)
            refs = self.placement.refs
            by_key = {o.key: o for o in offers_now}
            current = {sid: by_key[o.key] for sid, o in self.placement.assignment.items()}
            now = evaluate_assignment(self.rp, current, refs, w)
            cand = optimise(self.rp, offers_now, refs=refs)
            gain = (now.objective - cand.objective) / now.objective
            dec.replacement_gain = gain
            if gain > self.margin:
                dec.new_placement = cand
                dec.notes.append(f"re-placement recommended (relative objective gain {gain:.1%})")
        return dec
