"""E8: the price of sovereignty, on public data (companion study for ICT4S).

    python experiments/sovereignty.py
        -> experiments/results/e8_sovereignty.json
        -> paper/generated/sov_*.tex, sov_*.dat and sov_results.tex

Inputs are the public snapshot in data/public-2026-09-29 (prices, labels,
BoaviztAPI power and embedded emissions, Ember 2025 annual grid intensity,
RTE eCO2mix 2025 hourly CO2 rate). Nothing here uses the illustrative
catalogue of src/amlops/knowledge/providers.yaml.

Questions:
  RQ1 cost premium of each sovereignty level (cost-optimal placements);
  RQ2 carbon of each level (carbon-optimal and balanced placements);
  RQ3 share of embedded emissions and placements that change when they count;
  RQ4 saving of temporal shifting of deferrable jobs on the real French grid.
"""
from __future__ import annotations

import csv
import json
import statistics as st
from datetime import datetime
from pathlib import Path

import yaml

from amlops.dsl import derive, parse_file
from amlops.dsl.model import Resources
from amlops.placement.optimizer import Infeasible, Offer, evaluate_step, optimise

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "public-2026-09-29"
RES = ROOT / "experiments" / "results"
GEN = ROOT / "paper" / "generated"
CASES = {
    "churn": "churn_novice.amlops.yaml",
    "health": "health_triage.amlops.yaml",
    "maintenance": "predictive_maintenance_expert.amlops.yaml",
    "retail": "retail_forecast_expert.amlops.yaml",
}
WEIGHTS = {"cost": (1.0, 0.0, 0.0), "carbon": (0.0, 1.0, 0.0), "balanced": (0.5, 0.5, 0.0)}
MACROS: dict[str, str] = {}


DIGITS = {"0": "Zero", "1": "One", "2": "Two", "3": "Three", "4": "Four"}


def macro(name: str, value) -> None:
    """Register a LaTeX macro; digits (e.g. level names L0..L4) are spelled out."""
    MACROS["Sov" + "".join(DIGITS.get(c, c) for c in name)] = str(value)


def load_catalogue():
    return yaml.safe_load((DATA / "catalogue.yaml").read_text(encoding="utf-8"))


def build_offers(cat: dict, embedded: bool) -> list[Offer]:
    grid = cat["grid_gco2_per_kwh"]["values"]
    pue = cat["pue_assumed"]
    out = []
    for o in cat["offers"]:
        g = grid[o["country"]]
        if embedded:  # fold the embedded kgCO2e/h into an offer-specific effective intensity
            g = g + o["embedded_kgco2e_per_hour"] * 1e6 / (o["avg_power_w"] * pue)
        out.append(Offer(o["provider"], o["region"], o["class"], o["vcpu"], o["mem_gb"], o["gpu"],
                         o["eur_per_hour"], o["avg_power_w"], g, pue, 0.0,
                         cat["egress_eur_per_gb"][o["provider"]], frozenset(o["labels"])))
    return out


def prepare(case: str, level_labels: list[str]):
    rp = derive(parse_file(ROOT / "examples" / CASES[case]))
    for s in rp.steps:  # size GPU steps to the smallest common L4 host (4 vCPU / 16 GB)
        if s.resources.gpu:
            s.resources = Resources(vcpu=4, mem_gb=16, gpu=1)
    rp.placement.allowed_providers = []
    rp.placement.pin = {}
    rp.placement.max_latency_ms = None
    rp.placement.required_labels = list(level_labels)
    return rp


def split_carbon(rp, placement, cat) -> tuple[float, float]:
    """Operational and embedded kgCO2e/month of a placement."""
    grid, pue = cat["grid_gco2_per_kwh"]["values"], cat["pue_assumed"]
    emb = {(o["provider"], o["region"], o["class"]): o for o in cat["offers"]}
    op = em = 0.0
    steps = {s.id: s for s in rp.steps}
    for sid, off in placement.assignment.items():
        e = evaluate_step(steps[sid], off)
        hours = e.cost / off.price_h if off.price_h else 0.0
        o = emb[(off.provider, off.region, off.itype)]
        op += off.power_w / 1000 * hours * pue * grid[o["country"]] / 1000
        em += hours * o["embedded_kgco2e_per_hour"]
    return op, em


