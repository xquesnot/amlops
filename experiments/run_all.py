"""Reproducible experiments for the companion paper.

    python experiments/run_all.py            # writes experiments/results/*.json,
                                             # paper/figures/*.pdf, paper/generated/*.tex
All inputs are in this repository; all randomness is seeded. The provider
catalogue and the environment traces are ILLUSTRATIVE/SYNTHETIC (see
src/amlops/knowledge/providers.yaml and amlops.placement.dynamic).
"""
from __future__ import annotations

import copy
import hashlib
import json
import statistics as st
import time
from pathlib import Path

from amlops import knowledge
from amlops.dsl import derive, parse_file, parse_text
from amlops.dsl.model import PipelineSpec
from amlops.generators import covered_activities, generate
from amlops.placement import evaluate_assignment, load_offers, optimise, pareto_front
from amlops.placement.dynamic import Simulator, build_workload, synthetic_environment

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "experiments" / "results"
FIG = ROOT / "paper" / "figures"
GEN = ROOT / "paper" / "generated"
for d in (RES, FIG, GEN):
    d.mkdir(parents=True, exist_ok=True)

CASES = {
    "churn": "churn_novice.amlops.yaml",
    "health": "health_triage.amlops.yaml",
    "maintenance": "predictive_maintenance_expert.amlops.yaml",
    "retail": "retail_forecast_expert.amlops.yaml",
}
MACROS: dict[str, str] = {}


def macro(name: str, value) -> None:
    MACROS[name] = str(value)


def dump(name: str, obj) -> None:
    (RES / f"{name}.json").write_text(json.dumps(obj, indent=2, default=str))


# --------------------------------------------------------------------------- E1
def e1_coverage() -> dict:
    fm = knowledge.feature_model()
    acts = knowledge.activities()
    dims = knowledge.activity_dimensions()
    covered: set[str] = set()
    per_case = {}
    for key, fname in CASES.items():
        rp = derive(parse_file(ROOT / "examples" / fname))
        files = generate(rp, optimise(rp))
        c = covered_activities(files)
        per_case[key] = sorted(c)
        covered |= c
    by_dim = {}
    for d in ("Plan", "Data", "Model", "Soft", "Ops"):
        ids = [a for a in acts if dims[a] == d]
        by_dim[d] = {"covered": sum(a in covered for a in ids), "total": len(ids),
                     "missing": [f"{a} {acts[a]}" for a in ids if a not in covered]}
    for d, v in by_dim.items():
        macro(f"Cov{d}", f"{v['covered']}/{v['total']}")
    macro("CovTotal", f"{len(covered)}/{len(acts)}")
    macro("CovNonPlan", f"{len([a for a in covered if dims[a] != 'Plan'])}/{len([a for a in acts if dims[a] != 'Plan'])}")

    # Feature effectiveness: does flipping a leaf feature change the derived
    # model or the generated artefacts?
    base_sel = knowledge.profiles()["tabular-classification-continuous"]["select"]

    def fingerprint(select, deselect):
        spec = PipelineSpec(name="probe", select=list(select), deselect=list(deselect))
        rp = derive(spec)
        d = rp.to_dict()
        d.pop("configuration")
        d.pop("findings")
        d.pop("trace")
        try:
            files = generate(rp, optimise(rp))
            art = "".join(f.content for f in files if f.path != "trace.json")
        except Exception as exc:  # infeasible placement is itself an observable effect
            art = f"infeasible:{type(exc).__name__}"
        return hashlib.sha256((json.dumps(d, sort_keys=True, default=str) + art).encode()).hexdigest()

    eff = {}
    for leaf in fm.leaves():
        on_sel = [f for f in base_sel if f != leaf] + [leaf]
        off_sel = [f for f in base_sel if f != leaf]
        try:
            fm.complete(select=on_sel)
            fm.complete(select=off_sel, deselect=[leaf])
        except ValueError:
            eff[leaf] = None  # flip not satisfiable in this context
            continue
        try:
            eff[leaf] = fingerprint(on_sel, []) != fingerprint(off_sel, [leaf])
        except Exception:
            eff[leaf] = None
    per_cat = {}
    for cat, feats in fm.categories().items():
        leaves = [f for f in feats if f in eff]
        tested = [f for f in leaves if eff[f] is not None]
        per_cat[cat] = {"leaves": len(leaves), "tested": len(tested), "effective": sum(bool(eff[f]) for f in tested),
                        "no_effect": [f for f in tested if not eff[f]]}
    tested = [f for f in eff if eff[f] is not None]
    macro("FeatLeaves", len(eff))
    macro("FeatTested", len(tested))
    macro("FeatEffective", sum(bool(eff[f]) for f in tested))
    macro("FeatNoEffect", ", ".join(sorted(f for f in tested if not eff[f])))
    out = {"activities_by_dimension": by_dim, "per_case": per_case, "feature_effect": eff, "per_category": per_cat}
    dump("e1_coverage", out)

    rows = []
    for cat in ("Functional", "Behavioral", "Technical", "DomainSpecific", "NonFunctional", "Organizational"):
        v = per_cat[cat]
        rows.append(f"{cat} & {v['leaves']} & {v['tested']} & {v['effective']} \\\\")
    (GEN / "tab_features.tex").write_text("\n".join(rows) + "\n")
    return out


