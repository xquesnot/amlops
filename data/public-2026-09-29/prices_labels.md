# Prices and sovereignty labels (retrieved 2026-09-29)

FX: ECB reference rate, 1 EUR = 1.1378 USD. This is the 2026-09-28 fixing, the latest one published when the data was retrieved; the 29/09 fixing comes out around 14:00 UTC. All prices are on-demand, Linux, excluding VAT, in EUR/h.

| Provider / region | cpu-4 | cpu-16 | gpu (1 GPU) | Egress EUR/GB |
|---|---|---|---|---|
| Scaleway fr-par (zones 1/2) | POP2-4C-16G 0.147 | POP2-16C-64G 0.59 | L4-1-24G 0.7875 | 0 (included) |
| Scaleway nl-ams | 0.147 | 0.59 | none: no GPU in nl-ams | 0 |
| Scaleway pl-waw | 0.147 | 0.59 | L4-1-24G 0.7875 (pl-waw-2) | 0 |
| OVHcloud GRA | b3-16 0.1023 | b3-64 0.4092 | l4-90 0.75 | 0 (included) |
| OVHcloud LIM (DE) | 0.1023 | 0.4092 | l4-90 0.75 (availability in LIM not confirmed) | 0 |
| Outscale eu-west-2 | 0.232 (derived) | 0.928 (derived) | L40 + host 2.584 (derived; GPU alone 2.00) | 0 (free) |
| Outscale cloudgouv-eu-west-1 | 0.280 (derived) | 1.120 (derived) | L40 + host 3.104 (derived; GPU alone 2.40) | 0 (free) |
| AWS eu-west-3 | m7i.xlarge 0.2067 (USD 0.2352) | m7i.4xlarge 0.8269 (USD 0.9408) | g6.xlarge 0.8979 (USD 1.0216) | 0.0791 (USD 0.09) |
| AWS eu-central-1 | 0.2123 (USD 0.2415) | 0.8490 (USD 0.966) | 0.8845 (USD 1.0064) | 0.0791 (USD 0.09) |

## Labels

| Provider / offer | SecNumCloud | HDS |
|---|---|---|
| Scaleway public Instances | No. Qualification is "in progress" on ANSSI's list and would cover a future separate zone. | Yes. CPU and GPU Instances are listed. Requires a Business or Enterprise support plan and an HDS contract. |
| OVHcloud Public Cloud instances | No | Yes, through the HDS option on the project (EUR 0). Requires Business or Enterprise support and the Healthcare Addendum. |
| OVHcloud Hosted Private Cloud VMware, Bare Metal Pod, SNC Cloud Platform | Yes (IaaS). These are **not** the Public Cloud instances priced above. SNC Cloud Platform was qualified 31/07/2026 and has no public price. | partial / not verified per offer |
| Outscale "IaaS Cloud on Demand", cloudgouv-eu-west-1 | Yes. Valid **until 30/11/2026**. | Yes. HDS v2 certificate 36741-6, valid 27/08/2026 to 27/08/2029. |
| Outscale eu-west-2 | No. Only cloudgouv-eu-west-1 is described as the qualified region. | partial: the certificate is company-level and I did not check which sites its annex covers |
| AWS eu-west-3 / eu-central-1 | No | Yes. HDSv2 since 21/04/2026, activities 1 to 6 in EEA regions. |

## Uncertainties and caveats
1. **Outscale prices are derived.** Outscale publishes unit prices per vCore-h and per GiB-h, not a list of instance types. The CPU classes use v7 at the HIGH performance level, flag p2, which is the default; MEDIUM figures are given in the notes. The GPU host uses the HIGHEST level (p1), because flexible GPUs require it. The host size, 8 vCore / 48 GiB, is an assumption of this study, chosen to mirror Scaleway L4-1-24G. Outscale offers no L4, so the GPU row uses an L40, which is a more powerful card and not like-for-like. I did not check actual L40 stock per region.
2. **OVH availability by region.** The catalog has one price per flavour for all EU regions. The regions page marks the GPU family as available in GRA, ERI, LIM and WAW, and General Purpose everywhere. I could not confirm on an official page that l4-90 or b3 is actually orderable in LIM. The catalog also tags b3 as "coming_soon". Fallbacks are b2-15 at 0.1342 and b2-60 at 0.526.
3. **OVH public IPv4 becomes a paid extra from 2026-10-01.** The price is not published on the pricing page (NOT FOUND). Scaleway also excludes IPv4 from its list prices, and Outscale charges 0.005 or 0.006 EUR/h for a public IP.
4. **Scaleway fr-par-3 costs 1.5x** (POP2-4C-16G at 0.2205). The figures above use fr-par-1 and fr-par-2. Scaleway bills the L4 per minute and its page rounds the price to 0.79.
5. **The hosts differ between GPU offers.** AWS g6.xlarge is 4 vCPU / 16 GiB. Scaleway L4-1-24G is 8 vCPU / 48 GB. OVH l4-90 is 22 vCore / 90 GB.
6. **The ANSSI web page may lag its PDF.** OVH "SNC Cloud Platform" is qualified in the PDF catalogue (updated 15/09/2026) but still appears in the web "in progress" list. The PDF is used as the authority.
7. **Outscale's qualification expires before ICT4S 2027.** It ends 30/11/2026, so re-check renewal before camera-ready.
8. **The ANS/esante.gouv.fr HDS list could not be retrieved** because a bot-protection page blocked access. HDS status comes from each provider's official page or certificate instead. Per-region HDS scope for Scaleway is NOT FOUND.
9. **HDS carries a hidden cost at Scaleway and OVH.** Both require a Business or Enterprise support plan. That cost is not included in the hourly prices above.
10. **AWS egress** includes a 100 GB/month free tier, aggregated globally. Egress at Scaleway, OVH and Outscale is included at no charge for instance traffic in these regions.
