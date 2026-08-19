#!/usr/bin/env node
/**
 * Command line front end.
 *
 *   node src/cli.js --import draw.xlsx [--settings settings.json] [--out DIR]
 *   node src/cli.js plan.json [--out DIR]
 *
 * --import  a draw file (.xlsx, .csv, .tsv) — one row per entrant
 * --settings  days, courts, match lengths (defaults are used when omitted)
 * --out     write grid/matches/summary CSVs and plan.json into a directory
 * --sheet   pick a worksheet by name when the draw file has several
 */

import fs from 'node:fs';
import path from 'node:path';
import { planTournament } from './index.js';
import { withDefaults } from './config.js';
import { readDrawFile, sniffLayout, importDraw, applyImport } from './import.js';
import { gridCsv, matchesCsv, summaryCsv } from './report.js';
import { formatTime } from './scheduler.js';

const args = process.argv.slice(2);
const flag = (name) => {
  const i = args.indexOf(`--${name}`);
  return i >= 0 ? args[i + 1] : null;
};
const has = (name) => args.includes(`--${name}`);

const drawPath = flag('import');
const settingsPath = flag('settings');
const outDir = flag('out');
const sheetName = flag('sheet');
const flagValues = new Set([drawPath, settingsPath, outDir, sheetName].filter(Boolean));
const positional = args.filter((a) => !a.startsWith('--') && !flagValues.has(a));

if (!drawPath && !positional.length) {
  console.log(`Usage:
  node src/cli.js --import <draw.xlsx|draw.csv> [--settings settings.json] [--out DIR]
  node src/cli.js <plan.json> [--out DIR]

Draw files need one row per entrant, with columns for the event, the entrant and
(optionally) the group they were drawn into. See examples/ for a sample.`);
  process.exit(positional.length || drawPath ? 0 : 1);
}

let config;
if (drawPath) {
  const settings = withDefaults(settingsPath ? JSON.parse(fs.readFileSync(settingsPath, 'utf8')) : {});
  const buffer = fs.readFileSync(drawPath);
  const sheets = await readDrawFile({
    name: path.basename(drawPath),
    buffer: buffer.buffer.slice(buffer.byteOffset, buffer.byteOffset + buffer.byteLength),
    text: /\.(xlsx|xlsm)$/i.test(drawPath) ? undefined : buffer.toString('utf8'),
  });

  const sheet = sheetName
    ? sheets.find((s) => s.name.toLowerCase() === sheetName.toLowerCase())
    : sheets.map((s) => ({ s, layout: sniffLayout(s.rows) }))
      .sort((a, b) => (b.layout.score || 0) - (a.layout.score || 0))[0]?.s;
  if (!sheet) {
    console.error(`No usable sheet found. Sheets in this file: ${sheets.map((s) => s.name).join(', ')}`);
    process.exit(1);
  }

  const layout = sniffLayout(sheet.rows);
  if (!layout.confident) {
    console.error(`Could not identify the columns in "${sheet.name}". Expected headers such as `
      + `Event, Group, Entrant (or Player 1 / Player 2), Seed.`);
    process.exit(1);
  }

  const imported = importDraw(sheet.rows, layout, {
    dayForGrade: (grade) => settings.days.find((d) => (d.grades || []).includes(grade))?.id ?? settings.days[0].id,
  });
  config = applyImport(settings, imported);

  console.log(`Imported "${sheet.name}": ${imported.stats.entries} entries across `
    + `${imported.stats.events} events (${imported.stats.drawn} already drawn into groups)`);
  for (const issue of imported.issues) console.log(`  [${issue.level}] ${issue.message}`);
} else {
  config = withDefaults(JSON.parse(fs.readFileSync(positional[0], 'utf8')));
}

const plan = planTournament(config);

if (!has('quiet')) {
  console.log(`\n${config.name} — ${plan.summary.totals.planned} matches across ${plan.days.length} day(s)\n`);
  for (const day of plan.summary.days) {
    console.log(
      `${day.label}: ${day.matches} matches on ${day.courts} courts, `
      + `${day.firstStartLabel}-${day.finishLabel}, ${day.utilisation}% court use, `
      + `peak ${day.peakConcurrent} concurrent`
      + (day.unscheduled ? `, ${day.unscheduled} UNSCHEDULED` : ''),
    );
    if (day.busiestPlayer) {
      console.log(`  busiest entrant: ${day.busiestPlayer.name} (${day.busiestPlayer.matches} matches)`);
    }
  }
  console.log(`\nShuttles: ${plan.summary.totals.shuttles} (${plan.summary.totals.shuttleDozens} dozen)`);

  console.log('\nEvent check (planned vs scheduled):');
  for (const e of plan.summary.events) {
    console.log(
      `  ${e.code.padEnd(4)} day ${e.day}  entries ${String(e.entries).padStart(3)}  `
      + `groups ${String(e.groups).padStart(2)}  planned ${String(e.planned).padStart(3)}  `
      + `scheduled ${String(e.scheduled).padStart(3)}  variance ${e.variance}`,
    );
  }

  if (plan.warnings.length) {
    console.log('\nNotes:');
    for (const w of plan.warnings) console.log(`  [${w.level}] ${w.day}: ${w.message}`);
  }

  for (const day of plan.days) {
    console.log(`\n${day.day.label} — first matches:`);
    for (const m of day.scheduled.slice(0, 6)) {
      console.log(`  ${formatTime(m.start)} ${m.courtName.padEnd(8)} ${m.eventCode} ${m.round}`);
    }
  }
  console.log('');
}

if (outDir) {
  fs.mkdirSync(outDir, { recursive: true });
  for (const day of plan.days) {
    const slug = (day.day.date || `day${day.day.id}`).replace(/[^\w-]/g, '');
    fs.writeFileSync(path.join(outDir, `grid-${slug}.csv`), gridCsv(day));
  }
  fs.writeFileSync(path.join(outDir, 'matches.csv'), matchesCsv(plan.days));
  fs.writeFileSync(path.join(outDir, 'summary.csv'), summaryCsv(plan.summary));
  fs.writeFileSync(path.join(outDir, 'plan.json'), JSON.stringify(plan, null, 2));
  console.log(`Wrote grid, matches, summary and plan files to ${outDir}`);
}
