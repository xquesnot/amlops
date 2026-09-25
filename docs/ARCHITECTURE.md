# Architecture

**English** | [Français](ARCHITECTURE.fr.md)

This document describes the scientific and software architecture of `amlops`, the
GetCaaS prototype for the ANR project AdaptiveMLOps (ANR-24-IAS2-0004). Every
diagram reflects the code of version 0.1; module paths are relative to
`src/amlops/`.

## 1. End-to-end view: from a DSL model to running pipelines

```mermaid
flowchart LR
    subgraph K["Knowledge layer (knowledge/)"]
        FM["Feature model<br/>6 variability categories<br/>C1..C10 constraints"]
        BP["Best-practice rules"]
        PR["Profiles"]
        ACT["38 SkeltyMLOps activities"]
        CAT["Provider catalogue<br/>price, power, grid intensity, labels"]
    end

    U(["Non-expert or expert user"]) --> M["DSL model<br/>*.amlops.yaml"]
    M --> P["Parser<br/>dsl/parser.py"]
    P --> C["Configuration completion<br/>and advice<br/>variability/"]
    FM --> C
    BP --> C
    PR --> C
    C --> D["Derivation<br/>process line: base process<br/>+ variation points<br/>dsl/derivation.py"]
    ACT --> D
    D --> RP[("Resolved pipeline<br/>steps, triggers, contracts,<br/>constraints, trace links")]
    RP --> PL["Multi-objective placement<br/>branch and bound, Pareto front<br/>placement/optimizer.py"]
    CAT --> PL
    PL --> G["Generators<br/>generators/"]
    G --> TF["Terraform<br/>(IaC)"]
    G --> K8S["Kubernetes, Argo Workflows,<br/>Argo Events (PaC)"]
    G --> CI["CI workflow"]
    G --> TR["trace.json"]
    TF & K8S & CI --> CLOUD[["Cloud providers<br/>(one cluster per location)"]]
    CLOUD -. "production data, metrics" .-> AD["MAPE-K adaptation loop<br/>adaptation/"]
    AD -. "ticket under a coordination contract" .-> RP
    AD -. "re-placement" .-> PL
```

The chain is deterministic: the same model, knowledge base and catalogue always
produce the same artefacts, and `trace.json` links every generated element to the
feature, rule or user statement that caused it.

## 2. Package structure

```mermaid
flowchart TB
    CLI["cli.py<br/>validate · derive · place · generate · simulate · knowledge"]
    subgraph core[" "]
        direction TB
        DSL["dsl/<br/>model, parser, registry, derivation"]
        VAR["variability/<br/>feature_model, advisor"]
        PLA["placement/<br/>optimizer, dynamic"]
        GEN["generators/<br/>terraform, kubernetes, cicd"]
        ADA["adaptation/<br/>drift, contracts, loop"]
        KNO["knowledge/<br/>YAML files"]
    end
    CLI --> DSL & PLA & GEN & ADA
    DSL --> VAR
    VAR --> KNO
    DSL --> KNO
    PLA --> DSL
    PLA --> KNO
    GEN --> DSL
    GEN --> PLA
    ADA --> DSL
    ADA --> PLA
```

Extension points: new step kinds are registered with `register_step_kind` (or
through the `amlops.step_kinds` entry point), new targets with
`register_generator`. Neither requires changing the metamodel.

## 3. Variability model

The feature model follows FODA semantics (mandatory and optional features, XOR and
OR groups, propositional cross-tree constraints parsed from a safe subset of the
Python `ast`). Its six top-level categories are the variability categories of
El Hatimi et al. (GdR GPL 2025).

