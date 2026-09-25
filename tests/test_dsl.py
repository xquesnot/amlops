import pytest

from amlops.dsl import DerivationError, DSLError, derive, parse_file, parse_text


def test_minimal_model_derives(examples_dir):
    rp = derive(parse_file(examples_dir / "churn_novice.amlops.yaml"))
    kinds = [s.kind for s in rp.steps]
    assert kinds[0] == "ingest" and kinds[-1] == "monitor"
    assert {"impute", "normalize", "train", "serve"} <= set(kinds)
    assert {t.type for t in rp.triggers} >= {"drift"}
    assert rp.topological_order()[0] == "ingest"


def test_unknown_key_is_located():
    with pytest.raises(DSLError) as e:
        parse_text('amlops: "0.1"\npipeline: x\nobjectivs: {cost: 1}\n')
    assert e.value.path == "$"


def test_bad_trigger_type():
    with pytest.raises(DSLError):
        parse_text('pipeline: x\nprofile: timeseries-forecast-batch\ntriggers: [{type: sometimes}]\n')


def test_invalid_features_rejected():
    spec = parse_text('pipeline: x\nfeatures: {select: [ClassificationBaseline, DimReduction]}\n')
    with pytest.raises(DerivationError):
        derive(spec)


def test_expert_refinements(examples_dir):
    rp = derive(parse_file(examples_dir / "predictive_maintenance_expert.amlops.yaml"))
    wf = rp.step("window_features")
    assert wf.origin == "user" and wf.depends_on == ["remove_outliers"]
    assert rp.step("normalize").depends_on == ["window_features"]
    assert rp.step("train").hours_per_run == 3 and rp.step("train").resources.gpu == 1
    assert rp.step("serve").replicas == 3                                   # override
    assert rp.step("evaluate").params["min_precision_at_k"] == 0.9          # override
    assert rp.has("DataVersioning") and rp.has("Canary")                    # best-practice fixes
    drift = [t for t in rp.triggers if t.type == "drift"][0]
    assert drift.detector == "ks" and drift.threshold == 0.1


def test_regulated_profile_adds_gates_and_labels(examples_dir):
    rp = derive(parse_file(examples_dir / "health_triage.amlops.yaml"))
    assert "approve" in [s.kind for s in rp.steps] and "explain" in [s.kind for s in rp.steps]
    assert {"EU", "HDS"} <= set(rp.placement.required_labels)
    drift = next(c for c in rp.contracts if c.id == "drift-response")
    assert drift.gates[0].approval


def test_remove_step_reconnects_graph():
    rp = derive(parse_text('pipeline: x\nprofile: timeseries-forecast-batch\nsteps: [{id: validate, remove: true}]\n'))
    assert "validate" not in [s.id for s in rp.steps]
    assert rp.step("impute").depends_on == ["ingest"]


def test_bad_override_path():
    with pytest.raises(DerivationError):
        derive(parse_text('pipeline: x\nprofile: timeseries-forecast-batch\noverrides: {"steps.train.nope": 1}\n'))


def test_traceability_links_cover_all_steps(examples_dir):
    rp = derive(parse_file(examples_dir / "retail_forecast_expert.amlops.yaml"))
    traced = {t.element for t in rp.trace}
    assert all(f"step:{s.id}" in traced for s in rp.steps)
