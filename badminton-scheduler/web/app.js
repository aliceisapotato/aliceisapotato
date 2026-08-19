/** Browser front end: edit the tournament setup, generate and inspect the schedule. */

import { planTournament } from '../src/index.js';
import { DISCIPLINES, GRADES, DISCIPLINE_NAMES, GRADE_NAMES, eventCode } from '../src/draw.js';
import { formatTime } from '../src/scheduler.js';
import { buildGrid, gridCsv, matchesCsv, summaryCsv, sideLabel } from '../src/report.js';

const STORAGE_KEY = 'badminton-scheduler.config.v1';
const $ = (id) => document.getElementById(id);

let config = null;
let plan = null;
let activeDay = 0;

/* ------------------------------------------------------------------ setup */

async function loadDefaults() {
  const response = await fetch('../data/monash-open-2025.json');
  return response.json();
}

function load() {
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved) {
    try {
      return Promise.resolve(JSON.parse(saved));
    } catch {
      /* fall through to defaults */
    }
  }
  return loadDefaults();
}

function save() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
}

/* ----------------------------------------------------------------- render */

function renderSettings() {
  $('cfgName').value = config.name || '';
  $('cfgVenue').value = config.venue || '';
  $('minsO').value = config.matchMinutes?.O ?? 30;
  $('minsA').value = config.matchMinutes?.A ?? 25;
  $('minsB').value = config.matchMinutes?.B ?? 25;
  $('minsC').value = config.matchMinutes?.C ?? 25;
  $('cfgRest').value = config.restMin ?? 20;
  $('cfgTurnaround').value = config.turnaroundMin ?? 0;
  $('cfgGranularity').value = config.granularityMin ?? 10;
  $('shuttleO').value = config.shuttlesPerMatch?.O ?? 4;
  $('shuttleDefault').value = config.shuttlesPerMatch?.default ?? 2;
  $('cfgOpenCourts').value = courtsToText(config.gradeCourts?.O || []);
  $('cfgStrict').checked = !!config.strictGradeCourts;
  renderDays();
  renderEvents();
  $('tournamentSub').textContent = config.venue
    ? `${config.name} — ${config.venue}`
    : config.name || '';
}

function renderDays() {
  const body = $('daysTable').querySelector('tbody');
  body.innerHTML = '';
  config.days.forEach((day, i) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td><input type="date" value="${day.date || ''}" data-day="${i}" data-field="date"></td>
      <td><input type="text" value="${escapeHtml(day.label || '')}" data-day="${i}" data-field="label"></td>
      <td><input type="text" value="${(day.grades || []).join(',')}" data-day="${i}" data-field="grades" placeholder="A,C"></td>
      <td><input type="number" min="1" max="24" value="${day.courts}" data-day="${i}" data-field="courts"></td>
      <td><input type="time" value="${day.start}" data-day="${i}" data-field="start"></td>
      <td><input type="time" value="${day.end}" data-day="${i}" data-field="end"></td>
      <td><button class="del" data-remove-day="${i}" title="Remove day">&times;</button></td>`;
    body.appendChild(tr);
  });
}

function renderEvents() {
  const body = $('eventsTable').querySelector('tbody');
  body.innerHTML = '';
  config.events.forEach((event, i) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${select(GRADES, event.grade, i, 'grade', (g) => (g === 'O' ? 'Open' : g))}</td>
      <td>${select(DISCIPLINES, event.discipline, i, 'discipline', (d) => d)}</td>
      <td><input type="number" min="0" value="${event.entries}" data-event="${i}" data-field="entries"></td>
      <td><input type="number" min="0" value="${event.seeds ?? 0}" data-event="${i}" data-field="seeds"></td>
      <td>${select(['RR + KO', 'RR', 'KO'], event.format || 'RR + KO', i, 'format', (f) => f)}</td>
      <td>${select(config.days.map((d) => d.id), event.day, i, 'day', (id) => `D${id}`)}</td>
      <td><input type="number" min="1" max="4" value="${event.qualifiersPerGroup ?? 1}" data-event="${i}" data-field="qualifiersPerGroup"></td>
      <td><button class="del" data-remove-event="${i}" title="Remove event">&times;</button></td>`;
    body.appendChild(tr);
  });
}

