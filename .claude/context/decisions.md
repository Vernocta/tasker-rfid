# Decisions

All active unless marked otherwise.

## RF and hardware

**UHF RFID, not NFC.** NFC reads at 1–4 cm and talks to one tag at a time. A portal needs metres and anticollision. This reversed Christian's original plan.

**902–928 MHz (FCC band).** Argentina is ITU Region 2. EU 865–868 tags underperform without obviously failing, which is the worst kind of fault. Confirmed viable: Beontag's M830/M780 chips are broadband.

**Tags go outside the carton, on the label.** Cardboard is transparent at 915 MHz (εr ≈ 1.5) — it is a spacer, not a shield. Contents still detune the antenna from ~4 mm away.

## Architecture

**The TID is the primary key. Nothing is written to the tag.** Factory-locked, globally unique. Consequences: no RFID encoder needed (saves US$3–5k), generic tag stock works, double-counting becomes structurally impossible, lot tracking is free.

**Containers, not cartons.** A box, pallet or tote are all containers; they nest via `parent_id`. **The schema supports all three topologies — box-level, pallet-level, hybrid — without a migration.** Switching between them is a code change, not a config change: the `mode:` key exists in `config/tasker.yaml` but nothing reads it, and it is marked as such in the file. What the RF test cannot do is force a schema rewrite.

**State, not counts.** `REGISTERED → IN_STOCK → DISPATCHED`. Re-reads are idempotent no-ops. This is what kills the parked-tag failure mode (2,000+ raw reads → 1 movement, proven by test).

**Local-first persistence.** Postgres on the edge; cloud sync is asynchronous and opens its local connection **read-only**, with its cursor stored in the cloud. "Local stays primary" is structural, not promised.

**Acknowledge-after-commit in ingest.** Under a database outage the system applies backpressure and eventually stops accepting reads, rather than accepting reads it cannot keep. Loud failure over quiet corruption. Verified: SIGKILL holding 300 uncommitted reads lost nothing.

**Every dispatch is attributed.** No session, no dispatch.

## Judgement calls (each flagged before implementing)

| Decision | Reasoning |
|---|---|
| A returning reusable container **detaches** its children, which stay `DISPATCHED` | An empty pallet returns empty. Boxes at a customer's premises must not reappear as stock. Customer returns are a separate process for a human. |
| A dispatched non-reusable box reappearing → `ILLEGAL_TRANSITION` | A customer return isn't modelled; flag it rather than silently altering stock. |
| Goods leaving that were never booked in → `ILLEGAL_TRANSITION` | Same. |
| Manual correction applies to the **named container only**, no cascade | A correction is a targeted human decision. Cascading would change what the operator didn't ask for. |
| Malformed MQTT messages are acked and dropped, logged and counted | Otherwise the broker redelivers a poison message forever and wedges the queue. |
| Timestamps without timezone are kept as UTC with a warning | `reads_raw` is replayable, so a questionable timestamp can be corrected. A rejected read is gone forever. |
| RSSI/read-count filters live in layer 2, not layer 3 as SPEC §4 implies | They read aggregate properties that only exist after debouncing. |
| Direction `UNKNOWN` ≠ `NULL` | `NULL` = no layer has looked (entrance, which has no gate). `UNKNOWN` = the gate looked and couldn't say → `NO_DIRECTION` anomaly. |
| IR beams named `INNER`/`OUTER`, not A/B | Direction reads straight off the data: inner-first = leaving. |
| The mockup's CERRADO/ABIERTO header buttons were dropped | They were artboard state switches. On the real screen the dock is open because the warehouse says so; a button that appeared to toggle it would lie. |

## Settings that were removed rather than wired up

`direction_mode` and `require_session` were deleted from `config/tasker.yaml`
and SPEC §8. Neither was ever read. A portal is gated if it has a `gate_id`,
which makes `direction_mode` redundant; and a dispatch **always** requires an
open session (§2.5), so a key implying that could be switched off was a hazard
dressed as a setting. `mode` and the `rf:` block were kept — both are genuine
pending decisions — but marked in the file as not yet read.

## Reversed / corrected

**SPEC §3.1's stock query had `AND c.parent_id IS NULL`.** Wrong in hybrid mode: boxes carry contents and have a parent, so the guard filtered out exactly the rows holding the quantities — all palletised stock was invisible. The consumption query never had the guard, so the spec contradicted itself. Removed; SPEC corrected.

## Tables added beyond SPEC §3 (all additive)

`debouncer_cursor`, `gate_events`, `sync_cursor`, `reason` and `operator` on `movements` (for manual corrections), and `updated_at` columns on nine mutable tables. SPEC §3 has been brought back in line and verified by executing its DDL into a scratch schema.
