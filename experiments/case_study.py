"""E9: industrial case, an educational LLM platform in production (companion to E8).

    python experiments/case_study.py
        -> experiments/results/e9_case.json
        -> paper/generated/case_results.tex

Inputs: data/case-edu-llm-2026/case.yaml (anonymised aggregates authorised by the
client) and the public snapshot data/public-2026-09-29 (catalogue, Ember 2025,
RTE eCO2mix 2025). Every number of the case section of the ICT4S paper comes
from this script.

Questions:
  C1 cost structure of the platform, and weight of the managed inference;
  C2 managed inference API versus a self-hosted GPU (cost and break-even);
  C3 carbon of the platform: operational versus embedded, grid what-if;
  C4 the same compute re-priced at each sovereignty level of E8;
  C5 capacity head-room and effect of the teaching-hours schedule.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "data" / "case-edu-llm-2026" / "case.yaml"
PUB = ROOT / "data" / "public-2026-09-29"
RES = ROOT / "experiments" / "results"
GEN = ROOT / "paper" / "generated"
MACROS: dict[str, str] = {}
DIGITS = {"0": "Zero", "1": "One", "2": "Two", "3": "Three", "4": "Four"}


def macro(name: str, value) -> None:
    MACROS["Case" + "".join(DIGITS.get(c, c) for c in name)] = str(value)


def eur(x: float, nd: int = 0) -> str:
    s = f"{x:,.{nd}f}".replace(",", "\\,")
    return s


def hours_between(a: str, b: str) -> float:
    ha, ma = map(int, a.split(":"))
    hb, mb = map(int, b.split(":"))
    return (hb * 60 + mb - ha * 60 - ma) / 60


def main() -> None:
    RES.mkdir(parents=True, exist_ok=True)
    GEN.mkdir(parents=True, exist_ok=True)
    c = yaml.safe_load(CASE.read_text(encoding="utf-8"))
    cat = yaml.safe_load((PUB / "catalogue.yaml").read_text(encoding="utf-8"))
    grid = cat["grid_gco2_per_kwh"]["values"]
    pue = cat["pue_assumed"]
    hpm = c["case"]["hours_per_month"]
    res: dict = {}

    # C5 schedule: teaching-hours window
    day_h = hours_between(c["schedule"]["wake"], c["schedule"]["sleep"])
    week_on = day_h * c["schedule"]["days_per_week"]
    on_frac = week_on / 168
    month_on_h = hpm * on_frac
    res["schedule"] = {"day_hours": day_h, "week_on_hours": week_on, "on_fraction": on_frac}

    # C1 cost structure
    floor = c["platform"]["production_floor_eur_month"]
    floor_total = sum(floor.values())
    node = c["platform"]["node"]
    wk_days = c["case"]["workshop_days"]
    extra_nodes = c["platform"]["workshop_nodes"] - c["platform"]["floor_nodes"]
    freeze_cost = extra_nodes * node["eur_per_hour"] * 24 * wk_days
    im = c["inference_managed"]
    n_ex = im["planned_exchanges"]
    inf = {m: v["eur_per_exchange"] * n_ex for m, v in im["models"].items()}
    sel = im["selected"]
    month_total = floor_total + freeze_cost + inf[sel]
    res["c1"] = {"floor_total": floor_total, "freeze_cost": freeze_cost, "inference": inf,
                 "month_total": month_total,
                 "share_nodes": floor["nodes"] / floor_total,
                 "share_db": floor["managed_postgresql_ha"] / floor_total,
                 "share_inference": inf[sel] / month_total}

    # C2 managed API versus self-hosted GPU
    g = c["self_hosted_gpu"]
    gpu_always = g["eur_per_hour"] * hpm
    gpu_sched = g["eur_per_hour"] * month_on_h
    gpu_workshop = g["eur_per_hour"] * day_h * wk_days
    fit = [m for m, v in im["models"].items() if v["fits_single_l40s"]]
    be = {m: {"always": gpu_always / v["eur_per_exchange"], "scheduled": gpu_sched / v["eur_per_exchange"]}
          for m, v in im["models"].items()}
    res["c2"] = {"gpu_always": gpu_always, "gpu_scheduled": gpu_sched, "gpu_workshop": gpu_workshop,
                 "break_even_exchanges_per_month": be, "fits_single_l40s": fit}

    # C3 carbon (operational from average power, PUE and annual grid intensity; embedded from BoaviztAPI)
    px = c["platform"]["node_proxy"]

    def carbon(power_w, emb_h, hours, n, country):
        op = n * power_w / 1000 * pue * grid[country] / 1000 * hours
        emb = n * emb_h * hours
        return op, emb

    fl_op, fl_emb = carbon(px["avg_power_w"], px["embedded_kgco2e_per_hour"], hpm, c["platform"]["floor_nodes"], "FR")
    fr_op, fr_emb = carbon(px["avg_power_w"], px["embedded_kgco2e_per_hour"], 24 * wk_days, extra_nodes, "FR")
    ga_op, ga_emb = carbon(g["avg_power_w"], g["embedded_kgco2e_per_hour"], hpm, 1, "FR")
    gs_op, gs_emb = carbon(g["avg_power_w"], g["embedded_kgco2e_per_hour"], month_on_h, 1, "FR")
    whatif = {}
    for cc in ("FR", "NL", "DE", "PL"):
        op, emb = carbon(px["avg_power_w"], px["embedded_kgco2e_per_hour"], hpm, c["platform"]["floor_nodes"], cc)
        whatif[cc] = {"op": op, "emb": emb, "total": op + emb, "emb_share": emb / (op + emb)}
    res["c3"] = {"floor": {"op": fl_op, "emb": fl_emb}, "freeze": {"op": fr_op, "emb": fr_emb},
                 "gpu_always": {"op": ga_op, "emb": ga_emb}, "gpu_sched": {"op": gs_op, "emb": gs_emb},
                 "whatif_floor": whatif}

    # teaching window versus the French grid (Sept 2025, weekdays, Paris time)
    paris = ZoneInfo("Europe/Paris")
    on_v, off_v = [], []
    wake_h = hours_between("00:00", c["schedule"]["wake"])
    sleep_h = hours_between("00:00", c["schedule"]["sleep"])
    with open(PUB / "eco2mix_fr_2025_hourly.csv", encoding="utf-8") as f:
        rows = [r for r in csv.reader(f) if r and not r[0].startswith("#")][1:]
    for ts, v in rows:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(paris)
        if t.month != 9 or t.weekday() >= 5:
            continue
        hh = t.hour + 0.5
        (on_v if wake_h <= hh < sleep_h else off_v).append(float(v))
    res["grid_sept"] = {"teaching_mean": sum(on_v) / len(on_v), "night_mean": sum(off_v) / len(off_v)}

    # C4 the platform compute at each sovereignty level (cheapest cpu-4 and gpu offer of E8)
    vcpu_floor = c["platform"]["floor_nodes"] * node["vcpu"]
    units = vcpu_floor / 4
    levels = cat["levels"]
    lv_rows = {}
    for lv, spec in levels.items():
        req = set(spec["required"])
        cpu = [o for o in cat["offers"] if o["class"] == "cpu-4" and req <= set(o["labels"])]
        gpu = [o for o in cat["offers"] if o["class"].startswith("gpu") and req <= set(o["labels"])]
        bc = min(cpu, key=lambda o: o["eur_per_hour"])
        bg = min(gpu, key=lambda o: o["eur_per_hour"])
        lv_rows[lv] = {"label": spec["label"], "cpu_offer": f"{bc['provider']} {bc['instance']}",
                       "compute_month": units * bc["eur_per_hour"] * hpm,
                       "gpu_offer": f"{bg['provider']} {bg['instance'].split(' +')[0]}" + (" (L40)" if "L40" in bg["instance"] else ""),
                       "gpu_sched_month": bg["eur_per_hour"] * month_on_h}
    actual_compute = c["platform"]["floor_nodes"] * node["eur_per_hour"] * hpm
    for lv in lv_rows:
        lv_rows[lv]["vs_actual"] = lv_rows[lv]["compute_month"] / actual_compute
    res["c4"] = {"vcpu_floor": vcpu_floor, "actual_compute_month": actual_compute, "levels": lv_rows}

    # C5 capacity head-room
    lc = c["load_campaign"]
    ok = [r for r in lc["rows"] if r[3] == 0.0 and r[1] <= 2.0]
    limit_lo = max(r[0] for r in ok)
    limit_hi = min(r[0] for r in lc["rows"] if r[1] > 2.0)
    planned = c["case"]["planned_users"]
    res["c5"] = {"limit_lo": limit_lo, "limit_hi": limit_hi, "headroom_lo": limit_lo / planned,
                 "headroom_hi": limit_hi / planned, "cold_factor": lc["cold_start_login_s"] / lc["warm_login_s"]}
    (RES / "e9_case.json").write_text(json.dumps(res, indent=2), encoding="utf-8")

    # macros
    macro("Users", planned)
    macro("Accounts", c["case"]["student_accounts"])
    macro("Days", wk_days)
    macro("Exchanges", eur(n_ex))
    macro("DayHours", f"{day_h:.1f}")
    macro("WeekOn", f"{week_on:.1f}")
    macro("OnPct", f"{100 * on_frac:.0f}")
    macro("OffPct", f"{100 * (1 - on_frac):.0f}")
    macro("FloorTotal", eur(floor_total))
    macro("FreezeCost", eur(freeze_cost))
    macro("ShareNodes", f"{100 * res['c1']['share_nodes']:.0f}")
    macro("ShareDb", f"{100 * res['c1']['share_db']:.0f}")
    macro("InfSel", eur(inf[sel]))
    macro("InfMin", eur(min(inf.values())))
    macro("InfMax", eur(max(inf.values())))
    macro("MonthTotal", eur(month_total))
    macro("ShareInf", f"{100 * res['c1']['share_inference']:.0f}")
    macro("GpuHour", f"{g['eur_per_hour']:.2f}")
    macro("GpuAlways", eur(gpu_always))
    macro("GpuSched", eur(gpu_sched))
    macro("GpuWorkshop", eur(gpu_workshop))
    fm = fit[0]
    macro("FitModel", fm.replace("-", "\\mbox{-}"))
    macro("BeFitAlways", f"{be[fm]['always'] / 1e6:.1f}")
    macro("BeFitSched", f"{be[fm]['scheduled'] / 1e6:.1f}")
    macro("BeSelSched", eur(be[sel]["scheduled"] / 1e3))
    macro("BeRatioSched", f"{be[fm]['scheduled'] / n_ex:.0f}")
    macro("FloorKg", f"{fl_op + fl_emb:.1f}")
    macro("FloorEmbPct", f"{100 * fl_emb / (fl_op + fl_emb):.0f}")
    macro("FreezeKg", f"{fr_op + fr_emb:.1f}")
    macro("GpuAlwaysKg", f"{ga_op + ga_emb:.1f}")
    macro("GpuSchedKg", f"{gs_op + gs_emb:.1f}")
    macro("GpuEmbPct", f"{100 * ga_emb / (ga_op + ga_emb):.0f}")
    macro("WhatIfPlKg", f"{whatif['PL']['total']:.0f}")
    macro("WhatIfDeKg", f"{whatif['DE']['total']:.0f}")
    macro("WhatIfPlRatio", f"{whatif['PL']['total'] / whatif['FR']['total']:.1f}")
    macro("WhatIfPlEmbPct", f"{100 * whatif['PL']['emb_share']:.0f}")
    macro("GridTeach", f"{res['grid_sept']['teaching_mean']:.0f}")
    macro("GridNight", f"{res['grid_sept']['night_mean']:.0f}")
    macro("VcpuFloor", vcpu_floor)
    macro("ActualCompute", eur(actual_compute))
    for lv, r in lv_rows.items():
        macro(f"Compute{lv}", eur(r["compute_month"]))
        macro(f"Ratio{lv}", f"{r['vs_actual']:.2f}")
    macro("PremLFour", f"{100 * (lv_rows['L4']['compute_month'] / lv_rows['L1']['compute_month'] - 1):.0f}")
    macro("GpuPremLFour", f"{100 * (lv_rows['L4']['gpu_sched_month'] / lv_rows['L1']['gpu_sched_month'] - 1):.0f}")
    macro("LimitLo", eur(limit_lo))
    macro("LimitHi", eur(limit_hi))
    macro("HeadLo", f"{res['c5']['headroom_lo']:.1f}")
    macro("HeadHi", f"{res['c5']['headroom_hi']:.1f}")
    macro("ColdFactor", f"{res['c5']['cold_factor']:.0f}")
    macro("ProbeTtft", f"{c['production_probe']['ttft_median_s']:.2f}")
    macro("ProbeTotal", f"{c['production_probe']['total_median_s']:.1f}")
    macro("Concurrency", im["provider_limits"]["concurrent_requests"])
    macro("Rpm", im["provider_limits"]["requests_per_minute"])

    # tables
    t1 = ["\\begin{tabular}{@{}lr@{}}", "\\toprule",
          "Item & EUR \\\\", "\\midrule",
          f"Production floor, per month ({c['platform']['floor_nodes']} nodes) & {eur(floor_total)} \\\\",
          f"\\quad of which nodes / managed HA database & {eur(floor['nodes'])} / {eur(floor['managed_postgresql_ha'])} \\\\",
          f"Workshop freeze, +{extra_nodes} nodes for {wk_days} days & {eur(freeze_cost)} \\\\",
          f"Managed inference, {eur(n_ex)} exchanges (selected model) & {eur(inf[sel])} \\\\",
          f"\\quad range over the {len(inf)} benchmarked models & {eur(min(inf.values()))}--{eur(max(inf.values()))} \\\\",
          "\\midrule",
          f"Self-hosted L40S GPU, always on, per month & {eur(gpu_always)} \\\\",
          f"Self-hosted L40S GPU, teaching hours only, per month & {eur(gpu_sched)} \\\\",
          f"Self-hosted L40S GPU, the {wk_days} workshop days only & {eur(gpu_workshop)} \\\\",
          "\\bottomrule", "\\end{tabular}"]
    t2 = ["\\begin{tabular}{@{}llrr@{}}", "\\toprule",
          "Level & Cheapest CPU offer & EUR/month & vs.\\ actual \\\\", "\\midrule"]
    for lv, r in lv_rows.items():
        t2.append(f"{lv} & {r['cpu_offer']} & {eur(r['compute_month'])} & {r['vs_actual']:.2f} \\\\")
    t2 += ["\\bottomrule", "\\end{tabular}"]
    body = ["% Generated by experiments/case_study.py. Do not edit.", ""]
    body += [f"\\newcommand{{\\{k}}}{{{v}}}" for k, v in sorted(MACROS.items())]
    body.append("\\newcommand{\\CaseTabCost}{%\n" + "\n".join(t1) + "\n}")
    body.append("\\newcommand{\\CaseTabLevels}{%\n" + "\n".join(t2) + "\n}")
    (GEN / "case_results.tex").write_text("\n".join(body) + "\n", encoding="utf-8")
    print("E9 done:", len(MACROS), "macros")


if __name__ == "__main__":
    main()
