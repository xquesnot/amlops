# Public data snapshot (retrieved 2026-09-29)

Inputs of experiment E8 (`experiments/sovereignty.py`). Unlike `src/amlops/knowledge/providers.yaml`
(illustrative placeholders), every value here comes from a public source.

| File | Content | Source | Licence |
|---|---|---|---|
| `catalogue.yaml` | On-demand prices (EUR/h, excl. VAT), labels (EU-headquartered, HDS, SecNumCloud), average power and embedded emissions per offer | Provider price pages (see `price_source`), ANSSI catalogue of qualified services (15/09/2026), provider HDS pages and certificates, BoaviztAPI | prices and labels: facts from public pages; BoaviztAPI outputs |
| `prices_labels.md` | Retrieval notes, derivations and uncertainties | idem | |
| `boavizta.json` | Raw BoaviztAPI results (average power at 50 % load, embedded GWP per hour) | https://api.boavizta.org (v1 cloud/instance) | |
| `ember_co2_intensity_2025.csv` | Annual CO2 intensity of electricity, 2025 | Ember, Yearly electricity data | CC BY 4.0 |
| `eco2mix_fr_2025_hourly.csv` | Hourly CO2 rate of French electricity production, 2025 (mean of the 15-minute values) | RTE éCO2mix, national consolidated and final data, Open Data Réseaux Énergies (ODRÉ) | Licence Ouverte / Etalab |

Caveats: see `prices_labels.md` and the `notes` field of `catalogue.yaml`. Outscale prices are derived
from its unit prices; Outscale's SecNumCloud qualification in the ANSSI catalogue is valid until
30/11/2026; per-region HDS scope was not found for Scaleway. The Ember intensity and the éCO2mix
rate are not the same indicator (Ember: annual, per country; éCO2mix: direct emissions of French
production, hourly); they are used for different questions and never mixed in one figure.