# --------------------------------------------------------------------------- E2
def e2_abstraction() -> dict:
    out = {}
    rows = []
    for key, fname in CASES.items():
        spec = parse_file(ROOT / "examples" / fname)
        rp = derive(spec)
        files = generate(rp, optimise(rp))
        gen_lines = sum(f.lines for f in files if f.path != "trace.json")
        user_steps = len(spec.steps)
        out[key] = {"dsl_lines": spec.source_lines, "generated_lines": gen_lines,
                    "ratio": round(gen_lines / spec.source_lines, 1), "steps": len(rp.steps),
                    "user_step_entries": user_steps, "features": len(rp.configuration),
                    "findings": len(rp.findings), "files": len(files) - 1,
                    "locations": len({o.location for o in optimise(rp).assignment.values()})}
        o = out[key]
        rows.append(f"{key} & {o['dsl_lines']} & {o['steps']} & {o['files']} & {o['generated_lines']} & "
                    f"{o['ratio']} & {o['findings']} \\\\")
    (GEN / "tab_abstraction.tex").write_text("\n".join(rows) + "\n")
    ratios = [v["ratio"] for v in out.values()]
    macro("RatioMin", min(ratios))
    macro("RatioMax", max(ratios))
    dump("e2_abstraction", out)
    return out