def placements(cat: dict) -> dict:
    levels = cat["levels"]
    offers_emb = build_offers(cat, embedded=True)
    offers_op = build_offers(cat, embedded=False)
    out = {}
    for case in CASES:
        out[case] = {}
        for lv, spec in levels.items():
            out[case][lv] = {}
            for wname, w in WEIGHTS.items():
                rp = prepare(case, spec["required"])
                try:
                    p = optimise(rp, offers_emb, weights=w)
                except Infeasible:
                    out[case][lv][wname] = None
                    continue
                op, em = split_carbon(rp, p, cat)
                rec = {"cost": p.cost, "carbon_op": op, "carbon_emb": em, "carbon": op + em,
                       "locations": sorted(p.locations()), "optimal": p.optimal}
                if wname in ("carbon", "balanced"):  # RQ3: same problem without embedded emissions
                    p2 = optimise(prepare(case, spec["required"]), offers_op, weights=w)
                    rec["locations_without_embedded"] = sorted(p2.locations())
                out[case][lv][wname] = rec
    return out


def sensitivity(cat: dict) -> dict:
    """Cost premium of each level when the cheapest providers are unavailable."""
    offers = build_offers(cat, embedded=True)
    out = {}
    for excl in ((), ("ovhcloud",), ("scaleway",), ("ovhcloud", "scaleway")):
        subset = [o for o in offers if o.provider not in excl]
        key = "+".join(excl) or "none"
        out[key] = {}
        for case in CASES:
            costs = {}
            for lv, spec in cat["levels"].items():
                try:
                    costs[lv] = optimise(prepare(case, spec["required"]), subset, weights=WEIGHTS["cost"]).cost
                except Infeasible:
                    costs[lv] = None
            out[key][case] = {lv: (c / costs["L0"] - 1 if c is not None and costs["L0"] else None)
                              for lv, c in costs.items()}
    return out


def load_eco2mix() -> list[float]:
    vals = []
    with open(DATA / "eco2mix_fr_2025_hourly.csv", encoding="utf-8") as fh:
        rows = [r for r in fh if not r.startswith("#")]
    for r in csv.DictReader(rows):
        datetime.fromisoformat(r["hour_utc"].replace("Z", "+00:00"))
        vals.append(float(r["g_co2_per_kwh"]))
    return vals


def shifting(series: list[float]) -> dict:
    out = {}
    n = len(series)
    for h in (1, 3, 6):
        for win in (6, 12, 24):
            base, orc, naive = [], [], []
            for t in range(24, n - win):
                def mean(s0):
                    return sum(series[s0:s0 + h]) / h
                starts = range(t, t + win - h + 1)
                base.append(mean(t))
                orc.append(min(mean(s) for s in starts))
                s_hat = min(starts, key=lambda s: sum(series[s - 24:s - 24 + h]))  # seasonal naive
                naive.append(mean(s_hat))
            b = sum(base)
            out[f"{h}h_{win}h"] = {"job_hours": h, "window_hours": win,
                                   "saving_oracle": 1 - sum(orc) / b, "saving_naive": 1 - sum(naive) / b}
    days = [series[i:i + 24] for i in range(0, n - 23, 24)]
    by_hour = [sorted(series[i] for i in range(hh, n, 24)) for hh in range(24)]
    diurnal = [{"hour": hh, "mean": st.mean(v), "p10": v[int(0.1 * len(v))], "p90": v[int(0.9 * len(v))]}
               for hh, v in enumerate(by_hour)]
    stats = {"hours": n, "mean": st.mean(series), "p5": sorted(series)[int(0.05 * n)],
             "p95": sorted(series)[int(0.95 * n)], "min": min(series), "max": max(series),
             "mean_daily_range": st.mean(max(d) - min(d) for d in days)}
    return {"stats": stats, "shifting": out, "diurnal_utc": diurnal}


def fmt(x: float, d: int = 0) -> str:
    return f"{x:,.{d}f}".replace(",", "\\,")


