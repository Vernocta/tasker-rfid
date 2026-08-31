# Tasker RFID Stock Control — Technical Specification

**Version:** 2.0
**Status:** Implementation spec. Build against simulated data; hardware arrives later.

---

## 1. What this system does

Tracks finished-goods inventory at Tasker S.A. by reading UHF RFID tags at chokepoints in the warehouse. Answers three questions at any moment:

1. What is in stock, by SKU and lot?
2. What left the building, when, and to which customer?
3. What is the consumption rate per customer per SKU?

Question 3 is the business objective. Tasker sells machines once and consumables forever, so consumption rate per account is the number that drives purchasing, production planning, and sales attention.

---

## 2. Core architectural decisions

These are settled. Do not revisit them during implementation.

### 2.1 The tag is a serial number, not a database

Every EPC Gen2 chip has a factory-locked, globally unique **TID**. It is the primary key. Nothing is written to the tag.

```
tid → container → contents → SKU, lot, quantity
```

Consequence: no RFID encoder/printer is required. Generic pre-printed tag stock works. Data model changes never require re-tagging.

### 2.2 Containers, not cartons

A **container** is anything that holds product and carries a tag. A box is a container. A pallet is a container. A reusable tote is a container. Containers nest.

```
pallet P-1183
  ├── box A7F3…
  ├── box B21C…
  └── ...
```

This is the key flexibility decision. A pending physical test will determine whether tags go on individual boxes, on pallets only, or both. The container model supports all three **with no schema change** — it becomes a configuration choice, not a rewrite.

- Box-level: every box is a container, pallets optional
- Pallet-level: only pallets are containers, `declared_contents` holds SKU + quantity
- Hybrid: both, with parent-child links

### 2.3 State, not counts

The system never counts crossings. Each container has a status:

```
REGISTERED → IN_STOCK → DISPATCHED
```

Repeated reads of a container already in a state are idempotent no-ops. This structurally eliminates double-counting, which is the dominant failure mode of naive RFID portals.

### 2.4 Local-first persistence

Postgres runs on the edge device in the warehouse. Cloud sync is asynchronous. Warehouse networks are unreliable and a lost dispatch read is an unrecoverable inventory error.

### 2.5 Every dispatch is attributed

A dispatch event without a destination is an anomaly, not a valid state. The operator selects the customer/order at the dock before loading; everything read during that window is attributed to it.

---

## 3. Data model

