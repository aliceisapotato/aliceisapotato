/** Public entry point: config in, full tournament plan out. */

import { buildDraws } from './draw.js';
import { scheduleTournament } from './scheduler.js';
import { tournamentSummary, collectWarnings } from './report.js';

export * from './config.js';
export * from './draw.js';
export * from './import.js';
export * from './scheduler.js';
export * from './report.js';

export function planTournament(config) {
  const draws = buildDraws(config);
  const days = scheduleTournament(draws, config);
  return {
    config,
    draws,
    days,
    summary: tournamentSummary(draws, days, config),
    warnings: collectWarnings(days, config),
  };
}
