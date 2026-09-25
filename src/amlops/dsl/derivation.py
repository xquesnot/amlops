"""Product derivation: from a (partial) DSL model to a resolved pipeline.

Follows the Software Process Line view of the consortium: a *base process*
with *variation points* (VP) bound by the selected features, e.g. the
``Preprocess`` VP expands into one sub-step per selected preprocessing
feature, the ``Deploy`` VP depends on the DeploymentStrategy group, and the
``Serve`` VP on the Serving group.

Derivation steps
  1. configuration = profile ∪ user selection, completed against the FM (R8);
  2. optional best-practice fixes (advisor);
  3. base process instantiated at its variation points;
  4. user steps merged (override by id, new steps inserted with ``after``);
  5. dotted-path overrides applied (expert fine tuning, requirement iii);
  6. triggers and coordination contracts derived from behavioural features;
  7. placement constraints derived from organisational features.
Every generated element records a trace link to the feature(s) that caused it.
"""
from __future__ import annotations

import copy
from typing import Any

from amlops import knowledge
from amlops.variability import advise, apply_fixes

from .model import (
    Contract,
    Gate,
    PipelineSpec,
    PlacementConstraints,
    ResolvedPipeline,
    Resources,
    Step,
    TraceLink,
    Trigger,
)
from .registry import step_kind

PREPROCESS_VP = {
    "Imputation": "impute",
    "OutlierRemoval": "remove_outliers",
    "Normalization": "normalize",
    "FeatureSelection": "select_features",
    "DimReduction": "reduce_dims",
}


class DerivationError(ValueError):
    pass


def _mk(kind: str, sid: str | None = None, features: list[str] | None = None, **kw: Any) -> Step:
    k = step_kind(kind)
    s = Step(
        id=sid or kind,
        kind=kind,
        activity=k.activity,
        image=k.image,
        resources=Resources(k.vcpu, k.mem_gb, k.gpu),
        continuous=k.continuous,
        hours_per_run=k.hours_per_run,
        params=dict(k.defaults),
        features=list(features or []),
    )
    for key, val in kw.items():
        setattr(s, key, val)
    return s


def resolve_configuration(spec: PipelineSpec) -> tuple[set[str], list[dict]]:
    fm = knowledge.feature_model()
    select = list(spec.select)
    if spec.profile:
        profiles = knowledge.profiles()
        if spec.profile not in profiles:
            raise DerivationError(f"Unknown profile {spec.profile!r}; available: {sorted(profiles)}")
        select = list(profiles[spec.profile]["select"]) + select
    select = [f for f in select if f not in set(spec.deselect)]
    try:
        config = fm.complete(select=select, deselect=spec.deselect)
    except ValueError as exc:
        raise DerivationError(f"Invalid feature selection: {exc}") from exc
    findings = advise(fm, config, knowledge.best_practices())
    if spec.apply_best_practices and findings:
        config = apply_fixes(fm, config, findings)
        findings = advise(fm, config, knowledge.best_practices())
    return config, [f.as_dict() for f in findings]


def _base_process(cfg: set[str], spec: PipelineSpec, trace: list[TraceLink]) -> list[Step]:
    steps: list[Step] = []
    vol = spec.data.volume_gb

    def add(step: Step, reason: str) -> Step:
        if steps and not step.depends_on and step.kind not in ("monitor",):
            step.depends_on = [steps[-1].id]
        steps.append(step)
        trace.append(TraceLink(f"step:{step.id}", reason))
        return step

    add(_mk("ingest", output_gb=vol, params={"source": spec.data.source}), "base process (mandatory)")
    add(_mk("validate", output_gb=vol), "base process (mandatory)")
    for feat, kind in PREPROCESS_VP.items():  # VP Preprocess
        if feat in cfg:
            add(_mk(kind, features=[feat], output_gb=vol, variation_point="Preprocess"),
                f"VP Preprocess bound by {feat}")
    if "HyperparameterTuning" in cfg:
        add(_mk("tune", features=["HyperparameterTuning"], output_gb=0.01), "optional HyperparameterTuning")
    gpu = 1 if "GPU" in cfg else 0
    train = _mk("train", features=["GPU"] if gpu else [], output_gb=spec.data.model_gb,
                deferrable_hours=12.0 if "CarbonAware" in cfg else 0.0)
    if gpu:
        train.resources = Resources(vcpu=8, mem_gb=48, gpu=1)
    add(train, "base process (mandatory)" + ("; GPU feature" if gpu else ""))
    add(_mk("evaluate", output_gb=spec.data.model_gb), "base process (mandatory)")
    if "Explainability" in cfg:
        add(_mk("explain", features=["Explainability"], output_gb=spec.data.model_gb), "optional Explainability")
    add(_mk("register", output_gb=spec.data.model_gb), "base process (mandatory)")
    if "HumanGate" in cfg:
        add(_mk("approve", features=["HumanGate"], output_gb=spec.data.model_gb), "optional HumanGate")
    strategy = next(f for f in ("Recreate", "BlueGreen", "Canary", "Shadow") if f in cfg)
    if strategy != "Recreate" and "BatchScoring" not in cfg:
        add(_mk("deploy_test", features=[strategy], output_gb=spec.data.model_gb,
                params={"strategy": strategy.lower()}, variation_point="Deploy"), f"VP Deploy bound by {strategy}")
    add(_mk("deploy", features=[strategy], output_gb=spec.data.model_gb,
            params={"strategy": strategy.lower()}, variation_point="Deploy"), f"VP Deploy bound by {strategy}")
    if "BatchScoring" in cfg:
        add(_mk("batch_score", features=["BatchScoring"], runs_per_month=30, variation_point="Serve"),
            "VP Serve bound by BatchScoring")
    else:
        serving = "KServe" if "KServe" in cfg else "OnlineService"
        replicas = 3 if "LatencyCritical" in cfg else 2
        add(_mk("serve", features=[serving], replicas=replicas, output_gb=0.0,
                params={"runtime": serving.lower()}, variation_point="Serve"), f"VP Serve bound by {serving}")
    monitors = [f for f in ("DataDriftMonitoring", "ConceptDriftMonitoring", "InfraMonitoring") if f in cfg]
    last = steps[-1].id
    add(_mk("monitor", features=monitors, depends_on=[last], output_gb=0.0,
            params={"signals": [m.replace("Monitoring", "") for m in monitors]}, variation_point="Monitor"),
        "VP Monitor bound by " + ", ".join(monitors))
    return steps


