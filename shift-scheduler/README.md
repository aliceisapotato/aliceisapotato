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

### 1. EOI intake

Open the responses sheet, select everything including the header row, copy, and
paste it into **EOI intake**. Tab-separated (straight from a spreadsheet) and
CSV both work, or upload a `.csv`.

Columns are matched to fields by keyword, so the form can be reworded or
reordered without breaking the import. Every mapping is shown and overridable
before anything is read.

The fifteen columns of the current form all map automatically:

| Form question | Used for |
|---|---|
| Full name | name |
| Email Address | email, and Monash detection |
| Contact number | phone |
| Person type | Monash student vs public |
| Monash student email | Monash detection |
| Your availability [Saturday 5 September] | Saturday window |
| Your availability [Sunday 6 September] | Sunday window |
| Any relevant experience? | experience tier and suggested roles |
| Do you have First Aid? | priority bonus, shown on the roster |
| Working with Children's Check | priority bonus, shown on the roster |
| Any dietary requirements? | flagged for catering |
| Emergency contact | flagged when missing |
| How did you hear about Monash Open? | kept on the record |
| Any comments | read alongside experience |

Availability maps to real windows: **Full Day** → 7.45am–7pm, **Half Day
Morning** → 7.45am–2pm, **Half Day Afternoon** → 2pm–7pm, **Not available** →
that day is excluded. Somebody available mornings only can no longer be put on
an afternoon shift — the scheduler blocks it.

### 2. Reading the submissions

Each applicant is scored out of 100 against your stated preferences:

| Signal | Weight |
|---|---|
| Monash student | 25 |
| Availability — full day both days scores highest, one half day lowest | 35 |
| Relevant experience — strong / some / none | 30 |
| First Aid | 6 |
| Working with Children's Check | 4 |

Every score shows its reasoning as chips, so you can see why somebody ranks
where they do rather than trusting a number. Suggested roles come from what
they wrote — an accredited umpire is put up for Umpire, a barista for Snack
Area, a club committee member for Monash Badminton Club. Someone available both
full days with strong event or officiating experience is marked a **PIC
candidate**.

Filters narrow to recommended, Monash students, full-day, strong experience, or
anyone whose submission needs a look — no availability, no contact details, no
emergency contact, or a dietary requirement to pass to catering.

Tick who you want and press **Add selected to roster**. People with no
availability, or an email already on the roster, are skipped rather than
duplicated.

### 3. The deeper read of free text

The keyword rules are deliberately conservative — they cannot tell "played
socially for a few years" from "four years officiating at state tournaments"
as well as a person can.

**A published artifact cannot call a language model**, so this is done by
handing the work to Claude and bringing the answer back:

1. **Copy Claude prompt** puts a prompt on your clipboard containing each
   applicant's experience and comments — names and availability, but no phone
   numbers or emergency contacts.
2. Paste it into Claude and let it judge the wording.
3. **Apply Claude result** takes the JSON back, re-tiers each applicant,
   replaces the suggested roles, re-scores and re-ranks.

Rows assessed this way are badged **Claude**, and the banner tracks how many of
the submissions have had the deeper read.

### 4. Rostering

**Auto-fill event** staffs both days at once. It never breaks a hard rule and
levels the workload by always picking whoever has the fewest hours that day.
Where the only remaining candidates would go over their daily hours, it says how
many slots that affects and asks first.

The staffing drawer shows each candidate's experience tier, Monash status and
PIC eligibility, so manual picks have the same information the ranking used.

### 5. Fix what's flagged, then hand it out

The Issues panel lists every gap and conflict live. **Print** gives a clean
two-day board for the noticeboard; **CSV** exports one row per shift with the
assigned names, positions and PIC.

## Scheduling rules

Hard constraints — auto-fill will never violate these, and the app flags them
if you assign around them manually:

- the person is signed up for that role (or has no role restriction)
- the person is rostered at that venue
- they are available that day at all
- the shift fits inside that day's window — a morning-only person cannot be
  put on an afternoon shift
- they are not already on an overlapping shift

Soft constraint — warned about, and only crossed with your confirmation:

- their maximum hours in a single day

## Data

Data lives in the browser under the key `shift-scheduler.v4`, so it stays on
the machine and survives reloads, but it does **not** sync between browsers,
devices or people. **Backup** downloads the whole dataset as JSON and
**Restore** loads it back — that is how you move the schedule to another
machine or keep a copy before a big change.

If several coordinators need to edit one live schedule at the same time, that
needs a hosted backend and is a different build.
