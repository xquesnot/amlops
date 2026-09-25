import json

import yaml

from amlops.dsl import derive, parse_file
from amlops.generators import covered_activities, generate
from amlops.placement import optimise


def _gen(examples_dir, name, tmp_path):
    rp = derive(parse_file(examples_dir / name))
    files = generate(rp, optimise(rp), tmp_path)
    return rp, files


def test_all_yaml_parses_and_trace_written(examples_dir, tmp_path):
    rp, files = _gen(examples_dir, "predictive_maintenance_expert.amlops.yaml", tmp_path)
    for f in files:
        if f.path.endswith(".yaml"):
            docs = [d for d in yaml.safe_load_all(f.content) if d]
            assert docs, f.path
    trace = json.loads((tmp_path / "trace.json").read_text())
    assert trace["pipeline"] == rp.name and trace["files"]


def test_every_step_is_realised(examples_dir, tmp_path):
    rp, files = _gen(examples_dir, "health_triage.amlops.yaml", tmp_path)
    realised = {r for f in files for r in f.realises}
    assert all(f"step:{s.id}" in realised for s in rp.steps)


def test_human_gate_becomes_suspend(examples_dir, tmp_path):
    _, files = _gen(examples_dir, "health_triage.amlops.yaml", tmp_path)
    wf = next(f for f in files if f.path.endswith("workflow.yaml"))
    tpl = [t for d in yaml.safe_load_all(wf.content) if d and d["kind"] == "WorkflowTemplate"
           for t in d["spec"]["templates"]]
    assert any(t["name"] == "approve" and "suspend" in t for t in tpl)


def test_argo_names_are_dns_compatible(examples_dir, tmp_path):
    _, files = _gen(examples_dir, "predictive_maintenance_expert.amlops.yaml", tmp_path)
    import re
    for f in files:
        if f.path.startswith("k8s") and f.path.endswith("workflow.yaml"):
            for d in yaml.safe_load_all(f.content):
                if d and d["kind"] == "WorkflowTemplate":
                    for t in d["spec"]["templates"]:
                        assert re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", t["name"]), t["name"]


def test_scaleway_resources_native(examples_dir, tmp_path):
    _, files = _gen(examples_dir, "retail_forecast_expert.amlops.yaml", tmp_path)
    tf = next(f for f in files if f.path == "terraform/main.tf").content
    assert 'resource "scaleway_k8s_cluster"' in tf and 'resource "scaleway_k8s_pool"' in tf


def test_activity_coverage_nonempty(examples_dir, tmp_path):
    _, files = _gen(examples_dir, "churn_novice.amlops.yaml", tmp_path)
    assert {"O1", "O2", "O3", "O6", "O7", "O8", "M4", "M9"} <= covered_activities(files)
