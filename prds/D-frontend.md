# PRD D — Frontend (Angular)

**You own `frontend/` and nothing else.** You write no Python.

You are building the only thing the judges actually see. The demo is short, there is no
code review, and there is Q&A — so anything that does not render is worth zero, however
good the engine behind it is. Your job is to make the machinery *visible*.

You are also the least blocked person on the team and the most likely to find contract
problems early. Start now, against fixtures, before any backend exists.

---

## 0. Read these first, in this order

1. **`CONTRACT.md`** — ownership and commit rules. Non-negotiable.
2. **`contracts/api.md`** — every endpoint and every response shape. This is your spec.
3. **`contracts/fixtures/*.json`** — the same shapes with real values. Your mock data.
4. **`docs/02-demo-cases.md`** — the story the screen has to tell.

## 1. Setup

```bash
cd frontend
ng new . --routing --style=scss --skip-git --standalone
npm install
npm start                     # http://localhost:4200
```

Backend, when you want it (you do not need it to start):

```bash
make install && make build && make seed && make api    # http://localhost:8000/docs
```

**Scaffold and get one page rendering off fixtures before you touch any design.** Angular
has real boot cost that FastAPI does not. Do not spend hour three on a spinner animation.

## 2. Commit discipline — mandatory

**Commit every 30–45 minutes, and always before starting a new component.**

```bash
git add frontend
git commit -m "ui: suppression ledger with reason grouping"
git pull --rebase origin main
git push origin main
```

- Never `git add .` or `git add -A`. Stage `frontend` by name.
- Prefix every message `ui:`.
- **Commit `package-lock.json`.** Do not commit `node_modules/` or `.angular/` —
  `.gitignore` already excludes them.
- If a frozen file or a contract shape looks wrong, **say so in the group chat
  immediately**. You will hit these first; that is expected and useful. Do not edit
  anything outside `frontend/`.

## 3. Architecture

```
frontend/src/app/
├── core/
│   ├── api.service.ts        HTTP against localhost:8000
│   ├── mock.service.ts       reads contracts/fixtures/*.json
│   ├── data.service.ts       picks one based on environment.useMock
│   └── models.ts             interfaces mirroring contracts/api.md
├── replay/replay-clock.component.ts
├── incidents/incident-list.component.ts
├── incidents/incident-detail.component.ts
├── suppression/suppression-ledger.component.ts
├── memory/memory-panel.component.ts
├── memory/field-trust.component.ts
├── eval/report-card.component.ts
└── whatif/whatif.component.ts
```

**Every component talks to `DataService` only.** One flag in
`environments/environment.ts` switches the entire app between fixtures and live API.
That flag is what lets you build at hour 0 and integrate at hour 16 without rewriting
anything.

Copy the fixture JSON into `src/assets/fixtures/` at build time (an npm `prestart`
script, or just commit a copy — a copy is fine and simpler). Composite endpoints
(`incidents/{id}`, `cases/{id}`, `report-card`, `summary`) do not exist as fixture
files; assemble them client-side by joining on ids. **Keep that join inside
`mock.service.ts`** so no component ever knows which mode it is in.

Use Angular Material for tables, chips, cards and the layout shell. Do not hand-roll
components on a hackathon clock. Spend your design budget on the two screens below.

## 4. The five surfaces

Everything must be visible **at rest**, with no scrolling required to see that the app
works. Judges glance before they lean in.

### 4.1 Replay clock — the autonomy proof

Left rail, always visible. Shows the current business date, a progress bar across
1–31 July, and play / pause / step / seek.

The clock is a client-side timer stepping one day at roughly 1.5 s. On each step, fetch
that day's incidents and suppressions and **animate new cards in**. `POST /api/replay/seek`
keeps the backend cursor in sync so a reload resumes where you were.

Running counters: days elapsed, trips processed, signals raised, **suppressed**,
incidents open. The suppressed counter climbing much faster than the incident counter
*is* the point — make that legible.

Include a **jump-to-date** control with named presets. You will use it constantly in
rehearsal and it saves you on stage if a beat runs long:

- **19 Jul** — Santa Clara fake improvement
- **21 Jul** — Cedar Ridge site event
- **29 Jul** — fourth occurrence, memory reclassifies
- **31 Jul** — billing close and the memo

### 4.2 Incident detail — the reasoning made visible

Centre panel, the biggest thing on screen.

- Headline and narrative.
- **Four context blocks — trend, peer, threshold, impact — laid out as a fixed
  2×2 grid.** They are always all four. A judge should notice the shape and realise
  every number carries context by construction, not by luck.
- **Hypotheses**, ranked. Refuted ones **struck through** with their one-line reasoning
  visible. This is the most important visual on the page: it shows the agent considered
  and rejected explanations. The vanta-Aus EV hypothesis, refuted with a control group,
  is your showcase.
- **Recommendation** card: action, owner, due date, expected effect — and the
  `assumption` shown in full, never truncated. The honesty is the feature.
- Signals absorbed, with a link to each.

### 4.3 Suppression ledger — your best screen

This is the one nobody else will have. Give it real space.

A funnel at the top: **84 raw signals → 74 suppressed → 10 incidents**, for the window,
with the day's figures alongside.

