"""Dynamic (re)deployment under time-varying conditions.

Scope: the long-running serving group (serve + monitor, co-located) is
re-placed across locations, and drift-triggered retraining jobs are placed
and scheduled. Prices are kept static (on-demand pricing); what varies is
the hourly grid carbon intensity per location and location availability
(incidents). Both are *synthetic* in this module (see ``synthetic_environment``);
plug real traces (e.g. Electricity Maps exports, provider status history)
through ``Environment`` to replay reality.

Policies compared
  * ``static``      -- place once on mean conditions, move only on outage;
  * ``reactive``    -- follow the hourly argmin, migrate whenever it changes,
                       retrain immediately at the current best location;
  * ``hysteresis``  -- (proposed) migrate only if the forecast gain over a
                       look-ahead window exceeds the migration cost times
                       (1 + margin); retraining jobs are shifted within their
                       deferrable window to the lowest forecast objective;
  * ``oracle``      -- clairvoyant offline optimum for serving (dynamic
                       programming with switching costs); a lower bound, not
                       an implementable policy. Jobs use true future values.
Forecasts use a seasonal-naive predictor (value 24 h earlier), i.e. no
look-ahead on the realised trace.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from amlops import knowledge
from amlops.dsl.model import ResolvedPipeline
from amlops.placement.optimizer import Offer, evaluate_step, feasible, load_offers

HOURS_PER_MONTH = 730.0


@dataclass
class Environment:
    locations: list[str]
    grid: np.ndarray          # [L, T] gCO2e/kWh
    available: np.ndarray     # [L, T] bool

    @property
    def hours(self) -> int:
        return self.grid.shape[1]


def synthetic_environment(hours: int = 720, seed: int = 0, catalog: Optional[dict] = None,
                          outage_rate: float = 1 / 1500) -> Environment:
    cat = catalog or knowledge.provider_catalog()
    rng = np.random.default_rng(seed)
    locs, grids, avail = [], [], []
    h = np.arange(hours) % 24
    for pname, p in cat["providers"].items():
        for rname, r in p["regions"].items():
            locs.append(f"{pname}/{rname}")
            base = r["grid"]
            amp = 0.30 if r["country"] == "FR" else 0.20
            profile = 1 + amp * np.cos(2 * np.pi * (h - 19) / 24)
            if r["country"] in ("DE", "NL"):
                profile -= 0.15 * np.clip(np.sin(np.pi * (h - 7) / 12), 0, None)  # solar dip
            noise = np.zeros(hours)
            for t in range(1, hours):
                noise[t] = 0.9 * noise[t - 1] + rng.normal(0, 0.08)
            grids.append(base * profile * np.exp(noise))
            a = np.ones(hours, dtype=bool)
            t = 0
            while t < hours:
                if rng.random() < outage_rate:
                    d = int(rng.integers(2, 9))
                    a[t:t + d] = False
                    t += d
                t += 1
            avail.append(a)
    idx = np.argsort(locs)
    return Environment([locs[i] for i in idx], np.array(grids)[idx], np.array(avail)[idx])


@dataclass
class Workload:
    """Per-location hourly cost/energy of the serving group and one training job."""
    locations: list[str]
    serve_cost_h: np.ndarray      # [L] EUR/h
    serve_kwh_h: np.ndarray       # [L] kWh/h (incl. PUE)
    serve_latency: np.ndarray     # [L] ms
    job_cost_h: np.ndarray        # [L]
    job_kwh_h: np.ndarray         # [L]
    job_hours: int
    job_data_gb: float
    data_home: str
    egress: dict[str, float]      # location -> EUR/GB
    feasible: np.ndarray          # [L] bool (serving constraints)
    job_feasible: np.ndarray      # [L] bool


def build_workload(rp: ResolvedPipeline, offers: Optional[list[Offer]] = None,
                   data_home: Optional[str] = None, exclude_locations: tuple[str, ...] = ()) -> Workload:
    """``exclude_locations`` keeps the location axis (for alignment with the
    environment) but makes those locations infeasible (scenario restriction)."""
    offers = offers if offers is not None else load_offers()
    locs = sorted({o.location for o in offers})
    serving = [s for s in rp.steps if s.continuous]
    jobs = [s for s in rp.steps if s.kind in ("train", "tune")]
    L = len(locs)
    sc, sk, sl = np.full(L, np.inf), np.full(L, np.inf), np.zeros(L)
    jc, jk = np.full(L, np.inf), np.full(L, np.inf)
    eg = {}
    for i, loc in enumerate(locs):
        here = [o for o in offers if o.location == loc]
        eg[loc] = here[0].egress_eur_gb
        if loc in exclude_locations:
            continue
        tot_c = tot_k = 0.0
        ok = True
        for s in serving:
            cands = [o for o in here if feasible(s, o, rp)]
            if not cands:
                ok = False
                break
            best = min(cands, key=lambda o: evaluate_step(s, o).cost)
            e = evaluate_step(s, best)
            tot_c += e.cost / HOURS_PER_MONTH
            tot_k += e.carbon_kg * 1000 / best.grid / HOURS_PER_MONTH  # back to kWh
            sl[i] = max(sl[i], best.latency_ms)
        if ok and serving:
            sc[i], sk[i] = tot_c, tot_k
        jcost = jkwh = 0.0
        jok = True
        for s in jobs:
            cands = [o for o in here if feasible(s, o, rp)]
            if not cands:
                jok = False
                break
            best = min(cands, key=lambda o: evaluate_step(s, o).cost)
            n_runs_hours = s.hours_per_run
            e = evaluate_step(s, best)
            per_h = e.cost / max(s.monthly_hours(), 1e-9)
            jcost += per_h * n_runs_hours
            jkwh += (e.carbon_kg * 1000 / best.grid) / max(s.monthly_hours(), 1e-9) * n_runs_hours
        if jok and jobs:
            job_hours = max(1, int(round(sum(s.hours_per_run for s in jobs))))
            jc[i], jk[i] = jcost / job_hours, jkwh / job_hours
    job_hours = max(1, int(round(sum(s.hours_per_run for s in jobs)))) if jobs else 1
    home = data_home or rp.placement.pin.get("ingest") or next(
        (locs[i] for i in np.argsort(sc) if np.isfinite(sc[i])), locs[0])
    return Workload(locs, sc, sk, sl, jc, jk, job_hours, rp.data.volume_gb, home, eg,
                    np.isfinite(sc), np.isfinite(jc))


@dataclass
class SimResult:
    policy: str
    cost_eur: float
    carbon_kg: float
    migrations: int
    serving_cost: float = 0.0
    serving_carbon: float = 0.0
    job_cost: float = 0.0
    job_carbon: float = 0.0
    trajectory: list[int] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {k: (round(float(v), 3) if isinstance(v, (float, np.floating)) else v)
                for k, v in self.__dict__.items() if k != "trajectory"}


class Simulator:
    def __init__(self, wl: Workload, env: Environment, weights: tuple[float, float, float],
                 migration_fixed_eur: float = 2.0, state_gb: float = 20.0,
                 lookahead_h: int = 6, margin: float = 0.25, job_rate_per_h: float = 8 / 720,
                 job_slack_h: int = 12, seed: int = 0):
        assert wl.locations == env.locations, "workload and environment locations differ"
        self.wl, self.env, self.w = wl, env, weights
        self.mig_fixed, self.state_gb = migration_fixed_eur, state_gb
        self.lookahead, self.margin, self.slack = lookahead_h, margin, job_slack_h
        rng = np.random.default_rng(seed + 10_000)
        T = env.hours
        self.job_arrivals = [t for t in range(T) if rng.random() < job_rate_per_h and t < T - job_slack_h - wl.job_hours]
        mean_grid = env.grid.mean(axis=1)
        fs = wl.feasible
        self.c_ref = float(np.min(wl.serve_cost_h[fs]))
        self.g_ref = float(np.min((wl.serve_kwh_h * mean_grid / 1000)[fs]))
        self.l_ref = float(max(np.min(wl.serve_latency[fs]), 1.0))
        self.mean_grid = mean_grid

    # -- objective helpers -------------------------------------------------
    def serve_J(self, grid_col: np.ndarray, avail_col: np.ndarray) -> np.ndarray:
        wc, wg, wl_ = self.w
        J = (wc * self.wl.serve_cost_h / self.c_ref
             + wg * self.wl.serve_kwh_h * grid_col / 1000 / self.g_ref
             + wl_ * self.wl.serve_latency / self.l_ref)
        J = np.where(self.wl.feasible & avail_col, J, np.inf)
        return J

    def mig_cost_eur(self, src: int) -> float:
        return self.mig_fixed + self.state_gb * self.wl.egress[self.wl.locations[src]]

    def mig_J(self, src: int) -> float:
        return self.w[0] * self.mig_cost_eur(src) / self.c_ref

    def forecast_grid(self, t: int, k: int) -> np.ndarray:
        """Seasonal-naive forecast of grid at t+k made at time t."""
        src = t + k - 24 * ((k // 24) + 1)
        return self.env.grid[:, src] if src >= 0 else self.mean_grid

    # -- serving policies --------------------------------------------------
    def run_serving(self, policy: str) -> tuple[float, float, int, list[int]]:
        T = self.env.hours
        if policy == "oracle":
            return self._oracle()
        cur = int(np.argmin(self.serve_J(self.mean_grid, np.ones(len(self.wl.locations), bool))))
        home = cur
        cost = carbon = 0.0
        migs = 0
        traj = []
        for t in range(T):
            J = self.serve_J(self.env.grid[:, t], self.env.available[:, t])
            target = cur
            if not np.isfinite(J[cur]):
                target = int(np.argmin(J))                      # forced move (outage)
            elif policy == "static":
                if cur != home and np.isfinite(J[home]):
                    target = home                               # return home after outage
            elif policy == "reactive":
                target = int(np.argmin(J))
            elif policy == "hysteresis":
                best = int(np.argmin(J))
                if best != cur:
                    gain = 0.0
                    for k in range(self.lookahead):
                        Jf = self.serve_J(self.forecast_grid(t, k), np.ones(len(J), bool))
                        gain += Jf[cur] - Jf[best]
                    if gain > self.mig_J(cur) * (1 + self.margin):
                        target = best
            else:
                raise ValueError(policy)
            if target != cur:
                cost += self.mig_cost_eur(cur)
                migs += 1
                cur = target
            cost += self.wl.serve_cost_h[cur]
            carbon += self.wl.serve_kwh_h[cur] * self.env.grid[cur, t] / 1000
            traj.append(cur)
        return cost, carbon, migs, traj

    def _oracle(self) -> tuple[float, float, int, list[int]]:
        T, L = self.env.hours, len(self.wl.locations)
        Jt = np.stack([self.serve_J(self.env.grid[:, t], self.env.available[:, t]) for t in range(T)], axis=1)
        mig = np.array([self.mig_J(i) for i in range(L)])
        V = Jt[:, 0].copy()
        back = np.zeros((L, T), dtype=int)
        for t in range(1, T):
            trans = V[:, None] + mig[:, None] * (1 - np.eye(L))   # from i to j
            back[:, t] = np.argmin(trans, axis=0)
            V = trans[back[:, t], np.arange(L)] + Jt[:, t]
        path = [int(np.argmin(V))]
        for t in range(T - 1, 0, -1):
            path.append(int(back[path[-1], t]))
        path.reverse()
        cost = carbon = 0.0
        migs = 0
        for t, l in enumerate(path):
            if t and l != path[t - 1]:
                migs += 1
                cost += self.mig_cost_eur(path[t - 1])
            cost += self.wl.serve_cost_h[l]
            carbon += self.wl.serve_kwh_h[l] * self.env.grid[l, t] / 1000
        return cost, carbon, migs, path

    # -- retraining jobs ---------------------------------------------------
    def job_J(self, l: int, start: int, grid_fn) -> float:
        wc, wg, _ = self.w
        if not self.wl.job_feasible[l]:
            return np.inf
        h = self.wl.job_hours
        g = sum(grid_fn(start + k)[l] for k in range(h))
        c = self.wl.job_cost_h[l] * h + (0 if self.wl.locations[l] == self.wl.data_home
                                          else self.wl.job_data_gb * self.wl.egress[self.wl.data_home])
        return wc * c / self.c_ref + wg * self.wl.job_kwh_h[l] * g / 1000 / self.g_ref

    def run_jobs(self, policy: str, serving_traj: list[int]) -> tuple[float, float]:
        cost = carbon = 0.0
        L, h = len(self.wl.locations), self.wl.job_hours
        for t0 in self.job_arrivals:
            avail = lambda l, s: bool(self.env.available[l, s:s + h].all())  # noqa: E731
            if policy == "static":
                l = serving_traj[t0] if self.wl.job_feasible[serving_traj[t0]] else int(np.nanargmin(
                    np.where(self.wl.job_feasible, self.wl.job_cost_h, np.inf)))
                start = t0
            elif policy == "reactive":
                cands = [(self.job_J(l, t0, lambda s: self.env.grid[:, s]), l) for l in range(L) if avail(l, t0)]
                l, start = min(cands)[1], t0
            else:
                if policy == "oracle":
                    fn = lambda s: self.env.grid[:, s]  # noqa: E731
                else:
                    fn = lambda s, t0=t0: self.forecast_grid(t0, s - t0)  # noqa: E731
                cands = [(self.job_J(l, s, fn), l, s) for l in range(L)
                         for s in range(t0, t0 + self.slack - h + 1) if avail(l, s)]
                _, l, start = min(cands)
            c = self.wl.job_cost_h[l] * h
            if self.wl.locations[l] != self.wl.data_home:
                c += self.wl.job_data_gb * self.wl.egress[self.wl.data_home]
            cost += c
            carbon += self.wl.job_kwh_h[l] * float(self.env.grid[l, start:start + h].sum()) / 1000
        return cost, carbon

    def run(self, policy: str) -> SimResult:
        sc, sg, migs, traj = self.run_serving(policy)
        jc, jg = self.run_jobs(policy, traj)
        return SimResult(policy, sc + jc, sg + jg, migs, sc, sg, jc, jg, traj)
