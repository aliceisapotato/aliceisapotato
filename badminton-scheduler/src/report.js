/**
 * Turns scheduler output into the artefacts a tournament desk actually uses:
 * the court grid, the per-event variance check, shuttle counts and CSV exports.
 */

import { formatTime } from './scheduler.js';

export function shuttlesPerMatch(config, grade) {
  return config.shuttlesPerMatch?.[grade] ?? config.shuttlesPerMatch?.default ?? 2;
}

/**
 * Court grid in the shape of the master sheet: one row per time slot, one column
 * per court. A match spans ceil(duration / granularity) rows and is only written
 * into the first of them.
 */
export function buildGrid(dayResult) {
  const { day, courts, granularity, scheduled } = dayResult;
  const finish = scheduled.length ? Math.max(...scheduled.map((m) => m.end)) : day.startMin;
  const rows = [];
  for (let t = day.startMin; t < Math.max(finish, day.startMin + granularity); t += granularity) {
    rows.push({ time: t, label: formatTime(t), cells: courts.map(() => null) });
  }
  const rowIndex = (t) => Math.floor((t - day.startMin) / granularity);

  for (const match of scheduled) {
    const row = rows[rowIndex(match.start)];
    if (!row) continue;
    row.cells[match.courtIndex] = {
      match,
      span: Math.max(1, Math.ceil(match.durationMin / granularity)),
    };
  }

  // Mark rows covered by a match that started earlier so renderers can skip them.
  const covered = rows.map(() => courts.map(() => false));
  rows.forEach((row, ri) => {
    row.cells.forEach((cell, ci) => {
      if (!cell) return;
      for (let k = 1; k < cell.span; k += 1) {
        if (covered[ri + k]) covered[ri + k][ci] = true;
      }
    });
  });
  rows.forEach((row, ri) => {
    row.covered = covered[ri];
  });

  return rows;
}

/** Per-event planned vs scheduled check, mirroring the sheet's variance table. */
export function eventSummary(draws, dayResults, config) {
  const scheduledCounts = new Map();
  for (const result of dayResults) {
    for (const match of result.scheduled) {
      scheduledCounts.set(match.eventCode, (scheduledCounts.get(match.eventCode) || 0) + 1);
    }
  }

  return draws.map((draw) => {
    const scheduled = scheduledCounts.get(draw.code) || 0;
    const perMatch = shuttlesPerMatch(config, draw.grade);
    return {
      code: draw.code,
      grade: draw.grade,
      discipline: draw.discipline,
      day: draw.day,
      entries: draw.entries,
      seeds: draw.seeds,
      format: draw.format,
      groups: draw.groupSizes.length,
      groupSizes: draw.groupSizes.join('/'),
      counts: draw.counts,
      planned: draw.counts.total,
      scheduled,
      variance: scheduled - draw.counts.total,
      shuttles: draw.counts.total * perMatch,
    };
  });
}

export function tournamentSummary(draws, dayResults, config) {
  const events = eventSummary(draws, dayResults, config);
  const shuttles = events.reduce((sum, e) => sum + e.shuttles, 0);
  return {
    events,
    totals: {
      entries: events.reduce((s, e) => s + e.entries, 0),
      planned: events.reduce((s, e) => s + e.planned, 0),
      scheduled: events.reduce((s, e) => s + e.scheduled, 0),
      variance: events.reduce((s, e) => s + e.variance, 0),
      shuttles,
      shuttleDozens: Math.ceil(shuttles / 12),
    },
    days: dayResults.map((r) => ({
      id: r.day.id,
      label: r.day.label,
      date: r.day.date,
      courts: r.courts.length,
      ...r.stats,
      firstStartLabel: r.stats.firstStart === null ? '-' : formatTime(r.stats.firstStart),
      lastStartLabel: r.stats.lastStart === null ? '-' : formatTime(r.stats.lastStart),
      finishLabel: r.stats.finish === null ? '-' : formatTime(r.stats.finish),
      unscheduled: r.unscheduled.length,
      busiestPlayer: r.stats.busiestPlayer,
    })),
  };
}

