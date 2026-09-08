# Design

## Status

All five dashboard screens are on the current design, bilingual ES/EN. Origin: a Claude Design mockup of the dispatch screen (`Muelle de Carga`), translated — not copied — into Jinja2.

## System

| | |
|---|---|
| Brand blue | `#1D4ED8` |
| Neutrals | white, light grey, near-black |
| Headings | Archivo (extrabold, tight tracking) |
| Body | Public Sans |
| Fonts + Tailwind | **vendored locally**, latin + latin-ext |

Tailwind 3.4.17 browser build, no compile step. Edit a template, reload.

## Principles

- Warehouse tool, not a marketing site
- Read from 2–3 m by someone rushed and possibly holding a box
- Numbers are the content — make them big
- No cards, shadows, gradients, decorative icons
- Plain tables, clear rules between rows
- Heavy black rules and borders as structure
- Buttons name their action: *Abrir muelle*, *Cerrar muelle*
- Empty states say what would fill them

## Screens

| Path | Purpose | Pressure |
|---|---|---|
| `/` | Stock on hand by SKU | Low — on display all day |
| `/live` | Last 50 observations, newest read first | Low — confirming the portal works |
| `/dispatch` | Open/close the dock | **High — truck waiting** |
| `/anomalies` | Queue + resolve, can trigger a correction | Low |
| `/reports` | Consumption per customer per SKU | Low |

## Dispatch screen

The only screen used under time pressure. Two states, driven by real API state.

**CLOSED** — `CERRADO` at full height, customer picker, order reference field, one large `ABRIR MUELLE` button. Nothing else.

**OPEN** — full-width brand-blue header, customer name up to 104px, order and customer ID beneath. Box count as the largest element on screen. Fixed-height scrolling read list, newest first. `CERRAR MUELLE` at the bottom, always visible.

**The best idea in the design:** *"Última lectura hace 16 s"*. It is the only element that distinguishes a live portal from a dead one. Without it, a broken reader and a quiet dock look identical.

## Visual bugs found by looking, not by testing

Recorded because the class of bug matters more than the instances:

- Live feed sorted by observation id (close order) under a heading promising newest-first — a tag that goes quiet sooner gets a lower id
- `06:33:10 PM` wrapped onto two lines; also wrong convention for Argentina
- `CUST-0002` shown where the operator needs the customer's name
- Raw JSON dumped in the anomaly queue
- Database constants (`IN_STOCK`) shown as UI copy

**No test caught any of these.** Visual review is not optional.

## Not yet reviewed by a real user

Nobody has used the dashboard in a warehouse. Expect the next round of fixes to come from watching someone at the dock, not from reasoning.
