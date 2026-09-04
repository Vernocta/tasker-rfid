# Tasker RFID Stock Control

RFID-based finished-goods stock control for Tasker S.A.

**`SPEC.md` in this directory is the authoritative specification.** If the code
and the spec disagree, the spec wins and the code is a bug.

Current state: **build order step 7 of 10** — everything through the API.
Reads flow from the simulator to stock movements and anomalies, and the API
serves stock, dispatch, cycle counts, anomalies, consumption and health at
**http://localhost:8000/docs**. No dashboard or cloud sync yet.

---

## What you need installed first

1. **Docker Desktop** — <https://www.docker.com/products/docker-desktop/>
   Install it, launch it, and leave it running. Everything below assumes the
   whale icon in your menu bar / system tray is not showing an error.
2. **uv** — the Python package manager this project uses.
   - macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
   - Windows (PowerShell): `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`

   You do **not** need to install Python separately; `uv` handles that.

Check both are working:

```bash
docker --version
uv --version
```

Each should print a version number. If either says "command not found", the
install did not finish — restart your terminal and try again.

---

## First-time setup

Run these from the project root, in order.

**1. Create your local settings file**

```bash
cp .env.example .env
```

`.env` holds your database password and connection details. It is git-ignored
and must never be committed. The defaults work as-is for local development.

**2. Install the Python dependencies**

```bash
uv sync
```

*Expect:* a list of installed packages ending with `tasker-rfid`.

**3. Start Postgres and the MQTT broker**

```bash
docker compose up -d
```

*Expect:* two lines ending in `Started` (or `Running`).

Confirm both containers are healthy:

```bash
docker compose ps
```

*Expect:* `tasker-postgres`, `tasker-mosquitto`, `tasker-ingest`,
`tasker-debouncer`, `tasker-state-engine` and `tasker-api`, all `running`.
Postgres should say `(healthy)` after a few seconds. The API is then at
http://localhost:8000/docs

The first `docker compose up -d` builds the ingest image and takes a couple of
minutes. Later ones are quick.

*If Postgres says port 5432 is already in use:* you have another Postgres on
this machine. Change `POSTGRES_PORT` in `.env` to `5433`, update the port in
`DATABASE_URL` to match, and run `docker compose up -d` again.

**4. Create the database tables**

```bash
uv run alembic upgrade head
```

*Expect:* `Running upgrade  -> 0001_initial_schema, Initial schema — SPEC.md section 3.`

*If it says `DATABASE_URL is not set`:* you skipped step 1.
*If it says `connection refused`:* Postgres is not up yet — wait ten seconds
and run it again.

**5. Confirm every table exists**

```bash
docker compose exec -T postgres psql -U tasker -d tasker -f - < scripts/check_schema.sql
```

*Expect:* eleven rows, every one saying `ok`:

```
     table_name     | status
--------------------+--------
 anomalies          | ok
 container_contents | ok
 containers         | ok
 customers          | ok
 cycle_count_items  | ok
 cycle_counts       | ok
 dispatch_sessions  | ok
 movements          | ok
 observations       | ok
 reads_raw          | ok
 skus               | ok
(11 rows)
```

Any row saying `MISSING` means the migration did not fully apply. Re-run
step 4 and read the error.

---

**6. Load the SKU and customer lists**

```bash
uv run tasker-seed
```

*Expect:*

```
skus:      4 rows loaded from .../seeds/skus.csv
customers: 3 rows loaded from .../seeds/customers.csv
database now holds 4 skus and 3 customers
```

Safe to run as many times as you like — see *Editing the SKU and customer
lists* below.

---

## Everyday commands

```bash
docker compose up -d      # start Postgres + MQTT
docker compose down       # stop them (data is kept)
docker compose logs -f    # watch what they are doing

uv run alembic upgrade head       # apply any new migrations
uv run alembic current            # which migration is applied
uv run alembic downgrade base     # drop all tables (destroys data)

uv run pytest tests/unit          # fast tests
uv run pytest tests/integration   # failure-mode tests (stack must be up)
```

Open a database prompt:

```bash
docker compose exec postgres psql -U tasker -d tasker
```

