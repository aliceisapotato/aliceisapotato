# Badminton Match Scheduler

Import a tournament draw, get a court schedule.

Built for the Monash Open format — 4 grades (Open, A, B, C) x 5 disciplines
(MS, WS, MD, WD, XD), round-robin groups feeding a knockout — but nothing is
hard-coded to one year's entries: the draw comes from a file you supply.

## Run it

```bash
cd badminton-scheduler

npm start            # web app on http://localhost:8080
npm test             # constraint + import tests

# command line
node src/cli.js --import draw.xlsx --settings examples/settings.json --out ./out
```

Or open `dist/badminton-scheduler.html` in a browser — one self-contained file,
no server and no network. Rebuild it with `node build.js` after changing
anything under `src/` or `web/` (`dist/badminton-scheduler.embed.html` is the
same page without the document shell, for hosts that supply their own).

## Importing the draw

One row per entrant, in `.xlsx`, `.csv`/`.tsv`, or pasted straight into the app.
Header names are matched case-insensitively and title rows above the header are
skipped; anything the app gets wrong you can remap by hand in the import panel.

| Column | Recognised as | Needed |
|--------|---------------|--------|
| Event | `MSC`, `XD`, `Men's Singles B`, `Open Mixed Doubles` | yes (or Grade + Discipline) |
| Grade | `Open`, `A`, `B Grade` | if Event has no grade |
| Discipline | `MD`, `Mixed Doubles` | if Event is absent |
| Group | `Group 3`, `Gr 3`, `3`, `B` | optional |
| Entrant / Player 1 | entrant name | yes |
| Partner / Player 2 | doubles partner | doubles only |
| Seed | seeding number | optional |
| Day | day number | optional |
| Club | club or association | optional |

```csv
Event,Group,Player 1,Player 2,Seed,Club
MSC,Group 1,Ann Lee,,1,Monash
XDB,Group 2,Bo Chen,Cara Ng,,Deakin
```

**With a Group column** the draw is scheduled exactly as drawn — the groups, the
order and the names are yours. **Without one** the app forms the groups itself:
groups of 3 where the entry count divides by 3, otherwise 4, otherwise a 3/4 mix.

`examples/sample-draw-2026.csv` is a full worked example (217 entrants across 20
events, with players entered in up to three events).

## What happens next

1. **Draw** — round-robin fixtures per group (circle method, so every pair meets
   once and nobody plays twice in a round), then the knockout: group winners are
   seeded into the next power-of-two bracket with byes to the top seeds, so five
   qualifiers give one quarter-final, two semis and a final.
2. **Schedule** — every match gets a court and a start time.
3. **Check** — the finished schedule is re-validated independently, and anything
   that will not fit is reported rather than quietly dropped.

### Constraints

* One match per court at a time, for the full grade duration.
* **A person is never on two courts at once**, in any event — entrant names are
  matched across the whole draw, so a player in MS, MD and XD is one person to
  the scheduler, with `restMin` between each of their matches.
* A knockout match only starts once every match deciding its entrants has
  finished, so a final is always the last match of its event.
* Matches only run on the day their grade plays, inside that day's window.
* A grade can reserve a block of courts (`gradeCourts`) — soft by default, so the
  block is used by others when idle; `strictGradeCourts: true` keeps it exclusive.
* Priority is by critical path — how much play still depends on a match, counting
  the group rounds a team must still get through — which keeps a small event from
  being left with four back-to-back matches at closing time.

### Outputs

* **Schedule** — court grid per day, colour-coded by event; hover for the exact
  time, court, group and pairing.
* **Summary** — entries, groups, matches by round, planned vs scheduled variance,
  shuttle usage (Open matches at the higher rate).
* **Draws** — group compositions and knockout shape per event.
* **Checks** — independent re-validation, court utilisation, peak concurrent
  matches (your umpire requirement) and the busiest entrant of the day.
* **Exports** — grid CSV in the master-sheet layout, one-row-per-match CSV for
  scoring systems, summary CSV, and the whole plan as JSON.

## Settings

Everything except the draw: days, courts, match lengths. See
`examples/settings.json`; all of it is editable in the app.

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
    { "id": 1, "date": "", "label": "Day 1", "grades": ["A", "C"],
      "courts": 10, "start": "09:00", "end": "20:00" }
  ]
}
```

Match lengths follow the competition format — Open: best of 3 games to 15 with
settings to 21, 30-35 minutes; A/B/C: one game to 30, no settings, 25-30 minutes.
The defaults sit at 30 and 25.

Per-event overrides (in the app's event table, or in a saved plan): `format`
(`RR + KO`, `RR`, `KO`), `qualifiersPerGroup`, and `numGroups`/`groupSizes` when
you want a different automatic split.

## Verification

`npm test` runs 29 tests: the scheduler's constraints (no court, team or player
double-booked, dependencies ordered, finals last, day windows respected), the
importer (CSV quoting, xlsx cell handling, column detection, malformed rows
reported), and a regression against the 2025 Monash Open master sheet — the
same 20 events, 303 matches and 57 dozen shuttles it was planned with.
`examples/monash-open-2025.example.json` is kept only as that reference.

## Layout

```
src/import.js      draw import: xlsx/CSV reading, column detection, validation
src/draw.js        groups, round-robin fixtures, knockout brackets
src/scheduler.js   court/time assignment and its constraints
src/report.js      grid, summary, warnings, CSV exports
src/config.js      tournament settings and their defaults
src/cli.js         command line front end
web/               browser UI (plain ES modules, no build step)
build.js           bundles the UI into dist/badminton-scheduler.html
examples/          sample draw, sample settings, 2025 reference plan
test/              constraint, import and regression tests
```

No runtime dependencies; Node 18+ for the CLI and tests.