def main() -> None:
    cat = load_catalogue()
    res = {"retrieved": cat["retrieved"], "levels": {k: v["label"] for k, v in cat["levels"].items()}}
    pl = placements(cat)
    res["placements"] = pl
    res["sensitivity"] = sens = sensitivity(cat)
    ser = load_eco2mix()
    res["fr_grid_2025"] = shifting(ser)
    RES.mkdir(parents=True, exist_ok=True)
    GEN.mkdir(parents=True, exist_ok=True)
    (RES / "e8_sovereignty.json").write_text(json.dumps(res, indent=2))

    lv_names = list(cat["levels"])
    # --- table: cost premium (cost-optimal) and carbon (carbon-optimal), per case and level
    rows = []
    premiums, carb_ratios = {lv: [] for lv in lv_names}, {lv: [] for lv in lv_names}
    for case in CASES:
        base_c = pl[case]["L0"]["cost"]["cost"]
        base_g = pl[case]["L0"]["carbon"]["carbon"]
        cells_c, cells_g = [], []
        for lv in lv_names:
            c, g = pl[case][lv]["cost"], pl[case][lv]["carbon"]
            if c is None:
                cells_c.append("n/a")
                cells_g.append("n/a")
                continue
            prem = c["cost"] / base_c - 1
            premiums[lv].append(prem)
            carb_ratios[lv].append(g["carbon"] / base_g)
            cells_c.append(f"{fmt(c['cost'])} ({'+' if prem >= 0 else ''}{100 * prem:.0f}\\,\\%)")
            cells_g.append(f"{g['carbon']:.1f}")
        rows.append(f"{case} & " + " & ".join(cells_c) + " \\\\")
        rows.append(" & " + " & ".join(cells_g) + " \\\\[2pt]")
    (GEN / "sov_tab_levels.tex").write_text("\n".join(rows) + "\n")

    # --- table: cost-optimal placement carbon vs carbon-optimal (alignment), balanced locations
    rows = []
    for case in CASES:
        for lv in lv_names:
            c, g, b = (pl[case][lv][w] for w in ("cost", "carbon", "balanced"))
            if c is None:
                continue
            loc = ", ".join(x.replace("/", " ") for x in b["locations"])
            share = b["carbon_emb"] / b["carbon"] if b["carbon"] else 0
            changed = "yes" if b["locations"] != b["locations_without_embedded"] else "no"
            rows.append(f"{case} & {lv} & {c['carbon']:.1f} & {g['carbon']:.1f} & {fmt(g['cost'])} & "
                        f"{loc} & {100 * share:.0f} & {changed} \\\\")
        rows.append("\\midrule")
    (GEN / "sov_tab_detail.tex").write_text("\n".join(rows[:-1]) + "\n")

    # --- table: temporal shifting on the French grid
    sh = res["fr_grid_2025"]["shifting"]
    rows = [f"{v['job_hours']} & {v['window_hours']} & {100 * v['saving_oracle']:.1f} & {100 * v['saving_naive']:.1f} \\\\"
            for v in sh.values()]
    (GEN / "sov_tab_shift.tex").write_text("\n".join(rows) + "\n")

    # --- table: sensitivity of premiums to the unavailability of the cheapest providers
    rows = []
    names = {"none": "none", "ovhcloud": "OVHcloud", "scaleway": "Scaleway", "ovhcloud+scaleway": "OVHcloud and Scaleway"}
    for key, per_case in sens.items():
        cells = []
        for lv in lv_names[1:]:
            vals = [v[lv] for v in per_case.values() if v[lv] is not None]
            cells.append(f"{100 * min(vals):.0f} to {100 * max(vals):.0f}" if vals else "n/a")
        rows.append(f"{names[key]} & " + " & ".join(cells) + " \\\\")
    (GEN / "sov_tab_sens.tex").write_text("\n".join(rows) + "\n")
    for key, tag in (("ovhcloud", "NoOvh"), ("ovhcloud+scaleway", "NoOvhScw")):
        for lv in lv_names[1:]:
            vals = [v[lv] for v in sens[key].values() if v[lv] is not None]
            if vals:
                macro(f"Prem{lv}{tag}Min", f"{100 * min(vals):.0f}")
                macro(f"Prem{lv}{tag}Max", f"{100 * max(vals):.0f}")

    # --- table: the offer catalogue
    rows = []
    for o in cat["offers"]:
        lab = ", ".join(x for x in o["labels"] if x != "EU").replace("EUHQ", "EU-HQ").replace("SecNumCloud", "SNC")
        rows.append(f"{o['provider']} & {o['region']} & {o['instance']} & {o['eur_per_hour']:.3f} & "
                    f"{o['avg_power_w']:.0f} & {1000 * o['embedded_kgco2e_per_hour']:.1f} & {lab} \\\\")
    (GEN / "sov_tab_offers.tex").write_text("\n".join(rows) + "\n")

    # --- pgfplots data: cost vs carbon (balanced placement) per level, one file per case
    for case in CASES:
        lines = ["level cost carbon"]
        for lv in lv_names:
            b = pl[case][lv]["balanced"]
            if b:
                lines.append(f"{lv} {b['cost']:.2f} {b['carbon']:.3f}")
        (GEN / f"sov_fig_{case}.dat").write_text("\n".join(lines) + "\n")

    (GEN / "sov_fig_diurnal.dat").write_text("hour mean p10 p90\n" + "".join(
        f"{d['hour']} {d['mean']:.2f} {d['p10']:.2f} {d['p90']:.2f}\n" for d in res["fr_grid_2025"]["diurnal_utc"]))

    # --- macros
    for lv in lv_names:
        if premiums[lv]:
            macro(f"Prem{lv}Min", f"{100 * min(premiums[lv]):.0f}")
            macro(f"Prem{lv}Max", f"{100 * max(premiums[lv]):.0f}")
            macro(f"Carb{lv}Max", f"{max(carb_ratios[lv]):.2f}")
            macro(f"Carb{lv}Min", f"{min(carb_ratios[lv]):.2f}")
    shares = [pl[c][lv]["balanced"]["carbon_emb"] / pl[c][lv]["balanced"]["carbon"]
              for c in CASES for lv in lv_names if pl[c][lv]["balanced"]]
    macro("EmbShareMin", f"{100 * min(shares):.0f}")
    macro("EmbShareMax", f"{100 * max(shares):.0f}")
    changed = sum(pl[c][lv][w]["locations"] != pl[c][lv][w]["locations_without_embedded"]
                  for c in CASES for lv in lv_names for w in ("carbon", "balanced") if pl[c][lv][w])
    total = sum(1 for c in CASES for lv in lv_names for w in ("carbon", "balanced") if pl[c][lv][w])
    macro("EmbChanged", changed)
    macro("EmbTotal", total)
    align = sum(set(pl[c][lv]["cost"]["locations"]) == set(pl[c][lv]["carbon"]["locations"])
                for c in CASES for lv in lv_names if pl[c][lv]["cost"])
    ntot = sum(1 for c in CASES for lv in lv_names if pl[c][lv]["cost"])
    macro("Aligned", align)
    macro("AlignTotal", ntot)
    s = res["fr_grid_2025"]["stats"]
    macro("FrMean", f"{s['mean']:.0f}")
    macro("FrPfive", f"{s['p5']:.0f}")
    macro("FrPninetyfive", f"{s['p95']:.0f}")
    macro("FrMin", f"{s['min']:.0f}")
    macro("FrMax", f"{s['max']:.0f}")
    macro("FrDailyRange", f"{s['mean_daily_range']:.0f}")
    macro("ShiftOracleThreeTwelve", f"{100 * sh['3h_12h']['saving_oracle']:.0f}")
    macro("ShiftNaiveThreeTwelve", f"{100 * sh['3h_12h']['saving_naive']:.0f}")
    macro("ShiftOracleOneTwentyfour", f"{100 * sh['1h_24h']['saving_oracle']:.0f}")
    macro("ShiftNaiveOneTwentyfour", f"{100 * sh['1h_24h']['saving_naive']:.0f}")
    grid = cat["grid_gco2_per_kwh"]["values"]
    macro("GridFR", f"{grid['FR']:.0f}")
    macro("GridPL", f"{grid['PL']:.0f}")
    macro("GridDE", f"{grid['DE']:.0f}")
    macro("GridNL", f"{grid['NL']:.0f}")
    macro("GridRatio", f"{grid['PL'] / grid['FR']:.0f}")
    macro("NOffers", len(cat["offers"]))
    macro("NRegions", len({(o['provider'], o['region']) for o in cat["offers"]}))
    macro("Retrieved", cat["retrieved"])

    body = ["% Generated by experiments/sovereignty.py. Do not edit. Requires pgfplotstable.", ""]
    body += [f"\\newcommand{{\\{k}}}{{{v}}}" for k, v in sorted(MACROS.items())]
    for name, f in (("SovTabLevels", "sov_tab_levels.tex"), ("SovTabDetail", "sov_tab_detail.tex"),
                    ("SovTabShift", "sov_tab_shift.tex"), ("SovTabSens", "sov_tab_sens.tex"), ("SovTabOffers", "sov_tab_offers.tex")):
        body.append(f"\\newcommand{{\\{name}}}{{%\n{(GEN / f).read_text().strip()}\n}}")
    body.append(f"\\pgfplotstableread{{\n{(GEN / 'sov_fig_diurnal.dat').read_text().strip()}\n}}\\SovDatDiurnal")
    for case in CASES:
        body.append(f"\\pgfplotstableread{{\n{(GEN / f'sov_fig_{case}.dat').read_text().strip()}\n}}"
                    f"\\SovDat{case.capitalize()}")
    (GEN / "sov_results.tex").write_text("\n".join(body) + "\n", encoding="utf-8")
    print("E8 done:", len(MACROS), "macros")


if __name__ == "__main__":
    main()
