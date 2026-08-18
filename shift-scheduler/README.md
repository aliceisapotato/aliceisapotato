# Shift Scheduler

A single-file scheduler for staffing volunteer and workforce shifts. Open
`index.html` in any browser — no install, no server, no accounts. Everything is
stored in that browser's local storage.

## What it does

**Roster.** Each person gets the roles they're trained for, the days they're
available, an earliest/latest time, and a weekly shift cap. Leave roles blank
for someone who can cover anything.

**Shifts.** A shift is a date, a time range, a role, and how many people it
needs. "Add & repeat weekly" creates the same shift for the next *n* weeks in
one go. Overnight shifts (22:00–02:00) are handled correctly.

**Staffing.** Click any shift to open the staffing drawer. Candidates are
ranked with the best fit first; anyone who can't work it is greyed out with the
reason why, and can still be assigned over the warning if you decide to.

**Auto-fill week.** Fills every open slot in the visible week at once. It
never breaks a hard constraint, and it spreads work evenly by always picking
whoever has the fewest shifts that week. Where the only remaining candidates
are at their weekly cap, it tells you how many slots that affects and asks
before going over.

**Issues panel.** Live list of everything wrong with the current week —
understaffed shifts, double-bookings, people scheduled outside their
availability, anyone over their cap. Click a row to jump to the shift.

**Reports.** Coverage percentage, hours per person, and fill rate per role for
any date range, exportable to CSV.

## Scheduling rules

Hard constraints — auto-fill will never violate these, and the app flags them
if you assign around them manually:

- the person is trained for the role (or has no role restriction)
- the shift falls on a day they marked available
- the shift fits inside their earliest/latest times
- they aren't already on an overlapping shift that day

Soft constraint — warned about, and only crossed with your confirmation:

- their weekly shift cap

## Data

Data lives in the browser under the key `shift-scheduler.v1`, so it stays on the
machine and survives reloads, but it does not sync between browsers or devices.
**Backup** downloads the whole dataset as JSON and **Restore** loads it back —
that's how you move a schedule to another machine or keep a copy.

To start from scratch, clear the site data for the page; the app reseeds with a
small sample roster and week of shifts so nothing is ever a blank screen.

## Sharing a finished schedule

**Print** produces a clean week grid with the app chrome stripped out, which
prints or saves to PDF for a noticeboard. **CSV** exports the week (or any date
range from Reports) with one row per shift and the assigned names.
