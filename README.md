# Tasker RFID Stock Control

RFID-based finished-goods stock control for Tasker S.A.

**`SPEC.md` in this directory is the authoritative specification.** If the code
and the spec disagree, the spec wins and the code is a bug.

Current state: **build order step 2 of 10** — scaffold, containers, database
schema, and seed data. No services are implemented yet.

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

*Expect:* `tasker-postgres` and `tasker-mosquitto`, both `running`. Postgres
should say `(healthy)` after a few seconds.

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
docker-compose.yml           Postgres 16 + Mosquitto
docker/mosquitto/            broker config
config/tasker.yaml           runtime tuning (SPEC.md §8)
alembic.ini                  migration tool config
migrations/versions/         database schema, one file per change
scripts/check_schema.sql     the "did it work" query above
seeds/                       SKU and customer spreadsheets (CSV)
src/tasker_rfid/seed.py      loads seeds/ into the database
src/tasker_rfid/services/    ingest, debouncer, state_engine, api, sync, simulator
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
