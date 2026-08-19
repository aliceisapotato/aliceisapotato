/**
 * Draw generation: turns an entry count into groups, round-robin fixtures and a
 * knockout bracket, then emits the flat match list the scheduler consumes.
 */

export const DISCIPLINES = ['MS', 'WS', 'MD', 'WD', 'XD'];
export const GRADES = ['O', 'A', 'B', 'C'];

export const DISCIPLINE_NAMES = {
  MS: "Men's Singles",
  WS: "Women's Singles",
  MD: "Men's Doubles",
  WD: "Women's Doubles",
  XD: 'Mixed Doubles',
};

export const GRADE_NAMES = { O: 'Open', A: 'A Grade', B: 'B Grade', C: 'C Grade' };

/** Open events are written without a grade suffix, exactly as in the master sheet. */
export function eventCode(grade, discipline) {
  return grade === 'O' ? discipline : `${discipline}${grade}`;
}

/**
 * Split `entries` into group sizes.
 *
 * The tournament convention (and what the 2025 master sheet does) is: groups of 3
 * whenever the count divides evenly, otherwise groups of 4, otherwise a 3/4 mix.
 * Fewer than `minSize` entries stay in a single group.
 */
export function planGroupSizes(entries, { minSize = 3, maxSize = 5 } = {}) {
  if (entries <= 1) return entries === 1 ? [1] : [];
  if (entries <= minSize) return [entries];

  let numGroups;
  if (entries % 3 === 0) numGroups = entries / 3;
  else if (entries % 4 === 0) numGroups = entries / 4;
  else numGroups = Math.max(1, Math.round(entries / 3.5));

  return distribute(entries, numGroups, minSize, maxSize);
}

function distribute(entries, numGroups, minSize, maxSize) {
  // Shrink the group count until no group would be under minSize, grow it until
  // none is over maxSize.
  while (numGroups > 1 && Math.floor(entries / numGroups) < minSize) numGroups -= 1;
  while (Math.ceil(entries / numGroups) > maxSize) numGroups += 1;

  const base = Math.floor(entries / numGroups);
  const remainder = entries % numGroups;
  const sizes = [];
  // Larger groups first so the sheet reads 4,4,3,3 rather than 3,3,4,4.
  for (let i = 0; i < numGroups; i += 1) sizes.push(i < remainder ? base + 1 : base);
  return sizes;
}

/** Round-robin rounds via the circle method; a bye is inserted for odd group sizes. */
export function roundRobinRounds(teams) {
  const list = teams.slice();
  if (list.length < 2) return [];
  if (list.length % 2 === 1) list.push(null);

  const n = list.length;
  const rounds = [];
  const fixed = list[0];
  let rotating = list.slice(1);

  for (let r = 0; r < n - 1; r += 1) {
    const pairs = [];
    const ordered = [fixed, ...rotating];
    for (let i = 0; i < n / 2; i += 1) {
      const a = ordered[i];
      const b = ordered[n - 1 - i];
      if (a !== null && b !== null) pairs.push([a, b]);
    }
    rounds.push(pairs);
    rotating = [rotating[rotating.length - 1], ...rotating.slice(0, -1)];
  }
  return rounds;
}

/** Standard bracket seeding order, e.g. 8 -> [1,8,4,5,2,7,3,6]. */
export function seedOrder(size) {
  let order = [1];
  while (order.length < size) {
    const mirror = order.length * 2 + 1;
    order = order.flatMap((s) => [s, mirror - s]);
  }
  return order;
}

export function nextPowerOfTwo(n) {
  let p = 1;
  while (p < n) p *= 2;
  return p;
}

export const ROUND_NAMES = { 2: 'F', 4: 'SF', 8: 'QF', 16: 'R16', 32: 'R32', 64: 'R64', 128: 'R128' };

export function roundName(remaining) {
  return ROUND_NAMES[remaining] || `R${remaining}`;
}

