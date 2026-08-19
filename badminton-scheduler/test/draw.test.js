import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  planGroupSizes, roundRobinRounds, seedOrder, knockoutRounds,
  buildEventDraw, buildDraws, qualifierEntrants,
} from '../src/draw.js';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const config = JSON.parse(fs.readFileSync(path.join(root, 'examples/monash-open-2025.example.json'), 'utf8'));

test('group sizes follow the 3-then-4 convention', () => {
  assert.deepEqual(planGroupSizes(45), Array(15).fill(3));
  assert.deepEqual(planGroupSizes(16), Array(4).fill(4));
  assert.deepEqual(planGroupSizes(14), [4, 4, 3, 3]);
  assert.deepEqual(planGroupSizes(10), [4, 3, 3]);
  assert.deepEqual(planGroupSizes(2), [2]);
  assert.deepEqual(planGroupSizes(0), []);
});

test('round robin plays every pair once and nobody twice in a round', () => {
  for (const size of [2, 3, 4, 5, 6]) {
    const teams = Array.from({ length: size }, (_, i) => `T${i}`);
    const rounds = roundRobinRounds(teams);
    const seen = new Set();
    for (const round of rounds) {
      const inRound = new Set();
      for (const [a, b] of round) {
        assert.ok(!inRound.has(a) && !inRound.has(b), `${a}/${b} plays twice in one round`);
        inRound.add(a);
        inRound.add(b);
        const key = [a, b].sort().join('|');
        assert.ok(!seen.has(key), `${key} repeated`);
        seen.add(key);
      }
    }
    assert.equal(seen.size, (size * (size - 1)) / 2);
  }
});

test('seed order is the standard bracket ordering', () => {
  assert.deepEqual(seedOrder(2), [1, 2]);
  assert.deepEqual(seedOrder(4), [1, 4, 2, 3]);
  assert.deepEqual(seedOrder(8), [1, 8, 4, 5, 2, 7, 3, 6]);
});

test('byes go to the top seeds and shrink the first round', () => {
  const entrants = qualifierEntrants(5, 1); // 5 qualifiers -> 8 bracket, 3 byes
  const rounds = knockoutRounds(entrants);
  assert.deepEqual(rounds.map((r) => r.round), ['QF', 'SF', 'F']);
  assert.equal(rounds[0].ties.length, 1); // 5 - 8/2
  assert.equal(rounds[1].ties.length, 2);
  assert.equal(rounds[2].ties.length, 1);
});

test('reproduces the 2025 master sheet match counts', () => {
  const expected = {
    MS: 23, WS: 1, MD: 13, WD: 1, XD: 1,
    MSA: 19, MDA: 21, XDA: 3,
    MSB: 23, WSB: 3, MDB: 27, WDB: 7, XDB: 14,
    MSC: 59, WSC: 7, MDC: 39, WDC: 15, XDC: 27,
  };
  const draws = buildDraws(config);
  for (const draw of draws) {
    assert.equal(draw.counts.total, expected[draw.code], `${draw.code} match count`);
  }
  const total = draws.reduce((sum, d) => sum + d.counts.total, 0);
  assert.equal(total, 303);
});

test('knockout matches depend on the group matches that feed them', () => {
  const draw = buildEventDraw({ grade: 'C', discipline: 'WD', entries: 12, seeds: 2, format: 'RR + KO', day: 1 });
  const byId = new Map(draw.matches.map((m) => [m.id, m]));
  for (const match of draw.matches.filter((m) => m.stage === 'ko')) {
    assert.ok(match.deps.length > 0, `${match.id} has no dependencies`);
    for (const dep of match.deps) assert.ok(byId.has(dep), `${match.id} depends on unknown ${dep}`);
  }
  const semi = draw.matches.find((m) => m.round === 'SF');
  const groupsFeedingSemi = new Set(semi.deps.map((id) => byId.get(id).groupIndex));
  assert.equal(groupsFeedingSemi.size, 2, 'a semi-final waits on exactly two groups');
});

test('every entry is placed in exactly one group', () => {
  const draw = buildEventDraw({ grade: 'C', discipline: 'MS', entries: 45, seeds: 8, format: 'RR + KO', day: 1 });
  const placed = draw.groups.flat().map((t) => t.id);
  assert.equal(placed.length, 45);
  assert.equal(new Set(placed).size, 45);
});
