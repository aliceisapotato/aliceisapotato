# MO2026FEST · Giveaway Draw

A single-page giveaway draw app for the MO2026FEST badminton event — paste the
RSVP list, name a prize, hit **Draw**. No build step, no dependencies, no server:
open `index.html` or host the folder anywhere static.

## Loading the guest list

Three ways in, all landing in the same pool:

- **Paste** into the box — one guest per line (`Ada Lovelace`), or
  `Name, email@example.com`.
- **Import file** — pick a `.csv`, `.tsv` or `.txt`.
- **Drag and drop** a CSV anywhere on the page.

For a Luma export, paste or drop the whole file, header row and all. The parser
finds the name and email columns (including split `First Name` / `Last Name`),
handles quoted fields such as `"Wu, Alice"`, and skips rows whose approval
status is declined, cancelled, waitlisted or pending. `sample-guests.csv` is a
small file to try it with.

Duplicates are dropped by email (falling back to name) while **Remove
duplicates** is checked.

## Drawing

- Set a **Prize name** and how many **winners this draw** — multiple winners are
  drawn together, all distinct.
- **Draw** (or the `space` bar) rolls the reel and reveals the winners.
- **Exclude past winners** keeps anyone already drawn out of later draws. Turn
  it off to let people win twice.
- **Void & redraw** removes the last result from the log and immediately draws
  again — for when someone has already left the room.
- **Stage mode** (or `F`) hides the side panels for projecting the draw.
- **Export CSV** saves the winner log with prizes and timestamps.

Everything is kept in `localStorage`, so a reload mid-event does not lose the
pool or the winners.

## Fairness

Picks come from `crypto.getRandomValues`, not `Math.random`. Taking a raw 32-bit
value modulo the pool size would bias the draw toward the first few entrants, so
values landing in the ragged tail above the last exact multiple of the pool size
are rejected and redrawn. Multiple winners come from a partial Fisher–Yates
shuffle, which cannot pick the same person twice.

## Files

| File | Purpose |
| --- | --- |
| `index.html` | Markup and inline SVG artwork |
| `styles.css` | Court-blue theme, layout, animations |
| `app.js` | Parsing, RNG, draw sequence, storage, export |
| `sample-guests.csv` | Example import file |

Type is Barlow Condensed + Inter from Google Fonts, with system fallbacks if
those are blocked.