Type `\dt` to list tables, `\d containers` to inspect one, `\q` to quit.

`docker compose down -v` deletes the database volume and all its data. Do not
run it unless you mean to start clean.

---

## Editing the SKU and customer lists

The SKUs and customers live in two spreadsheets in `seeds/`:

| File | Columns |
|---|---|
| `seeds/skus.csv` | `sku_id, name, family, units_per_box, tag_class, active` |
| `seeds/customers.csv` | `customer_id, name, active` |

Open them in Excel, Numbers or Google Sheets, edit, **save as CSV** (keep the
same file name), then run:

```bash
uv run tasker-seed
```

Rules the script enforces — it refuses the whole file and writes nothing if
any row breaks one, and tells you which row:

- `sku_id` / `customer_id` — required, must be unique. This is the permanent
  identifier; do not change it once the SKU or customer is in use.
- `family` — one of `cones`, `cups`, `powder`, `bag_in_box`, `tetra`, `sauce`,
  `accessories`.
- `units_per_box` — whole number above 0.
- `tag_class` — leave blank until the RF test decides it (SPEC.md §11); then
  `paper`, `paper_long` or `on_metal`.
- `active` — `true` or `false` (`yes`/`no` also accepted).

Running the script again is safe. A row whose ID already exists is **updated**
to match the spreadsheet; a new ID is **added**; nothing is ever duplicated.

**Deleting a row from the CSV does not delete it from the database.** Old
stock and dispatch history may point at that SKU or customer, and removing it
would break the consumption report. To retire something, set `active` to
`false` instead.

---

## The simulator

A fake RFID reader. It publishes to the same MQTT broker a real reader will,
in the same format, so everything else can be built and tested before any
hardware exists (SPEC.md §5.1).

Add `--dry-run` to any command to print the messages instead of publishing
them. That needs no broker and is the quickest way to see what is going on.

```bash
uv run sim box     --tid A7F3 --portal ENTRANCE     # one box through a portal
uv run sim pallet  --boxes 50 --portal EXIT --miss-rate 0.08
uv run sim stray   --tid B21C --duration 600        # box parked near an antenna
uv run sim reverse --tid C99A --portal ENTRANCE     # carried back out
uv run sim burst   --count 900                      # throughput test
```

*Expect*, for the first one:

```
box A7F3 through ENTRANCE on antennas [1, 2]
published 193 reads to tasker/reads/... in 1.79s (108 reads/sec)
```

Nearly two hundred messages for one box is correct, not a bug — see below.

*If it says* `could not reach the MQTT broker`: the containers are not
running. `docker compose up -d`.

Useful flags on every command:

| Flag | What it does |
|---|---|
| `--dry-run` | print the messages, publish nothing, no broker needed |
| `--speed N` | `1` is real time; `60` compresses a minute into a second; `0` is as fast as possible |
| `--seed N` | repeat a run exactly — same seed, same reads |
| `--portal` | `ENTRANCE` or `EXIT`; picks the antennas from `config/tasker.yaml` |

`sim stray --duration 600` takes ten real minutes at default speed. Add
`--speed 60` to watch it in ten seconds.

To watch the messages arriving while a command runs, open a second terminal:

```bash
docker compose exec mosquitto mosquitto_sub -t 'tasker/reads/#' -q 1
```

### The message format

One JSON message per read, published on `tasker/reads/<reader_id>`:

```json
{
  "tid": "E28011702000A7F312345678",
  "epc": "3034F4A2B1C80D0000000001",
  "reader_id": "READER-EXIT",
  "antenna_id": 3,
  "rssi": -52,
  "read_at": "2026-09-03T14:22:31.482000+00:00"
}
```

Those six fields are exactly the six columns of `reads_raw` (SPEC.md §3).
SPEC.md §4 says the ingest service must do nothing but validate and insert,
so the message deliberately carries nothing that needs interpreting first.

**`portal` is not in the message, deliberately.** A reader knows it has
antennas 1 to 4; it does not know the warehouse calls antennas 3 and 4 "the
exit". `reads_raw` has no portal column either — portal first appears on
`observations`, once `config/tasker.yaml` has been consulted. The simulator
reads that same file to decide which antennas to fire.