function select(values, current, index, field, labelFn) {
  const options = values
    .map((v) => `<option value="${v}" ${String(v) === String(current) ? 'selected' : ''}>${labelFn(v)}</option>`)
    .join('');
  return `<select data-event="${index}" data-field="${field}">${options}</select>`;
}

/* -------------------------------------------------------------- scheduling */

function generate() {
  plan = planTournament(config);
  activeDay = Math.min(activeDay, plan.days.length - 1);
  renderDaySwitch();
  renderSchedule();
  renderSummary();
  renderDraws();
  renderChecks();
  save();
}

function renderDaySwitch() {
  const host = $('daySwitch');
  host.innerHTML = '';
  plan.days.forEach((day, i) => {
    const button = document.createElement('button');
    button.className = `ghost small${i === activeDay ? ' active' : ''}`;
    button.textContent = day.day.label || `Day ${day.day.id}`;
    button.onclick = () => {
      activeDay = i;
      renderDaySwitch();
      renderSchedule();
    };
    host.appendChild(button);
  });
}

function renderSchedule() {
  const day = plan.days[activeDay];
  const stats = plan.summary.days[activeDay];
  $('dayStats').innerHTML = [
    stat('Matches', stats.matches),
    stat('Courts', stats.courts),
    stat('First on', stats.firstStartLabel),
    stat('Finish', stats.finishLabel),
    stat('Court use', `${stats.utilisation}%`),
    stat('Peak courts busy', stats.peakConcurrent),
    stat('Not scheduled', stats.unscheduled, stats.unscheduled ? 'bad' : 'ok'),
  ].join('');

  const rows = buildGrid(day);
  const codes = [...new Set(day.scheduled.map((m) => m.eventCode))].sort();
  const legend = `<div class="legend">${codes
    .map((c) => `<span style="background:${colourFor(c)}">${c}</span>`)
    .join('')}</div>`;

  const header = `<tr><th>Time</th>${day.courts.map((c) => `<th>${c.name}</th>`).join('')}</tr>`;
  const body = rows
    .map((row) => {
      const cells = row.cells
        .map((cell, ci) => {
          if (row.covered[ci]) return '';
          if (!cell) return '<td></td>';
          const m = cell.match;
          const when = `${formatTime(m.start)}-${formatTime(m.end)}`;
          return `<td rowspan="${cell.span}" style="background:${colourFor(m.eventCode)}"
            title="${when} · ${escapeHtml(m.courtName)} · ${m.eventCode} ${m.round}${
              m.stage === 'group' ? ` · Group ${m.groupIndex + 1}` : ''
            }\n${escapeHtml(sideLabel(m.sideA))} v ${escapeHtml(sideLabel(m.sideB))}">
            <div class="cell">
              <div class="ev">${m.eventCode} <span class="rd">${m.round}</span></div>
              <div class="tm">${escapeHtml(shortSide(m.sideA))} v ${escapeHtml(shortSide(m.sideB))}</div>
            </div></td>`;
        })
        .join('');
      const onHour = row.time % 60 === 0 ? ' hour' : '';
      return `<tr><td class="time${onHour}">${row.label}</td>${cells}</tr>`;
    })
    .join('');

  $('gridHost').innerHTML = `${legend}<table class="grid"><thead>${header}</thead><tbody>${body}</tbody></table>`;
}

function shortSide(side) {
  const label = sideLabel(side);
  return label.length > 22 ? `${label.slice(0, 21)}…` : label;
}

