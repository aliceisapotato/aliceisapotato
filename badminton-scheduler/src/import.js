/**
 * Draw import.
 *
 * Reads a draw as published by the tournament desk — .xlsx, .csv/.tsv or the
 * app's own JSON — works out which columns hold the event, group and entrant,
 * and turns it into the event specs the draw builder and scheduler consume.
 *
 * Two shapes are accepted:
 *   - a full draw: one row per entrant, with the group they were drawn into
 *   - an entry list: the same without a group column, in which case the app
 *     forms the groups itself
 */

import { DISCIPLINES, GRADES, eventCode } from './draw.js';

/* ------------------------------------------------------------- xlsx reader */

/** Minimal .xlsx reader: zip central directory + raw inflate + sheet XML. */
export async function readXlsx(arrayBuffer) {
  const files = await unzip(new Uint8Array(arrayBuffer));
  const xml = (name) => (files.has(name) ? decode(files.get(name)) : '');

  const shared = [...xml('xl/sharedStrings.xml').matchAll(/<si>([\s\S]*?)<\/si>/g)]
    .map(([, block]) => [...block.matchAll(/<t[^>]*>([\s\S]*?)<\/t>/g)]
      .map(([, text]) => unescapeXml(text))
      .join(''));

  const rels = new Map(
    [...xml('xl/_rels/workbook.xml.rels').matchAll(/<Relationship\b[^>]*\/>/g)]
      .map(([tag]) => [attr(tag, 'Id'), attr(tag, 'Target')]),
  );

  const sheets = [];
  for (const [tag] of xml('xl/workbook.xml').matchAll(/<sheet\b[^>]*\/>/g)) {
    const name = unescapeXml(attr(tag, 'name') || `Sheet ${sheets.length + 1}`);
    const target = rels.get(attr(tag, 'r:id')) || `worksheets/sheet${sheets.length + 1}.xml`;
    const path = `xl/${target.replace(/^\/?xl\//, '').replace(/^\//, '')}`;
    const sheetXml = xml(path);
    if (sheetXml) sheets.push({ name, rows: parseSheet(sheetXml, shared) });
  }
  return sheets;
}

function parseSheet(sheetXml, shared) {
  const rows = [];
  for (const [rowTag] of sheetXml.matchAll(/<row\b[^>]*\/>|<row\b[^>]*>[\s\S]*?<\/row>/g)) {
    // Spreadsheets omit empty rows from the XML but number the ones they keep,
    // so place each row at its stated index and leave the gaps blank.
    const rowNumber = Number(attr(rowTag, 'r')) || rows.length + 1;
    while (rows.length < rowNumber - 1) rows.push([]);
    const cells = [];
    for (const [cellTag] of rowTag.matchAll(/<c\b[^>]*\/>|<c\b[^>]*>[\s\S]*?<\/c>/g)) {
      const ref = attr(cellTag, 'r') || '';
      const index = ref ? columnIndex(ref) : cells.length;
      const type = attr(cellTag, 't');
      let value = '';
      if (type === 'inlineStr') {
        value = [...cellTag.matchAll(/<t[^>]*>([\s\S]*?)<\/t>/g)].map(([, t]) => unescapeXml(t)).join('');
      } else {
        const raw = cellTag.match(/<v>([\s\S]*?)<\/v>/);
        if (raw) value = type === 's' ? (shared[Number(raw[1])] ?? '') : unescapeXml(raw[1]);
      }
      cells[index] = String(value).trim();
    }
    rows[rowNumber - 1] = Array.from(cells, (c) => c ?? '');
  }
  return Array.from(rows, (row) => row || []);
}

function columnIndex(ref) {
  const letters = ref.match(/^[A-Z]+/i)?.[0] || 'A';
  return [...letters.toUpperCase()].reduce((n, ch) => n * 26 + (ch.charCodeAt(0) - 64), 0) - 1;
}

function attr(tag, name) {
  const match = tag.match(new RegExp(`${name.replace(':', '\\:')}="([^"]*)"`));
  return match ? match[1] : '';
}