```sql
-- ============ Catalogue ============

CREATE TABLE skus (
  sku_id        TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  family        TEXT NOT NULL,   -- 'cones','cups','powder','bag_in_box','tetra','sauce','accessories'
  units_per_box INTEGER NOT NULL,
  tag_class     TEXT,            -- 'paper','paper_long','on_metal' (set after RF test)
  active        BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE customers (
  customer_id   TEXT PRIMARY KEY,
  name          TEXT NOT NULL,
  active        BOOLEAN NOT NULL DEFAULT TRUE
);

-- ============ Physical objects ============

CREATE TABLE containers (
  container_id  BIGSERIAL PRIMARY KEY,
  tid           TEXT UNIQUE NOT NULL,        -- factory-locked chip serial
  epc           TEXT,                        -- informational only
  kind          TEXT NOT NULL,               -- 'BOX','PALLET','TOTE'
  parent_id     BIGINT REFERENCES containers(container_id),
  status        TEXT NOT NULL DEFAULT 'REGISTERED',
  reusable      BOOLEAN NOT NULL DEFAULT FALSE,  -- pallets/totes: TRUE
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at  TIMESTAMPTZ,
  last_portal   TEXT
);
CREATE INDEX ON containers (status);
CREATE INDEX ON containers (parent_id);
CREATE INDEX ON containers (kind, status);

-- Contents of a container. A box has one row. A pallet-level container
-- may have several (mixed-SKU pallet). Empty for a pallet whose contents
-- are derived from child boxes.
CREATE TABLE container_contents (
  id            BIGSERIAL PRIMARY KEY,
  container_id  BIGINT NOT NULL REFERENCES containers(container_id) ON DELETE CASCADE,
  sku_id        TEXT NOT NULL REFERENCES skus(sku_id),
  quantity      INTEGER NOT NULL,       -- number of boxes
  lot           TEXT,
  produced_at   DATE,
  expiry        DATE
);
CREATE INDEX ON container_contents (container_id);
CREATE INDEX ON container_contents (sku_id);

-- ============ Read pipeline ============

-- Append-only. NEVER delete or truncate. Replay source of truth.
CREATE TABLE reads_raw (
  id          BIGSERIAL PRIMARY KEY,
  tid         TEXT NOT NULL,
  epc         TEXT,
  reader_id   TEXT NOT NULL,
  antenna_id  SMALLINT NOT NULL,
  rssi        SMALLINT,
  read_at     TIMESTAMPTZ NOT NULL,
  ingested_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON reads_raw (tid, read_at);
CREATE INDEX ON reads_raw (read_at);

-- Debounced and filtered
CREATE TABLE observations (
  id           BIGSERIAL PRIMARY KEY,
  tid          TEXT NOT NULL,
  portal       TEXT NOT NULL,        -- 'ENTRANCE','EXIT'
  direction    TEXT,                 -- 'IN','OUT','UNKNOWN'
  first_read   TIMESTAMPTZ NOT NULL,
  last_read    TIMESTAMPTZ NOT NULL,
  read_count   INTEGER NOT NULL,
  peak_rssi    SMALLINT,
  processed    BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX ON observations (processed) WHERE processed = FALSE;

-- ============ Business events ============

CREATE TABLE dispatch_sessions (
  session_id   BIGSERIAL PRIMARY KEY,
  customer_id  TEXT REFERENCES customers(customer_id),
  order_ref    TEXT,
  opened_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at    TIMESTAMPTZ,
  operator     TEXT
);

CREATE TABLE movements (
  id           BIGSERIAL PRIMARY KEY,
  container_id BIGINT NOT NULL REFERENCES containers(container_id),
  from_status  TEXT,
  to_status    TEXT NOT NULL,
  portal       TEXT,
  session_id   BIGINT REFERENCES dispatch_sessions(session_id),
  occurred_at  TIMESTAMPTZ NOT NULL,
  source       TEXT NOT NULL         -- 'PORTAL','HANDHELD','MANUAL'
);
CREATE INDEX ON movements (container_id, occurred_at);
CREATE INDEX ON movements (session_id);

CREATE TABLE anomalies (
  id           BIGSERIAL PRIMARY KEY,
  tid          TEXT,
  container_id BIGINT REFERENCES containers(container_id),
  kind         TEXT NOT NULL,
  detail       JSONB,
  occurred_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved     BOOLEAN NOT NULL DEFAULT FALSE,
  resolved_by  TEXT,
  resolved_at  TIMESTAMPTZ
);
CREATE INDEX ON anomalies (resolved) WHERE resolved = FALSE;

-- Anomaly kinds:
--   UNKNOWN_TID          read from a tag with no container record
--   ILLEGAL_TRANSITION   e.g. DISPATCHED → DISPATCHED
--   NO_DIRECTION         portal could not resolve direction
--   NO_SESSION           exit read with no open dispatch session
--   SHORT_PALLET         pallet read but child count < declared
--   COUNT_MISMATCH       cycle count variance

-- ============ Cycle counts ============

CREATE TABLE cycle_counts (
  id           BIGSERIAL PRIMARY KEY,
  started_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at  TIMESTAMPTZ,
  operator     TEXT,
  notes        TEXT
);

CREATE TABLE cycle_count_items (
  cycle_id     BIGINT NOT NULL REFERENCES cycle_counts(id) ON DELETE CASCADE,
  tid          TEXT NOT NULL,
  found        BOOLEAN NOT NULL,
  PRIMARY KEY (cycle_id, tid)
);
```

### 3.1 Key queries

**Stock on hand:**
```sql
SELECT s.sku_id, s.name, SUM(cc.quantity) AS boxes
FROM containers c
JOIN container_contents cc ON cc.container_id = c.container_id
JOIN skus s ON s.sku_id = cc.sku_id
WHERE c.status = 'IN_STOCK'
  AND c.parent_id IS NULL          -- avoid double-counting nested containers
GROUP BY s.sku_id, s.name
ORDER BY s.name;
```

**Consumption per customer per SKU, last 90 days:**
```sql
SELECT cu.name, s.name AS sku, SUM(cc.quantity) AS boxes
FROM movements m
JOIN dispatch_sessions ds ON ds.session_id = m.session_id
JOIN customers cu ON cu.customer_id = ds.customer_id
JOIN container_contents cc ON cc.container_id = m.container_id
JOIN skus s ON s.sku_id = cc.sku_id
WHERE m.to_status = 'DISPATCHED'
  AND m.occurred_at > now() - INTERVAL '90 days'
GROUP BY cu.name, s.name
ORDER BY cu.name, boxes DESC;
```

---

## 4. Read pipeline

Four layers. Each is independently testable.

### Layer 1 — Ingest
Subscribe to MQTT, validate, insert into `reads_raw`. Nothing else. Must never block or drop.

### Layer 2 — Debounce
Group by `(tid, portal)`. Emit one `observation` when a TID has been absent for `quiet_period_ms` (default 2000). Collapses thousands of raw reads into one event.

### Layer 3 — Direction
- **Entrance:** state machine only. A container entering storage is unambiguous.
- **Exit:** IR beam gating. Reader inventories only during a beam-break window; break order gives direction.

