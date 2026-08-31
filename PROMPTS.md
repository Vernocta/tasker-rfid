# Claude Code — Build Prompts

Work through these in order. **One at a time.** After each, run the verification and confirm it works before pasting the next.

If Claude Code proposes something that contradicts `SPEC.md`, stop and ask why. If a command fails, paste the whole error back to Claude Code — don't try to fix it yourself.

---

## Step 0 — Set the ground rules

Paste this once, at the very start of the session. It shapes everything after.

```
Read SPEC.md in the repo root. It is the authoritative specification for this
project. Follow it. If anything I ask contradicts it, tell me instead of
silently diverging.

Context about me: I'm the owner of an importing and distribution business,
not a developer. I can follow instructions and run commands but I won't spot
subtle bugs. So:

- Explain what you're doing in plain language before you do it
- Tell me exactly what to type when I need to run something
- Tell me what I should see if it worked, and what it means if I don't
- Prefer boring, obvious code over clever code
- Small commits with clear messages

Never do more than the step I ask for. If you think a later step should
change, say so and wait.

Confirm you've read SPEC.md and summarise the architecture back to me in
five sentences so I know we're aligned.
```

**Check:** the summary should mention TID as primary key, containers rather than boxes, state machine rather than counting, local-first Postgres, and customer attribution on dispatch. If it misses one, the spec didn't land — say so and ask it to re-read.

---

## Step 1 — Project scaffold

```
Build order task 1 only. Do not start task 2.

Set up the project skeleton:
- Python project managed with uv, src layout
- docker-compose.yml with Postgres 16 and Mosquitto (MQTT broker)
- Alembic migrations implementing the complete schema from SPEC.md section 3
- .env.example with all config, .env in .gitignore
- README.md with setup steps for someone starting from nothing
- Sensible .gitignore

When done, give me the exact commands to start the containers, run the
migrations, and one SQL query to confirm every table exists.
```

**Check:** run the commands. The query should list all tables from the spec: `skus`, `customers`, `containers`, `container_contents`, `reads_raw`, `observations`, `dispatch_sessions`, `movements`, `anomalies`, `cycle_counts`, `cycle_count_items`.

If Docker isn't installed it'll fail here. Install Docker Desktop, then retry.

---

## Step 2 — Seed data

You need your real SKU list for this. If you don't have it yet, use placeholders and replace later.

```
Task 2. Seed data.

Create a seeding script that loads SKUs and customers from CSV files in a
seeds/ directory, so I can edit them in a spreadsheet without touching code.

Give me the CSV templates with the right column headers and 3-4 example rows
each so I can see the format. Make the script idempotent — safe to run twice.

SKU families in use: cones, cups, powder, bag_in_box, tetra, sauce,
accessories.
```

**Check:** open the CSVs, add a handful of your real products, run the seed, query the `skus` table.

---

## Step 3 — The simulator

This is the most important step. Take time over it.

```
Task 3. The simulator — SPEC.md section 5.1.

Build a CLI that publishes fake RFID reads to MQTT, mimicking a real reader.
This is how we test everything else without hardware.

Commands needed:
  sim box      --tid X --portal ENTRANCE|EXIT
  sim pallet   --boxes N --portal EXIT --miss-rate 0.08
  sim stray    --tid X --duration 600     (box parked near an antenna)
  sim reverse  --tid X --portal ENTRANCE  (carried back out)
  sim burst    --count 900                (throughput test)

Realism matters: a real tag passing a portal produces dozens to hundreds of
reads over 1-3 seconds, with RSSI rising and falling as it approaches and
leaves. Model that, don't just emit one message per tag.

Show me the MQTT message format you're using and explain why.
```

**Check:** run the simulator and watch messages arrive. Claude Code should give you a command to subscribe to the broker and see them live.

---

## Step 4 — Ingest

```
Task 4. Ingest service — SPEC.md section 4 layer 1.

Subscribe to MQTT, validate, write to reads_raw. Nothing else. It must never
block and never drop a read.

Include a docker-compose service entry so it runs alongside the others.
```

**Check:** start ingest, run `sim pallet --boxes 50`, then count rows in `reads_raw`. Should be thousands, not 50.

---

## Step 5 — Debouncer

```
Task 5. Debouncer — SPEC.md section 4 layer 2.

Collapse raw reads into observations. Apply the RSSI floor and minimum read
count from config.

Then show me: after running `sim pallet --boxes 50 --portal EXIT`, how many
rows land in reads_raw versus observations. I want to see the collapse ratio.
```

