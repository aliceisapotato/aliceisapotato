# Shift Ledger

A single-file scheduler for staffing the competition weekend of
**Saturday 5 – Sunday 6 September 2026**. Open `index.html` in any browser —
no install, no server, no accounts. Everything is stored in that browser.

## The event as configured

The day runs from **bump in at 7.45am** to **bump out finishing at 7pm**, across
two venues:

| Venue | | Roles | Public hours |
|---|---|---|---|
| **Stadium Hall** | competition hall | Umpire, Match Control, Warden | matches from 9.00am |
| **Games Hall** | activation festival | Monash Badminton Club, Booth support, Warden, Snack Area | open from 10.30am |

The run sheet built from this is 42 shifts — 21 per day:

**Stadium Hall** — bump in 7.45–9.00 · matches 9.00–12.00, 12.00–3.00, 3.00–6.00 · bump out 6.00–7.00
**Games Hall** — bump in 7.45–10.30 · festival 10.30–2.00, 2.00–5.00 · bump out 5.00–7.00

Blocks run back-to-back with no gaps, and every role is staffed in every
session block.

### Assumptions worth checking

These were not specified, so they are starting points rather than settings in
stone. All are editable per shift on the **Shifts** tab, and **Reset run sheet**
rebuilds the default at any time.

- **Session block lengths.** Stadium Hall is cut into three ~3-hour blocks and
  Games Hall into two, so volunteers rotate rather than standing a ten-hour day.
- **Games Hall closes at 5pm**, with the last two hours used for bump out.
  Stadium Hall runs to 6pm with a one-hour bump out.
- **Headcounts.** Per session block: Stadium Hall 4 Umpires, 2 Match Control,
  2 Wardens; Games Hall 2 Monash Badminton Club, 3 Booth support, 2 Wardens,
  2 Snack Area. Bump in/out crews are 4 (Stadium) and 3 (Games).
- **Bump in and bump out are Warden shifts**, since Warden is the general crew
  role at both venues. Anyone with no role restriction can be assigned to them.

## Using it

**1. Add your volunteers.** People tab → *Paste a list*, one per line, roles
after a comma:

```
Priya Raman, Umpire, Match Control
Tom Nguyen, Umpire
Sam Wu
```

Everyone pasted is available both days at both venues with an 8-hour daily
limit; edit individuals afterwards to narrow their days, venues, roles, hours
or arrival/departure times. Leave someone's roles blank and they can be put
anywhere.

**2. Auto-fill event.** Fills every open slot across both days at once. It
never breaks a hard rule, and it levels the workload by always picking whoever
has the fewest hours that day. Where the only remaining candidates would go
over their daily hours, it says how many slots that affects and asks first.

**3. Fix what's flagged.** The Issues panel lists every gap and conflict live,
and says whether auto-fill can actually cover each gap. Click a row to jump
straight to that shift.

**4. Hand it out.** **Print** gives a clean two-day board for the noticeboard.
**CSV** exports one row per shift with the assigned names, for the whole event
or a single day.

## Scheduling rules

Hard constraints — auto-fill will never violate these, and the app flags them
if you assign around them manually:

- the person is signed up for that role (or has no role restriction)
- the person is rostered at that venue
- the shift falls on a day they are available
- the shift fits inside their arrival/departure times
- they are not already on an overlapping shift

Soft constraint — warned about, and only crossed with your confirmation:

- their maximum hours in a single day

## Data

Data lives in the browser under the key `shift-scheduler.v2`, so it stays on
the machine and survives reloads, but it does **not** sync between browsers,
devices or people. **Backup** downloads the whole dataset as JSON and
**Restore** loads it back — that is how you move the schedule to another
machine or keep a copy before a big change.

If several coordinators need to edit one live schedule at the same time, that
needs a hosted backend and is a different build.
