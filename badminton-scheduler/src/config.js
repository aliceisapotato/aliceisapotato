/**
 * Tournament settings — everything except the draw itself.
 *
 * The draw is imported; these are the knobs the tournament desk sets: how long
 * the days are, how many courts, how long a match takes in each grade.
 */

export function defaultSettings() {
  return {
    name: 'Monash Open',
    venue: '',
    granularityMin: 10,
    restMin: 20,
    turnaroundMin: 0,
    // Open: best of 3 to 15 (settings to 21). A/B/C: one game to 30, no settings.
    matchMinutes: { O: 30, A: 25, B: 25, C: 25, default: 25 },
    shuttlesPerMatch: { O: 4, default: 2 },
    gradeCourts: { O: [1, 2, 3, 4] },
    strictGradeCourts: false,
    days: [
      { id: 1, date: '', label: 'Day 1', grades: ['A', 'C'], courts: 10, start: '09:00', end: '20:00' },
      { id: 2, date: '', label: 'Day 2', grades: ['O', 'B'], courts: 10, start: '09:00', end: '20:00' },
    ],
    events: [],
  };
}

/** Settings from a file, with anything absent filled in from the defaults. */
export function withDefaults(settings = {}) {
  const base = defaultSettings();
  return {
    ...base,
    ...settings,
    matchMinutes: { ...base.matchMinutes, ...(settings.matchMinutes || {}) },
    shuttlesPerMatch: { ...base.shuttlesPerMatch, ...(settings.shuttlesPerMatch || {}) },
    gradeCourts: settings.gradeCourts ?? base.gradeCourts,
    days: settings.days?.length ? settings.days : base.days,
    events: settings.events || [],
  };
}
