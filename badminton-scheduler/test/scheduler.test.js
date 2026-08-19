import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { planTournament } from '../src/index.js';
import { parseTime, formatTime } from '../src/scheduler.js';
import { buildGrid, matchesCsv } from '../src/report.js';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const config = JSON.parse(fs.readFileSync(path.join(root, 'data/monash-open-2025.json'), 'utf8'));
const plan = planTournament(config);

test('every planned match gets a court and a start time', () => {
  assert.equal(plan.summary.totals.scheduled, plan.summary.totals.planned);
  assert.equal(plan.summary.totals.variance, 0);
  for (const day of plan.days) assert.equal(day.unscheduled.length, 0);
});

test('no court hosts two matches at once', () => {
  for (const day of plan.days) {
    const byCourt = new Map();
    for (const m of day.scheduled) {
      const list = byCourt.get(m.courtIndex) || [];
      list.push(m);
      byCourt.set(m.courtIndex, list);
    }
    for (const [court, list] of byCourt) {
      list.sort((a, b) => a.start - b.start);
      for (let i = 1; i < list.length; i += 1) {
        assert.ok(list[i].start >= list[i - 1].end,
          `court ${court}: ${list[i].id} starts before ${list[i - 1].id} ends`);
      }
    }
  }
});

test('no team is on two courts at once and rest is respected', () => {
  const rest = config.restMin;
  for (const day of plan.days) {
    const byTeam = new Map();
    for (const m of day.scheduled) {
      for (const teamId of m.teamIds) {
        const list = byTeam.get(teamId) || [];
        list.push(m);
        byTeam.set(teamId, list);
      }
    }
    for (const [teamId, list] of byTeam) {
      list.sort((a, b) => a.start - b.start);
      for (let i = 1; i < list.length; i += 1) {
        const gap = list[i].start - list[i - 1].end;
        assert.ok(gap >= rest,
          `${teamId} gets ${gap} min between ${list[i - 1].id} and ${list[i].id}, needs ${rest}`);
      }
    }
  }
});

test('a match never starts before the matches it depends on have finished', () => {
  const starts = new Map();
  for (const day of plan.days) for (const m of day.scheduled) starts.set(m.id, m);
  for (const day of plan.days) {
    for (const m of day.scheduled) {
      for (const depId of m.deps) {
        const dep = starts.get(depId);
        assert.ok(dep, `${m.id} depends on unscheduled ${depId}`);
        assert.ok(m.start >= dep.end + config.restMin,
          `${m.id} at ${formatTime(m.start)} precedes ${depId} ending ${formatTime(dep.end)}`);
      }
    }
  }
});

test('finals are the last match of their event', () => {
  const byEvent = new Map();
  for (const day of plan.days) {
    for (const m of day.scheduled) {
      const list = byEvent.get(m.eventCode) || [];
      list.push(m);
      byEvent.set(m.eventCode, list);
    }
  }
  for (const [code, list] of byEvent) {
    const final = list.find((m) => m.round === 'F');
    if (!final) continue;
    const latest = Math.max(...list.map((m) => m.start));
    assert.equal(final.start, latest, `${code} final is not the last match`);
  }
});

test('play stays inside the day window and on the right day', () => {
  for (const day of plan.days) {
    const end = parseTime(day.day.end);
    const start = parseTime(day.day.start);
    for (const m of day.scheduled) {
      assert.ok(m.start >= start && m.end <= end, `${m.id} falls outside ${day.day.start}-${day.day.end}`);
      assert.ok(day.day.grades.includes(m.grade), `${m.id} (grade ${m.grade}) on the wrong day`);
    }
  }
});

test('Open matches mostly sit on their reserved court block', () => {
  const openDay = plan.days.find((d) => d.day.grades.includes('O'));
  const reserved = config.gradeCourts.O;
  const openMatches = openDay.scheduled.filter((m) => m.grade === 'O');
  const onReserved = openMatches.filter((m) => reserved.includes(m.courtIndex + 1));
  assert.ok(onReserved.length / openMatches.length > 0.7,
    `only ${onReserved.length}/${openMatches.length} Open matches landed on the reserved courts`);
});

test('strict court reservation keeps the block exclusive without stranding matches', () => {
  const strictPlan = planTournament({ ...config, strictGradeCourts: true });
  const openDay = strictPlan.days.find((d) => d.day.grades.includes('O'));
  for (const m of openDay.scheduled) {
    const onBlock = config.gradeCourts.O.includes(m.courtIndex + 1);
    assert.equal(onBlock, m.grade === 'O', `${m.id} (grade ${m.grade}) sits on the wrong side of the block`);
  }
  assert.equal(strictPlan.summary.totals.variance, 0);
});

test('a reserved block does not lock other grades out on days its grade is idle', () => {
  const dayWithoutOpen = plan.days.find((d) => !d.day.grades.includes('O'));
  const onReserved = dayWithoutOpen.scheduled.filter((m) => config.gradeCourts.O.includes(m.courtIndex + 1));
  assert.ok(onReserved.length > 0, 'reserved courts should be reused when Open is not playing');
});

test('grid and CSV exports line up with the schedule', () => {
  for (const day of plan.days) {
    const rows = buildGrid(day);
    const cells = rows.flatMap((r) => r.cells.filter(Boolean));
    assert.equal(cells.length, day.scheduled.length);
  }
  const csvLines = matchesCsv(plan.days).trim().split('\n');
  assert.equal(csvLines.length, plan.summary.totals.scheduled + 1);
});

test('shuttle estimate counts Open matches at the higher rate', () => {
  assert.equal(plan.summary.totals.shuttles, 684);
  assert.equal(plan.summary.totals.shuttleDozens, 57);
});

test('time helpers round-trip', () => {
  assert.equal(formatTime(parseTime('09:30')), '09:30');
  assert.equal(parseTime('13:05'), 785);
});