# --------------------------------------------------------------------------- E3
def e3_placement() -> dict:
    offers = load_offers()
    out = {}
    rows = []
    for key, fname in CASES.items():
        rp = derive(parse_file(ROOT / "examples" / fname))
        t0 = time.perf_counter()
        opt = optimise(rp, offers)
        dt = time.perf_counter() - t0
        w = (rp.objectives.cost, rp.objectives.carbon, rp.objectives.latency)
        base = {}
        # single-provider baselines (best region and types within one provider)
        for prov in knowledge.provider_catalog()["providers"]:
            r2 = copy.deepcopy(rp)
            r2.placement.allowed_providers = [prov] if not rp.placement.allowed_providers or \
                prov in rp.placement.allowed_providers else ["__none__"]
            try:
                p = optimise(r2, offers, refs=opt.refs)
                base[prov] = {"cost": p.cost, "carbon": p.carbon_kg, "J": p.objective}
            except Exception:
                base[prov] = None
        cost_only = optimise(rp, offers, weights=(1, 0, 0), refs=opt.refs)
        co = evaluate_assignment(rp, cost_only.assignment, opt.refs, w)
        feas = {k: v for k, v in base.items() if v}
        best_single = min(feas.items(), key=lambda kv: kv[1]["J"]) if feas else None
        out[key] = {"optimised": {"cost": opt.cost, "carbon": opt.carbon_kg, "J": opt.objective,
                                  "locations": sorted(opt.locations()), "nodes": opt.nodes, "seconds": dt,
                                  "optimal": opt.optimal},
                    "cost_only": {"cost": co.cost, "carbon": co.carbon_kg, "J": co.objective},
                    "single_provider": base,
                    "best_single_provider": best_single[0] if best_single else None}
        bs = best_single[1] if best_single else None
        worst = max(feas.values(), key=lambda v: v["J"]) if feas else None
        rows.append(
            f"{key} & {len(feas)} & {opt.cost:.0f} & {opt.carbon_kg:.1f} & {opt.objective:.3f} & "
            f"{bs['J']:.3f} & {worst['J']:.3f} & {co.cost:.0f} & {co.carbon_kg:.1f} & {opt.nodes} \\\\")
    (GEN / "tab_placement.tex").write_text("\n".join(rows) + "\n")

    # carbon saving vs cost-only for the carbon-weighted cases
    sav = {k: 1 - v["optimised"]["carbon"] / v["cost_only"]["carbon"] for k, v in out.items()
           if v["cost_only"]["carbon"] > 0}
    extra = {k: v["optimised"]["cost"] / v["cost_only"]["cost"] - 1 for k, v in out.items()}
    macro("CarbonSavingChurn", f"{100 * sav['churn']:.0f}")
    macro("CostExtraChurn", f"{100 * extra['churn']:.1f}")
    macro("CarbonSavingRetail", f"{100 * sav['retail']:.0f}")
    macro("CostExtraRetail", f"{100 * extra['retail']:.1f}")

    # scalability: chains of n steps (added transform steps) on the full catalogue
    scal = []
    for n_extra in (0, 5, 10, 15, 20):
        steps = "\n".join(f"  - {{id: t{i}, kind: transform, after: {'validate' if i == 0 else f't{i-1}'}}}"
                          for i in range(n_extra))
        text = ("pipeline: scal\nprofile: tabular-classification-continuous\n"
                "objectives: {cost: 0.5, carbon: 0.5}\n" + (f"steps:\n{steps}\n" if n_extra else ""))
        rp = derive(parse_text(text))
        t0 = time.perf_counter()
        p = optimise(rp, offers)
        scal.append({"steps": len(rp.steps), "nodes": p.nodes, "seconds": round(time.perf_counter() - t0, 3),
                     "optimal": p.optimal})
    out["scalability"] = scal
    macro("ScalMaxSteps", scal[-1]["steps"])
    macro("ScalMinSteps", scal[0]["steps"])
    macro("ScalNodesMin", scal[0]["nodes"])
    macro("ScalNodesMax", scal[-1]["nodes"])
    worst = max((max(v["J"] for v in c["single_provider"].values() if v) / c["optimised"]["J"] - 1)
                for c in (out[k] for k in CASES))
    macro("WorstSinglePct", f"{100 * worst:.0f}")
    macro("MaintenanceNodes", f"{out['maintenance']['optimised']['nodes']:,}".replace(",", "\\,"))
    macro("ScalMaxSeconds", f"{max(s['seconds'] for s in scal):.2f}")

    # Pareto fronts
    fronts = {}
    for key in ("churn", "retail"):
        rp = derive(parse_file(ROOT / "examples" / CASES[key]))
        fronts[key] = [{"cost": p.cost, "carbon": p.carbon_kg, "locations": sorted(p.locations())}
                       for p in pareto_front(rp, offers, n=21)]
    out["pareto"] = fronts
    dump("e3_placement", out)
    for key, front in fronts.items():  # pgfplots data for the paper
        (GEN / f"fig_pareto_{key}.dat").write_text("cost carbon label\n" + "".join(
            f"{p['cost']:.2f} {p['carbon']:.3f} {'+'.join(loc.split('/')[1] for loc in p['locations'])}\n"
            for p in front))
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, axes = plt.subplots(1, 2, figsize=(7, 2.6))
        for ax, key in zip(axes, ("churn", "retail")):
            xs = [p["cost"] for p in fronts[key]]
            ys = [p["carbon"] for p in fronts[key]]
            ax.plot(xs, ys, "o-", ms=4)
            for p in fronts[key]:
                ax.annotate("+".join(loc.split("/")[1] for loc in p["locations"]), (p["cost"], p["carbon"]),
                            fontsize=6, xytext=(3, 3), textcoords="offset points")
            ax.set_xlabel("cost (EUR/month)")
            ax.set_ylabel("carbon (kgCO2e/month)")
            ax.set_yscale("log")
            ax.set_title(key, fontsize=9)
            ax.grid(alpha=.3)
        fig.tight_layout()
        fig.savefig(FIG / "pareto.pdf")
        plt.close(fig)
    except ImportError:
        pass
    return out


# --------------------------------------------------------------------------- E4
POLICIES = ("static", "reactive", "hysteresis", "oracle")