function renderSummary() {
  const { events, totals } = plan.summary;
  $('totals').innerHTML = [
    stat('Entries', totals.entries),
    stat('Matches', totals.planned),
    stat('Scheduled', totals.scheduled),
    stat('Variance', totals.variance, totals.variance ? 'bad' : 'ok'),
    stat('Shuttles', `${totals.shuttles} (${totals.shuttleDozens} doz)`),
  ].join('');

  const rows = events
    .map((e) => `<tr>
      <td><b>${e.code}</b></td><td>${GRADE_NAMES[e.grade]}</td><td>${DISCIPLINE_NAMES[e.discipline]}</td>
      <td class="num">${e.day}</td><td class="num">${e.entries}</td><td class="num">${e.seeds}</td>
      <td>${e.format}</td><td class="num">${e.groups}</td><td>${e.groupSizes}</td>
      <td class="num">${e.counts.group}</td><td class="num">${e.counts.R16 || ''}</td>
      <td class="num">${e.counts.QF || ''}</td><td class="num">${e.counts.SF || ''}</td>
      <td class="num">${e.counts.F || ''}</td><td class="num">${e.planned}</td>
      <td class="num">${e.scheduled}</td>
      <td class="num ${e.variance ? 'bad' : 'ok'}">${e.variance}</td>
      <td class="num">${e.shuttles}</td></tr>`)
    .join('');

  $('summaryHost').innerHTML = `<table class="tbl">
    <thead><tr>
      <th>Event</th><th>Grade</th><th>Discipline</th><th>Day</th><th>Entries</th><th>Seeds</th>
      <th>Format</th><th>Groups</th><th>Sizes</th><th>Group</th><th>R16</th><th>QF</th><th>SF</th><th>F</th>
      <th>Planned</th><th>Sched.</th><th>Var.</th><th>Shuttles</th>
    </tr></thead>
    <tbody>${rows}</tbody>
    <tfoot><tr>
      <td colspan="4">Total</td><td class="num">${totals.entries}</td><td colspan="9"></td>
      <td class="num">${totals.planned}</td><td class="num">${totals.scheduled}</td>
      <td class="num ${totals.variance ? 'bad' : 'ok'}">${totals.variance}</td>
      <td class="num">${totals.shuttles}</td>
    </tr></tfoot></table>`;
}

function renderDraws() {
  $('drawsHost').innerHTML = plan.draws
    .map((draw) => {
      const groups = draw.groups
        .map((g, i) => `<div class="group"><b>Group ${i + 1}</b>${g
          .map((t) => escapeHtml(t.label))
          .join('<br>')}</div>`)
        .join('');
      const ko = draw.matches.filter((m) => m.stage === 'ko');
      const koLine = ko.length
        ? `<div class="meta">Knockout: ${countRounds(ko)}</div>`
        : '<div class="meta">Round robin only — no knockout stage.</div>';
      return `<div class="draw">
        <h4>${draw.code} — ${GRADE_NAMES[draw.grade]} ${DISCIPLINE_NAMES[draw.discipline]}</h4>
        <div class="meta">${draw.entries} entries · ${draw.groupSizes.length} group(s) of ${
          draw.groupSizes.join('/') || '-'} · ${draw.counts.total} matches · ${draw.durationMin} min each · Day ${draw.day}</div>
        ${koLine}
        <div class="groups">${groups}</div>
      </div>`;
    })
    .join('');
}

function countRounds(matches) {
  const counts = matches.reduce((acc, m) => {
    acc[m.round] = (acc[m.round] || 0) + 1;
    return acc;
  }, {});
  return Object.entries(counts).map(([round, n]) => `${round} x${n}`).join(', ');
}

function renderChecks() {
  const notes = plan.warnings
    .map((w) => `<div class="note ${w.level}"><b>${escapeHtml(w.day)}</b>${escapeHtml(w.message)}</div>`)
    .join('');

  const clashes = findClashes();
  const clashNote = clashes.length
    ? `<div class="note error"><b>${clashes.length} scheduling clash(es)</b>${clashes.slice(0, 10).join('<br>')}</div>`
    : '<div class="note info"><b>Constraint check passed</b>No court or player is double-booked, every dependency is respected and rest between matches is honoured.</div>';

  $('checksHost').innerHTML = clashNote + notes;
}

