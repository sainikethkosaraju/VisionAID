# Business Model

> No prices are proposed here. Pricing requires customer discovery with facilities. This document fixes the **structure** of the model, the **verified** cost inputs, and the **formulas** so real numbers can be dropped in.

## 1. Revenue streams

| Stream | Form | Notes |
|---|---|---|
| Hardware | Per-camera sale or lease bundled into subscription | Leasing lowers the adoption barrier for small homes; ties hardware refresh to us |
| SaaS subscription | Per monitored bed/room per month | Aligns with how facilities budget (per bed) |
| Enterprise | Multi-site contracts, SSO, custom retention, on-prem/VPC deployment | Hospital groups, chains |
| Advanced analytics | Facility intelligence (repeat-fall rooms, night-time risk, response KPIs) | Upsell once data accumulates |
| Services | Installation, site Wi-Fi survey, integration (nurse call, EHR), training | Often required anyway |

## 2. Customers & buyer
Nursing homes, old-age homes, assisted living, rehabilitation centres, hospitals (step-down/geriatric wards), senior living. **Buyer:** administrator/owner (budget). **User:** caregivers/nurses. **Influencers:** families (safety), regulators/insurers (incident reporting). The ROI story is fall response time and documented incident handling, not "AI".

## 3. Unit economics — per monitored room

### Verified inputs
| Item | Value | Source |
|---|---|---|
| DFR1154 camera module | $18.90 (1), $17.50 (10+) retail | dfrobot.com, Sept 2026 |

### Inputs to quote / measure
Enclosure + mount, power supply + cabling, labour per install, volume pricing (100+/1000+), shipping/duties, cloud hosting per facility, SMS/push unit costs in target markets, support hours per facility-month, hardware failure rate.

### Derived from our architecture (not prices — volumes)
| Driver | Volume | Basis |
|---|---|---|
| Heartbeat traffic | ≈ 40 MB / camera / month | 2 req/min × ~0.5 KB × 43.2k min |
| Evidence per alerted incident | ≈ 2.7 MB | 45 s × 4 fps × ~15 KB (estimate) |
| Evidence storage | incidents/month × 2.7 MB × retention | bounded by 30-day purge |
| Continuous video upload | **0** | privacy architecture — also the main cloud-cost advantage |
| Verifier compute | 1 inference job per trigger, not per frame | event-driven |

### Formulas
```
COGS_hw        = module + enclosure + psu + cabling + assembly + shipping + warranty_reserve
Install        = labour_hours × rate + survey
Cloud/room/mo  = (compute_facility + db_facility) / rooms + storage + egress
Notify/room/mo = alerts/room/mo × escalation_fanout × Σ(channel_unit_cost)
Support/room/mo= support_hours_facility × rate / rooms
Gross margin   = (MRR_room − Cloud − Notify − Support − HW_amortised) / MRR_room
MRR            = Σ rooms × price_per_room
CAC            = (sales + marketing spend) / new facilities   (pilot-led sales: long cycles)
LTV            = MRR_facility × gross_margin / monthly_churn
Payback (mo)   = (CAC + upfront HW subsidy + install) / (MRR_facility × gross_margin)
```

Targets to test against: LTV/CAC ≥ 3, payback ≤ 12–18 months, SaaS gross margin ≥ 70% excluding hardware.

## 4. Strategic observations
- **False alarms are a cost line.** Each false alert costs caregiver time; above some rate staff stop responding and the product fails. Precision is a business metric.
- **Hardware is cheap; installation and support are not.** Designing for zero-touch provisioning and remote diagnostics (heartbeat, commands) protects margin.
- **Evidence and response-time records** may matter to facilities for incident reporting and liability — potentially a stronger purchase driver than detection itself. Validate in discovery.
- **Competition:** wearables (compliance problem — residents remove them), radar sensors (privacy-friendly, no visual evidence), CCTV analytics (privacy-hostile). VisionAID's position: visual verification with ephemeral-by-default privacy + caregiver workflow.

## 5. Discovery questions for the first 10 facilities
Current fall-detection method and response time · who responds at night and how they are reached · staff phone policy · Wi-Fi coverage in rooms · consent practices for cameras · incident-reporting obligations · budget holder and cycle · acceptable false-alarm rate.