def _scenario(rp, exclude, seeds, weights, **kw):
    wl = build_workload(rp, exclude_locations=exclude)
    res = {p: [] for p in POLICIES}
    for s in seeds:
        env = synthetic_environment(hours=720, seed=s)
        sim = Simulator(wl, env, weights, seed=s, **kw)
        runs = {p: sim.run(p) for p in POLICIES}
        ref = runs["static"]
        for p, r in runs.items():
            J = weights[0] * r.cost_eur / ref.cost_eur + weights[1] * r.carbon_kg / ref.carbon_kg
            res[p].append({"cost": r.cost_eur, "carbon": r.carbon_kg, "migrations": r.migrations, "J": J,
                           "job_carbon": r.job_carbon})
    return res, wl


def _agg(vals):
    return {"mean": st.mean(vals), "sd": st.pstdev(vals)}


def e4_dynamic() -> dict:
    rp = derive(parse_file(ROOT / "examples" / CASES["maintenance"]))
    seeds = list(range(20))
    w = (0.5, 0.5, 0.0)
    all_locs = build_workload(rp).locations
    fr = tuple(loc for loc in all_locs if loc.endswith(("fr-par", "fr-gra", "fr-gouv", "fr-paris")))
    scenarios = {"A": (), "B": fr}
    out = {"seeds": len(seeds), "weights": w, "scenarios": {}}
    rows = []
    for name, excl in scenarios.items():
        res, wl = _scenario(rp, excl, seeds, w)
        agg = {p: {k: _agg([r[k] for r in res[p]]) for k in ("cost", "carbon", "migrations", "J", "job_carbon")}
               for p in POLICIES}
        out["scenarios"][name] = {"excluded": list(excl),
                                  "feasible_serving": [loc for loc, f in zip(wl.locations, wl.feasible) if f],
                                  "results": agg}
        for p in POLICIES:
            a = agg[p]
            rows.append(f"{name} & {p} & {a['cost']['mean']:.0f} $\\pm$ {a['cost']['sd']:.0f} & "
                        f"{a['carbon']['mean']:.1f} $\\pm$ {a['carbon']['sd']:.1f} & "
                        f"{a['migrations']['mean']:.1f} & {a['J']['mean']:.3f} & {a['job_carbon']['mean']:.2f} \\\\")
        rows.append("\\midrule")
        s, r, h, o = (agg[p] for p in POLICIES)
        macro(f"Mig{name}Reactive", f"{r['migrations']['mean']:.0f}")
        macro(f"Mig{name}Hyst", f"{h['migrations']['mean']:.1f}")
        macro(f"Mig{name}Oracle", f"{o['migrations']['mean']:.1f}")
        macro(f"Cost{name}ReactivePct", f"{100 * (r['cost']['mean'] / s['cost']['mean'] - 1):.0f}")
        macro(f"Carbon{name}ReactivePct", f"{100 * (1 - r['carbon']['mean'] / s['carbon']['mean']):.1f}")
        macro(f"Carbon{name}HystPct", f"{100 * (1 - h['carbon']['mean'] / s['carbon']['mean']):.1f}")
        macro(f"Cost{name}HystPct", f"{100 * (h['cost']['mean'] / s['cost']['mean'] - 1):.1f}")
        macro(f"J{name}Hyst", f"{h['J']['mean']:.3f}")
        macro(f"J{name}Oracle", f"{o['J']['mean']:.3f}")
        macro(f"J{name}Reactive", f"{r['J']['mean']:.3f}")
        macro(f"JobCarbon{name}Pct", f"{100 * (1 - h['job_carbon']['mean'] / s['job_carbon']['mean']):.0f}")
        macro(f"JobCarbon{name}OraclePct", f"{100 * (1 - o['job_carbon']['mean'] / s['job_carbon']['mean']):.0f}")
        macro(f"OracleGain{name}Pct", f"{100 * (1 - o['J']['mean']):.1f}")
    (GEN / "tab_dynamic.tex").write_text("\n".join(rows[:-1]) + "\n")

    # ablation on scenario B: margin and look-ahead
    abl = []
    for margin in (0.0, 0.25, 1.0):
        for look in (3, 6, 12):
            res, _ = _scenario(rp, scenarios["B"], seeds[:10], w, margin=margin, lookahead_h=look)
            abl.append({"margin": margin, "lookahead": look,
                        "J": st.mean(r["J"] for r in res["hysteresis"]),
                        "migrations": st.mean(r["migrations"] for r in res["hysteresis"])})
    out["ablation_B"] = abl
    (GEN / "tab_ablation.tex").write_text("\n".join(
        f"{a['margin']} & {a['lookahead']} & {a['J']:.3f} & {a['migrations']:.1f} \\\\" for a in abl) + "\n")
    dump("e4_dynamic", out)

    # pgfplots data: first week of scenario B, seed 0 (grid intensity of the admissible
    # serving locations and intensity experienced by the placed service)
    wl = build_workload(rp, exclude_locations=scenarios["B"])
    env = synthetic_environment(hours=720, seed=0)
    sim = Simulator(wl, env, w, seed=0)
    T = 24 * 7
    feas = [i for i, f in enumerate(wl.feasible) if f]
    trajs = {p: sim.run(p).trajectory[:T] for p in ("reactive", "hysteresis")}
    head = "hour " + " ".join(f"loc{j}" for j in range(len(feas))) + " reactive hysteresis\n"
    body = "".join(
        f"{t} " + " ".join(f"{env.grid[i, t]:.1f}" for i in feas)
        + f" {env.grid[trajs['reactive'][t], t]:.1f} {env.grid[trajs['hysteresis'][t], t]:.1f}\n"
        for t in range(T))
    (GEN / "fig_trace_B.dat").write_text(head + body)
    (GEN / "fig_trace_B_locations.txt").write_text("\n".join(wl.locations[i] for i in feas) + "\n")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        wl = build_workload(rp, exclude_locations=scenarios["B"])
        env = synthetic_environment(hours=720, seed=0)
        sim = Simulator(wl, env, w, seed=0)
        T = 24 * 7
        fig, ax = plt.subplots(figsize=(7, 2.6))
        feas = [i for i, f in enumerate(wl.feasible) if f]
        for i in feas:
            ax.plot(env.grid[i, :T], lw=1, label=wl.locations[i])
        for p, ls in (("reactive", ":"), ("hysteresis", "-")):
            traj = sim.run(p).trajectory[:T]
            ax.plot([env.grid[l, t] for t, l in enumerate(traj)], ls, color="k", lw=1.4 if p == "hysteresis" else .8,
                    label=f"{p} (placed)")
        ax.set_xlabel("hour")
        ax.set_ylabel("gCO2e/kWh")
        ax.legend(fontsize=6, ncol=2)
        ax.grid(alpha=.3)
        fig.tight_layout()
        fig.savefig(FIG / "trace_B.pdf")
        plt.close(fig)
    except ImportError:
        pass
    return out


