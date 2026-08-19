# Badminton Match Scheduler

Automatic match scheduling for the Monash Open format: 4 grades (Open, A, B, C) x 5
disciplines (MS, WS, MD, WD, XD), round-robin groups feeding a knockout, laid out
across courts and time slots for each day of the tournament.

Give it entry numbers per event and it produces the draw, the court grid, the
per-event match-count check and the shuttle estimate — the same artefacts the
2025 Monash Open master spreadsheet holds, generated instead of hand-placed.

## Run it

```bash
cd badminton-scheduler

npm start            # web app on http://localhost:8080
npm run schedule     # print the 2025 Monash Open plan in the terminal
npm test             # constraint + regression tests
```

Or open `dist/badminton-scheduler.html` directly in a browser — that build is one
self-contained file with no server, no network and no dependencies. Rebuild it
with `node build.js` after changing anything under `src/` or `web/`
(`dist/badminton-scheduler.embed.html` is the same page without the document
shell, for hosts that supply their own).

Export a plan from the command line:

```bash
node src/cli.js data/monash-open-2025.json --out ./out
# out/grid-2025-09-05.csv   court grid, one column per court (spreadsheet layout)
# out/grid-2025-09-06.csv
# out/matches.csv           one row per match, for draw/scoring systems
# out/summary.csv           per-event counts, variance and shuttles
# out/plan.json             everything, including group compositions
```

## What the app does

1. **Draw** — splits entries into groups, generates the round-robin fixtures and
   builds the knockout bracket that the group winners feed into.
2. **Schedule** — places every match on a court and a start time, respecting the
   constraints below.
3. **Check** — re-validates the finished schedule and reports anything that does
   not fit, plus the peak number of simultaneous matches (your umpire requirement).

### Group sizing

Groups of 3 when the entry count divides by 3, otherwise groups of 4, otherwise a
3/4 mix. This reproduces the 2025 draw exactly (45 entries → 15 groups of 3;
16 → 4 groups of 4; 14 → 4/4/3/3). Override per event with `numGroups` or
`groupSizes` in the config.

### Knockout

Group winners (or the top `qualifiersPerGroup` from each group) are seeded into
the next power-of-two bracket, with byes going to the top seeds. Five qualifiers
therefore give one quarter-final, two semis and a final — again matching the
master sheet, event for event:

| Event | Entries | Groups | Group matches | KO | Total |
|-------|---------|--------|---------------|----|-------|
| MSC   | 45      | 15 x 3 | 45            | R16 7, QF 4, SF 2, F 1 | 59 |
| MDC   | 30      | 10 x 3 | 30            | R16 2, QF 4, SF 2, F 1 | 39 |
| MSB   | 18      | 6 x 3  | 18            | QF 2, SF 2, F 1 | 23 |
| MDA   | 14      | 4      | 18            | SF 2, F 1 | 21 |

All 20 events reconcile to the sheet's 303 matches — `npm test` asserts it.

### Scheduling constraints

* One match per court at a time; a match occupies its full grade duration.
* A player/pair is never on two courts at once and gets `restMin` between matches.
* A knockout match only starts once every match that decides its entrants has
  finished (plus rest), so finals are always the last match of their event.
* Matches only run on the day their grade is scheduled, inside the day's window.
* A grade can reserve a block of courts (`gradeCourts`) — soft by default, so the
  block is used by others when idle; `strictGradeCourts: true` keeps it exclusive.
* Matches are prioritised by how much play still depends on them (critical path,
  including the group rounds a team must still get through), which is what keeps
  a small event from being left with four back-to-back matches at closing time.

### Outputs

* **Schedule** — court grid per day, colour-coded by event, matching the layout of
  the master sheet. Hover a match for its exact time, court and pairing.
* **Summary** — entries, groups, matches by round, planned vs scheduled variance,
  shuttle usage (Open matches counted at the higher rate).
* **Draws** — group compositions and the knockout shape for every event.
* **Checks** — an independent re-validation of the produced schedule, plus notes
  on court utilisation and peak concurrent matches.

## Configuration

`data/monash-open-2025.json` is the worked example — the 2025 entry numbers, the
competition dates and the format from the tournament brief:

```jsonc
{
  "granularityMin": 10,        // time grid the schedule snaps to
  "restMin": 20,               // minimum rest between a player's matches
  "turnaroundMin": 0,          // court changeover between matches
  "matchMinutes": { "O": 30, "A": 25, "B": 25, "C": 25 },
  "shuttlesPerMatch": { "O": 4, "default": 2 },
  "gradeCourts": { "O": [1, 2, 3, 4] },
  "strictGradeCourts": false,
  "days": [
    { "id": 1, "date": "2025-09-05", "label": "Sat 5 September 2025",
      "grades": ["A", "C"], "courts": 10, "start": "09:00", "end": "20:00" },
    { "id": 2, "date": "2025-09-06", "label": "Sun 6 September 2025",
      "grades": ["O", "B"], "courts": 10, "start": "09:00", "end": "20:00" }
  ],
  "events": [
    { "grade": "C", "discipline": "MS", "entries": 45, "seeds": 8,
      "format": "RR + KO", "day": 1 }
  ]
}
```

Per-event options: `format` (`RR + KO`, `RR`, `KO`), `qualifiersPerGroup`,
`numGroups` or `groupSizes` to override the automatic split, and `entrants` — a
list of names/pairs, which replaces the placeholder labels in every export.

Match lengths follow the competition brief (Open: best of 3 to 15, 30-35 min;
A/B/C: one game to 30, 25-30 min). The defaults sit at 30 and 25; the master sheet
budgeted 30 and 20, which is what the `Open (mins)` / grade fields are there to
change. On 10 courts the 2025 entry list schedules as:

| Day | Grades | Matches | Window | Court use |
|-----|--------|---------|--------|-----------|
| Sat 5 Sep | A, C | 190 | 09:00-19:15 | 77% |
| Sun 6 Sep | Open, B | 113 | 09:00-15:15 | 81% |

Drop C-grade matches to 20 minutes and Saturday finishes at 16:40 instead.

## Layout

```
src/draw.js        groups, round-robin fixtures, knockout brackets
src/scheduler.js   court/time assignment and its constraints
src/report.js      grid, summary, warnings, CSV exports
src/cli.js         command line front end
web/               browser UI (plain ES modules, no build step)
build.js           bundles the UI into dist/badminton-scheduler.html
test/              constraint tests + regression against the 2025 sheet
```

No runtime dependencies; Node 18+ for the CLI and tests.
