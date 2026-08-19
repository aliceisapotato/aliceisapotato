/**
 * Court/time scheduler.
 *
 * List scheduling over a discrete time grid: at every slot, the matches that are
 * ready (dependencies played, teams rested) are ranked by how much work still
 * hangs off them (critical path) and dropped onto the free courts.
 */

export function parseTime(hhmm) {
  const [h, m] = String(hhmm).split(':').map(Number);
  return h * 60 + (m || 0);
}

export function formatTime(minutes) {
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`;
}

/**
 * @param {object[]} matches  flat match list (from buildDraws), all for one day
 * @param {object} day        { date, label, courts, start, end, courtNames? }
 * @param {object} options    { granularityMin, restMin, turnaroundMin, gradeCourts }
 */
export function scheduleDay(matches, day, options = {}) {
  const granularity = options.granularityMin ?? 10;
  const restMin = options.restMin ?? 20;
  const turnaround = options.turnaroundMin ?? 0;
  const strictCourts = options.strictGradeCourts ?? false;
  // A court block is only reserved on days its grade actually plays, otherwise
  // the other grades would be locked out of those courts.
  const gradesToday = new Set(matches.map((m) => m.grade));
  const gradeCourts = Object.fromEntries(
    Object.entries(options.gradeCourts || {}).filter(([grade]) => gradesToday.has(grade)),
  );

  const dayStart = parseTime(day.start);
  const dayEnd = parseTime(day.end);
  const courts = Array.from({ length: day.courts }, (_, i) => ({
    index: i,
    name: day.courtNames?.[i] || `Court ${i + 1}`,
    freeAt: dayStart,
  }));

  const byId = new Map(matches.map((m) => [m.id, m]));
  const priority = criticalPaths(matches, byId, restMin);

  const assignments = new Map(); // matchId -> { start, end, courtIndex }
  const teamFreeAt = new Map(); // teamId -> minute the team can play again
  const pending = new Set(matches.map((m) => m.id));
  const eventAnchor = new Map(); // eventCode -> court index the event last used

  const readyAt = (match) => {
    let t = dayStart;
    for (const depId of match.deps) {
      const dep = assignments.get(depId);
      if (!dep) return Infinity; // dependency not played yet
      t = Math.max(t, dep.end + restMin);
    }
    for (const teamId of match.teamIds) {
      t = Math.max(t, teamFreeAt.get(teamId) ?? dayStart);
    }
    if (match.notBefore) t = Math.max(t, parseTime(match.notBefore));
    return t;
  };

  for (let t = dayStart; t <= dayEnd && pending.size; t += granularity) {
    let freeCourts = courts.filter((c) => c.freeAt <= t);
    if (!freeCourts.length) continue;

    const ready = [];
    for (const id of pending) {
      const match = byId.get(id);
      if (readyAt(match) <= t) ready.push(match);
    }
    if (!ready.length) continue;

    // Longest remaining chain first; then keep an event's matches together so the
    // grid reads in blocks and umpires/shuttles stay with one event.
    ready.sort((a, b) => {
      if (priority.get(b.id) !== priority.get(a.id)) return priority.get(b.id) - priority.get(a.id);
      // Keep every group of an event on the same round before anyone moves ahead.
      if ((a.groupRound || 0) !== (b.groupRound || 0)) return (a.groupRound || 0) - (b.groupRound || 0);
      if (a.eventCode !== b.eventCode) return a.eventCode.localeCompare(b.eventCode);
      return a.id.localeCompare(b.id);
    });

    for (const match of ready) {
      if (!freeCourts.length) break;
      if (t + match.durationMin > dayEnd) continue;
      // Re-check: an earlier assignment in this same slot may have taken one of
      // this match's teams (two matches of a group can both look ready at t).
      if (readyAt(match) > t) continue;

      const court = pickCourt(freeCourts, match, gradeCourts, eventAnchor, strictCourts);
      if (!court) continue; // reserved courts only, and none of them is free
      const end = t + match.durationMin;
      assignments.set(match.id, {
        start: t,
        end,
        courtIndex: court.index,
        courtName: court.name,
      });
      court.freeAt = end + turnaround;
      eventAnchor.set(match.eventCode, court.index);
      for (const teamId of match.teamIds) teamFreeAt.set(teamId, end + restMin);
      pending.delete(match.id);
      freeCourts = freeCourts.filter((c) => c !== court);
    }
  }

  const scheduled = matches
    .filter((m) => assignments.has(m.id))
    .map((m) => ({ ...m, ...assignments.get(m.id) }))
    .sort((a, b) => a.start - b.start || a.courtIndex - b.courtIndex);

  const unscheduled = matches.filter((m) => !assignments.has(m.id));

  return {
    day: { ...day, startMin: dayStart, endMin: dayEnd },
    courts: courts.map((c) => ({ index: c.index, name: c.name })),
    granularity,
    scheduled,
    unscheduled,
    stats: dayStats(scheduled, courts.length, dayStart),
  };
}

/**
 * Preferred court: the grade's reserved block if any court in it is free
 * (e.g. keeping Open events on courts 1-4), otherwise the court closest to
 * where the event last played.
 */
function pickCourt(freeCourts, match, gradeCourts, eventAnchor, strict = false) {
  const reserved = gradeCourts[match.grade];
  let pool = freeCourts;
  if (reserved && reserved.length) {
    const inBlock = freeCourts.filter((c) => reserved.includes(c.index + 1));
    if (inBlock.length) pool = inBlock;
    else if (strict) return null; // wait for the block rather than spill out
  } else {
    // Courts reserved for another grade are a last resort.
    const reservedElsewhere = new Set(
      Object.entries(gradeCourts)
        .filter(([grade]) => grade !== match.grade)
        .flatMap(([, list]) => list || []),
    );
    const outside = freeCourts.filter((c) => !reservedElsewhere.has(c.index + 1));
    if (outside.length) pool = outside;
    else if (strict) return null;
  }

  const anchor = eventAnchor.get(match.eventCode);
  if (anchor === undefined) return pool[0];
  return pool.reduce((best, c) =>
    Math.abs(c.index - anchor) < Math.abs(best.index - anchor) ? c : best,
  );
}

/**
 * Longest path (in minutes, including rest) from each match to the end of its
 * event. Group matches that feed a long knockout run therefore get on court first.
 */
export function criticalPaths(matches, byId, restMin) {
  const successors = new Map(matches.map((m) => [m.id, []]));
  for (const m of matches) {
    for (const dep of m.deps) if (successors.has(dep)) successors.get(dep).push(m.id);
  }

  // A group's rounds are played in sequence even though they are not hard
  // dependencies (the teams are shared). Counting that chain is what stops a
  // small event from being left with four sequential matches at closing time.
  const byGroupRound = new Map();
  for (const m of matches) {
    if (m.stage !== 'group') continue;
    const key = `${m.eventCode}|${m.groupIndex}|${m.groupRound}`;
    if (!byGroupRound.has(key)) byGroupRound.set(key, []);
    byGroupRound.get(key).push(m.id);
  }
  for (const m of matches) {
    if (m.stage !== 'group') continue;
    const next = byGroupRound.get(`${m.eventCode}|${m.groupIndex}|${m.groupRound + 1}`);
    if (next) successors.get(m.id).push(...next);
  }

  const memo = new Map();
  const visiting = new Set();
  const walk = (id) => {
    if (memo.has(id)) return memo.get(id);
    if (visiting.has(id)) return 0; // defensive: never expected, deps are a DAG
    visiting.add(id);
    const match = byId.get(id);
    let best = 0;
    for (const next of successors.get(id) || []) {
      best = Math.max(best, walk(next));
    }
    const value = match.durationMin + restMin + best;
    visiting.delete(id);
    memo.set(id, value);
    return value;
  };

  for (const m of matches) walk(m.id);
  return memo;
}

function dayStats(scheduled, courtCount, dayStart) {
  if (!scheduled.length) {
    return { matches: 0, firstStart: null, lastStart: null, finish: null, courtHours: 0, utilisation: 0, peakConcurrent: 0 };
  }
  const firstStart = Math.min(...scheduled.map((m) => m.start));
  const lastStart = Math.max(...scheduled.map((m) => m.start));
  const finish = Math.max(...scheduled.map((m) => m.end));
  const playedMinutes = scheduled.reduce((sum, m) => sum + m.durationMin, 0);
  const window = finish - Math.min(dayStart, firstStart);

  // Peak simultaneous matches = umpires/scorers needed at the busiest moment.
  const events = scheduled.flatMap((m) => [
    { t: m.start, delta: 1 },
    { t: m.end, delta: -1 },
  ]);
  events.sort((a, b) => a.t - b.t || a.delta - b.delta);
  let current = 0;
  let peak = 0;
  for (const e of events) {
    current += e.delta;
    peak = Math.max(peak, current);
  }

  return {
    matches: scheduled.length,
    firstStart,
    lastStart,
    finish,
    courtHours: +(playedMinutes / 60).toFixed(1),
    utilisation: window > 0 ? +((playedMinutes / (window * courtCount)) * 100).toFixed(1) : 0,
    peakConcurrent: peak,
  };
}

/** Schedule every configured day of a tournament. */
export function scheduleTournament(draws, config) {
  const allMatches = draws.flatMap((d) => d.matches);
  return config.days.map((day) => {
    const dayMatches = allMatches.filter((m) => m.day === day.id);
    return scheduleDay(dayMatches, day, {
      granularityMin: config.granularityMin,
      restMin: config.restMin,
      turnaroundMin: config.turnaroundMin,
      gradeCourts: config.gradeCourts,
      strictGradeCourts: config.strictGradeCourts,
    });
  });
}
