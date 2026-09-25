"""Command-line interface: ``amlops <command> model.amlops.yaml``."""
from __future__ import annotations

import argparse
import json
import sys

from amlops import __version__, knowledge
from amlops.dsl import DerivationError, DSLError, derive, parse_file


def _load(path: str):
    return derive(parse_file(path))


def cmd_validate(a: argparse.Namespace) -> int:
    rp = _load(a.model)
    print(f"OK  {rp.name}: {len(rp.steps)} steps, {len(rp.triggers)} triggers, "
          f"{len(rp.configuration)} features selected")
    for f in rp.findings:
        print(f"  [{f['severity']}] {f['rule']}: {f['advice']}")
    return 0


def cmd_derive(a: argparse.Namespace) -> int:
    rp = _load(a.model)
    print(json.dumps(rp.to_dict(), indent=2, default=str))
    return 0


def cmd_place(a: argparse.Namespace) -> int:
    from amlops.placement import optimise, pareto_front

    rp = _load(a.model)
    p = optimise(rp)
    out = {"placement": p.summary()}
    if a.pareto:
        out["pareto"] = [{"cost": round(s.cost, 2), "carbon_kg": round(s.carbon_kg, 2),
                          "locations": sorted(s.locations())} for s in pareto_front(rp)]
    print(json.dumps(out, indent=2))
    return 0


def cmd_generate(a: argparse.Namespace) -> int:
    from amlops.generators import generate
    from amlops.placement import optimise

    rp = _load(a.model)
    files = generate(rp, optimise(rp), a.output, a.target or None)
    for f in files:
        print(f"  {a.output}/{f.path}  ({f.lines} lines)")
    return 0


def cmd_simulate(a: argparse.Namespace) -> int:
    from amlops.placement.dynamic import Simulator, build_workload, synthetic_environment

    rp = _load(a.model)
    wl = build_workload(rp)
    env = synthetic_environment(hours=a.hours, seed=a.seed)
    w = (rp.objectives.cost, rp.objectives.carbon, rp.objectives.latency)
    sim = Simulator(wl, env, w, seed=a.seed)
    print(json.dumps([sim.run(p).as_dict() for p in ("static", "reactive", "hysteresis", "oracle")], indent=2))
    return 0


def cmd_knowledge(a: argparse.Namespace) -> int:
    fm = knowledge.feature_model()
    print(json.dumps({
        "features": len(fm.features), "leaves": len(fm.leaves()), "constraints": len(fm.constraints),
        "categories": {k: len(v) for k, v in fm.categories().items()},
        "profiles": sorted(knowledge.profiles()),
        "best_practices": [r["id"] for r in knowledge.best_practices()],
        "activities": len(knowledge.activities()),
    }, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="amlops", description="AdaptiveMLOps DSL toolchain (GetCaaS prototype)")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn, hlp in [("validate", cmd_validate, "check a model and print best-practice findings"),
                          ("derive", cmd_derive, "print the resolved pipeline"),
                          ("place", cmd_place, "optimise multi-provider placement"),
                          ("generate", cmd_generate, "emit Terraform / Kubernetes / CI artefacts"),
                          ("simulate", cmd_simulate, "simulate dynamic re-deployment policies")]:
        p = sub.add_parser(name, help=hlp)
        p.add_argument("model")
        p.set_defaults(fn=fn)
        if name == "place":
            p.add_argument("--pareto", action="store_true")
        if name == "generate":
            p.add_argument("-o", "--output", default="generated")
            p.add_argument("-t", "--target", action="append", choices=["terraform", "kubernetes", "cicd"])
        if name == "simulate":
            p.add_argument("--hours", type=int, default=720)
            p.add_argument("--seed", type=int, default=0)
    k = sub.add_parser("knowledge", help="summarise the packaged domain knowledge")
    k.set_defaults(fn=cmd_knowledge)
    a = ap.parse_args(argv)
    try:
        return a.fn(a)
    except (DSLError, DerivationError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