**Check:** ~50 observations from thousands of raw reads. If you get 50, the debouncer works.

---

## Step 6 — State engine and tests

The critical step. Don't rush past it.

```
Task 6. State engine — SPEC.md section 4 layer 4.

This is the only module allowed to write containers.status. Everything else
goes through it.

Then write an automated test for every failure mode in SPEC.md section 7,
driven by the simulator. I specifically want tests proving:
- a stray tag parked near an antenna for 10 minutes produces exactly one
  state change, not thousands
- the same container cannot be dispatched twice
- an exit read with no open dispatch session creates a NO_SESSION anomaly
  and does not silently dispatch
- moving a pallet moves all its child boxes in one transaction
- a pallet read with fewer children than declared raises SHORT_PALLET

Show me how to run the tests and what passing output looks like.
```

**Check:** all tests pass. Then deliberately break something — change a config value, rerun — and watch a test fail. You want to see the tests actually catch problems, not just go green.

---

## Step 7 — API

```
Task 7. API — SPEC.md section 6.

FastAPI, all endpoints in the spec. Include the two key queries from section
3.1. Add OpenAPI docs.

Give me the URL for the interactive docs page so I can click through the
endpoints myself.
```

**Check:** open `/docs` in the browser, create a container, open a dispatch session, run the stock query. You can drive the whole system from that page.

---

## Step 8 — The dashboard

Simple on purpose. No React, no npm, no build step.

```
Task 8. Dashboard.

Keep it simple and dependency-light: FastAPI serving Jinja2 templates,
styling with Tailwind from a CDN, plain JavaScript polling the API every 5
seconds. No React, no npm, no build step. I need to be able to read and
change this myself.

Five screens:

1. /            Stock on hand. The main screen. A table of SKU, product name,
                boxes in stock, sorted by name. This is what's on display most
                of the day.

2. /live        Live read feed. Last 50 observations, newest first: time, TID,
                portal, what it was identified as. Useful for confirming the
                portal is working.

3. /dispatch    Dispatch control. Pick a customer and enter an order
                reference to open a session. While open, show what's been read
                into it, updating live, with a running box total. Big obvious
                Close button.

4. /anomalies   Anomaly queue. What happened, when, and a Resolve button.

5. /reports     Consumption by customer by SKU, with a date range picker.

Design brief — this is a warehouse tool, not a marketing site:

- It will be read from two or three metres away, on a screen mounted near
  the dock, sometimes by someone holding a box. Large type, high contrast,
  generous row height. Numbers are the content; make them big.
- Tasker's identity: primary blue #1D4ED8 (azul-700), white and light grey
  neutrals. Type: Archivo for headings, Public Sans for body — both on
  Google Fonts.
- No cards, no shadows, no gradients, no decorative icons. Plain tables with
  clear rules between rows.
- Every screen states plainly what it's showing. An empty screen says what
  would fill it, not "No data".
- Buttons say what they do: "Open dispatch", "Close dispatch", "Mark
  resolved".
- The dispatch screen is the one people use under time pressure with a truck
  waiting. It should be usable in three taps.

Spanish labels throughout. Code and comments in English.
```

**Check:** open each screen. Run the simulator and watch the stock number change on screen without refreshing.

---

## Step 9 — Cloud sync

```
Task 9. Sync worker — SPEC.md section 2.4.

Push new rows from local Postgres to a Neon cloud database. Must be
idempotent, survive disconnection, and resume cleanly. Local stays the
primary — nothing depends on the cloud being reachable.

Include a test that kills the connection mid-sync and confirms no data loss.
```

**Check:** stop the sync worker, run the simulator, restart it. All the missed data should catch up.

---

## When the hardware arrives

```
Swap the simulator for the real reader. The reader is [MODEL] publishing over
[MQTT / LLRP]. Keep the simulator working — I still want it for testing.
```

Nothing downstream of ingest should need to change. If it does, the abstraction was wrong.

---

## If you get stuck

- **A command fails:** paste the entire error into Claude Code. Don't summarise it.
- **You don't understand what it built:** ask "explain this file to me like I've never seen Python." That's a legitimate request and it'll answer properly.
- **It wants to do several steps at once:** tell it no. One step, verified, then the next.
- **Something works but you're not sure why:** ask it to prove it with a test.
