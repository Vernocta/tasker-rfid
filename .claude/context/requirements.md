# Requirements

Marked **[E]** explicit from Christian, **[D]** derived from the spec/architecture, **[A]** assumption.

## Functional

- **[E]** Track every carton and pallet leaving the warehouse, attributed to a customer
- **[E]** Report consumption per customer per SKU over a date range
- **[D]** Stock on hand by SKU, live
- **[D]** Cycle counting to reconcile the floor against the system
- **[D]** Anomaly queue for everything the system cannot resolve
- **[D]** Manual correction with recorded operator and reason

## Must not change

- **[E]** Only the state engine writes `containers.status`. Manual corrections go *through* it, not around it.
- **[E]** No dispatch without an open session naming a customer. An exit read with no session raises `NO_SESSION` and the container does not move.
- **[E]** `reads_raw` is append-only and never truncated. It is the replay source of truth.
- **[E]** Local Postgres is primary. Nothing depends on the cloud being reachable.
- **[E]** Nothing loads from the internet — Tailwind and fonts are vendored.
- **[D]** Reads are acknowledged to the broker only after Postgres commits.

## UX

- **[E]** Simple enough for an administrative employee under time pressure. *Design for busy and interrupted, not for unintelligent.*
- **[E]** Dock screen readable from 2–3 m. Large type, high contrast, numbers dominant.
- **[E]** Plain: no cards, shadows, gradients, or decorative icons.
- **[E]** Spanish labels, bilingual ES/EN with a switch.
- **[D]** 24-hour clock (Argentine convention; also fixes column wrapping).
- **[D]** Empty states say what would fill them.
- **[D]** The read list is fixed-height and scrolls — it must never push the close button off screen.

## The failure the dispatch screen exists to prevent

Loading for the wrong customer because a previous session was left open. The open state must be unmistakable from across the room.

## Acceptance criteria (SPEC §10)

1. All SPEC §7 failure modes reproduced by the simulator and handled correctly
2. Zero double-counts under any input
3. No dispatch recorded without customer attribution
4. 900 reads/sec sustained without loss
5. Survives Postgres and MQTT restarts without losing `reads_raw`
6. Consumption report correct for seeded data