# --------------------------------------------------------------------------- E5
def e5_analysis() -> dict:
    """Automated analysis of the feature model (exact #SAT) and completion cost."""
    from amlops.variability import analyse, to_cnf
    fm = knowledge.feature_model()
    a = analyse(fm)
    cnf = to_cnf(fm)
    leaves = fm.leaves()
    comm = sorted(a.commonality[f] for f in leaves)
    completion = {}
    for name, prof in knowledge.profiles().items():
        t0 = time.perf_counter()
        cfg = fm.complete(select=prof.get("select", []), deselect=prof.get("deselect", []))
        completion[name] = {"selected": len(cfg), "seconds": time.perf_counter() - t0}
    out = {"analysis": a.as_dict(), "n_vars": cnf.n_vars, "completion": completion,
           "leaf_commonality_min": comm[0], "leaf_commonality_max": comm[-1]}
    dump("e5_analysis", out)
    n = a.n_configurations
    exp = len(str(n)) - 1
    macro("FMConfigs", f"{n / 10 ** exp:.2f}\\times10^{{{exp}}}")
    macro("FMConfigsExact", f"{n:,}".replace(",", "\\,"))
    macro("FMCore", len(a.core))
    macro("FMDead", len(a.dead))
    macro("FMFalseOptional", len(a.false_optional))
    macro("FMClauses", a.n_clauses)
    macro("FMAux", a.n_aux_vars)
    macro("FMVars", cnf.n_vars)
    macro("FMSeconds", f"{a.seconds:.2f}")
    macro("FMLeafCommMin", f"{100 * comm[0]:.1f}")
    macro("FMLeafCommMax", f"{100 * comm[-1]:.1f}")
    macro("FMCompletionMaxMs", f"{1000 * max(c['seconds'] for c in completion.values()):.0f}")
    return out