Messages are published at **QoS 1** (at-least-once). A warehouse network
drops out and a lost dispatch read is an unrecoverable inventory error
(SPEC.md §2.4), so losing a message is unacceptable. A *duplicated* message
is harmless — the state machine in SPEC.md §2.3 makes seeing the same tag
twice a no-op. QoS 1 trades duplicates, which cost nothing, for losses,
which cost everything.

### Why one box produces two hundred messages

A real reader does not report "a box went past". It interrogates its
antennas dozens of times a second and reports every single time a tag
answers. One box through a portal in under two seconds produces roughly
50–300 separate reads, and their signal strength rises as the box approaches
and falls as it leaves. Collapsing that back into one business event is the
entire job of the debouncer and the state engine. If the simulator emitted
one tidy message per tag, every test written against it would be testing a
fiction.

So the simulator models:

- **Rise and fall.** Signal peaks at the closest point of approach and drops
  about 25 dB by the edges of the field, plus noise.
- **Weak signal means missed reads.** A passive tag runs on the reader's own
  energy, so the edges of a pass are sparse and the middle is dense. Reads
  below about −75 dBm do not happen at all.
- **A shared attention budget.** A reader time-slices between tags. One tag
  alone is read constantly; fifty boxes on a pallet each get a fraction. This
  is why `sim pallet --boxes 50` produces about 1,500 reads rather than
  50 × 200.
- **Shadowing.** Boxes buried inside a stack read weaker than the pallet tag
  on the outside — which is why pallets lose boxes, and why `SHORT_PALLET`
  is a failure mode in the first place.
- **Antenna order.** The two antennas of a portal are separated along the
  direction of travel, so one peaks slightly before the other. `sim reverse`
  flips that order. It is the only thing in the raw stream that tells
  "came in" from "carried back out".

The numbers behind all of this are constants at the top of
`src/tasker_rfid/services/simulator/model.py`, each with a comment saying
what it represents. They are guesses based on how UHF equipment behaves and
should be re-measured against the real reader when it arrives.

---

## The ingest service

Subscribes to MQTT, validates each message, writes it to `reads_raw`, and
does nothing else (SPEC.md §4, layer 1). It runs as a container alongside
Postgres and Mosquitto, started by `docker compose up -d`.

```bash
docker compose logs -f ingest      # watch it work
docker compose restart ingest      # restart it
```

*Expect*, on startup:

```
ingest running; waiting for reads
connected to MQTT, subscribed to tasker/reads/#
```

and every ten seconds while running:

```
inserted=9784 rejected=0 queued=23
```

`inserted` is the running total written to `reads_raw`. `gate_events` counts IR beam
changes stored. `rejected` counts messages that could not become a row — see below. `queued` is how far
behind the database is; it should sit at or near zero and come back down
after a burst. A `queued` figure that climbs and stays high means Postgres
cannot keep up.

To confirm reads are landing:

```bash
docker compose exec postgres psql -U tasker -d tasker -c "select count(*) from reads_raw;"
```

Run `uv run sim box --tid A7F3 --portal ENTRANCE`, then run that count
again — it should have gone up by roughly two hundred.

### Never block, never drop

Those two requirements pull against each other, and the way they are
reconciled is the main thing to understand about this service.

**Never block.** The MQTT callback does almost nothing: it drops the raw
bytes on an in-memory queue and returns. Decoding, validating and writing
all happen on a separate writer thread. Nothing touches the database on the
network thread, so a slow database cannot stall the MQTT client.

**Never drop.** The writer acknowledges an MQTT message only *after*
Postgres has committed it. Until then the message still belongs to the
broker. If this service crashes, or the machine loses power, the broker
redelivers everything that was never acknowledged.

**When Postgres is down**, the writer stops acknowledging. Unacknowledged
messages pile up at the broker, the broker stops sending, and the system
applies backpressure instead of losing reads. When Postgres returns, the
same batch is retried. The cost, stated plainly: during a database outage
ingest slows down and eventually stops accepting reads, rather than
accepting reads it cannot keep. For stock control that is the right way
round.