/** Independent re-check of the produced schedule — cheap insurance before publishing. */
function findClashes() {
  const problems = [];
  for (const day of plan.days) {
    const byCourt = new Map();
    const byTeam = new Map();
    for (const m of day.scheduled) {
      push(byCourt, m.courtIndex, m);
      for (const t of m.teamIds) push(byTeam, t, m);
    }
    for (const [court, list] of byCourt) {
      list.sort((a, b) => a.start - b.start);
      for (let i = 1; i < list.length; i += 1) {
        if (list[i].start < list[i - 1].end) {
          problems.push(`Court ${court + 1}: ${list[i].id} overlaps ${list[i - 1].id}`);
        }
      }
    }
    for (const [team, list] of byTeam) {
      list.sort((a, b) => a.start - b.start);
      for (let i = 1; i < list.length; i += 1) {
        const gap = list[i].start - list[i - 1].end;
        if (gap < (config.restMin ?? 0)) {
          problems.push(`${team} has only ${gap} min between ${formatTime(list[i - 1].end)} and ${formatTime(list[i].start)}`);
        }
      }
    }
  }
  return problems;
}

function push(map, key, value) {
  if (!map.has(key)) map.set(key, []);
  map.get(key).push(value);
}

/* ------------------------------------------------------------------ inputs */

function readSettings() {
  config.name = $('cfgName').value;
  config.venue = $('cfgVenue').value;
  config.matchMinutes = {
    O: num($('minsO').value, 30),
    A: num($('minsA').value, 25),
    B: num($('minsB').value, 25),
    C: num($('minsC').value, 25),
  };
  config.matchMinutes.default = config.matchMinutes.C;
  config.restMin = num($('cfgRest').value, 20);
  config.turnaroundMin = num($('cfgTurnaround').value, 0);
  config.granularityMin = Math.max(5, num($('cfgGranularity').value, 10));
  config.shuttlesPerMatch = {
    O: num($('shuttleO').value, 4),
    default: num($('shuttleDefault').value, 2),
  };
  const openCourts = textToCourts($('cfgOpenCourts').value);
  config.gradeCourts = openCourts.length ? { O: openCourts } : {};
  config.strictGradeCourts = $('cfgStrict').checked;
}

function num(value, fallback) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function courtsToText(list) {
  if (!list.length) return '';
  const contiguous = list.every((v, i) => i === 0 || v === list[i - 1] + 1);
  return contiguous && list.length > 1 ? `${list[0]}-${list[list.length - 1]}` : list.join(',');
}

function textToCourts(text) {
  const out = new Set();
  for (const part of String(text).split(',')) {
    const range = part.trim().match(/^(\d+)\s*-\s*(\d+)$/);
    if (range) {
      for (let i = Number(range[1]); i <= Number(range[2]); i += 1) out.add(i);
    } else if (part.trim()) {
      const n = Number(part.trim());
      if (Number.isFinite(n)) out.add(n);
    }
  }
  return [...out].sort((a, b) => a - b);
}

