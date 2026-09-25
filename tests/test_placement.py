import itertools

import pytest

from amlops.dsl import derive, parse_file, parse_text
from amlops.placement import Infeasible, evaluate_assignment, feasible, load_offers, optimise, pareto_front


def test_branch_and_bound_matches_brute_force():
    rp = derive(parse_text(
        'pipeline: tiny\nprofile: timeseries-forecast-batch\n'
        'steps: [{id: impute, remove: true}, {id: evaluate, remove: true}, {id: register, remove: true}]\n'
        'objectives: {cost: 0.5, carbon: 0.5}\n'))
    offers = [o for o in load_offers() if o.provider in ("scaleway", "ovhcloud")]
    best = optimise(rp, offers)
    order = rp.topological_order()
    steps = {s.id: s for s in rp.steps}
    cands = [[o for o in offers if feasible(steps[s], o, rp)] for s in order]
    w = (rp.objectives.cost, rp.objectives.carbon, rp.objectives.latency)
    brute = min(evaluate_assignment(rp, dict(zip(order, combo)), best.refs, w).objective
                for combo in itertools.product(*cands))
    assert best.optimal and best.objective == pytest.approx(brute, rel=1e-9)


def test_labels_restrict_providers(examples_dir):
    rp = derive(parse_file(examples_dir / "health_triage.amlops.yaml"))
    p = optimise(rp)
    assert all("HDS" in o.labels for o in p.assignment.values())


def test_infeasible_constraints_raise():
    rp = derive(parse_text('pipeline: x\nprofile: timeseries-forecast-batch\n'
                           'placement: {allowed_providers: [scaleway], required_labels: [SecNumCloud]}\n'))
    with pytest.raises(Infeasible):
        optimise(rp)


def test_colocation_respected(examples_dir):
    rp = derive(parse_file(examples_dir / "predictive_maintenance_expert.amlops.yaml"))
    p = optimise(rp)
    assert p.assignment["serve"].location == p.assignment["monitor"].location
    assert p.assignment["serve"].latency_ms <= rp.placement.max_latency_ms


def test_pareto_front_is_non_dominated(examples_dir):
    rp = derive(parse_file(examples_dir / "retail_forecast_expert.amlops.yaml"))
    front = pareto_front(rp)
    for a in front:
        for b in front:
            assert not (b.cost < a.cost * 0.999 and b.carbon_kg < a.carbon_kg * 0.999)


def test_carbon_weight_moves_away_from_high_carbon_grid():
    base = 'pipeline: x\nprofile: timeseries-forecast-batch\nplacement: {allowed_providers: [scaleway]}\n'
    cheap = optimise(derive(parse_text(base + 'objectives: {cost: 1, carbon: 0}\n')))
    green = optimise(derive(parse_text(base + 'objectives: {cost: 0, carbon: 1}\n')))
    assert green.carbon_kg < cheap.carbon_kg and cheap.cost <= green.cost