Two settings exist purely to support this and should not be lowered:
`INGEST_CLIENT_ID` in `.env` (a fixed id plus a persistent session is what
makes the broker hold reads for us) and `max_inflight_messages` in
`docker/mosquitto/mosquitto.conf` (the broker must tolerate many unacked
messages at once, or throughput collapses).

### What counts as a bad message

A message that cannot become a row is logged in full, counted in
`rejected`, and then acknowledged so it does not block everything behind
it. It is not a lost read — it was never a read. The full payload is in
`docker compose logs ingest`, so nothing disappears silently:

```
REJECTED (read_at 'never' is not a valid ISO 8601 timestamp) | payload=b'{"tid":"X",...
```

Refused: malformed JSON, a missing `tid` / `reader_id` / `antenna_id` /
`read_at`, a blank id, a non-numeric antenna, an unparseable timestamp, and
values too large for their column.

Accepted with a warning: a timestamp with no timezone (assumed UTC — a
questionable time can be corrected by replaying `reads_raw`, a rejected
read cannot be recovered at all). Unknown extra fields are ignored, so a
future reader firmware that adds a field does not stop the line.

Duplicate reads are inserted as duplicate rows on purpose. `reads_raw` is
the append-only record of what the antennas actually heard; collapsing
duplicates is the debouncer's job, not ingest's.

---

## The debouncer

Collapses the many raw reads of one physical event into a single
observation (SPEC.md §4, layer 2), then applies the RSSI floor and minimum
read count from `config/tasker.yaml`. Runs as a container alongside the
others.

```bash
docker compose logs -f debouncer
```

*Expect*, on startup:

```
debouncer running; resuming after read id 0. quiet_period=2000ms rssi_floor=-65dBm min_read_count=3
```

and every ten seconds:

```
reads=3120 observations=53 rejected=2 open_groups=0
```

`open_groups` is how many tags are mid-event right now. It should return to
zero shortly after a portal goes quiet.

### The collapse

One box through a portal is a couple of hundred rows in `reads_raw` and
exactly one row in `observations`:

| Command | `reads_raw` | `observations` | Ratio |
|---|---|---|---|
| `sim box` | 188 | 1 | 188 : 1 |
| `sim pallet --boxes 50` | 1,500 | 47 | 32 : 1 |
| `sim stray --duration 120` | 836 | 1 | 836 : 1 |

The pallet's 47 is the interesting number: one pallet tag plus 46 readable
boxes, because 4 of the 50 were missed at the default 8% miss rate. The
count of observations equals the count of distinct tags, which is what
"one observation per physical event" means.

To see it yourself, on an empty database:

```bash
docker compose exec postgres psql -U tasker -d tasker -c "select count(*) from reads_raw;"
uv run sim pallet --boxes 50 --portal EXIT
sleep 8
docker compose exec postgres psql -U tasker -d tasker -c "
  select (select count(*) from reads_raw) as reads_raw,
         (select count(*) from observations) as observations;"
```

### How an event is bounded

A tag's reads at a portal belong to the same event until it has been quiet
there for `quiet_period_ms` (2 seconds). The next read after that silence
starts a new observation, so a box carried past twice produces two rows,
not one.

The quiet period is measured **per (tid, portal)**, against that tag's own
last read — never against a shared "latest read seen anywhere" clock. That
matters: a shared clock lets one reader with a wrong clock decide that
every other tag has gone quiet, which shatters each pass into fragments.
A group is also closed when nothing has arrived for it at all within the
quiet period, which is what ends the last tag of a burst.

A tag parked in the field never goes quiet, so it stays one open group,
however long it sits there. Groups hold running totals rather than the
reads themselves, so this costs nothing in memory.

### Direction, and the IR gate

`observations.direction` says which way something went. How it is decided
depends on the portal, and the two are deliberately different (SPEC.md §4,
layer 3):

| Portal | How | `direction` |
|---|---|---|
| **EXIT** | IR beam gate | `OUT`, `IN`, or `UNKNOWN` |
| **ENTRANCE** | state machine, later | always `NULL` |

