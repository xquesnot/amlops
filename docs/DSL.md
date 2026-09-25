# DSL reference (v0.1)

Top-level keys (unknown keys are rejected with a JSON-path-like location):

| Key | Type | Meaning |
|---|---|---|
| `amlops` | `"0.1"` | DSL version |
| `pipeline` | name | alphanumeric, `-` and `_` |
| `profile` | string | partial configuration from `knowledge/profiles.yaml` |
| `features.select / deselect` | list | leaf or group features of the feature model |
| `best_practices` | bool | apply the advisor's valid automatic fixes |
| `data` | `{source, volume_gb, model_gb}` | sizes drive egress costs |
| `objectives` | `{cost, carbon, latency}` | non-negative weights, normalised |
| `placement` | `{allowed_providers, required_labels, pin, max_latency_ms, colocate}` | hard constraints |
| `steps` | list | refine a derived step by `id`, add one (`kind`, `after`), or `remove: true` |
| `triggers` | list | `type: schedule|drift|performance|new_data|manual`, `cron`, `detector: psi|ks`, `threshold`, `contract` |
| `overrides` | map | dotted paths: `steps.<id>.<field>[.<key>]`, `objectives.<w>` |

Step fields: `kind`, `activity` (SkeltyMLOps id, e.g. `D5`), `image`,
`resources {vcpu, mem_gb, gpu}`, `hours`, `runs_per_month`, `replicas`,
`output_gb`, `deferrable_hours`, `continuous`, `params`, `after`, `remove`.

Derivation order: profile ∪ select − deselect → completion against the feature
model → optional fixes → base process with variation points (Preprocess, Deploy,
Serve, Monitor) → user steps → overrides → triggers, contracts, placement labels.

Organisational features translate into placement labels: `EUResidency`/`GDPR` → `EU`,
`SecNumCloud` → `EU, SecNumCloud`, `HDS` → `EU, HDS`; `LatencyCritical` sets
`max_latency_ms: 20` unless given.