# --------------------------------------------------------------------------- E6
def _greedy(rp, offers, refs, w):
    from amlops.placement.optimizer import evaluate_step, feasible
    assign = {}
    for s in rp.steps:
        cands = [o for o in offers if feasible(s, o, rp)]

        def score(o, s=s):
            e = evaluate_step(s, o)
            return w[0] * e.cost / refs["cost"] + w[1] * e.carbon_kg / refs["carbon"] + w[2] * e.latency / refs["latency"]
        assign[s.id] = min(cands, key=score)
    return assign


def _colocation_ok(rp, assign) -> bool:
    return all(len({assign[m].location for m in grp if m in assign}) <= 1 for grp in rp.placement.colocate)


def e6_baselines() -> dict:
    """Exact branch and bound versus a per-step greedy heuristic and the best single location."""
    offers = load_offers()
    out, rows = {}, []
    for key, fname in CASES.items():
        rp = derive(parse_file(ROOT / "examples" / fname))
        w = (rp.objectives.cost, rp.objectives.carbon, rp.objectives.latency)
        t0 = time.perf_counter()
        opt = optimise(rp, offers)
        t_exact = time.perf_counter() - t0
        t0 = time.perf_counter()
        g = _greedy(rp, offers, opt.refs, w)
        t_greedy = time.perf_counter() - t0
        pg = evaluate_assignment(rp, g, opt.refs, w)
        single = []
        for loc in sorted({o.location for o in offers}):
            r2 = copy.deepcopy(rp)
            sub = [o for o in offers if o.location == loc]
            try:
                single.append(optimise(r2, sub, refs=opt.refs))
            except Exception:
                pass
        bs = min(single, key=lambda p: p.objective)
        out[key] = {"exact": {"J": opt.objective, "seconds": t_exact, "locations": sorted(opt.locations())},
                    "greedy": {"J": pg.objective, "seconds": t_greedy, "locations": sorted(pg.locations()),
                               "egress": pg.egress_cost, "colocation_ok": _colocation_ok(rp, g)},
                    "best_single_location": {"J": bs.objective, "locations": sorted(bs.locations())}}
        gap_g = 100 * (pg.objective / opt.objective - 1)
        gap_s = 100 * (bs.objective / opt.objective - 1)
        rows.append(f"{key} & {opt.objective:.3f} & {pg.objective:.3f} & {gap_g:.1f} & "
                    f"{'yes' if _colocation_ok(rp, g) else 'no'} & {len(pg.locations())} & "
                    f"{bs.objective:.3f} & {gap_s:.1f} \\\\")
    (GEN / "tab_baselines.tex").write_text("\n".join(rows) + "\n")
    gaps = [100 * (v["greedy"]["J"] / v["exact"]["J"] - 1) for v in out.values()]
    macro("GreedyGapMax", f"{max(gaps):.1f}")
    macro("GreedyGapMin", f"{min(gaps):.1f}")
    macro("GreedyColocViol", sum(not v["greedy"]["colocation_ok"] for v in out.values()))
    dump("e6_baselines", out)
    return out


# --------------------------------------------------------------------------- E7
def _perturbed_catalog(rng, sigma: float) -> dict:
    import math
    cat = copy.deepcopy(knowledge.provider_catalog())

    def ln():
        return math.exp(rng.gauss(-sigma * sigma / 2, sigma))  # mean-preserving log-normal factor
    for t in cat["instance_types"].values():
        t["power_w"] *= ln()
    for p in cat["providers"].values():
        for r in p["regions"].values():
            r["price_factor"] = r.get("price_factor", 1.0) * ln()
            r["grid"] *= ln()
    return cat