Reject observations with `peak_rssi < rssi_floor` (default −65 dBm) or `read_count < min_read_count` (default 3).

### Layer 4 — State engine
Apply the transition. **All status changes go through this module — no other code writes `containers.status`.**

Rules:
- Illegal transitions → `anomalies`, never silently dropped
- Exit read with no open `dispatch_session` → `NO_SESSION` anomaly
- Moving a pallet moves all children in the same transaction
- Unknown TID → `UNKNOWN_TID` anomaly, not counted
- Reusable containers (pallets, totes) return to `REGISTERED` on re-entry rather than being consumed

---

## 5. Services

```
services/
  ingest/        MQTT → reads_raw
  debouncer/     reads_raw → observations
  state_engine/  observations → movements | anomalies
  api/           FastAPI
  sync/          local Postgres → cloud replica
  simulator/     fake reader for development   ← BUILD THIS FIRST
web/             dashboard
```

### 5.1 The simulator is the most important early component

A CLI that publishes synthetic reads to MQTT, reproducing every failure mode:

```bash
sim pallet --boxes 50 --portal EXIT --miss-rate 0.08
sim box --tid A7F3 --portal ENTRANCE
sim stray --tid B21C --duration 600   # box parked near antenna
sim reverse --tid C99A --portal ENTRANCE
sim burst --count 900                 # throughput test
```

Every failure mode in Section 7 must have a corresponding simulator command and an automated test. This is what allows the entire system to be built and validated before any hardware exists.

---

## 6. API

```
GET  /stock                          stock on hand by SKU
GET  /stock/{sku_id}                 containers holding this SKU
GET  /containers/{tid}               full history
POST /containers                     register a container + contents
POST /containers/{tid}/children      attach child containers (pallet build)

POST /dispatch-sessions              open (customer, order_ref)
POST /dispatch-sessions/{id}/close   close
GET  /dispatch-sessions/{id}         contents read during session

POST /cycle-counts                   start
POST /cycle-counts/{id}/scan         {tid}
POST /cycle-counts/{id}/close        variance report

GET  /anomalies?resolved=false       queue
POST /anomalies/{id}/resolve         disposition

GET  /reports/consumption            per customer per SKU per period
GET  /health                         reader status, last read, queue depth
```

---

## 7. Failure modes — each needs a test

| Failure | Handling |
|---|---|
| Stationary stock read continuously | RSSI floor + state idempotency + 3 m floor-marked exclusion zone |
| Container missed at portal | Cycle count reconciliation; `SHORT_PALLET` anomaly if children < declared |
| Container counted twice | Structurally impossible (unique TID + state machine) |
| Carried back out the entrance | `ILLEGAL_TRANSITION` anomaly + manual correction |
| Exit read, no session open | `NO_SESSION` anomaly; blocks silent unattributed dispatch |
| Reader offline | `/health`; alert if no read in 4 h during working hours |
| Network drop | Local Postgres primary, async sync |
| Tag destroyed | Cycle count catches it; re-register |
| Pallet read but boxes missed | Declared contents vs read children → `SHORT_PALLET` |

---

## 8. Configuration

```yaml
mode: hybrid                # box_level | pallet_level | hybrid

rf:
  tx_power_dbm: 25
  session: 2

filters:
  quiet_period_ms: 2000
  rssi_floor_dbm: -65
  min_read_count: 3

portals:
  entrance:
    antennas: [1, 2]
    direction_mode: state_machine
  exit:
    antennas: [3, 4]
    direction_mode: ir_gated
    ir_gate_timeout_ms: 3000
    require_session: true

health:
  no_read_alert_hours: 4
  working_hours: "08:00-18:00"
  timezone: "America/Argentina/Buenos_Aires"
```

---

## 9. Build order

1. Repo scaffold, `docker-compose` (Postgres + Mosquitto), migrations
2. Seed data: SKUs, customers
3. **Simulator** — fake reader publishing to MQTT
4. Ingest service
5. Debouncer
6. State engine + full test suite against simulator
7. API
8. Dashboard
9. Sync worker
10. Swap simulator for real reader

**Steps 1–9 require no hardware.**

---

## 10. Acceptance criteria

1. Simulator reproduces all Section 7 failure modes; all handled correctly
2. Zero double-counts under any simulated input
3. No dispatch recorded without a customer attribution
4. 900 reads/sec sustained without loss
5. Survives Postgres restart and MQTT broker restart without losing `reads_raw`
6. Consumption report returns correct figures for seeded test data

---

## 11. Open items (pending physical test)

- `mode`: box_level / pallet_level / hybrid — decided by RF read-rate test
- `tag_class` per SKU family — decided by read-distance test
- Whether pallets are shrink-wrapped tight or loosely stacked
- Label dimensions and printer model, for RFID label stock compatibility