The entrance stays NULL on purpose. SPEC.md §4 says a container entering
storage is unambiguous and needs no beams, so the state engine decides it.
NULL here means "not this layer's job", which is why an exit read the gate
could not explain is written as `UNKNOWN` rather than NULL — that
distinguishes "the gate looked and could not say" from "nobody has looked".

**The gate.** Two infrared beams a few centimetres apart across the exit
doorway. `INNER` faces the warehouse, `OUTER` faces the street:

```
   warehouse  |  INNER   OUTER  |  street
                  |       |
   leaving  ------|------>|------>     INNER breaks first  -> OUT
   coming in <----|<------|------      OUTER breaks first  -> IN
```

Break order is the whole signal, and the beams are centimetres apart, so
the gap between them is a few hundredths of a second. The simulator models
that gap, and its jitter, honestly.

The gate publishes each beam change on `tasker/gates/<gate_id>`:

```json
{
  "gate_id": "GATE-EXIT",
  "beam": "INNER",
  "state": "BROKEN",
  "occurred_at": "2026-09-03T14:22:31.100000+00:00"
}
```

Four fields, four columns of `gate_events` — the same rule as tag reads,
so ingest stays a dumb insert. And as with antenna numbers, the message
says *which gate*, never which portal: that mapping is warehouse layout,
and lives in `config/tasker.yaml` under `exit: gate_id:`.

**Matching a crossing to a tag.** The RF field reaches well beyond the
beams, so a tag is read before and after it crosses them — the crossing
window always sits inside the observation window, and overlap is the test.
One pallet is one crossing however many tags ride on it, so all 47
observations of a 50-box pallet take the same direction from the same four
beam events. Where two loads pass in quick succession, the nearer crossing
by midpoint wins.

The 2-second quiet period doubles as the grace period here: an observation
is only written 2 seconds after its last read, by which time the beam
events it needs are long since stored.

**Which commands emit a crossing:**

```bash
uv run sim box     --portal EXIT     # INNER then OUTER  -> OUT
uv run sim reverse --portal EXIT     # OUTER then INNER  -> IN
uv run sim pallet  --portal EXIT     # one crossing for the whole load
uv run sim box     --portal ENTRANCE # no beams at all: the entrance has no gate
uv run sim stray   --portal EXIT     # reads, but nothing crosses -> UNKNOWN
uv run sim burst                     # a throughput test, not a movement
```

`sim reverse --portal ENTRANCE` produces no beam events either, for the
same reason: there is no gate there to break.

---

### What gets thrown away

| Rejected | Because |
|---|---|
| `peak_rssi` below `rssi_floor_dbm` (−65) | a tag two aisles away catching a reflection was never at the portal |
| `read_count` below `min_read_count` (3) | a single spurious read is not a box going past |

Rejections are logged with the reason and counted in `rejected`:

```
rejected FAINT-TAG at EXIT: peak_rssi -78 dBm below floor -65 dBm
rejected BLIP-TAG at EXIT: read_count 2 below minimum 3
```

Rejected events are not written to `observations` at all. Nothing is lost —
every read is still in `reads_raw`, and lowering a threshold in
`config/tasker.yaml` and replaying would produce them.

A read from an antenna that no portal claims is logged and skipped, since
`observations.portal` cannot be guessed. That means a config error, not a
tag problem.

### Not done here

Nothing reads `observations` yet — that is the state engine, step 6. The
debouncer raises no anomalies either; a `NO_DIRECTION` anomaly for an exit
read written as `UNKNOWN` belongs to the state engine.

---

## The state engine

The only code in the system that writes `containers.status`. Ingest, the
debouncer and the API all stop short of it; every state change goes
through this one service (SPEC.md §4, layer 4). One place to reason about,
one place to audit.

```bash
docker compose logs -f state_engine
```

*Expect*, every ten seconds:

```
observations=412 movements=53 anomalies=4 no_ops=359
```

`no_ops` is the healthy number. It counts reads of containers that were
already in the state they would have moved to — a box sitting near an
antenna, a pallet read twice. In a working warehouse it should dwarf
`movements`.

### The transition table

