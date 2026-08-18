# Shift Ledger

A single-file scheduler for staffing the competition weekend of
**Saturday 5 – Sunday 6 September 2026**. Open `index.html` in any browser —
no install, no server, no accounts. Everything is stored in that browser.

## The event as configured

The day runs from **bump in at 7.45am** to **bump out finishing at 7pm**, across
two venues:

| Venue | | Roles | Public hours |
|---|---|---|---|
| **Stadium Hall** | competition hall | Match Control, Umpire, Usher | matches 9.00am–7.00pm |
| **Games Hall** | activation festival | Monash Badminton Club, Booth support, Check in desk, Snack Area, Usher | open 10.30am–5.00pm |

### Staffing levels

| Venue | Role | On at once | Posts | Slots/day |
|---|---|---|---|---|
| Stadium Hall | Match Control | 3 (one is PIC) | PIC desk, Control desk | 8 |
| Stadium Hall | Usher | 4 | 2 foyer, 2 corridor | 12 |
| Stadium Hall | Umpire | 8 at peak | whole day / morning / afternoon | 12 *(placeholder)* |
| Games Hall | Usher | 4 | 2 sports centre canteen, 2 inside | 12 |
| Games Hall | Booth support | 10 | Team A, Team B | 20 |
| Games Hall | Monash Badminton Club | 2 | — | 4 |
| Games Hall | Check in desk | 2 | — | 4 |
| Games Hall | Snack Area | 2 | — | 4 |

96 shifts over the weekend — 48 a day, 76 person-slots a day, 152 across both days.

### How the shifts are cut

Every shift is **3–5 hours**, and posts are **staggered rather than rotated as
whole waves**. Each post hands over at a different time, so shift changes
overlap the neighbouring post while the number of people on duty stays exactly
as briefed.

Rotating whole waves would have been the obvious way to get overlap, but it
doubles the headcount during the handover — a 2-person post would need 4 people
present. Staggering gives the same continuity at the briefed headcount.

Worked example, Stadium Hall ushers (4 on, all day):

```
Foyer    7.45─────12.00────────15.30───────19.00
Foyer    7.45───11.30──────15.00──────────19.00
Corridor 7.45──────12.15─────────15.45────19.00
Corridor 7.45────11.45───────15.15────────19.00
```

Four people on the floor at every moment, four different handover times, and no
shift longer than 4.5 hours.

**Umpires are the exception.** Their patterns are fixed by the brief — whole day
9am–7pm, morning 9am–2pm, afternoon 2pm–7pm — so the whole-day shift is 10 hours
and overlaps both half-days. Headcounts on all three are **placeholders**
(4 each) until the umpire list is known; they are marked *placeholder* on the
board.

### Person in charge

Match Control shifts require a **PIC**. The PIC desk is a one-person post that
changes over at 2pm, alongside two more Match Control staff who change at 1pm
and 4pm. Auto-fill nominates a PIC automatically; hand it to someone else with
**Make PIC** in the staffing drawer. A Match Control shift with people on it but
no PIC is flagged in the Issues panel.

### Volunteers already named

Ten people are pre-loaded and already rostered, all at Games Hall:

| Role | People |
|---|---|
| Monash Badminton Club | Alex, Charlie |
| Usher | Sean, Patrick, Clemen, Malcolm |
| Check in desk | Darren, Lily |
| Snack Area | Darrell, **TBC** |

*TBC* is a placeholder person — rename them once the name is confirmed.

### How many more you need

Roughly **35 people on site at peak** (15 Stadium Hall, 20 Games Hall). Across a
whole day that is 76 person-slots, so:

- about **38 volunteers a day** if each does two shifts
- about **76 a day** if each does one

Biggest gaps right now are Booth support (10 on at once, 20 slots a day) and
Umpires.

### Assumptions worth checking

- **Games Hall closes at 5pm**, with the last two hours used for bump out.
  You gave an opening time but not a closing one.
- **Umpire headcounts** of 4 per pattern are pure placeholders.
- **Booth support runs as two teams of five** changing half an hour apart. If
  the booths need individually named posts instead, say so.
- **Ushers do bump in and bump out** at both venues, since they are the general
  crew role. Their first and last shifts absorb it rather than there being
  separate 75-minute setup slots.

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
