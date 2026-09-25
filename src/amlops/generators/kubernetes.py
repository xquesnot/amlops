"""Platform-as-Code generator: Kubernetes + Argo Workflows + Argo Events.

Per placement location (one cluster each):
  * a WorkflowTemplate with the batch steps placed there (DAG);
    a human gate becomes an Argo ``suspend`` node;
  * cross-location hand-offs: the upstream segment ends with a ``handoff``
    task that posts to a webhook EventSource of the downstream location,
    whose Sensor submits the downstream WorkflowTemplate;
  * a CronWorkflow for schedule triggers, a Sensor for drift / performance
    triggers (events emitted by the monitor);
  * Deployments/Services (or a KServe InferenceService) for serving, a
    monitor Deployment and ConfigMaps holding the drift policy and the
    executable coordination contracts.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict

from amlops.dsl.model import ResolvedPipeline, Step
from amlops.placement.optimizer import Placement

from . import GeneratedFile, dump_yaml_docs, register_generator, slug

ARGO = "argoproj.io/v1alpha1"
NS = "amlops"


def _container(step: Step, itype: str) -> dict:
    return {
        "image": step.image,
        "args": ["--step", step.id, "--params", json.dumps(step.params, sort_keys=True)],
        "resources": {"requests": {"cpu": str(step.resources.vcpu), "memory": f"{step.resources.mem_gb}Gi",
                                   **({"nvidia.com/gpu": str(step.resources.gpu)} if step.resources.gpu else {})}},
    }


@register_generator("kubernetes")
def generate_kubernetes(rp: ResolvedPipeline, placement: Placement) -> list[GeneratedFile]:
    name = slug(rp.name)
    steps = {s.id: s for s in rp.steps}
    order = rp.topological_order()
    loc_of = {sid: placement.assignment[sid].location for sid in order}
    itype_of = {sid: placement.assignment[sid].itype for sid in order}
    batch = [sid for sid in order if not steps[sid].continuous]
    by_loc: dict[str, list[str]] = defaultdict(list)
    for sid in batch:
        by_loc[loc_of[sid]].append(sid)

    # cross-location hand-offs between batch steps
    handoffs = [(a, b) for (a, b) in rp.edges()
                if a in batch and b in batch and loc_of[a] != loc_of[b]]
    files: list[GeneratedFile] = []
    first_loc = loc_of[batch[0]] if batch else None

    for loc, sids in by_loc.items():
        lslug = slug(loc)
        wt_name = f"{name}-{lslug}"
        templates, tasks, acts = [], [], ["O3"]
        for sid in sids:
            s = steps[sid]
            acts.append(s.activity)
            tname = slug(sid)  # Argo names: alphanumerics and '-'
            if s.kind == "approve":
                templates.append({"name": tname, "suspend": {}})
            else:
                templates.append({"name": tname, "nodeSelector": {"amlops.io/pool": itype_of[sid]},
                                  "container": _container(s, itype_of[sid])})
            deps = [slug(d) for d in s.depends_on if d in sids]
            task = {"name": tname, "template": tname}
            if deps:
                task["dependencies"] = deps
            tasks.append(task)
        for a, b in handoffs:
            if loc_of[a] == loc:
                target = slug(loc_of[b])
                templates.append({"name": f"handoff-{slug(b)}", "http": {
                    "url": f"https://events.{target}.{name}.example.internal/{target}/{b}",  # ingress of the downstream cluster
                    "method": "POST", "body": json.dumps({"from": a, "to": b, "artifact": f"s3://{name}-artifacts/{a}"})}})
                tasks.append({"name": f"handoff-{slug(b)}", "template": f"handoff-{slug(b)}", "dependencies": [slug(a)]})
        templates.insert(0, {"name": "main", "dag": {"tasks": tasks}})
        wt = {"apiVersion": ARGO, "kind": "WorkflowTemplate",
              "metadata": {"name": wt_name, "namespace": NS, "labels": {"amlops.io/pipeline": name}},
              "spec": {"entrypoint": "main", "serviceAccountName": "amlops-runner", "templates": templates}}
        docs = [wt]
        incoming = [(a, b) for a, b in handoffs if loc_of[b] == loc]
        if incoming:
            docs.append({"apiVersion": ARGO, "kind": "EventSource",
                         "metadata": {"name": f"{name}-handoff", "namespace": NS},
                         "spec": {"webhook": {f"handoff-{slug(b)}": {"port": "12000", "endpoint": f"/{lslug}/{b}",
                                                                "method": "POST"} for _, b in incoming}}})
            docs.append({"apiVersion": ARGO, "kind": "Sensor",
                         "metadata": {"name": f"{name}-handoff-{lslug}", "namespace": NS},
                         "spec": {"dependencies": [{"name": f"handoff-{slug(b)}", "eventSourceName": f"{name}-handoff",
                                                    "eventName": f"handoff-{slug(b)}"} for _, b in incoming],
                                  "triggers": [{"template": {"name": "submit", "argoWorkflow": {
                                      "operation": "submit", "source": {"resource": {
                                          "apiVersion": ARGO, "kind": "Workflow",
                                          "metadata": {"generateName": f"{wt_name}-"},
                                          "spec": {"workflowTemplateRef": {"name": wt_name}}}}}}}]}})
            acts.append("O8")
        realises = [f"step:{s}" for s in sids] + [f"handoff:{a}->{b}" for a, b in handoffs if loc in (loc_of[a], loc_of[b])]
        files.append(GeneratedFile(f"k8s/{lslug}/workflow.yaml", dump_yaml_docs(docs), realises, acts))

    # triggers (hosted where the pipeline starts)
    if first_loc:
        trig_docs, trig_real, trig_acts = [], [], []
        wt_first = f"{name}-{slug(first_loc)}"
        for t in rp.triggers:
            if t.type == "schedule":
                trig_docs.append({"apiVersion": ARGO, "kind": "CronWorkflow",
                                  "metadata": {"name": f"{name}-scheduled", "namespace": NS},
                                  "spec": {"schedule": t.cron, "concurrencyPolicy": "Forbid",
                                           "workflowSpec": {"workflowTemplateRef": {"name": wt_first}}}})
                trig_real.append("trigger:schedule")
                trig_acts += ["O8", "O3"]
            elif t.type in ("drift", "performance", "new_data"):
                trig_docs.append({"apiVersion": ARGO, "kind": "Sensor",
                                  "metadata": {"name": f"{name}-{t.type}", "namespace": NS,
                                               "annotations": {"amlops.io/contract": t.contract or ""}},
                                  "spec": {"dependencies": [{"name": t.type, "eventSourceName": f"{name}-monitor",
                                                             "eventName": t.type}],
                                           "triggers": [{"template": {"name": "retrain", "argoWorkflow": {
                                               "operation": "submit", "source": {"resource": {
                                                   "apiVersion": ARGO, "kind": "Workflow",
                                                   "metadata": {"generateName": f"{name}-{t.type}-"},
                                                   "spec": {"workflowTemplateRef": {"name": wt_first}}}}}}}]}})
                trig_real.append(f"trigger:{t.type}")
                trig_acts.append("O8")
        if any(t.type != "schedule" for t in rp.triggers if t.type != "manual"):
            trig_docs.append({"apiVersion": ARGO, "kind": "EventSource",
                              "metadata": {"name": f"{name}-monitor", "namespace": NS},
                              "spec": {"webhook": {t.type: {"port": "12000", "endpoint": f"/{t.type}", "method": "POST"}
                                                   for t in rp.triggers if t.type in ("drift", "performance", "new_data")}}})
        if trig_docs:
            files.append(GeneratedFile(f"k8s/{slug(first_loc)}/triggers.yaml", dump_yaml_docs(trig_docs),
                                       trig_real, trig_acts))

    # continuous steps: serving + monitoring
    for sid in order:
        s = steps[sid]
        if not s.continuous:
            continue
        loc = slug(loc_of[sid])
        labels = {"app": f"{name}-{slug(sid)}", "amlops.io/pipeline": name}
        docs: list[dict] = []
        acts = [s.activity]
        if s.kind == "serve" and rp.has("KServe"):
            docs.append({"apiVersion": "serving.kserve.io/v1beta1", "kind": "InferenceService",
                         "metadata": {"name": f"{name}-{slug(sid)}", "namespace": NS,
                                      "annotations": {"amlops.io/strategy": rp.step("deploy").params.get("strategy", "recreate")}},
                         "spec": {"predictor": {"minReplicas": s.replicas, "nodeSelector": {"amlops.io/pool": itype_of[sid]},
                                                "model": {"modelFormat": {"name": "mlflow"},
                                                          "storageUri": f"s3://{name}-artifacts/models/current"}}}})
        else:
            docs.append({"apiVersion": "apps/v1", "kind": "Deployment",
                         "metadata": {"name": f"{name}-{slug(sid)}", "namespace": NS, "labels": labels},
                         "spec": {"replicas": s.replicas, "selector": {"matchLabels": {"app": labels["app"]}},
                                  "template": {"metadata": {"labels": labels},
                                               "spec": {"nodeSelector": {"amlops.io/pool": itype_of[sid]},
                                                        "containers": [{"name": slug(sid), **_container(s, itype_of[sid])}]}}}})
            if s.kind == "serve":
                docs.append({"apiVersion": "v1", "kind": "Service",
                             "metadata": {"name": f"{name}-{slug(sid)}", "namespace": NS},
                             "spec": {"selector": {"app": labels["app"]}, "ports": [{"port": 80, "targetPort": 8080}]}})
        if s.kind == "monitor":
            policy = {"pipeline": name, "signals": s.params.get("signals", []),
                      "triggers": [asdict(t) for t in rp.triggers],
                      "emit_to": f"http://{name}-monitor-eventsource-svc.{NS}.svc:12000"}
            docs.append({"apiVersion": "v1", "kind": "ConfigMap",
                         "metadata": {"name": f"{name}-drift-policy", "namespace": NS},
                         "data": {"policy.json": json.dumps(policy, indent=2)}})
            docs.append({"apiVersion": "v1", "kind": "ConfigMap",
                         "metadata": {"name": f"{name}-contracts", "namespace": NS},
                         "data": {"contracts.json": json.dumps([asdict(c) for c in rp.contracts], indent=2)}})
            acts.append("O9")
        files.append(GeneratedFile(f"k8s/{loc}/{sid}.yaml", dump_yaml_docs(docs), [f"step:{sid}"] +
                                   (["contracts", "triggers"] if s.kind == "monitor" else []), acts))
    return files