```
REGISTERED  ->  IN_STOCK  ->  DISPATCHED
```

| Where it was seen | Status now | What happens |
|---|---|---|
| Entrance, or exit inbound | `REGISTERED` | → `IN_STOCK` |
| Entrance, or exit inbound | `IN_STOCK` | nothing at all |
| Entrance, or exit inbound | `DISPATCHED`, reusable | → `REGISTERED` (empty pallet back) |
| Entrance, or exit inbound | `DISPATCHED`, not reusable | `ILLEGAL_TRANSITION` |
| Exit, outbound | `IN_STOCK` | → `DISPATCHED`, needs an open session |
| Exit, outbound | `REGISTERED` | `ILLEGAL_TRANSITION` |
| Exit, outbound | `DISPATCHED` | `ILLEGAL_TRANSITION` |
| Exit, `UNKNOWN` direction | anything | `NO_DIRECTION`, nothing moves |
| Any, tag not registered | — | `UNKNOWN_TID`, nothing moves |

Row 2 is the one that matters most. **A container already in the state it
would move to is left alone.** That is why a box parked by an antenna for
ten minutes produces thousands of reads and no stock movement at all —
not because anything detects and discards duplicates, but because there is
nothing for a repeat read to do (SPEC.md §2.3).

Two rows are judgement calls the spec does not state outright, both
resolved the same way — surface it, do not guess:

- **A dispatched box coming back** is a customer return, which this system
  does not model. It raises an anomaly rather than quietly changing stock.
- **Goods leaving that were never booked into stock** skips a state, so a
  person looks rather than the system inventing the missing step.

### Rules it enforces

- **Nothing leaves unattributed.** A dispatch needs an open
  `dispatch_session`. Without one the container stays put and a
  `NO_SESSION` anomaly is raised (SPEC.md §2.5).
- **Moving a pallet moves everything on it**, however deep the nesting, in
  one transaction — including boxes whose tags were never read.
- **A short load is recorded, not blocked.** A pallet read with fewer boxes
  than are attached still leaves, and raises `SHORT_PALLET` alongside.
- Each observation is handled in its own transaction: the status changes,
  the movement rows, any anomaly, and marking the observation processed
  all commit together or not at all.

A pallet's observation is held back a few seconds before its load is
judged. The boxes on a pallet cross the portal together but their
observations are not written together, and counting too early reports
boxes missing that are simply still in flight — a false `SHORT_PALLET` on
a pallet that was complete.

---

## Running the tests

Two sets, two commands.

**Fast tests — no Docker needed.** Pure logic: the transition table, the
debouncer's grouping rules, IR beam direction, ingest validation, the
simulator's read model.

```bash
uv run pytest tests/unit
```

*Expect:* `66 passed in 0.10s`

**Failure-mode tests — the whole system, end to end.** These publish real
MQTT messages with the simulator and wait for ingest, the debouncer and
the state engine to do their work. Nothing is stubbed. Start the stack
first:

```bash
docker compose up -d
uv run alembic upgrade head
uv run pytest tests/integration -v
```

*Expect:* `31 passed`. It takes about two and a half minutes, because the tests wait for the real 2-second quiet periods
rather than pretending.

```
tests/integration/test_failure_modes.py::test_a_box_parked_by_an_antenna_for_ten_minutes_changes_state_once PASSED [  8%]
tests/integration/test_failure_modes.py::test_the_same_container_cannot_be_dispatched_twice PASSED [ 16%]
tests/integration/test_failure_modes.py::test_an_exit_read_with_no_open_session_does_not_dispatch PASSED [ 25%]
tests/integration/test_failure_modes.py::test_the_same_read_dispatches_once_a_session_is_open PASSED [ 33%]
tests/integration/test_failure_modes.py::test_moving_a_pallet_moves_every_box_on_it PASSED [ 41%]
tests/integration/test_failure_modes.py::test_a_pallet_moves_its_boxes_even_when_their_tags_are_never_read PASSED [ 50%]
tests/integration/test_failure_modes.py::test_a_pallet_with_fewer_boxes_than_declared_raises_short_pallet PASSED [ 58%]
tests/integration/test_failure_modes.py::test_a_complete_pallet_raises_nothing PASSED [ 66%]
tests/integration/test_failure_modes.py::test_an_exit_read_the_gate_cannot_explain_does_not_dispatch PASSED [ 75%]
tests/integration/test_failure_modes.py::test_a_dispatched_box_reappearing_is_flagged_not_absorbed PASSED [ 83%]
tests/integration/test_failure_modes.py::test_an_empty_pallet_coming_back_is_ready_to_use_again PASSED [ 91%]
tests/integration/test_failure_modes.py::test_a_tag_with_no_container_record_is_flagged_and_not_counted PASSED [100%]

======================== 12 passed in 99.17s (0:01:39) =========================
```