```mermaid
flowchart TB
    R["MLOpsPipeline"]
    R --> F["Functional"]
    R --> B["Behavioral"]
    R --> T["Technical"]
    R --> DS["DomainSpecific"]
    R --> NF["NonFunctional"]
    R --> O["Organizational"]
    F --> F1["MLTask (xor)<br/>classification, regression,<br/>forecasting, anomaly detection"]
    F --> F2["Preprocessing (or)"]
    F --> F3["HyperparameterTuning ?"]
    F --> F4["Explainability ?"]
    B --> B1["TrainingTrigger (or)<br/>Manual, Scheduled, DriftTriggered,<br/>PerformanceTriggered, NewDataTriggered"]
    B --> B2["DeploymentStrategy (xor)<br/>Recreate, BlueGreen, Canary, Shadow"]
    B --> B3["HumanGate ?"]
    T --> T1["Orchestrator (xor)<br/>ArgoWorkflows, Kubeflow, Airflow"]
    T --> T2["Serving (xor)<br/>OnlineService, KServe, BatchScoring"]
    T --> T3["Runtime (xor)<br/>Kubernetes, Serverless"]
    T --> T4["GPU ?, ExperimentTracking ?, DataVersioning ?"]
    DS --> D1["Domain (xor)<br/>Generic, Industry40IoT, Healthcare,<br/>Retail, Finance"]
    NF --> N1["Objectives (or)<br/>CostOptimized, CarbonAware,<br/>LatencyCritical"]
    NF --> N2["Monitoring (or)<br/>Data drift, Concept drift, Infra"]
    O --> O1["Sovereignty (xor)<br/>NoResidencyConstraint,<br/>EUResidency, SecNumCloud"]
    O --> O2["Compliance (or)<br/>GDPR, HDS, AIActHighRisk"]
```

`?` marks optional features. Examples of cross-tree constraints
(`knowledge/mlops_feature_model.yaml`):

| Id | Constraint | Rationale |
|---|---|---|
| C1 | ¬(ClassificationBaseline ∧ DimReduction) | example reported in the GdR GPL 2025 poster |
| C2 | DriftTriggered ⇒ DataDriftMonitoring ∨ ConceptDriftMonitoring | a drift trigger needs a drift signal |
| C6 | Healthcare ⇒ HDS ∧ ¬NoResidencyConstraint | French health data hosting |
| C8 | AIActHighRisk ⇒ HumanGate ∧ Explainability | human oversight and transparency |

**Configuration completion.** A partial selection is completed by a pre-order
depth-first search with Kleene three-valued evaluation of the constraints, which
prunes a branch as soon as a constraint is certainly false. Completion is minimal:
features are tried false first, except features marked `default: true`, which are
tried true first. The advisor then applies best-practice rules and reports
findings with suggested fixes.

## 4. Derivation: a software process line

Derivation follows the software process line view of the consortium: a base
process whose variation points (VP) are bound by the selected features. Every
created element receives a `TraceLink` to its cause.

```mermaid
flowchart LR
    I["ingest"] --> V["validate"]
    V --> VP1{{"VP Preprocess<br/>impute · remove_outliers · normalize ·<br/>select_features · reduce_dims"}}
    VP1 --> TU["tune ?"]
    TU --> TRN["train<br/>(GPU ?, deferrable if CarbonAware)"]
    TRN --> EV["evaluate"]
    EV --> EX["explain ?"]
    EX --> REG["register"]
    REG --> AP["approve ?<br/>(HumanGate)"]
    AP --> VP2{{"VP Deploy<br/>deploy_test if BlueGreen,<br/>Canary or Shadow; deploy"}}
    VP2 --> VP3{{"VP Serve<br/>OnlineService · KServe ·<br/>BatchScoring"}}
    VP3 --> VP4{{"VP Monitor<br/>data drift · concept drift ·<br/>infrastructure"}}
```

After the base process, user-defined steps are merged, triggers and coordination
contracts are derived, organisational features become placement labels (for
instance `HDS` adds the labels `EU` and `HDS`), expert `overrides` are applied, and
the result is checked for cycles and dangling references.

## 5. Multi-objective placement

Each step *s* is assigned to an offer *o(s)* (provider, region, instance type) from
the catalogue, subject to hard constraints (required labels, allowed providers,
pins, co-location groups, maximum latency). The objective is a weighted sum of
normalised criteria:

$$
J = w_c \frac{\sum_s c_{s,o(s)} + E}{C^\*} + w_g \frac{\sum_s g_{s,o(s)}}{G^\*} + w_\ell \frac{\sum_s \ell_{s,o(s)}}{L^\*}
$$

where *c*, *g* and *ℓ* are the monthly cost, operational carbon (kg CO₂e, energy
times grid intensity) and latency of a step on an offer, *E* is the data egress
cost between different locations, and *C\**, *G\**, *L\** are the sums of per-step
minima. The weights are the normalised `objectives` of the model, so *J* ≥ 1 and
*J* = 1 would mean every step reaches its individual optimum.

