# Tasker Smart Factory — Phase 1

## What this is

Finished-goods stock control for **Tasker S.A.** (Aquino 6061, CABA, Argentina), driven by UHF RFID reads at warehouse chokepoints.

Tasker imports and distributes machinery and consumable inputs for the soft-serve ice cream and granita industry. 30+ years in market. B2B: kiosks, restaurants, foodservice operators.

## Why it exists

The business runs a razor-and-blade model — machines sold once, consumables sold forever. The system answers three questions:

1. What is in stock, by SKU and lot
2. What left the building, when, and for whom
3. **What each customer consumes, per SKU, over time**

The third is the objective. Consumption rate per account drives purchasing, production planning, and which accounts get sales attention.

**Not built for labour savings.** At ~40–70 boxes/day a barcode scanner would be cheaper. RFID was chosen for *compliance* (a portal reads whether or not a rushed operator cooperates) and because dozens of pallets can leave at once, which barcode cannot practically handle.

## Users

- **Dock operator** — opens/closes dispatch sessions. Rushed, interrupted, reading a mounted screen from 2–3 m, sometimes holding a box.
- **Administrative staff** — stock levels, anomaly queue, consumption reports. Desk, unhurried.
- **Owner (Christian)** — consumption data, demand planning.

## Stack

| Layer | Choice |
|---|---|
| Language | Python, managed with `uv` |
| Database | Postgres 16 (local primary + cloud replica) |
| Messaging | Mosquitto MQTT |
| API | FastAPI, port 8000 |
| Dashboard | FastAPI + Jinja2, port 8080 |
| Styling | Tailwind 3.4.17 + Archivo/Public Sans, **all vendored locally** |
| Orchestration | Docker Compose |

Repo: `github.com/Vernocta/tasker-rfid`
Local dev: `~/tasker-rfid` on a MacBook Air M5. Docker Desktop with Rosetta disabled (all images are ARM64-native).

## Pipeline

```
reader → MQTT → ingest → reads_raw
                       → debouncer → observations
                                   → state_engine → movements | anomalies
                                                  → API → dashboard
                                                  → sync → cloud replica
```

## Constraints

- **Nothing loads from the internet.** Warehouse networks are unreliable; local-first is architectural, not a preference.
- **Argentina is ITU Region 2** — all RF hardware must be 902–928 MHz (FCC band), never 865–868 (EU).
- Spanish is the default UI language; English available.

## Working method

Christian is the product owner, not a developer. Claude Desktop orchestrates, Claude Design does visual work, Claude Code implements. Build proceeds one verified step at a time — no step starts before the previous one is proven to work.