def _merge_user_steps(steps: list[Step], user_steps: list[dict], trace: list[TraceLink]) -> list[Step]:
    by_id = {s.id: s for s in steps}
    for us in user_steps:
        sid = us["id"]
        if us.get("remove"):
            if sid not in by_id:
                raise DerivationError(f"Cannot remove unknown step {sid!r}")
            victim = by_id.pop(sid)
            steps = [s for s in steps if s.id != sid]
            for s in steps:  # reconnect successors to the removed step's predecessors
                if sid in s.depends_on:
                    s.depends_on = [d for d in s.depends_on if d != sid] + [
                        d for d in victim.depends_on if d not in s.depends_on]
            trace.append(TraceLink(f"step:{sid}", "removed by user"))
            continue
        if sid in by_id:
            s = by_id[sid]
            reason = "refined by user"
        else:
            if "kind" not in us:
                raise DerivationError(f"New step {sid!r} needs a 'kind'")
            s = _mk(us["kind"], sid=sid)
            s.origin = "user"
            reason = "added by user"
            after = us.get("after")
            if after:
                if after not in by_id:
                    raise DerivationError(f"Step {sid!r}: 'after' refers to unknown step {after!r}")
                for other in steps:  # insert in the chain
                    if after in other.depends_on:
                        other.depends_on = [sid if d == after else d for d in other.depends_on]
                s.depends_on = [after]
                idx = steps.index(by_id[after]) + 1
                steps.insert(idx, s)
            else:
                s.depends_on = [steps[-1].id] if steps else []
                steps.append(s)
            by_id[sid] = s
        for key in ("activity", "image"):
            if key in us:
                setattr(s, key, us[key])
        if "resources" in us:
            r = us["resources"]
            s.resources = Resources(r.get("vcpu", s.resources.vcpu), r.get("mem_gb", s.resources.mem_gb),
                                    r.get("gpu", s.resources.gpu))
        if "params" in us:
            s.params.update(us["params"])
        if "hours" in us:
            s.hours_per_run = float(us["hours"])
        for key in ("runs_per_month", "replicas", "output_gb", "deferrable_hours", "continuous"):
            if key in us:
                setattr(s, key, us[key])
        trace.append(TraceLink(f"step:{sid}", reason))
    return steps


def _apply_overrides(rp: ResolvedPipeline, overrides: dict[str, Any], trace: list[TraceLink]) -> None:
    for path, value in overrides.items():
        parts = path.split(".")
        if parts[0] == "steps" and len(parts) >= 3:
            target: Any = rp.step(parts[1])
            rest = parts[2:]
        elif parts[0] == "objectives" and len(parts) == 2:
            target, rest = rp.objectives, parts[1:]
        else:
            raise DerivationError(f"Unsupported override path {path!r}")
        for i, key in enumerate(rest):
            last = i == len(rest) - 1
            if isinstance(target, dict):
                if last:
                    target[key] = value
                else:
                    target = target.setdefault(key, {})
            else:
                if not hasattr(target, key):
                    raise DerivationError(f"Override {path!r}: unknown attribute {key!r}")
                if last:
                    setattr(target, key, value)
                else:
                    target = getattr(target, key)
        trace.append(TraceLink(path, f"expert override = {value!r}"))


