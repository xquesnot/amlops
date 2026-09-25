# Architecture notes

* **Knowledge layer** — YAML files loaded once (`amlops.knowledge`). Replace
  `providers.yaml` with contractual data before any operational use.
* **Feature model** — FODA semantics (mandatory/optional, XOR/OR groups,
  propositional cross-tree constraints via a safe `ast` subset). Completion is a
  pre-order DFS with Kleene 3-valued pruning; minimal completion (false-first,
  `default: true` features true-first).
* **Derivation** — software-process-line view: a base process with variation
  points bound by features; each element gets a `TraceLink`.
* **Coordination contracts** — tickets move gate by gate only with the owner's
  artefacts and approvals; conditional gates are skipped when false; trace in JSON.
  Operationalises SkeltyMLOps (FGCS 2026, Sect. 5.8–5.9).
* **Placement** — exact branch & bound, admissible bound = partial objective +
  per-step minima; location-level dominance; node budget flag. Objective terms are
  normalised by per-step minima (J ≥ 1).
* **Generators** — registry (`register_generator`). Native Scaleway Terraform;
  module stubs elsewhere. One cluster per location; Argo WorkflowTemplates per
  location; cross-location hand-offs via Argo Events webhooks (ingress hostnames
  are placeholders).
* **Dynamic** — serving group re-placement and retraining scheduling; policies
  static / reactive / hysteresis / oracle (DP lower bound). Seasonal-naive forecasts.

Known limitations (v0.1): no Ecore metamodel or BPMN view; generators only for
Argo on Kubernetes; nothing generated for experiment tracking or data versioning
(see the inert-feature metric in the paper); hysteresis has no return-to-home rule
after outage-forced moves; availability is assumed known when scheduling jobs.