/**
 * Knockout bracket over `qualifiers` entrants (byes go to the top seeds).
 * `entrants[i]` describes who fills seed position i+1.
 * Returns rounds of { round, sideA, sideB } where each side is either an entrant
 * or { type: 'winner', of: <index into the previous round> }.
 */
export function knockoutRounds(entrants) {
  const q = entrants.length;
  if (q < 2) return [];

  const size = nextPowerOfTwo(q);
  const order = seedOrder(size);
  let slots = order.map((seed) => (seed <= q ? entrants[seed - 1] : null));
  const rounds = [];

  while (slots.length > 1) {
    const name = roundName(slots.length);
    const ties = [];
    const winners = [];
    for (let i = 0; i < slots.length; i += 2) {
      const a = slots[i];
      const b = slots[i + 1];
      if (a && b) {
        winners.push({ type: 'winner', round: name, index: ties.length });
        ties.push({ round: name, sideA: a, sideB: b });
      } else {
        winners.push(a || b); // walkover: the present side advances without a match
      }
    }
    if (ties.length) rounds.push({ round: name, ties });
    slots = winners;
  }
  return rounds;
}

/** Team/pair identifiers for an event, e.g. MSC-01 ... MSC-45. */
function makeTeams(code, entries, names = []) {
  return Array.from({ length: entries }, (_, i) => ({
    id: `${code}-${String(i + 1).padStart(2, '0')}`,
    label: names[i] || `${code} #${i + 1}`,
    players: splitPlayers(names[i]),
  }));
}

function splitPlayers(name) {
  if (!name) return [];
  return name
    .split(/\s*[/&+]\s*/)
    .map((p) => p.trim())
    .filter(Boolean);
}

/**
 * Snake-distribute teams over groups so seeds land in different groups:
 * seeds 1..g go to groups 1..g, then the next block fills in reverse, etc.
 */
export function assignTeamsToGroups(teams, sizes) {
  const groups = sizes.map(() => []);
  let index = 0;
  let pass = 0;
  while (index < teams.length) {
    const order = pass % 2 === 0 ? groups.map((_, i) => i) : groups.map((_, i) => i).reverse();
    let placedThisPass = false;
    for (const g of order) {
      if (groups[g].length < sizes[g] && index < teams.length) {
        groups[g].push(teams[index]);
        index += 1;
        placedThisPass = true;
      }
    }
    if (!placedThisPass) break;
    pass += 1;
  }
  return groups;
}

/**
 * Build the full draw for one event.
 *
 * spec: { grade, discipline, entries, seeds, format: 'RR + KO'|'RR'|'KO',
 *         day, qualifiersPerGroup, numGroups (optional override), entrants (optional names) }
 */
