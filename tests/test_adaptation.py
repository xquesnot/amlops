import numpy as np
import pytest

from amlops.adaptation import AdaptationLoop, GateError, Ticket, ks_2samp, psi
from amlops.dsl import derive, parse_file
from amlops.placement import load_offers, optimise
from amlops.placement.dynamic import Simulator, build_workload, synthetic_environment


def test_psi_and_ks_detect_shift():
    rng = np.random.default_rng(0)
    ref, same, shifted = rng.normal(0, 1, 5000), rng.normal(0, 1, 5000), rng.normal(0.8, 1, 5000)
    assert psi(ref, same) < 0.05 < 0.2 < psi(ref, shifted)
    d0, p0 = ks_2samp(ref, same)
    d1, p1 = ks_2samp(ref, shifted)
    assert p0 > 0.01 and p1 < 1e-6 and d1 > d0


def test_contract_gates_enforced_and_conditional_skip(examples_dir):
    rp = derive(parse_file(examples_dir / "health_triage.amlops.yaml"))
    c = next(c for c in rp.contracts if c.id == "drift-response")
    t = Ticket.open("T1", c, {"model": {"input_schema": "v1"}, "api": {"current_schema": "v1"}}, clock=lambda: 0)
    with pytest.raises(GateError):
        t.submit("ProductOwner", {"decision": "retrain"})               # approval missing
    t.submit("ProductOwner", {"decision": "retrain"}, approved=True, clock=lambda: 1)
    with pytest.raises(GateError):
        t.submit("ModelEngineer", {"model": "m"})                       # wrong owner
    t.submit("DataEngineer", {"dataset": "dataset-v3"}, clock=lambda: 2)
    t.submit("ModelEngineer", {"model": "model-v2.1", "evaluation_report": "r", "explanations": "e"}, clock=lambda: 3)
    assert t.current.id == "G5-deploy"                                  # G4 skipped (schema unchanged)
    t.submit("OperationsEngineer", {"deployment": "d"}, approved=True, clock=lambda: 4)
    assert t.closed and [e.kind for e in t.trace].count("skip") == 1


def test_loop_opens_ticket_on_drift(examples_dir):
    rp = derive(parse_file(examples_dir / "churn_novice.amlops.yaml"))
    loop = AdaptationLoop(rp, optimise(rp))
    rng = np.random.default_rng(1)
    ref = {"tenure": rng.normal(0, 1, 3000)}
    assert loop.step(ref, {"tenure": rng.normal(0, 1, 3000)}).ticket is None
    dec = loop.step(ref, {"tenure": rng.normal(1.0, 1, 3000)})
    assert dec.ticket is not None and dec.ticket.contract.id == "drift-response"


def test_loop_recommends_replacement_on_carbon_change(examples_dir):
    rp = derive(parse_file(examples_dir / "retail_forecast_expert.amlops.yaml"))
    p = optimise(rp)
    here = p.assignment["train"].location
    worse = load_offers(conditions={here: {"grid": 900}})
    dec = AdaptationLoop(rp, p).step({}, {}, offers_now=worse)
    assert dec.new_placement is not None and dec.new_placement.assignment["train"].location != here


def test_simulation_policies_ordering(examples_dir):
    rp = derive(parse_file(examples_dir / "predictive_maintenance_expert.amlops.yaml"))
    sim = Simulator(build_workload(rp), synthetic_environment(hours=240, seed=3), (0.5, 0.5, 0.0), seed=3)
    res = {p: sim.run(p) for p in ("static", "reactive", "hysteresis", "oracle")}
    assert res["hysteresis"].migrations <= res["reactive"].migrations
    assert res["reactive"].migrations >= res["oracle"].migrations