If the stack is not running you get `12 skipped`, with a line telling you
what to start. That is not a pass — nothing was checked.

**If a test fails**, it prints the assertion and the actual database values
alongside. Send me the whole output.

The tests never delete anything. `reads_raw` is append-only by design
(SPEC.md §3), so each test invents its own tag ids and asserts only on its
own rows. Running them repeatedly is safe, and they leave real history
behind in the same tables the warehouse will use.

### What each failure mode test proves

| SPEC.md §7 failure | Test |
|---|---|
| Stationary stock read continuously | thousands of reads, **one** state change, then nothing |
| Container counted twice | the second exit read is an anomaly, not a second dispatch |
| Exit read, no session open | `NO_SESSION`; the container stays `IN_STOCK` |
| Carried back out the entrance | `ILLEGAL_TRANSITION`; stock unchanged |
| Pallet read but boxes missed | `SHORT_PALLET` naming how many are missing |
| Container missed at portal | a box whose tag never answers still leaves on its pallet |
| Direction unresolved at the exit | `NO_DIRECTION`; nothing is dispatched on a guess |
| Tag with no container record | `UNKNOWN_TID`; not counted |
| Network drop / Postgres restart | ingest holds and retries — see *Never block, never drop* |
| Reader offline | `/health`, step 7 |
| Tag destroyed | cycle count reconciliation, step 7 |

---

## The API

Everything in SPEC.md §6, as a web service. Start the stack and open:

### **http://localhost:8000/docs**

That page lists every endpoint with a **Try it out** button — you can fill
in a form, send a real request and see the real response, without typing
any commands. It is the fastest way to see what the system holds.

```bash
docker compose up -d
```

*Expect:* `tasker-api` in `docker compose ps` with `0.0.0.0:8000->8000/tcp`.

*If the page does not load:* check `docker compose logs api`. If port 8000
is already used by something else on your machine, change `API_PORT` in
`.env` and restart.

The raw specification, for other software to consume, is at
`http://localhost:8000/openapi.json`.

### The endpoints

| | |
|---|---|
| `GET /stock` | boxes of each SKU in the warehouse now |
| `GET /stock/{sku_id}` | which containers hold that SKU |
| `GET /containers/{tid}` | one container's full history |
| `POST /containers` | register a container and its contents |
| `POST /containers/{tid}/children` | build a pallet |
| `POST /dispatch-sessions` | open the dock for a customer |
| `POST /dispatch-sessions/{id}/close` | close it, with a summary of what left |
| `GET /dispatch-sessions/{id}` | what went out during a session |
| `POST /cycle-counts` | start a count |
| `POST /cycle-counts/{id}/scan` | record a tag seen on the floor |
| `POST /cycle-counts/{id}/close` | the variance report |
| `GET /anomalies?resolved=false` | the queue of things needing a decision |
| `POST /anomalies/{id}/resolve` | disposition one |
| `GET /reports/consumption` | boxes per customer per SKU |
| `GET /health` | reader status, last read, queue depth |

### The API never moves stock

Registering a container starts it at `REGISTERED`. Everything after that
happens because a portal read it, and only the state engine applies those
changes (SPEC.md §4). There is no endpoint that sets a status, on purpose:
one writer is what makes double-counting impossible.

So `POST /containers` puts a container in the system; walking it through
the entrance is what puts it in stock.

### A whole cycle, from the terminal