export function buildEventDraw(spec, options = {}) {
  const code = spec.code || eventCode(spec.grade, spec.discipline);
  const format = (spec.format || 'RR + KO').toUpperCase().replace(/\s+/g, '');
  const usesGroups = format !== 'KO';
  const usesKnockout = format !== 'RR';
  const durationMin = options.durationMin ?? 20;
  const qualifiersPerGroup = spec.qualifiersPerGroup ?? 1;

  const teams = makeTeams(code, spec.entries, spec.entrants || []);
  const sizes = usesGroups
    ? spec.groupSizes
      || (spec.numGroups
        ? distribute(spec.entries, spec.numGroups, 2, 8)
        : planGroupSizes(spec.entries))
    : [];
  const groups = usesGroups ? assignTeamsToGroups(teams, sizes) : [];

  const matches = [];
  const base = {
    eventCode: code,
    grade: spec.grade,
    discipline: spec.discipline,
    day: spec.day,
    durationMin,
  };

  if (usesGroups) {
    groups.forEach((groupTeams, gi) => {
      const rounds = roundRobinRounds(groupTeams);
      rounds.forEach((pairs, ri) => {
        pairs.forEach(([a, b], mi) => {
          matches.push({
            ...base,
            id: `${code}-G${gi + 1}R${ri + 1}M${mi + 1}`,
            stage: 'group',
            round: `R${ri + 1}`,
            groupIndex: gi,
            groupRound: ri + 1,
            sideA: { type: 'team', id: a.id, label: a.label },
            sideB: { type: 'team', id: b.id, label: b.label },
            teamIds: [a.id, b.id],
            deps: [],
          });
        });
      });
    });
  }

  if (usesKnockout) {
    const entrants = usesGroups
      ? qualifierEntrants(groups.length, qualifiersPerGroup)
      : teams.map((t) => ({ type: 'team', id: t.id, label: t.label }));

    // A group's qualifiers are only known once every match in that group is done.
    const groupMatchIds = matches.reduce((acc, m) => {
      if (m.stage === 'group') (acc[m.groupIndex] ||= []).push(m.id);
      return acc;
    }, {});

    const koRounds = knockoutRounds(entrants);
    const idOf = (round, index) => `${code}-${round}${index + 1}`;

    koRounds.forEach(({ round, ties }) => {
      ties.forEach((tie, index) => {
        const deps = [];
        for (const side of [tie.sideA, tie.sideB]) {
          if (side.type === 'winner') deps.push(idOf(side.round, side.index));
          else if (side.type === 'qualifier') deps.push(...(groupMatchIds[side.groupIndex] || []));
        }
        matches.push({
          ...base,
          id: idOf(round, index),
          stage: 'ko',
          round,
          sideA: tie.sideA,
          sideB: tie.sideB,
          teamIds: [tie.sideA, tie.sideB].filter((s) => s.type === 'team').map((s) => s.id),
          deps: [...new Set(deps)],
        });
      });
    });
  }

  return {
    code,
    grade: spec.grade,
    discipline: spec.discipline,
    day: spec.day,
    format: spec.format || 'RR + KO',
    entries: spec.entries,
    seeds: spec.seeds ?? 0,
    durationMin,
    groupSizes: sizes,
    groups: groups.map((g) => g.map((t) => ({ id: t.id, label: t.label }))),
    teams,
    matches,
    counts: countByStage(matches),
  };
}

/**
 * Rank qualifiers across groups: all group winners first (in group order), then
 * runners-up in reverse group order so a group's pair meets as late as possible.
 */
export function qualifierEntrants(numGroups, qualifiersPerGroup) {
  const entrants = [];
  for (let rank = 1; rank <= qualifiersPerGroup; rank += 1) {
    const order = Array.from({ length: numGroups }, (_, i) => i);
    if (rank % 2 === 0) order.reverse();
    for (const gi of order) {
      entrants.push({
        type: 'qualifier',
        groupIndex: gi,
        rank,
        label: `Gr ${gi + 1} ${rank === 1 ? 'winner' : `#${rank}`}`,
      });
    }
  }
  return entrants;
}

export function countByStage(matches) {
  const counts = { group: 0, R128: 0, R64: 0, R32: 0, R16: 0, QF: 0, SF: 0, F: 0, total: matches.length };
  for (const m of matches) {
    if (m.stage === 'group') counts.group += 1;
    else counts[m.round] = (counts[m.round] || 0) + 1;
  }
  return counts;
}

/** Build every event's draw for a tournament config. */
export function buildDraws(config) {
  return config.events
    .filter((e) => (e.entries ?? 0) > 1)
    .map((e) => buildEventDraw(
      { ...e, day: e.day ?? dayForGrade(config, e.grade) },
      { durationMin: matchDuration(config, e.grade) },
    ));
}

/** Events without an explicit day fall on the day their grade is scheduled. */
export function dayForGrade(config, grade) {
  const day = (config.days || []).find((d) => (d.grades || []).includes(grade));
  return day ? day.id : config.days?.[0]?.id;
}

export function matchDuration(config, grade) {
  return config.matchMinutes?.[grade] ?? config.matchMinutes?.default ?? 20;
}
