import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  parseDelimited, readXlsx, readDrawFile, sniffLayout, importDraw,
  applyImport, parseEventLabel, parseGroupCell,
} from '../src/import.js';
import { withDefaults } from '../src/config.js';
import { planTournament } from '../src/index.js';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const csvText = fs.readFileSync(path.join(root, 'examples/sample-draw-2026.csv'), 'utf8');
const xlsxBuffer = fs.readFileSync(path.join(root, 'test/fixtures/sample-draw.xlsx'));

test('delimited parsing handles quotes, commas and blank lines', () => {
  const rows = parseDelimited('Event,Entrant\nMSC,"Lee, Ann"\n\nMSC,"He said ""hi"""\n');
  assert.deepEqual(rows, [['Event', 'Entrant'], ['MSC', 'Lee, Ann'], ['MSC', 'He said "hi"']]);
});

test('event labels are read from codes and from words', () => {
  assert.deepEqual(parseEventLabel('MSC'), { discipline: 'MS', grade: 'C' });
  assert.deepEqual(parseEventLabel('XD'), { discipline: 'XD', grade: 'O' });
  assert.deepEqual(parseEventLabel("Men's Singles B"), { discipline: 'MS', grade: 'B' });
  assert.deepEqual(parseEventLabel('Open Mixed Doubles'), { discipline: 'XD', grade: 'O' });
  assert.deepEqual(parseEventLabel("Ladies Doubles - A Grade"), { discipline: 'WD', grade: 'A' });
  assert.equal(parseEventLabel('Raffle winners'), null);
});

test('group cells sort naturally', () => {
  assert.deepEqual(parseGroupCell('Group 10'), { key: 'Group 10', order: 10 });
  assert.deepEqual(parseGroupCell('B'), { key: 'B', order: 2 });
  assert.equal(parseGroupCell(''), null);
});

test('the header row is found below title rows', () => {
  const rows = parseDelimited(csvText);
  const layout = sniffLayout(rows);
  assert.equal(layout.headerRow, 0);
  assert.ok(layout.confident);
  assert.equal(layout.columns.event, 0);
  assert.equal(layout.columns.group, 1);
  assert.equal(layout.columns.entrant, 2);
  assert.equal(layout.columns.partner, 3);
});

test('xlsx reads to the same draw as the CSV, ignoring title rows', async () => {
  const sheets = await readXlsx(xlsxBuffer.buffer.slice(
    xlsxBuffer.byteOffset, xlsxBuffer.byteOffset + xlsxBuffer.byteLength,
  ));
  assert.equal(sheets[0].name, 'Draw');

  const xlsxLayout = sniffLayout(sheets[0].rows);
  assert.equal(xlsxLayout.headerRow, 2, 'header sits under the two title rows');

  const fromXlsx = importDraw(sheets[0].rows, xlsxLayout);
  const fromCsv = importDraw(parseDelimited(csvText), sniffLayout(parseDelimited(csvText)));

  // Compare the draws in full — codes and counts alone hide cell-level damage,
  // such as an empty singles partner cell picking up the next column's value.
  assert.deepEqual(fromXlsx.events, fromCsv.events);
  assert.equal(fromXlsx.stats.entries, 217);

  const singles = fromXlsx.events.find((e) => e.discipline === 'MS');
  assert.ok(singles.groups.flat().every((label) => !label.includes(' / ')),
    'singles entrants must not pick up a partner from an empty cell');
});

test('readDrawFile dispatches on file type', async () => {
  const sheets = await readDrawFile({ name: 'draw.csv', text: csvText });
  assert.equal(sheets.length, 1);
  assert.ok(sheets[0].rows.length > 200);
});

test('imported draws keep their groups exactly as drawn', () => {
  const rows = parseDelimited(csvText);
  const imported = importDraw(rows, sniffLayout(rows));
  assert.equal(imported.stats.events, 20);
  assert.equal(imported.stats.drawn, 20);

  // Compare against the file itself rather than hard-coded numbers.
  const mscRows = rows.filter((r) => r[0] === 'MSC');
  const mscGroups = [...new Set(mscRows.map((r) => r[1]))];
  const msc = imported.events.find((e) => e.code === 'MSC');
  assert.equal(msc.entries, mscRows.length);
  assert.equal(msc.groups.length, mscGroups.length);
  assert.deepEqual(
    msc.groups.map((g) => g.length),
    mscGroups.map((name) => mscRows.filter((r) => r[1] === name).length),
  );

  // The names in the file are the names in the draw, in file order.
  const firstMscRow = rows.find((r) => r[0] === 'MSC');
  assert.equal(msc.groups[0][0], firstMscRow[2]);

  const xd = imported.events.find((e) => e.code === 'XD');
  assert.ok(xd.groups.flat().every((label) => label.includes(' / ')), 'doubles pairs are joined');
});

test('rows the importer cannot read are reported, not silently dropped', () => {
  const rows = parseDelimited([
    'Event,Group,Player 1,Seed',
    'MSC,Group 1,Ann Lee,1',
    'Raffle,Group 1,Bo Chen,',
    'MSC,Group 1,,',
  ].join('\n'));
  const imported = importDraw(rows, sniffLayout(rows));
  assert.equal(imported.stats.entries, 1);
  assert.equal(imported.issues.filter((i) => i.row).length, 2);
  assert.ok(imported.issues.some((i) => /could not tell which event/.test(i.message)));
  assert.ok(imported.issues.some((i) => /no entrant name/.test(i.message)));
});

test('an imported draw schedules with no clash for players in several events', () => {
  const rows = parseDelimited(csvText);
  const imported = importDraw(rows, sniffLayout(rows));
  const config = applyImport(withDefaults({ name: '2026 Monash Open' }), imported);
  const plan = planTournament(config);

  assert.equal(plan.summary.totals.variance, 0, 'every drawn match is scheduled');

  const multiEvent = new Map();
  for (const day of plan.days) {
    const byPlayer = new Map();
    for (const m of day.scheduled) {
      for (const player of m.playerIds) {
        if (!byPlayer.has(player)) byPlayer.set(player, []);
        byPlayer.get(player).push(m);
      }
    }
    for (const [player, list] of byPlayer) {
      const events = new Set(list.map((m) => m.eventCode));
      if (events.size > 1) multiEvent.set(player, events.size);
      list.sort((a, b) => a.start - b.start);
      for (let i = 1; i < list.length; i += 1) {
        assert.ok(list[i].start >= list[i - 1].end + config.restMin,
          `${player} is double-booked between ${list[i - 1].eventCode} and ${list[i].eventCode}`);
      }
    }
  }
  assert.ok(multiEvent.size > 20, 'the sample really does have players in several events');
});

test('imported events fall on the day their grade plays', () => {
  const rows = parseDelimited(csvText);
  const config = applyImport(withDefaults({}), importDraw(rows, sniffLayout(rows)));
  for (const event of config.events) {
    const day = config.days.find((d) => d.id === event.day);
    assert.ok(day.grades.includes(event.grade), `${event.code} landed on the wrong day`);
  }
});