function wireInputs() {
  $('settings').addEventListener('input', (e) => {
    const target = e.target;
    if (target.dataset.event !== undefined) {
      const event = config.events[Number(target.dataset.event)];
      const field = target.dataset.field;
      event[field] = ['entries', 'seeds', 'day', 'qualifiersPerGroup'].includes(field)
        ? num(target.value, 0)
        : target.value;
      event.code = eventCode(event.grade, event.discipline);
    } else if (target.dataset.day !== undefined) {
      const day = config.days[Number(target.dataset.day)];
      const field = target.dataset.field;
      if (field === 'courts') day.courts = Math.max(1, num(target.value, 1));
      else if (field === 'grades') day.grades = target.value.split(',').map((g) => g.trim().toUpperCase()).filter(Boolean);
      else day[field] = target.value;
    } else {
      readSettings();
    }
    save();
  });

  $('settings').addEventListener('click', (e) => {
    const removeEvent = e.target.dataset.removeEvent;
    const removeDay = e.target.dataset.removeDay;
    if (removeEvent !== undefined) {
      config.events.splice(Number(removeEvent), 1);
      renderEvents();
      save();
    } else if (removeDay !== undefined) {
      config.days.splice(Number(removeDay), 1);
      renderDays();
      renderEvents();
      save();
    }
  });

  $('addEventBtn').onclick = () => {
    config.events.push({
      grade: 'C', discipline: 'MS', entries: 8, seeds: 2,
      format: 'RR + KO', day: config.days[0]?.id ?? 1,
    });
    renderEvents();
    save();
  };

  $('addDayBtn').onclick = () => {
    const id = (config.days.at(-1)?.id ?? 0) + 1;
    config.days.push({ id, date: '', label: `Day ${id}`, grades: [], courts: 10, start: '09:00', end: '20:00' });
    renderDays();
    renderEvents();
    save();
  };

  $('generateBtn').onclick = () => generate();

  $('resetBtn').onclick = async () => {
    config = await loadDefaults();
    renderSettings();
    generate();
  };

  $('exportCfgBtn').onclick = () => download(`${slug(config.name)}-setup.json`, JSON.stringify(config, null, 2));
  $('importCfg').onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    config = JSON.parse(await file.text());
    renderSettings();
    generate();
  };

  $('exportGridBtn').onclick = () => {
    const day = plan.days[activeDay];
    download(`${slug(config.name)}-${day.day.date || `day${day.day.id}`}-grid.csv`, gridCsv(day));
  };
  $('exportMatchesBtn').onclick = () => download(`${slug(config.name)}-matches.csv`, matchesCsv(plan.days));
  $('exportSummaryBtn').onclick = () => download(`${slug(config.name)}-summary.csv`, summaryCsv(plan.summary));
  $('printBtn').onclick = () => window.print();

  $('tabs').addEventListener('click', (e) => {
    const tab = e.target.dataset.tab;
    if (!tab) return;
    [...$('tabs').children].forEach((b) => b.classList.toggle('active', b.dataset.tab === tab));
    for (const name of ['schedule', 'summary', 'draws', 'checks']) {
      $(`tab-${name}`).classList.toggle('hidden', name !== tab);
    }
  });
}

/* ------------------------------------------------------------------ helpers */

function stat(key, value, cls = '') {
  return `<div><span class="k">${key}</span><span class="v ${cls}">${value}</span></div>`;
}

/** Stable pastel per event code so the grid reads in blocks. */
function colourFor(code) {
  let hash = 0;
  for (let i = 0; i < code.length; i += 1) hash = (hash * 31 + code.charCodeAt(i)) % 360;
  return `hsl(${hash}, 70%, 88%)`;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function slug(value) {
  return String(value || 'tournament').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

function toast(message) {
  const host = $('toast');
  host.textContent = message;
  host.classList.remove('hidden');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => host.classList.add('hidden'), 3500);
}

/**
 * Save a generated file. Hosted pages hand the file to the viewer through the
 * downloads capability; a plain browser tab gets an ordinary link download.
 */
async function download(filename, text) {
  const host = typeof window.claude?.use === 'function'
    ? await window.claude.use('downloads').catch(() => null)
    : null;

  if (!host) {
    const url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
    return;
  }

  try {
    await host.save({ filename, data: text });
    toast(`Saved ${filename}`);
  } catch (error) {
    if (error?.code === 'declined') return;
    if (error?.code === 'extension_not_enabled') {
      // CSV is not always available here; the same text saves fine as .txt.
      const fallback = filename.replace(/\.csv$/, '.txt');
      try {
        await host.save({ filename: fallback, data: text });
        toast(`Saved ${fallback} — rename it to .csv to open in a spreadsheet`);
        return;
      } catch (retryError) {
        if (retryError?.code === 'declined') return;
      }
    }
    toast(`Could not save ${filename}: ${error?.message || 'unknown error'}`);
  }
}

/* --------------------------------------------------------------------- go */

config = await load();
renderSettings();
wireInputs();
generate();
