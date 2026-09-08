# Current state

_Last updated: after step 9 + dashboard restyle._

## Working

- Steps 1–9 of the build order complete
- **165 tests passing** (77 unit + 88 integration)
- Full pipeline verified end to end with the simulator on Christian's Mac
- All nine SPEC §7 failure modes have tests
- Five dashboard screens, bilingual, styled, zero external requests verified
- Cloud sync verified under two mid-flight outages: 79,037 = 79,037 rows, zero duplicates, matching id checksum

## Verified numbers worth keeping

| Check | Result |
|---|---|
| One box past a portal | 191 published → 191 stored |
| 50-box pallet | 1,500 raw reads → 47 observations (1 pallet + 46 readable, 4 missed at 8%) |
| Tag parked 10 min | 2,000+ raw reads → **1** movement |
| Sustained throughput | 9,000 over 10s, zero loss |
| SIGKILL mid-batch | 300 of 300 redelivered |

## Not started

- **Step 10** — swap the simulator for a real reader (needs hardware)
- Demo seed command (one command that populates a realistic system for showing people)
- Fresh-machine smoke test (the warehouse box will be a clean install)
- Real SKU and customer data — `seeds/*.csv` still hold placeholder rows

## Blocked on the RF test

- `mode`: `box_level` / `pallet_level` / `hybrid`
- `tag_class` per SKU family
- Whether pallets are shrink-wrapped tight or loosely stacked (Christian's team says less dense than assumed; unverified)

## Blocked on Christian

- Label dimensions (mm)
- Printer make and model
- Photos of pallets, boxes and contents for Beontag
- **Date conflict: Beontag's email says 16 Sep 11:30; Christian says 17 Sep 09:00. Unresolved.**

## Deferred

- Neon connection string (sync runs against a local second Postgres standing in for the cloud)
- Reader and antenna supplier — Beontag sells labels only; ask them for an integrator referral

## Known unknowns

RF read rate on dense liquid loads (bag-in-box syrup, foil-lined Tetra Pak). Estimates ranged 85–96% for tightly packed pallets. The container model means either answer works, but the number decides `mode`.
