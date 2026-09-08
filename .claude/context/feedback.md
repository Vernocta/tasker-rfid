# Feedback log

Resolved items are kept, not deleted.

| # | Problem | Source | Area | Action | Status |
|---|---|---|---|---|---|
| 1 | Debouncer used one shared clock; any tag with a future timestamp fragmented every other tag's pass | Claude Code testing | debouncer | Per-`(tid, portal)` session windows | **Resolved** |
| 2 | Complete pallets raised false `SHORT_PALLET` — sibling observations still in flight when counted | Claude Code testing | state engine | Hold a pallet's observation before judging its load | **Resolved** |
| 3 | `-infinity` cursor sentinel broke every sync cycle after the first | Claude Code testing | sync | Real epoch timestamp | **Resolved** |
| 4 | JSONB `anomalies.detail` couldn't be sent back as a parameter | Claude Code testing | sync | Generic wrapping | **Resolved** |
| 5 | SPEC §3.1 stock query hid all palletised stock | Claude Code | spec | Guard removed, SPEC corrected | **Resolved** |
| 6 | Live feed sorted by observation id, not read time | Visual review | dashboard | Order by read time + test | **Resolved** |
| 7 | 12-hour clock wrapped onto two lines; wrong convention | Visual review | dashboard | 24-hour throughout | **Resolved** |
| 8 | `CUST-0002` shown instead of the customer's name | Visual review | dashboard | Name large, ID small | **Resolved** |
| 9 | Raw JSON in the anomaly queue | Visual review | dashboard | Sentence + labelled specifics | **Resolved** |
| 10 | Migrations not run after `git pull` → services retry-loop on missing tables | Christian's environment | ops | **Reopened**: the old "fix" was a habit, not a guard. Now every service names the command in its error — ingest, debouncer, state engine, sync, and the API (503, not a bare 500) | **Resolved** |
| 11 | Dashboard never used by a real operator | — | dashboard | Review after first real dispatch | **Open** |
| 12 | Beontag visit date conflict (16th vs 17th) | Email vs Christian | ops | Confirm in writing | **Open** |
| 13 | Sync worker blamed the cloud for **local** database errors — a missing local table logged "cannot reach the cloud replica" | Context audit | sync | Local and cloud failures raised as separate types, each with its own message | **Resolved** |
| 14 | Four `config/tasker.yaml` settings were never read (`mode`, `direction_mode`, `require_session`, the `rf:` block) | Context audit | config | `direction_mode` and `require_session` deleted; `mode` and `rf:` kept and marked not-yet-read | **Resolved** |
| 15 | The 902–928 MHz band decision was recorded nowhere in code, config or SPEC | Context audit | spec | Added to SPEC §11 above the open hardware items | **Resolved** |
| 16 | `decisions.md` said `mode:` makes box/pallet/hybrid a config choice; nothing reads it | Context audit | docs | Reworded: the schema supports all three without migration, but switching is a code change | **Resolved** |
| 17 | Any source edit re-downloaded every dependency on `docker compose build`, despite a comment claiming otherwise | Verifying #10 | build | `uv sync` split in two: dependencies without the project, then the project. A code change now rebuilds in seconds | **Resolved** |
| 18 | A read published on the bare topic `tasker/reads` (no portal suffix) is rejected as a malformed *gate event*, not as a malformed read | Verifying #10 | ingest | Cosmetic: the simulator and readers always publish to `tasker/reads/<portal>`, so this only misleads someone testing by hand. Not fixed | **Open** |
| 19 | `test_new_rows_reach_the_cloud` failed once under load, then passed twice | Verifying #10 | tests | Race in the test, not the code: it waits for the container *row* to reach the cloud, then asserts on its *status*, which arrives in a later sync cycle. Needs to wait for the status, not the row. Not fixed — flagged for Christian | **Open** |
