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
| 10 | Migrations not run after `git pull` → services crash-looped on missing tables | Christian's environment | ops | Run `uv run alembic upgrade head` after any pull with a new migration | **Resolved** |
| 11 | Dashboard never used by a real operator | — | dashboard | Review after first real dispatch | **Open** |
| 12 | Beontag visit date conflict (16th vs 17th) | Email vs Christian | ops | Confirm in writing | **Open** |
| 13 | Sync worker blames the cloud for **local** database errors — a missing local table logs "cannot reach the cloud replica" | Context audit | sync | One `except psycopg.Error` covers both the local read and the cloud write; split them | **Open** |
| 14 | Four `config/tasker.yaml` settings are never read (`mode`, `direction_mode`, `require_session`, the `rf:` block) | Context audit | config | Either wire them up or mark them as not-yet-used in the file | **Open** |
| 15 | The 902–928 MHz band decision is recorded nowhere in code, config or SPEC | Context audit | spec | Add to SPEC §11 so whoever orders tags sees it | **Open** |
| 16 | `decisions.md` says `mode:` makes box/pallet/hybrid a config choice; nothing reads it | Context audit | docs | Reword to describe the container model, not the key | **Open** |

Item 10 is marked resolved, but the fix is a habit rather than a guard: the
services still retry-loop on a missing table (they do not crash), and only the
sync worker names the command to run.
