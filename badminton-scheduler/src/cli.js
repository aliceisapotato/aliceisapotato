#!/usr/bin/env node
/**
 * Command line front end.
 *
 *   node src/cli.js [config.json] [--out DIR] [--quiet]
 *
 * Prints the day-by-day plan and writes grid/match/summary CSVs when --out is given.
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { planTournament } from './index.js';
import { gridCsv, matchesCsv, summaryCsv } from './report.js';
import { formatTime } from './scheduler.js';

const here = path.dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const flags = new Set(args.filter((a) => a.startsWith('--')));
const positional = args.filter((a) => !a.startsWith('--'));

const outIndex = args.indexOf('--out');
const outDir = outIndex >= 0 ? args[outIndex + 1] : null;
const configPath = positional.filter((p) => p !== outDir)[0]
  || path.join(here, '..', 'data', 'monash-open-2025.json');

const config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
const plan = planTournament(config);

if (!flags.has('--quiet')) {
  console.log(`\n${config.name} — ${plan.summary.totals.planned} matches across ${plan.days.length} day(s)\n`);
  for (const day of plan.summary.days) {
    console.log(
      `${day.label}: ${day.matches} matches on ${day.courts} courts, `
      + `${day.firstStartLabel}-${day.finishLabel}, ${day.utilisation}% court use, `
      + `peak ${day.peakConcurrent} concurrent`
      + (day.unscheduled ? `, ${day.unscheduled} UNSCHEDULED` : ''),
    );
  }
  console.log(`\nShuttles: ${plan.summary.totals.shuttles} (${plan.summary.totals.shuttleDozens} dozen)`);

  console.log('\nEvent check (planned vs scheduled):');
  for (const e of plan.summary.events) {
    console.log(
      `  ${e.code.padEnd(4)} day ${e.day}  entries ${String(e.entries).padStart(2)}  `
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
    for (const m of day.scheduled.slice(0, 8)) {
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