def e7_robustness(draws: int = 100) -> dict:
    """Do the static-placement conclusions survive catalogue uncertainty?

    Prices (per region), power draws (per instance type) and grid intensities
    (per region) are multiplied by independent mean-preserving log-normal
    factors of dispersion sigma. For each draw we re-optimise and record
    (i) whether the optimal set of locations is unchanged, (ii) the regret of
    keeping the nominal placement, (iii) whether the optimum still uses a
    single provider, and (iv) the carbon saving of the carbon-aware weights
    over the cost-only placement.
    """
    import random
    nominal = load_offers()
    out, rows = {}, []
    for key, fname in CASES.items():
        rp = derive(parse_file(ROOT / "examples" / fname))
        w = (rp.objectives.cost, rp.objectives.carbon, rp.objectives.latency)
        nom = optimise(rp, nominal)
        nom_keys = {sid: o.key for sid, o in nom.assignment.items()}
        out[key] = {}
        for sigma in (0.1, 0.25, 0.5):
            rng = random.Random(f"{key}-{sigma}")
            same, regrets, single, savings = 0, [], 0, []
            for _ in range(draws):
                offers = load_offers(_perturbed_catalog(rng, sigma))
                by_key = {o.key: o for o in offers}
                opt = optimise(rp, offers)
                keep = evaluate_assignment(rp, {sid: by_key[k] for sid, k in nom_keys.items()}, opt.refs, w)
                same += opt.locations() == nom.locations()
                regrets.append(keep.objective / opt.objective - 1)
                single += len({o.provider for o in opt.assignment.values()}) == 1
                if w[1] > 0 and key in ("churn", "retail"):
                    co = optimise(rp, offers, weights=(1, 0, 0), refs=opt.refs)
                    if co.carbon_kg > 0:
                        savings.append(1 - opt.carbon_kg / co.carbon_kg)
            regrets.sort()
            res = {"p_same_locations": same / draws, "regret_mean": sum(regrets) / draws,
                   "regret_p95": regrets[int(0.95 * (draws - 1))], "regret_max": regrets[-1],
                   "p_single_provider": single / draws}
            if savings:
                savings.sort()
                res.update({"carbon_saving_median": savings[len(savings) // 2],
                            "carbon_saving_p5": savings[int(0.05 * (len(savings) - 1))]})
            out[key][str(sigma)] = res
            sav = (f"{100 * res['carbon_saving_median']:.0f} ({100 * res['carbon_saving_p5']:.0f})"
                   if savings else "n/a")
            rows.append(f"{key} & {sigma} & {100 * res['p_same_locations']:.0f} & "
                        f"{100 * res['regret_mean']:.1f} & {100 * res['regret_p95']:.1f} & "
                        f"{100 * res['p_single_provider']:.0f} & {sav} \\\\")
        rows.append("\\midrule")
    (GEN / "tab_robustness.tex").write_text("\n".join(rows[:-1]) + "\n")
    out["draws"] = draws
    dump("e7_robustness", out)
    r = {k: v for k, v in out.items() if k != "draws"}
    macro("RobDraws", draws)
    macro("RobSingleMinHalf", f"{100 * min(v['0.5']['p_single_provider'] for v in r.values()):.0f}")
    macro("RobSameMinQuarter", f"{100 * min(v['0.25']['p_same_locations'] for v in r.values()):.0f}")
    macro("RobSameMaxQuarter", f"{100 * max(v['0.25']['p_same_locations'] for v in r.values()):.0f}")
    macro("RobRegretMaxMeanQuarter", f"{100 * max(v['0.25']['regret_mean'] for v in r.values()):.1f}")
    macro("RobRegretMaxPNinetyFiveHalf", f"{100 * max(v['0.5']['regret_p95'] for v in r.values()):.1f}")
    macro("RobSavingChurnHalf", f"{100 * r['churn']['0.5']['carbon_saving_median']:.0f}")
    macro("RobSavingRetailHalf", f"{100 * r['retail']['0.5']['carbon_saving_median']:.0f}")
    return out


def main() -> None:
    t0 = time.time()
    fm = knowledge.feature_model()
    macro("NFeatures", len(fm.features))
    macro("NLeaves", len(fm.leaves()))
    macro("NConstraints", len(fm.constraints))
    macro("NRules", len(knowledge.best_practices()))
    macro("NProfiles", len(knowledge.profiles()))
    macro("NOffers", len(load_offers()))
    macro("NLocations", len({o.location for o in load_offers()}))
    print("E1 coverage ...")
    e1_coverage()
    print("E2 abstraction ...")
    e2_abstraction()
    print("E3 placement ...")
    e3_placement()
    print("E4 dynamic ...")
    e4_dynamic()
    print("E5 feature-model analysis ...")
    e5_analysis()
    print("E6 placement baselines ...")
    e6_baselines()
    print("E7 robustness to catalogue uncertainty ...")
    e7_robustness()
    (GEN / "macros.tex").write_text("\n".join(f"\\newcommand{{\\{k}}}{{{v}}}" for k, v in sorted(MACROS.items())) + "\n")
    print(f"done in {time.time() - t0:.0f}s; macros: {len(MACROS)}")


if __name__ == "__main__":
    main()