```bash
# 1. register a box with 24 cones in it
curl -X POST http://localhost:8000/containers \
  -H 'content-type: application/json' \
  -d '{"tid":"DEMO-1","contents":[{"sku_id":"CONE-STD-120","quantity":24}]}'

# 2. carry it in through the entrance
uv run sim box --tid DEMO-1 --portal ENTRANCE
sleep 10
curl http://localhost:8000/stock            # 24 boxes of cones

# 3. open the dock for a customer, load it, close the dock
curl -X POST http://localhost:8000/dispatch-sessions \
  -H 'content-type: application/json' \
  -d '{"customer_id":"CUST-0001","order_ref":"SO-99312"}'
uv run sim box --tid DEMO-1 --portal EXIT
sleep 10
curl -X POST http://localhost:8000/dispatch-sessions/1/close

# 4. the number the business runs on
curl 'http://localhost:8000/reports/consumption?days=90'
```

### `/health`, and what to watch

```json
{
  "status": "ok",
  "within_working_hours": true,
  "readers": [
    {"reader_id": "READER-ENTRANCE", "minutes_since_last_read": 0.6, "healthy": true}
  ],
  "queue_depth": {"reads_awaiting_debounce": 0, "observations_awaiting_state_engine": 0},
  "unresolved_anomalies": 5
}
```

`status` turns `degraded` if a reader has been silent during working hours
for longer than `no_read_alert_hours` in `config/tasker.yaml`. A reader
that has quietly died is the most dangerous failure in the system, because
everything downstream keeps working perfectly on a stream of nothing.
Outside working hours, silence is expected and not a fault.

Both `queue_depth` numbers should sit near zero. If either climbs and
stays up, the pipeline is falling behind.

---

## Configuration

Two files, deliberately kept separate:

| File | Holds | Committed? |
|---|---|---|
| `.env` | Connection details and secrets — database, MQTT broker | No, git-ignored |
| `config/tasker.yaml` | RF, filter, portal and health tuning (SPEC.md §8) | Yes |

`.env.example` is the committed template for `.env`. When you add a setting,
add it to `.env.example` too, so the next person knows it exists.

---

## Layout

```
SPEC.md                      the specification — read this first
PROMPTS.md                   the build plan
docker-compose.yml           Postgres 16 + Mosquitto + the four services
Dockerfile                   image for the background services
docker/mosquitto/            broker config
config/tasker.yaml           runtime tuning (SPEC.md §8)
src/tasker_rfid/config.py    the one place that reads tasker.yaml
alembic.ini                  migration tool config
migrations/versions/         database schema, one file per change
scripts/check_schema.sql     the "did it work" query above
seeds/                       SKU and customer spreadsheets (CSV)
src/tasker_rfid/seed.py      loads seeds/ into the database
tests/unit/                  fast tests, no Docker needed
tests/integration/           failure-mode tests against the running stack
src/tasker_rfid/services/    ingest, debouncer, state_engine, api, sync, simulator
  simulator/model.py           what a read burst looks like (pure, testable)
  simulator/publisher.py       MQTT publishing and message format
  simulator/cli.py             the `sim` command
  ingest/validation.py         what counts as a valid read (pure, testable)
  ingest/service.py            MQTT to reads_raw
  debouncer/grouping.py        how reads become one event (pure, testable)
  debouncer/direction.py       beam breaks to IN / OUT (pure, testable)
  debouncer/service.py         reads_raw to observations
  state_engine/transitions.py  the transition table (pure, testable)
  state_engine/service.py      the ONLY writer of containers.status
  api/app.py                   FastAPI app; docs at /docs
  api/routers/                 one module per SPEC.md section 6 group
web/                         dashboard
```

The service directories are empty placeholders. They are filled in from step 3
of the build order onward — the simulator first, per SPEC.md §5.1.

---

## Changing the schema

Never edit an already-applied migration. Create a new one:

```bash
uv run alembic revision -m "short description of the change"
```

Then edit the generated file in `migrations/versions/` and run
`uv run alembic upgrade head`.

Update `SPEC.md` in the same commit — the spec is the source of truth, not the
migration.