/** Warnings the tournament desk should see before publishing a draw. */
export function collectWarnings(dayResults, config) {
  const warnings = [];
  for (const result of dayResults) {
    if (result.unscheduled.length) {
      const byEvent = countBy(result.unscheduled, (m) => m.eventCode);
      warnings.push({
        level: 'error',
        day: result.day.label,
        message: `${result.unscheduled.length} match(es) did not fit before ${result.day.end}: ${
          Object.entries(byEvent).map(([k, v]) => `${k} x${v}`).join(', ')
        }. Add courts, extend the day or shorten match slots.`,
      });
    }
    if (result.stats.finish !== null && result.stats.finish > result.day.endMin) {
      warnings.push({
        level: 'error',
        day: result.day.label,
        message: `Play finishes at ${formatTime(result.stats.finish)}, past the ${result.day.end} cut-off.`,
      });
    }
    if (result.stats.utilisation && result.stats.utilisation < 55) {
      warnings.push({
        level: 'info',
        day: result.day.label,
        message: `Court utilisation is ${result.stats.utilisation}% — the day could run on fewer courts or a shorter window.`,
      });
    }
    const gradesOnDay = new Set(result.scheduled.map((m) => m.grade));
    const expected = new Set(result.day.grades || []);
    for (const grade of gradesOnDay) {
      if (expected.size && !expected.has(grade)) {
        warnings.push({
          level: 'warn',
          day: result.day.label,
          message: `Grade ${grade} is scheduled on a day set up for ${[...expected].join(', ')}.`,
        });
      }
    }
    warnings.push({
      level: 'info',
      day: result.day.label,
      message: `Peak of ${result.stats.peakConcurrent} simultaneous matches — that is the umpire/scorer requirement at the busiest point.`,
    });
  }
  return warnings;
}

function countBy(list, keyFn) {
  return list.reduce((acc, item) => {
    const key = keyFn(item);
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {});
}

/** Grid CSV in the master-sheet layout: time column, one column per court. */
export function gridCsv(dayResult) {
  const rows = buildGrid(dayResult);
  const header = ['Time', ...dayResult.courts.map((c) => c.name)];
  const lines = [header];
  for (const row of rows) {
    lines.push([
      row.label,
      ...row.cells.map((cell, ci) => {
        if (cell) return `${cell.match.eventCode} ${cell.match.round}`;
        return row.covered[ci] ? '' : '';
      }),
    ]);
  }
  return toCsv(lines);
}

/** One row per match — the format most draw/scoring systems import. */
export function matchesCsv(dayResults) {
  const header = [
    'Day', 'Date', 'Start', 'End', 'Court', 'Event', 'Grade', 'Discipline',
    'Stage', 'Round', 'Group', 'Side A', 'Side B',
  ];
  const lines = [header];
  for (const result of dayResults) {
    for (const m of result.scheduled) {
      lines.push([
        result.day.label,
        result.day.date || '',
        formatTime(m.start),
        formatTime(m.end),
        m.courtName,
        m.eventCode,
        m.grade,
        m.discipline,
        m.stage === 'group' ? 'Group' : 'Knockout',
        m.round,
        m.stage === 'group' ? (m.groupName || `Group ${m.groupIndex + 1}`) : '',
        sideLabel(m.sideA),
        sideLabel(m.sideB),
      ]);
    }
  }
  return toCsv(lines);
}

export function sideLabel(side) {
  if (!side) return '';
  if (side.type === 'team') return side.label || side.id;
  if (side.type === 'qualifier') return side.label;
  if (side.type === 'winner') return `Winner ${side.round}${side.index + 1}`;
  return '';
}

export function summaryCsv(summary) {
  const lines = [[
    'Event', 'Grade', 'Day', 'Entries', 'Seeds', 'Format', 'Groups', 'Group sizes',
    'Group matches', 'R16', 'QF', 'SF', 'F', 'Planned', 'Scheduled', 'Variance', 'Shuttles',
  ]];
  for (const e of summary.events) {
    lines.push([
      e.code, e.grade, e.day, e.entries, e.seeds, e.format, e.groups, e.groupSizes,
      e.counts.group, e.counts.R16 || 0, e.counts.QF || 0, e.counts.SF || 0, e.counts.F || 0,
      e.planned, e.scheduled, e.variance, e.shuttles,
    ]);
  }
  lines.push([]);
  lines.push(['TOTAL', '', '', summary.totals.entries, '', '', '', '', '', '', '', '', '',
    summary.totals.planned, summary.totals.scheduled, summary.totals.variance, summary.totals.shuttles]);
  lines.push(['Shuttles (dozen)', summary.totals.shuttleDozens]);
  return toCsv(lines);
}

export function toCsv(rows) {
  return rows
    .map((row) => row.map(csvCell).join(','))
    .join('\n');
}

function csvCell(value) {
  const s = value === null || value === undefined ? '' : String(value);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}