function unescapeXml(text) {
  return text
    .replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"').replace(/&apos;/g, "'")
    .replace(/&#(\d+);/g, (_, code) => String.fromCharCode(Number(code)))
    .replace(/&amp;/g, '&');
}

function decode(bytes) {
  return new TextDecoder().decode(bytes);
}

/** Read a zip through its central directory, inflating deflate entries. */
async function unzip(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let eocd = -1;
  for (let i = bytes.length - 22; i >= 0 && i > bytes.length - 65558; i -= 1) {
    if (view.getUint32(i, true) === 0x06054b50) { eocd = i; break; }
  }
  if (eocd < 0) throw new Error('Not a valid .xlsx file (no zip directory found)');

  const count = view.getUint16(eocd + 10, true);
  let pointer = view.getUint32(eocd + 16, true);
  const files = new Map();

  for (let i = 0; i < count; i += 1) {
    if (view.getUint32(pointer, true) !== 0x02014b50) break;
    const method = view.getUint16(pointer + 10, true);
    const compressedSize = view.getUint32(pointer + 20, true);
    const nameLength = view.getUint16(pointer + 28, true);
    const extraLength = view.getUint16(pointer + 30, true);
    const commentLength = view.getUint16(pointer + 32, true);
    const localOffset = view.getUint32(pointer + 42, true);
    const name = decode(bytes.subarray(pointer + 46, pointer + 46 + nameLength));

    const localNameLength = view.getUint16(localOffset + 26, true);
    const localExtraLength = view.getUint16(localOffset + 28, true);
    const start = localOffset + 30 + localNameLength + localExtraLength;
    const raw = bytes.subarray(start, start + compressedSize);

    files.set(name, method === 0 ? raw : await inflateRaw(raw));
    pointer += 46 + nameLength + extraLength + commentLength;
  }
  return files;
}

async function inflateRaw(bytes) {
  const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('deflate-raw'));
  return new Uint8Array(await new Response(stream).arrayBuffer());
}

/* ------------------------------------------------------ delimited text */

/** CSV/TSV parser with quoted-field support; the delimiter is detected. */
export function parseDelimited(text, delimiter) {
  const body = text.replace(/^﻿/, '');
  const sep = delimiter || detectDelimiter(body);
  const rows = [];
  let row = [];
  let field = '';
  let quoted = false;

  for (let i = 0; i < body.length; i += 1) {
    const char = body[i];
    if (quoted) {
      if (char === '"') {
        if (body[i + 1] === '"') { field += '"'; i += 1; } else quoted = false;
      } else field += char;
    } else if (char === '"') quoted = true;
    else if (char === sep) { row.push(field.trim()); field = ''; }
    else if (char === '\n') {
      row.push(field.trim());
      if (row.some((c) => c !== '')) rows.push(row);
      row = [];
      field = '';
    }
    else if (char !== '\r') field += char;
  }
  row.push(field.trim());
  if (row.some((c) => c !== '')) rows.push(row);
  return rows;
}

function detectDelimiter(text) {
  const sample = text.split('\n').slice(0, 20).join('\n');
  const counts = [',', '\t', ';'].map((d) => [d, sample.split(d).length]);
  return counts.sort((a, b) => b[1] - a[1])[0][1] > 1 ? counts[0][0] : ',';
}

/** Read any supported file into sheets of rows. */
export async function readDrawFile({ name = '', text, buffer }) {
  const extension = name.split('.').pop().toLowerCase();
  if (extension === 'xlsx' || extension === 'xlsm') {
    return readXlsx(buffer);
  }
  if (extension === 'json') {
    return [{ name: 'json', json: JSON.parse(text), rows: [] }];
  }
  return [{ name: name || 'pasted', rows: parseDelimited(text) }];
}

/* ------------------------------------------------------- column detection */