Below, suppressions grouped by `reason_code`, each with a colour and a count:

| Code | Reads as |
|---|---|
| `composition` | mix changed, not performance |
| `small_sample` | too few trips to be signal |
| `child_of_parent` | folded into a bigger event |
| `known_pattern` | seen before — reclassified, not dismissed |
| `target_moved` | improvement rejected |

Each row expands to show the suppressed signal and the full `explanation` sentence.
**One click from "why is this not an alert" to a sentence with a number in it.**

Style `known_pattern` and `target_moved` differently from the other three. They are not
dismissals — one is a chronic problem being reclassified, the other is a *rejected
improvement*. Conflating them with noise loses the two most interesting ideas on the
screen.

### 4.4 Memory panel — the learning made visible

Right rail. Three stacked sections.

**Field trust** — every audited column with a trust bar. Quarantined ones in red with
strikethrough, evidence sentence on hover. `delay_minutes` at 0.04 struck out is the
first thing on screen at 0:00 in the demo.

**Cases** — open case files with occurrence count and status chip
(`open` → `recurring` → `structural`). Expanding one shows its diagnosis and its
**prediction record**: `3 confirmed · 0 refuted · 1 pending`. Confirmed predictions get
a tick, refuted get a cross. **Show the refuted ones.** An agent that displays its own
wrong calls is more credible than one that hides them.

**Playbook** — starts empty. Entries appear during the replay with their confidence and
the case ids behind them. If a judge is watching this fill up, you have won.

### 4.5 Report card — always on screen

Bottom strip, four numbers, never hidden behind a tab:

```
Faithfulness   0 unsourced numbers / 1,247 statements
Detection      P 0.86  ·  R 0.75  ·  19 days median lead
Trace          3/3 complete
Behaviour      12/12 probes passed
```

Small, permanent, quietly authoritative. Clicking opens the detail.

### 4.6 What-if — the Q&A weapon

On an incident, a control to change the lever and re-ask. `POST /api/reason/whatif`
with `{incident_id, lever, param}`.

Preload the levers that exist in `counterfactual` so a judge picks from a dropdown
rather than typing. Two must work flawlessly:

- **vanta-Aus, `schedule_pad_min`, 10** → 81.5% to 90.4%, with the assumption shown:
  this moves the metric, not the commute.
- **Santa Clara, `vendor_substitute`, Rohan Mikhailov Travel** → 47.8% to 47.2%. The
  answer is *"slightly worse, not recommended."* **Render a negative result as a real
  answer, not an error.** A judge choosing the bad option and getting an honest "no"
  is the best thirty seconds available to you.

## 5. Traps

- CORS is already open for `localhost:4200`. Do not add a proxy.
- Dates are `YYYY-MM-DD` strings. `json` columns arrive as objects, not strings.
- Office `entity_id` is `"business_unit / office"` — display it split, but never merge
  two offices with the same name. Cedar Ridge exists under two business units with a
  14-point gap.
- The window is **1–31 July 2026**. Do not show May or June in the clock.
- Endpoints under `/api/sense`, `/api/reason` or `/api/memory` return `404` until that
  service exists. **Handle it gracefully** — an empty state, never a crash. You will
  spend real time in this condition.
- Nothing may sit at `opacity: 0` waiting on a scroll observer. Everything meant to be
  read is visible on load.
- Wide tables scroll inside their own container. The page body never scrolls sideways.

## 6. Done when

- [ ] `npm start` renders every surface off fixtures with `useMock = true`
- [ ] Flipping `useMock = false` renders the same screens off the live API
- [ ] Replay clock steps 1–31 July, animating cards in, with working jump-to-date presets
- [ ] Incident detail shows all four context blocks and struck-through refuted hypotheses
- [ ] Suppression ledger shows the funnel and all five reason groups, each expandable
- [ ] Memory panel shows field trust with quarantined columns struck out, cases with
      prediction records, and a playbook that fills during replay
- [ ] Report card is visible at all times with four numbers
- [ ] What-if returns an honest negative for the Rohan substitution
- [ ] No console errors; missing endpoints degrade to empty states
- [ ] Readable on a 1280×720 projector — assume a bad room and a washed-out screen
- [ ] Everything committed and pushed, `package-lock.json` included

## 7. Demo beats your UI must land

Rehearse against these. If one does not work, fix it before adding anything new.

| Time | Beat | Surface |
|---|---|---|
| 0:00 | 90.2% vs 64.9%, `delay_minutes` struck out in red | Field trust |
| 0:40 | Clock runs, cards appear unprompted | Replay clock |
| 1:20 | 84 signals, 74 suppressed, 10 incidents | Suppression ledger |
| 1:50 | Two vendor alerts fold into one site event, "no vendor penalty" | Suppression detail |
| 2:20 | EV hypothesis struck through, refuted with a control group | Incident detail |
| 2:50 | **19 July: the number jumps 16 points and the agent rejects it** | Incident detail |
| 3:20 | Case at 4 occurrences, 3 predictions confirmed | Memory panel |
| 3:40 | Four eval numbers | Report card |

The 2:50 beat is the one to protect. Everything else can be plainer.