def _triggers(cfg: set[str], spec: PipelineSpec, trace: list[TraceLink]) -> list[Trigger]:
    out: list[Trigger] = []
    if "Scheduled" in cfg:
        out.append(Trigger("schedule", cron="0 3 * * 1", contract="scheduled-retraining"))
    if "DriftTriggered" in cfg:
        out.append(Trigger("drift", detector="psi", threshold=0.2, contract="drift-response"))
    if "PerformanceTriggered" in cfg:
        out.append(Trigger("performance", metric="f1", threshold=0.05, contract="drift-response"))
    if "NewDataTriggered" in cfg:
        out.append(Trigger("new_data", contract="scheduled-retraining"))
    if "Manual" in cfg:
        out.append(Trigger("manual", contract="scheduled-retraining"))
    for t in out:
        trace.append(TraceLink(f"trigger:{t.type}", "behavioural feature"))
    for i, ut in enumerate(spec.triggers):  # user triggers refine derived ones of same type
        existing = [t for t in out if t.type == ut["type"]]
        tgt = existing[0] if existing else Trigger(ut["type"])
        for k, v in ut.items():
            setattr(tgt, k, v)
        if not existing:
            out.append(tgt)
        trace.append(TraceLink(f"trigger:{tgt.type}", f"user trigger #{i}"))
    return out


def _contracts(cfg: set[str], steps: list[Step]) -> list[Contract]:
    """Coordination contracts (Daoud et al., FGCS 2026, Sect. 5.8-5.9) made executable."""
    human = "HumanGate" in cfg
    ids = {s.id for s in steps}
    model_artefacts = ["model", "evaluation_report"] + (["explanations"] if "Explainability" in cfg else [])
    drift = Contract(
        id="drift-response",
        ticket_type="drift",
        gates=[
            Gate("G1-triage", "ProductOwner", ["decision"], approval=human),
            Gate("G2-data", "DataEngineer", ["dataset"], automated_by="validate"),
            Gate("G3-model", "ModelEngineer", model_artefacts, automated_by="register"),
            Gate("G4-api-impact", "SoftwareEngineer", ["api_release"],
                 condition="model.input_schema != api.current_schema"),
            Gate("G5-deploy", "OperationsEngineer", ["deployment"], approval=human,
                 automated_by="deploy" if "deploy" in ids else None),
        ],
    )
    scheduled = Contract(
        id="scheduled-retraining",
        ticket_type="schedule",
        gates=[
            Gate("G1-data", "DataEngineer", ["dataset"], automated_by="validate"),
            Gate("G2-model", "ModelEngineer", model_artefacts, automated_by="register"),
            Gate("G3-deploy", "OperationsEngineer", ["deployment"], approval=human, automated_by="deploy"),
        ],
    )
    return [drift, scheduled]


def _placement(cfg: set[str], spec: PipelineSpec, trace: list[TraceLink]) -> PlacementConstraints:
    pc = copy.deepcopy(spec.placement)
    labels = set(pc.required_labels)
    if "EUResidency" in cfg or "GDPR" in cfg:
        labels.add("EU")
    if "SecNumCloud" in cfg:
        labels.update({"EU", "SecNumCloud"})
    if "HDS" in cfg:
        labels.update({"EU", "HDS"})
    pc.required_labels = sorted(labels)
    if "LatencyCritical" in cfg and pc.max_latency_ms is None:
        pc.max_latency_ms = 20.0
    trace.append(TraceLink("placement", f"labels {pc.required_labels} from organisational features"))
    return pc


def derive(spec: PipelineSpec) -> ResolvedPipeline:
    trace: list[TraceLink] = []
    cfg, findings = resolve_configuration(spec)
    steps = _base_process(cfg, spec, trace)
    steps = _merge_user_steps(steps, spec.steps, trace)
    rp = ResolvedPipeline(
        name=spec.name,
        configuration=sorted(cfg),
        steps=steps,
        triggers=_triggers(cfg, spec, trace),
        contracts=_contracts(cfg, steps),
        objectives=spec.objectives.normalised(),
        placement=_placement(cfg, spec, trace),
        data=spec.data,
        findings=findings,
        trace=trace,
    )
    _apply_overrides(rp, spec.overrides, trace)
    rp.topological_order()  # raises on cycles
    known = {s.id for s in rp.steps}
    for s in rp.steps:
        missing = set(s.depends_on) - known
        if missing:
            raise DerivationError(f"Step {s.id!r} depends on unknown steps {sorted(missing)}")
    for sid in rp.placement.pin:
        if sid not in known:
            raise DerivationError(f"Placement pin refers to unknown step {sid!r}")
    return rp