const COLUMN_PATTERNS = {
  event: /^(event|discipline\s*&?\s*grade|category|event\s*code)$/i,
  grade: /^(grade|division|level)$/i,
  discipline: /^(discipline|type|event\s*type)$/i,
  group: /^(group|gr|pool|group\s*(no|number|#))$/i,
  entrant: /^(entrant|player|name|team|pair|partnership|player\s*1|player\s*a|entry)$/i,
  partner: /^(partner|player\s*2|player\s*b|second\s*player)$/i,
  seed: /^(seed|seeding|seed\s*(no|number|#))$/i,
  day: /^(day|date|play\s*day)$/i,
  club: /^(club|team\s*name|association|state)$/i,
};

/**
 * Find the header row and map the columns we understand. Rows above the header
 * (titles, notes) are ignored.
 */
export function sniffLayout(rows) {
  let best = { score: -1, headerRow: 0, columns: {} };

  for (let r = 0; r < Math.min(rows.length, 25); r += 1) {
    const columns = {};
    let score = 0;
    rows[r].forEach((cell, c) => {
      const header = String(cell || '').trim();
      if (!header) return;
      for (const [field, pattern] of Object.entries(COLUMN_PATTERNS)) {
        if (columns[field] === undefined && pattern.test(header)) {
          columns[field] = c;
          score += field === 'entrant' || field === 'event' ? 2 : 1;
        }
      }
    });
    if (score > best.score) best = { score, headerRow: r, columns };
  }

  if (best.score <= 0) return { headerRow: 0, columns: {}, confident: false };
  const usable = best.columns.entrant !== undefined
    && (best.columns.event !== undefined || best.columns.discipline !== undefined);
  return { ...best, confident: usable };
}

/* ---------------------------------------------------------- event parsing */

const DISCIPLINE_WORDS = [
  [/mixed/i, 'XD'],
  [/(men|male|boy).*(double|db|dbl)/i, 'MD'],
  [/(women|ladies|female|girl).*(double|db|dbl)/i, 'WD'],
  [/(men|male|boy).*(single|sg)/i, 'MS'],
  [/(women|ladies|female|girl).*(single|sg)/i, 'WS'],
];

/** Read 'MSC', 'XD', "Men's Singles C" or 'Open Mixed Doubles' into grade + discipline. */
export function parseEventLabel(label) {
  const text = String(label || '').trim();
  if (!text) return null;

  const code = text.toUpperCase().replace(/[^A-Z]/g, '');
  const codeMatch = code.match(/^(MS|WS|MD|WD|XD)(O|A|B|C)?$/);
  if (codeMatch) {
    return { discipline: codeMatch[1], grade: codeMatch[2] || 'O' };
  }

  const discipline = DISCIPLINE_WORDS.find(([pattern]) => pattern.test(text))?.[1];
  if (!discipline) return null;

  let grade = 'O';
  if (/\bopen\b/i.test(text)) grade = 'O';
  else {
    const graded = text.match(/\b(?:grade\s*)?([ABC])\b(?:\s*grade)?/i);
    if (graded) grade = graded[1].toUpperCase();
  }
  return { discipline, grade };
}

export function parseGradeCell(value) {
  const text = String(value || '').trim().toUpperCase();
  if (!text) return null;
  if (/^O(PEN)?$/.test(text)) return 'O';
  const letter = text.match(/^([ABC])(\s*GRADE)?$/) || text.match(/GRADE\s*([ABC])/);
  return letter ? letter[1] : null;
}

export function parseDisciplineCell(value) {
  const text = String(value || '').trim();
  const code = text.toUpperCase().replace(/[^A-Z]/g, '');
  if (DISCIPLINES.includes(code)) return code;
  return DISCIPLINE_WORDS.find(([pattern]) => pattern.test(text))?.[1] || null;
}

/** 'Group 3', 'Gr 3', '3' and 'C' all identify a group; keep a sortable key. */
export function parseGroupCell(value) {
  const text = String(value || '').trim();
  if (!text) return null;
  const numbered = text.match(/(\d+)/);
  if (numbered) return { key: text, order: Number(numbered[1]) };
  const letter = text.replace(/^(group|gr|pool)\s*/i, '').trim().toUpperCase();
  if (!letter) return null;
  return { key: text, order: letter.charCodeAt(0) - 64 };
}

/* -------------------------------------------------------------- importing */

/**
 * Turn parsed rows into event specs.
 *
 * @param {string[][]} rows
 * @param {object} layout  { headerRow, columns } — usually from sniffLayout
 * @param {object} options { defaultFormat, dayForGrade }
 */
export function importDraw(rows, layout, options = {}) {
  const { headerRow = 0, columns = {} } = layout || {};
  const issues = [];
  const events = new Map();
  let imported = 0;

  for (let r = headerRow + 1; r < rows.length; r += 1) {
    const row = rows[r];
    if (!row || row.every((cell) => !String(cell || '').trim())) continue;

    const cell = (field) => (columns[field] === undefined ? '' : String(row[columns[field]] ?? '').trim());

    let grade = parseGradeCell(cell('grade'));
    let discipline = parseDisciplineCell(cell('discipline'));
    if (cell('event')) {
      const parsed = parseEventLabel(cell('event'));
      if (parsed) {
        discipline = discipline || parsed.discipline;
        grade = grade || parsed.grade;
      }
    }
    if (!discipline) {
      issues.push({ level: 'warn', row: r + 1, message: `Row ${r + 1}: could not tell which event "${cell('event') || cell('discipline')}" is — skipped.` });
      continue;
    }
    grade = grade || 'O';
    if (!GRADES.includes(grade)) {
      issues.push({ level: 'warn', row: r + 1, message: `Row ${r + 1}: unknown grade "${grade}" — treated as Open.` });
      grade = 'O';
    }

    const name = cell('entrant');
    if (!name) {
      issues.push({ level: 'warn', row: r + 1, message: `Row ${r + 1}: no entrant name — skipped.` });
      continue;
    }
    const partner = cell('partner');
    const label = partner ? `${name} / ${partner}` : name;
    const seed = Number(cell('seed')) || 0;
    const group = parseGroupCell(cell('group'));

    const code = eventCode(grade, discipline);
    if (!events.has(code)) {
      events.set(code, { code, grade, discipline, groups: new Map(), entrants: [], seeds: 0, day: null });
    }
    const event = events.get(code);
    const dayCell = cell('day');
    if (dayCell && event.day === null) event.day = Number(dayCell) || dayCell;

    const entrant = { label, seed, club: cell('club') };
    if (seed) event.seeds += 1;
    event.entrants.push(entrant);
    if (group) {
      if (!event.groups.has(group.key)) event.groups.set(group.key, { order: group.order, entrants: [] });
      event.groups.get(group.key).entrants.push(entrant);
    }
    imported += 1;
  }

  const specs = [...events.values()].map((event) => {
    const drawn = [...event.groups.entries()].sort((a, b) => a[1].order - b[1].order || a[0].localeCompare(b[0]));
    const partial = drawn.length > 0 && drawn.reduce((n, [, g]) => n + g.entrants.length, 0) !== event.entrants.length;
    if (partial) {
      issues.push({
        level: 'warn',
        message: `${event.code}: some entrants have no group — the app will draw the ungrouped ones itself.`,
      });
    }
    const spec = {
      grade: event.grade,
      discipline: event.discipline,
      code: event.code,
      entries: event.entrants.length,
      seeds: event.seeds,
      format: options.defaultFormat || 'RR + KO',
      day: event.day ?? options.dayForGrade?.(event.grade) ?? null,
      entrants: event.entrants.map((e) => e.label),
    };
    if (drawn.length && !partial) {
      spec.groups = drawn.map(([, g]) => g.entrants.map((e) => e.label));
      spec.groupNames = drawn.map(([key]) => key);
    }
    return spec;
  });

  for (const spec of specs) {
    const duplicates = findDuplicates(spec.entrants);
    for (const name of duplicates) {
      issues.push({ level: 'warn', message: `${spec.code}: "${name}" appears more than once in the draw.` });
    }
    if (spec.entries < 2) {
      issues.push({ level: 'warn', message: `${spec.code}: only ${spec.entries} entry — no matches to schedule.` });
    }
  }

  specs.sort((a, b) => GRADES.indexOf(a.grade) - GRADES.indexOf(b.grade)
    || DISCIPLINES.indexOf(a.discipline) - DISCIPLINES.indexOf(b.discipline));

  return {
    events: specs,
    issues,
    stats: {
      rows: imported,
      events: specs.length,
      entries: specs.reduce((sum, s) => sum + s.entries, 0),
      drawn: specs.filter((s) => s.groups).length,
    },
  };
}

function findDuplicates(labels) {
  const seen = new Set();
  const duplicates = new Set();
  for (const label of labels) {
    const key = label.toLowerCase().replace(/\s+/g, ' ');
    if (seen.has(key)) duplicates.add(label);
    seen.add(key);
  }
  return [...duplicates];
}

/** Merge imported events into a tournament config, keeping day/court settings. */
export function applyImport(config, imported) {
  const days = config.days || [];
  const dayForGrade = (grade) => days.find((d) => (d.grades || []).includes(grade))?.id ?? days[0]?.id ?? 1;
  return {
    ...config,
    events: imported.events.map((spec) => ({ ...spec, day: spec.day ?? dayForGrade(spec.grade) })),
  };
}
