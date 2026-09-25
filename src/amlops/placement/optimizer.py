"""Multi-provider placement of pipeline steps.

Problem. Given a resolved pipeline (steps with resource needs and monthly
usage, data edges with volumes) and a catalogue of offers
(provider x region x instance type), choose one offer per step minimising

    J = w_cost * C / C_ref + w_carbon * G / G_ref + w_latency * L / L_ref

where C includes compute and inter-provider egress, G is the location-based
operational carbon (kWh x PUE x grid intensity) and L the user-facing
latency of serving steps. Hard constraints: resource fit, required labels
(EU, SecNumCloud, HDS...), allowed providers, pins, co-location groups,
maximum latency. References *_ref are per-step lower bounds, so each term is
>= 1 and J is dimensionless.

Solver. Depth-first branch and bound over steps in topological order with an
admissible bound (partial cost + sum of per-step standalone minima; egress
costs are non-negative), hence exact. A node budget guards pathological
catalogues; when it is hit the incumbent is returned and flagged.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from amlops import knowledge
from amlops.dsl.model import ResolvedPipeline, Step

SERVING_KINDS = {"serve"}


@dataclass(frozen=True)
class Offer:
    provider: str
    region: str
    itype: str
    vcpu: int
    mem_gb: int
    gpu: int
    price_h: float
    power_w: float
    grid: float
    pue: float
    latency_ms: float
    egress_eur_gb: float
    labels: frozenset

    @property
    def location(self) -> str:
        return f"{self.provider}/{self.region}"

    @property
    def key(self) -> str:
        return f"{self.provider}/{self.region}/{self.itype}"


def load_offers(catalog: Optional[dict] = None, conditions: Optional[dict] = None) -> list[Offer]:
    """Expand the catalogue into offers.

    ``conditions`` optionally overrides time-varying values per location,
    e.g. ``{"scaleway/fr-par": {"grid": 42, "price_factor": 1.1}}``.
    """
    cat = catalog or knowledge.provider_catalog()
    conditions = conditions or {}
    offers = []
    for pname, p in cat["providers"].items():
        for rname, r in p["regions"].items():
            loc = f"{pname}/{rname}"
            cond = conditions.get(loc, {})
            labels = frozenset(r.get("labels_override", p.get("labels", [])))
            for tname, t in cat["instance_types"].items():
                price = t["base_price"] * p["price_factor"] * r.get("price_factor", 1.0) * cond.get("price_factor", 1.0)
                offers.append(Offer(pname, rname, tname, t["vcpu"], t["mem_gb"], t["gpu"], round(price, 5),
                                    t["power_w"], cond.get("grid", r["grid"]), r["pue"], r["latency_ms"],
                                    p["egress_eur_gb"], labels))
    return offers


@dataclass
class StepEval:
    cost: float
    carbon_kg: float
    latency: float


def evaluate_step(step: Step, offer: Offer) -> StepEval:
    n = max(1, math.ceil(max(step.resources.vcpu / offer.vcpu, step.resources.mem_gb / offer.mem_gb,
                             (step.resources.gpu / offer.gpu) if offer.gpu else 0)))
    hours = step.monthly_hours() * n
    cost = offer.price_h * hours
    kwh = offer.power_w / 1000.0 * hours * offer.pue
    carbon = kwh * offer.grid / 1000.0
    latency = offer.latency_ms if step.kind in SERVING_KINDS else 0.0
    return StepEval(cost, carbon, latency)


def feasible(step: Step, offer: Offer, rp: ResolvedPipeline) -> bool:
    pc = rp.placement
    if step.resources.gpu > 0 and offer.gpu == 0:
        return False
    if step.resources.gpu == 0 and offer.gpu > 0:
        return False  # do not waste accelerators on CPU steps
    if pc.allowed_providers and offer.provider not in pc.allowed_providers:
        return False
    if not set(pc.required_labels) <= offer.labels:
        return False
    pin = pc.pin.get(step.id)
    if pin and not (offer.location == pin or offer.key == pin):
        return False
    if step.kind in SERVING_KINDS and pc.max_latency_ms is not None and offer.latency_ms > pc.max_latency_ms:
        return False
    return True


@dataclass
class Placement:
    assignment: dict[str, Offer]
    cost: float
    carbon_kg: float
    latency: float
    egress_cost: float
    objective: float
    optimal: bool
    nodes: int
    refs: dict[str, float] = field(default_factory=dict)

    def locations(self) -> set[str]:
        return {o.location for o in self.assignment.values()}

    def summary(self) -> dict:
        return {
            "objective": round(self.objective, 4),
            "cost_eur_month": round(self.cost, 2),
            "egress_eur_month": round(self.egress_cost, 2),
            "carbon_kg_month": round(self.carbon_kg, 2),
            "serving_latency_ms": self.latency,
            "optimal": self.optimal,
            "nodes": self.nodes,
            "providers": sorted(self.locations()),
            "assignment": {k: v.key for k, v in self.assignment.items()},
        }


class Infeasible(RuntimeError):
    pass


def optimise(
    rp: ResolvedPipeline,
    offers: Optional[list[Offer]] = None,
    weights: Optional[tuple[float, float, float]] = None,
    refs: Optional[dict[str, float]] = None,
    node_budget: int = 2_000_000,
) -> Placement:
    offers = offers if offers is not None else load_offers()
    obj = rp.objectives
    w_c, w_g, w_l = weights if weights is not None else (obj.cost, obj.carbon, obj.latency)
    order = rp.topological_order()
    steps = {s.id: s for s in rp.steps}

    cands: dict[str, list[tuple[Offer, StepEval]]] = {}
    for sid in order:
        cs = [(o, evaluate_step(steps[sid], o)) for o in offers if feasible(steps[sid], o, rp)]
        if not cs:
            raise Infeasible(f"No feasible offer for step {sid!r} under constraints "
                             f"labels={rp.placement.required_labels} providers={rp.placement.allowed_providers}")
        cands[sid] = cs

    if refs is None:
        refs = {
            "cost": sum(min(e.cost for _, e in cands[s]) for s in order) or 1.0,
            "carbon": sum(min(e.carbon_kg for _, e in cands[s]) for s in order) or 1.0,
            "latency": sum(min(e.latency for _, e in cands[s]) for s in order) or 1.0,
        }

    def score(e: StepEval) -> float:
        return w_c * e.cost / refs["cost"] + w_g * e.carbon_kg / refs["carbon"] + w_l * e.latency / refs["latency"]

    # Location-level dominance: egress and co-location only depend on the
    # location, so per step only the best offer of each location can be optimal.
    for sid in order:
        best_at: dict[str, tuple[Offer, StepEval]] = {}
        for o, e in cands[sid]:
            cur = best_at.get(o.location)
            if cur is None or score(e) < score(cur[1]):
                best_at[o.location] = (o, e)
        cands[sid] = sorted(best_at.values(), key=lambda oe: score(oe[1]))
    suffix_lb = [0.0] * (len(order) + 1)
    for i in range(len(order) - 1, -1, -1):
        suffix_lb[i] = suffix_lb[i + 1] + score(cands[order[i]][0][1])

    preds = {sid: steps[sid].depends_on for sid in order}
    group_of: dict[str, int] = {}
    for gi, grp in enumerate(rp.placement.colocate):
        for sid in grp:
            if sid in steps:
                group_of[sid] = gi

    def egress(a: str, oa: Offer, ob: Offer) -> float:
        if oa.location == ob.location:
            return 0.0
        sa = steps[a]
        gb = sa.output_gb * (sa.runs_per_month if not sa.continuous else 1.0)
        return gb * oa.egress_eur_gb

    best: dict = {"J": math.inf, "assign": None}
    assign: dict[str, Offer] = {}
    nodes = 0
    budget_hit = False

    def dfs(i: int, acc: float) -> None:
        nonlocal nodes, budget_hit
        nodes += 1
        if nodes > node_budget:
            budget_hit = True
            return
        if acc + suffix_lb[i] >= best["J"] - 1e-12:
            return
        if i == len(order):
            best["J"], best["assign"] = acc, dict(assign)
            return
        sid = order[i]
        for o, e in cands[sid]:
            g = group_of.get(sid)
            if g is not None:
                mates = [m for m in rp.placement.colocate[g] if m in assign]
                if any(assign[m].location != o.location for m in mates):
                    continue
            eg = sum(egress(p, assign[p], o) for p in preds[sid])
            assign[sid] = o
            dfs(i + 1, acc + score(e) + w_c * eg / refs["cost"])
            del assign[sid]
            if budget_hit:
                return

    dfs(0, 0.0)
    if best["assign"] is None:
        raise Infeasible("No placement satisfies co-location and other constraints")
    return _totals(rp, best["assign"], refs, (w_c, w_g, w_l), optimal=not budget_hit, nodes=nodes)


def _totals(rp: ResolvedPipeline, assign: dict[str, Offer], refs: dict, w: tuple, optimal: bool,
            nodes: int) -> Placement:
    steps = {s.id: s for s in rp.steps}
    cost = carbon = lat = eg = 0.0
    for sid, o in assign.items():
        e = evaluate_step(steps[sid], o)
        cost += e.cost
        carbon += e.carbon_kg
        lat += e.latency
        for p in steps[sid].depends_on:
            if assign[p].location != o.location:
                sp = steps[p]
                gb = sp.output_gb * (sp.runs_per_month if not sp.continuous else 1.0)
                eg += gb * assign[p].egress_eur_gb
    J = w[0] * (cost + eg) / refs["cost"] + w[1] * carbon / refs["carbon"] + w[2] * lat / refs["latency"]
    return Placement(assign, cost + eg, carbon, lat, eg, J, optimal, nodes, dict(refs))


def evaluate_assignment(rp: ResolvedPipeline, assign: dict[str, Offer], refs: dict,
                        weights: tuple[float, float, float]) -> Placement:
    return _totals(rp, assign, refs, weights, optimal=False, nodes=0)


def pareto_front(rp: ResolvedPipeline, offers: Optional[list[Offer]] = None, n: int = 11) -> list[Placement]:
    """Cost/carbon trade-off by weight sweep (latency weight kept as specified)."""
    offers = offers if offers is not None else load_offers()
    wl = rp.objectives.latency
    ref = optimise(rp, offers).refs
    sols = []
    for k in range(n):
        a = k / (n - 1)
        sols.append(optimise(rp, offers, weights=((1 - wl) * (1 - a), (1 - wl) * a, wl), refs=ref))
    front: list[Placement] = []
    eps = 1e-3  # relative tolerance: near-identical solutions are merged
    for s in sols:
        dominated = any(o.cost <= s.cost * (1 + eps) and o.carbon_kg <= s.carbon_kg * (1 + eps) and
                        (o.cost < s.cost * (1 - eps) or o.carbon_kg < s.carbon_kg * (1 - eps)) for o in sols)
        dup = any(abs(f.cost - s.cost) <= eps * s.cost and abs(f.carbon_kg - s.carbon_kg) <= eps * s.carbon_kg
                  for f in front)
        if not dominated and not dup:
            front.append(s)
    return sorted(front, key=lambda p: p.cost)
