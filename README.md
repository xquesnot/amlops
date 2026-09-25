# amlops — Adaptive MLOps DSL (ANR AdaptiveMLOps, industrial partner prototype)

`amlops` compiles a short, variability-aware description of an MLOps pipeline into
Infrastructure- and Platform-as-Code (Terraform, Argo Workflows / Argo Events,
Kubernetes, CI), places its steps across several cloud providers under cost /
carbon / latency / sovereignty objectives, and adapts it at run time (drift
detection, executable coordination contracts, re-placement).

It is the GetCaaS contribution to the ANR project **AdaptiveMLOps**
(ANR-24-IAS2-0004) and builds on the consortium's results:
the six variability categories and requirements R1–R8 of *Toward Adaptive MLOps:
Variability Mapping and Modeling* (GdR GPL 2025, hal-05127859) and the 38
activities and coordination contracts of **SkeltyMLOps** (MLOps@ECAI 2025,
hal-05337700; FGCS 185 (2026) 108700).

> **Status: research prototype, v0.1.** The provider catalogue
> (`src/amlops/knowledge/providers.yaml`) contains *illustrative* prices, power
> draws and grid intensities, and the dynamic experiments use *synthetic* traces.
> Generated Terraform/Kubernetes artefacts are parsed and unit-tested but have not
> been deployed. The feature model and rules are a proposal awaiting consortium review.

## Quick start

```bash
pip install -e ".[dev,experiments]"
amlops knowledge                                         # what the knowledge base contains
amlops validate examples/churn_novice.amlops.yaml        # best-practice findings
amlops place    examples/churn_novice.amlops.yaml --pareto
amlops generate examples/churn_novice.amlops.yaml -o out/churn
amlops simulate examples/predictive_maintenance_expert.amlops.yaml
pytest -q                                                # 35 tests
python experiments/run_all.py                            # regenerates all paper numbers
```

A non-expert model is five lines:

```yaml
amlops: "0.1"
pipeline: churn-prediction
profile: tabular-classification-continuous
data: {source: "s3://datalake/crm/churn.parquet", volume_gb: 40}
objectives: {cost: 0.6, carbon: 0.4}
```

Experts refine features, steps, triggers, placement and parameters (see
`examples/predictive_maintenance_expert.amlops.yaml` and `docs/DSL.md`).

## Mapping to the project call

| Project requirement | Where |
|---|---|
| Feature models / product lines to capture commonalities | `knowledge/mlops_feature_model.yaml`, `variability/` |
| Documented best practices guiding design | `knowledge/best_practices.yaml`, `variability/advisor.py` |
| (i) generic, extensible DSL | step-kind & generator registries, `amlops.step_kinds` entry points |
| (ii) abstract, ready-to-use by non-experts | profiles, `examples/churn_novice.amlops.yaml` |
| (iii) parameterisation by experts | `features`, `steps`, `overrides` |
| (iv) pivotal model → PaC/IaC | `dsl/derivation.py`, `generators/` (+ `trace.json`) |
| Continuous training on drift | `adaptation/drift.py`, contracts, Argo sensors |
| Dynamic multi-provider (re)deployment, cost / footprint | `placement/optimizer.py`, `placement/dynamic.py` |

## Repository layout

```
src/amlops/
  knowledge/     feature model, rules, profiles, 38 SkeltyMLOps activities, provider catalogue
  variability/   feature-model semantics, configuration completion, advisor
  dsl/           metamodel, YAML parser, registry, derivation (process-line base model)
  placement/     exact multi-objective placement, Pareto front, dynamic policies simulator
  generators/    Terraform, Kubernetes/Argo, GitHub Actions, traceability
  adaptation/    PSI/KS drift detection, executable coordination contracts, MAPE-K loop
examples/        four illustrative case studies (+ generated output of one)
experiments/     run_all.py and results/*.json
paper/           LaTeX draft (main.tex, EN) and French reading copy (main_fr.tex/.pdf); generated/; figures/
scripts/         git hooks (run `sh scripts/install-hooks.sh` after cloning)
```

## Citation / licence
Apache-2.0 © 2026 GetCaaS SARL. See `CITATION.cff`. The accompanying paper
(`paper/main.pdf`) is a draft for consortium review — do not circulate before the
publication review foreseen by the consortium agreement.