The optimum is computed exactly by branch and bound over steps in topological
order. The bound is admissible (partial objective plus the sum of per-step minima
of the remaining steps) and candidates are reduced by location-level dominance,
since egress and co-location only depend on the location. A node budget flags
non-proven optima. Varying the weights yields the Pareto front
(`amlops place --pareto`).

## 6. Run-time adaptation: MAPE-K loop

```mermaid
flowchart LR
    MON["Monitor<br/>PSI or KS drift detection<br/>on production batches"] --> ANA["Analyse<br/>does a trigger of the<br/>resolved pipeline fire?"]
    ANA --> PLN["Plan<br/>open a ticket under the<br/>trigger's contract;<br/>re-place if the environment<br/>changed beyond a margin"]
    PLN --> EXE["Execute<br/>regenerate IaC and PaC<br/>(diff-able artefacts)"]
    EXE --> MON
    KB[("Knowledge<br/>resolved pipeline, contracts,<br/>provider catalogue")]
    KB --- MON & ANA & PLN & EXE
```

## 7. Executable coordination contracts

The coordination model of SkeltyMLOps (Daoud et al., FGCS 2026, Sect. 5.8 and 5.9)
is made executable: a ticket moves gate by gate only when the owning actor submits
the required artefacts and, where requested, an approval. Conditional gates are
skipped when their condition is false. Every transition is recorded in a JSON
trace.

```mermaid
stateDiagram-v2
    [*] --> G1_triage: drift event
    G1_triage: G1 triage (Product Owner)<br/>decision [+ approval if HumanGate]
    G2_data: G2 data (Data Engineer)<br/>dataset, automated by validate
    G3_model: G3 model (Model Engineer)<br/>model, evaluation report [, explanations]
    G4_api: G4 API impact (Software Engineer)<br/>api_release
    G5_deploy: G5 deploy (Operations Engineer)<br/>deployment [+ approval if HumanGate]
    G1_triage --> G2_data
    G2_data --> G3_model
    G3_model --> G4_api: input schema changed
    G3_model --> G5_deploy: schema unchanged (G4 skipped)
    G4_api --> G5_deploy
    G5_deploy --> [*]: ticket closed, trace recorded
```

A second contract, `scheduled-retraining`, covers scheduled triggers with the gates
data, model and deploy.

## 8. Dynamic re-placement policies

The simulator (`placement/dynamic.py`) replays hourly grid intensity and
availability traces for the serving group and the deferrable training jobs.

| Policy | Serving decision at each hour | Role |
|---|---|---|
| static | keep the initial location; forced move on outage, return home afterwards | baseline |
| reactive | move to the current best location | upper bound on churn |
| hysteresis | move only if the forecast gain over a look-ahead window exceeds the migration cost times (1 + margin) | proposed |
| oracle | dynamic programming over locations with switching costs and the true future | lower bound |

Forecasts are seasonal-naive. Migration cost is a fixed penalty plus egress.

## 9. Traceability to the project call and to consortium results

```mermaid
flowchart LR
    subgraph Call["ANR project call"]
        A1["Feature models and<br/>product lines"]
        A2["Documented best practices"]
        A3["DSL: generic, abstract,<br/>expert-tunable, pivotal"]
        A4["Continuous training<br/>on drift"]
        A5["Dynamic multi-provider<br/>(re)deployment"]
    end
    subgraph Con["Consortium results reused"]
        R1["6 variability categories,<br/>R1..R8 (GdR GPL 2025)"]
        R2["38 activities, actors,<br/>coordination contracts<br/>(SkeltyMLOps)"]
    end
    subgraph Imp["amlops modules"]
        M1["knowledge/, variability/"]
        M2["dsl/"]
        M3["generators/"]
        M4["adaptation/"]
        M5["placement/"]
    end
    A1 --> M1
    A2 --> M1
    A3 --> M2
    A3 --> M3
    A4 --> M4
    A5 --> M5
    R1 --> M1
    R2 --> M2
    R2 --> M4
```

## Known limitations (v0.1)

- No Ecore metamodel and no BPMN view yet; both are natural bridges to the
  academic partners' process-line modelling.
- Generators target Argo on Kubernetes only; nothing is generated yet for
  experiment tracking or data versioning.
- The provider catalogue holds illustrative values and the dynamic experiments use
  synthetic traces; generated artefacts are parsed and unit-tested, not deployed.
- Hysteresis has no return-to-home rule after an outage-forced move; availability
  is assumed known when scheduling jobs.
