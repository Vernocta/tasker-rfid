# Current state

_Last updated: after the context audit's findings were fixed._

## Working

- Steps 1–9 of the build order complete
- **165 tests passing** (77 unit + 88 integration)
- Full pipeline verified end to end with the simulator on Christian's Mac
- All nine SPEC §7 failure modes have tests
- Five dashboard screens, bilingual, styled, zero external requests verified
- Cloud sync verified under two mid-flight outages: 79,037 = 79,037 rows, zero duplicates, matching id checksum
- Context bundle split into `.claude/context/`, audited against the code, and every finding fixed (feedback #13–#16)
- Every service now names the migration command when the schema is behind, rather than retrying against an error nobody can act on (feedback #10)
- Verified live: hiding a local table makes sync say *warehouse*, not *cloud*; ingest holds its message unacknowledged and inserts it once the table is back; the API answers 503 with the command to run

## Verified numbers worth keeping

| Check | Result |
|---|---|
| One box past a portal | published == stored, every time (the count is seed-dependent: 180–193) |
| 50-box pallet | 1,500 raw reads → 47 observations (1 pallet + 46 readable, 4 missed at 8%) |
| Tag parked 10 min | 2,000+ raw reads → **1** movement |
| Sustained throughput | 9,000 over 10s, zero loss |
| SIGKILL mid-batch | 300 of 300 redelivered |

## Config that is written but not read

Two settings remain in `config/tasker.yaml` that nothing consumes. Both are
real pending decisions, so they are kept and **marked in the file itself**:

| Setting | When it becomes live |
|---|---|
| `mode: hybrid` | When the RF test decides the topology. Switching is a code change, not a config change. |
| `rf.tx_power_dbm`, `rf.session` | Build step 10, when there is a physical reader. |

`direction_mode` and `require_session` were deleted from both the config and
SPEC §8: neither was read, and `require_session` in particular implied a
dispatch could be allowed without a customer, which is the one thing the
system must never do.

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
