/* MO2026FEST — giveaway draw
   Entrant import, cryptographically uniform draws, winner log. No deps. */
'use strict';

const STORAGE_KEY = 'mo2026fest-draw-v1';

const state = {
  guests: [],   // { id, name, email, note }
  winners: [],  // { id, name, email, prize, ts }
  prize: '',
  winnerCount: 1,
  dedupe: true,
  excludeWinners: true,
  sound: true,
};

let lastDraw = null;   // { winners: [...], prize } — for "redraw last"
let drawing = false;

const $ = (id) => document.getElementById(id);
const el = {
  guestInput: $('guestInput'), addBtn: $('addBtn'), replaceBtn: $('replaceBtn'),
  fileInput: $('fileInput'), dedupe: $('dedupe'), excludeWinners: $('excludeWinners'),
  search: $('search'), clearBtn: $('clearBtn'), entrantList: $('entrantList'),
  prizeInput: $('prizeInput'), winnerCount: $('winnerCount'),
  poolPill: $('poolPill'), winnerPill: $('winnerPill'),
  reel: $('reel'), reelName: $('reelName'), reelMeta: $('reelMeta'),
  stagePrize: $('stagePrize'), drawBtn: $('drawBtn'), redrawBtn: $('redrawBtn'),
  winnerCards: $('winnerCards'), winnerLog: $('winnerLog'),
  exportBtn: $('exportBtn'), resetWinnersBtn: $('resetWinnersBtn'),
  soundBtn: $('soundBtn'), soundIcon: $('soundIcon'), stageBtn: $('stageBtn'),
  toast: $('toast'), confetti: $('confetti'), shuttleField: $('shuttleField'),
};

/* ── Random ────────────────────────────────────────────────────────────
   Uniform integer in [0, max) from crypto entropy. The modulo of a raw
   32-bit value is biased toward low indices, so values in the ragged tail
   above the largest exact multiple of `max` are rejected and redrawn.    */
function randomInt(max) {
  if (max <= 0) throw new RangeError('max must be positive');
  const RANGE = 2 ** 32;
  const limit = RANGE - (RANGE % max);
  const buf = new Uint32Array(1);
  let x;
  do { crypto.getRandomValues(buf); x = buf[0]; } while (x >= limit);
  return x % max;
}

/** Pick `k` distinct items via a partial Fisher–Yates shuffle. */
function sampleWithoutReplacement(items, k) {
  const pool = items.slice();
  const picked = [];
  const n = Math.min(k, pool.length);
  for (let i = 0; i < n; i++) {
    const j = i + randomInt(pool.length - i);
    [pool[i], pool[j]] = [pool[j], pool[i]];
    picked.push(pool[i]);
  }
  return picked;
}

/* ── Parsing ─────────────────────────────────────────────────────────── */

/** Split one CSV line, honouring "quoted, fields" and "" escapes. */
function splitCsvLine(line, delim) {
  const out = [];
  let cur = '', inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (inQuotes) {
      if (c === '"') {
        if (line[i + 1] === '"') { cur += '"'; i++; } else { inQuotes = false; }
      } else { cur += c; }
    } else if (c === '"') {
      inQuotes = true;
    } else if (c === delim) {
      out.push(cur); cur = '';
    } else {
      cur += c;
    }
  }
  out.push(cur);
  return out.map((s) => s.trim());
}

function detectDelimiter(line) {
  const counts = { ',': 0, '\t': 0, ';': 0 };
  let inQuotes = false;
  for (const c of line) {
    if (c === '"') inQuotes = !inQuotes;
    else if (!inQuotes && c in counts) counts[c]++;
  }
  const best = Object.keys(counts).reduce((a, b) => (counts[b] > counts[a] ? b : a), ',');
  return counts[best] > 0 ? best : null;
}

/** False when a "…name" header names something other than the guest. */
function isPersonNameHeader(h) {
  return !/first|last|ticket|event|compan|organi[sz]|host|file|user\s*name/.test(h);
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * Parse pasted text into entrants. Handles:
 *   - plain "Name" lines
 *   - "Name, email@x.com" lines
 *   - a full Luma CSV export (header row with name / email columns)
 */
function parseGuests(text) {
  const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
  if (!lines.length) return [];

  const delim = detectDelimiter(lines[0]);
  if (delim) {
    const header = splitCsvLine(lines[0], delim).map((h) => h.toLowerCase());
    const col = (pred) => header.findIndex(pred);

    const firstCol = col((h) => /^(first[\s_-]*name|first|given[\s_-]*name)$/.test(h));
    const lastCol = col((h) => /^(last[\s_-]*name|last|surname|family[\s_-]*name)$/.test(h));
    const emailCol = col((h) => /e-?mail/.test(h));
    const statusCol = col((h) => /(approval[\s_-]*status|^status$|rsvp)/.test(h));
    // Prefer an exact name column; otherwise any "…name" that isn't a
    // first/last part or some other entity's name (ticket, event, company).
    let nameCol = col((h) => /^(name|full[\s_-]*name|attendee[\s_-]*name|guest[\s_-]*name|display[\s_-]*name)$/.test(h));
    if (nameCol === -1) {
      nameCol = col((h) => h.includes('name') && isPersonNameHeader(h));
    }
    // "First Name"/"Last Name" without a combined name column.
    const splitName = firstCol !== -1 && nameCol === -1;
    if (nameCol === -1 && !splitName && emailCol === -1) {
      return parsePlainLines(lines);   // delimiter but no recognisable header
    }
    if (nameCol === -1 && !splitName) nameCol = emailCol;

    const rows = [];
    for (let i = 1; i < lines.length; i++) {
      const cells = splitCsvLine(lines[i], delim);
      const name = splitName
        ? [cells[firstCol], lastCol !== -1 ? cells[lastCol] : ''].filter(Boolean).join(' ').trim()
        : (cells[nameCol] || '').trim();
      const email = emailCol !== -1 ? (cells[emailCol] || '').trim() : '';
      const status = statusCol !== -1 ? (cells[statusCol] || '').trim() : '';
      if (!name && !email) continue;
      // Luma marks declined/pending RSVPs in the status column.
      if (/^(declined|cancel|waitlist|pending)/i.test(status)) continue;
      rows.push({ name: name || email, email, note: status });
    }
    return rows.length ? rows : parsePlainLines(lines);
  }

  return parsePlainLines(lines);
}

function parsePlainLines(lines) {
  return lines.map((line) => {
    const parts = line.split(/[,\t;]/).map((p) => p.trim()).filter(Boolean);
    const email = parts.find((p) => EMAIL_RE.test(p)) || '';
    const name = parts.find((p) => !EMAIL_RE.test(p)) || email;
    return { name, email, note: '' };
  }).filter((g) => g.name);
}

/* ── State ───────────────────────────────────────────────────────────── */

const keyOf = (g) => (g.email ? g.email.toLowerCase() : g.name.trim().toLowerCase());
let idSeq = 0;
const nextId = () => `g${Date.now().toString(36)}${(idSeq++).toString(36)}`;

function addGuests(rows, { replace = false } = {}) {
  if (replace) state.guests = [];
  const seen = new Set(state.guests.map(keyOf));
  let added = 0, skipped = 0;
  for (const row of rows) {
    const g = { id: nextId(), name: row.name, email: row.email || '', note: row.note || '' };
    if (state.dedupe) {
      const k = keyOf(g);
      if (seen.has(k)) { skipped++; continue; }
      seen.add(k);
    }
    state.guests.push(g);
    added++;
  }
  save(); render();
  toast(`${added} entrant${added === 1 ? '' : 's'} added${skipped ? ` · ${skipped} duplicate${skipped === 1 ? '' : 's'} skipped` : ''}`);
}

const winnerKeys = () => new Set(state.winners.map(keyOf));

function eligibleGuests() {
  if (!state.excludeWinners) return state.guests;
  const won = winnerKeys();
  return state.guests.filter((g) => !won.has(keyOf(g)));
}

function save() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({
      guests: state.guests, winners: state.winners, prize: state.prize,
      winnerCount: state.winnerCount, dedupe: state.dedupe,
      excludeWinners: state.excludeWinners, sound: state.sound,
    }));
  } catch (_) { /* private mode / quota — the app still works in-memory */ }
}

function load() {
  let raw;
  try { raw = localStorage.getItem(STORAGE_KEY); } catch (_) { return; }
  if (!raw) return;
  try {
    const data = JSON.parse(raw);
    if (Array.isArray(data.guests)) state.guests = data.guests;
    if (Array.isArray(data.winners)) state.winners = data.winners;
    if (typeof data.prize === 'string') state.prize = data.prize;
    if (Number.isFinite(data.winnerCount)) state.winnerCount = data.winnerCount;
    if (typeof data.dedupe === 'boolean') state.dedupe = data.dedupe;
    if (typeof data.excludeWinners === 'boolean') state.excludeWinners = data.excludeWinners;
    if (typeof data.sound === 'boolean') state.sound = data.sound;
  } catch (_) { /* corrupt payload — start fresh */ }
}

/* ── Rendering ───────────────────────────────────────────────────────── */

function render() {
  renderEntrants();
  renderWinners();
  const pool = eligibleGuests().length;
  el.poolPill.textContent = `${pool} eligible`;
  el.winnerPill.textContent = `${state.winners.length} drawn`;
  el.drawBtn.disabled = pool === 0 || drawing;
  el.stagePrize.textContent = state.prize
    ? state.prize
    : (pool ? 'Prize unnamed' : 'Set a prize to begin');
  if (!drawing && !lastDraw) {
    el.reelMeta.textContent = pool
      ? `${pool} entrant${pool === 1 ? '' : 's'} in the hat`
      : 'Add entrants to start the draw';
  }
  el.redrawBtn.hidden = !lastDraw;
}

function renderEntrants() {
  const q = el.search.value.trim().toLowerCase();
  const won = winnerKeys();
  const frag = document.createDocumentFragment();
  const shown = state.guests.filter(
    (g) => !q || g.name.toLowerCase().includes(q) || g.email.toLowerCase().includes(q)
  );
  for (const g of shown) {
    const hasWon = won.has(keyOf(g));
    const li = document.createElement('li');
    if (hasWon) li.className = 'won';

    const name = document.createElement('span');
    name.className = 'name';
    name.textContent = g.name;
    if (g.email) {
      const em = document.createElement('span');
      em.className = 'email';
      em.textContent = ` · ${g.email}`;
      name.appendChild(em);
    }
    li.appendChild(name);

    if (hasWon) {
      const tag = document.createElement('span');
      tag.className = 'tag';
      tag.textContent = 'won';
      li.appendChild(tag);
    }

    const x = document.createElement('button');
    x.className = 'icon-x';
    x.type = 'button';
    x.textContent = '✕';
    x.title = `Remove ${g.name}`;
    x.setAttribute('aria-label', `Remove ${g.name}`);
    x.addEventListener('click', () => {
      state.guests = state.guests.filter((o) => o.id !== g.id);
      save(); render();
    });
    li.appendChild(x);
    frag.appendChild(li);
  }
  el.entrantList.replaceChildren(frag);
}

function renderWinners() {
  const frag = document.createDocumentFragment();
  for (const w of state.winners.slice().reverse()) {
    const li = document.createElement('li');
    const name = document.createElement('div');
    name.className = 'wl-name';
    name.textContent = w.name;
    li.appendChild(name);
    if (w.prize) {
      const p = document.createElement('div');
      p.className = 'wl-prize';
      p.textContent = w.prize;
      li.appendChild(p);
    }
    const meta = document.createElement('div');
    meta.className = 'wl-meta';
    meta.textContent = [w.email, new Date(w.ts).toLocaleTimeString()].filter(Boolean).join(' · ');
    li.appendChild(meta);
    frag.appendChild(li);
  }
  el.winnerLog.replaceChildren(frag);
}

function renderWinnerCards(winners, prize) {
  const frag = document.createDocumentFragment();
  for (const w of winners) {
    const li = document.createElement('li');
    const n = document.createElement('span');
    n.className = 'wc-name';
    n.textContent = w.name;
    li.appendChild(n);
    const m = document.createElement('span');
    m.className = 'wc-meta';
    m.textContent = w.email || prize || 'winner';
    li.appendChild(m);
    frag.appendChild(li);
  }
  el.winnerCards.replaceChildren(frag);
}

/* ── The draw ────────────────────────────────────────────────────────── */

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const prefersReducedMotion = () =>
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

async function runDraw() {
  if (drawing) return;
  const pool = eligibleGuests();
  if (!pool.length) { toast('No eligible entrants left'); return; }

  const count = Math.max(1, Math.min(Number(el.winnerCount.value) || 1, pool.length));
  const picked = sampleWithoutReplacement(pool, count);
  const prize = state.prize;

  drawing = true;
  el.drawBtn.disabled = true;
  el.redrawBtn.hidden = true;
  el.winnerCards.replaceChildren();
  el.reel.classList.remove('revealed');
  document.body.classList.add('drawing');
  el.reelMeta.textContent = `Drawing ${count} of ${pool.length}…`;
  setReelScale(1);

  await rollNames(pool, picked[0]);

  document.body.classList.remove('drawing');
  el.reel.classList.add('revealed');
  setReelScale(picked.length);
  el.reelName.textContent = picked.map((w) => w.name).join('  ·  ');
  el.reelMeta.textContent = prize
    ? `${count === 1 ? 'Winner' : 'Winners'} of ${prize}`
    : (count === 1 ? 'Winner' : 'Winners');
  renderWinnerCards(picked, prize);
  chime();
  if (!prefersReducedMotion()) burstConfetti();

  const ts = Date.now();
  for (const w of picked) {
    state.winners.push({ id: w.id, name: w.name, email: w.email, prize, ts });
  }
  lastDraw = { winners: picked, prize };
  drawing = false;
  save(); render();
}

/** Fit several names in the reel by scaling the display type down. */
function setReelScale(count) {
  const scale = count <= 1 ? 1 : count === 2 ? 0.62 : count <= 4 ? 0.46 : 0.32;
  el.reel.style.setProperty('--reel-scale', String(scale));
}

/** Slot-machine name roll: fast, then decelerating into the reveal. */
async function rollNames(pool, finalGuest) {
  if (prefersReducedMotion() || pool.length === 1) {
    el.reelName.textContent = finalGuest.name;
    await sleep(320);
    return;
  }
  const totalMs = 2600;
  const start = performance.now();
  let delay = 45;
  while (performance.now() - start < totalMs) {
    const g = pool[randomInt(pool.length)];
    el.reelName.textContent = g.name;
    tick();
    await sleep(delay);
    const progress = (performance.now() - start) / totalMs;
    delay = 45 + 235 * Math.pow(progress, 3);   // ease-out into the stop
  }
  el.reelName.textContent = finalGuest.name;
}

function undoLastDraw() {
  if (!lastDraw) return;
  const ids = new Set(lastDraw.winners.map((w) => w.id));
  // Drop only the most recent entry per winner id.
  for (const id of ids) {
    for (let i = state.winners.length - 1; i >= 0; i--) {
      if (state.winners[i].id === id) { state.winners.splice(i, 1); break; }
    }
  }
  lastDraw = null;
  el.winnerCards.replaceChildren();
  el.reel.classList.remove('revealed');
  el.reelName.textContent = 'Ready';
  setReelScale(1);
  save(); render();
  runDraw();
}

/* ── Sound (synthesised, no assets) ──────────────────────────────────── */

let audioCtx = null;
function ctx() {
  if (!state.sound) return null;
  if (!audioCtx) {
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!AC) return null;
    audioCtx = new AC();
  }
  if (audioCtx.state === 'suspended') audioCtx.resume();
  return audioCtx;
}

function blip(freq, duration, type = 'sine', gain = 0.06) {
  const ac = ctx();
  if (!ac) return;
  const osc = ac.createOscillator();
  const g = ac.createGain();
  osc.type = type;
  osc.frequency.value = freq;
  g.gain.setValueAtTime(gain, ac.currentTime);
  g.gain.exponentialRampToValueAtTime(0.0001, ac.currentTime + duration);
  osc.connect(g).connect(ac.destination);
  osc.start();
  osc.stop(ac.currentTime + duration);
}

const tick = () => blip(880 + randomInt(240), 0.035, 'square', 0.022);

function chime() {
  [523.25, 659.25, 783.99, 1046.5].forEach((f, i) => {
    setTimeout(() => blip(f, 0.55, 'triangle', 0.075), i * 110);
  });
}

/* ── Confetti ────────────────────────────────────────────────────────── */

const cvs = el.confetti;
const cx = cvs.getContext('2d');
let particles = [];
let rafId = null;

function sizeCanvas() {
  const dpr = window.devicePixelRatio || 1;
  cvs.width = window.innerWidth * dpr;
  cvs.height = window.innerHeight * dpr;
  cx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
sizeCanvas();
window.addEventListener('resize', sizeCanvas);

function burstConfetti() {
  const colors = ['#1E5BFF', '#4A82FF', '#8FB8FF', '#FFFFFF', '#05070F'];
  const w = window.innerWidth;
  for (let i = 0; i < 150; i++) {
    particles.push({
      x: w / 2 + (Math.random() - 0.5) * w * 0.5,
      y: window.innerHeight * 0.32 + (Math.random() - 0.5) * 60,
      vx: (Math.random() - 0.5) * 9,
      vy: Math.random() * -11 - 3,
      size: 4 + Math.random() * 6,
      rot: Math.random() * Math.PI,
      vr: (Math.random() - 0.5) * 0.32,
      color: colors[Math.floor(Math.random() * colors.length)],
      life: 1,
    });
  }
  if (!rafId) rafId = requestAnimationFrame(stepConfetti);
}

function stepConfetti() {
  cx.clearRect(0, 0, window.innerWidth, window.innerHeight);
  particles = particles.filter((p) => p.life > 0 && p.y < window.innerHeight + 60);
  for (const p of particles) {
    p.vy += 0.32;          // gravity
    p.vx *= 0.995;         // drag
    p.x += p.vx;
    p.y += p.vy;
    p.rot += p.vr;
    p.life -= 0.0055;
    cx.save();
    cx.translate(p.x, p.y);
    cx.rotate(p.rot);
    cx.globalAlpha = Math.max(0, Math.min(1, p.life));
    cx.fillStyle = p.color;
    cx.fillRect(-p.size / 2, -p.size / 4, p.size, p.size / 2);
    cx.restore();
  }
  if (particles.length) {
    rafId = requestAnimationFrame(stepConfetti);
  } else {
    cx.clearRect(0, 0, window.innerWidth, window.innerHeight);
    rafId = null;
  }
}

/* ── Background shuttlecocks ─────────────────────────────────────────── */

function seedDrifters() {
  if (prefersReducedMotion()) return;
  const svg = `<svg viewBox="0 0 40 54" width="34" height="46" fill="none">
      <path d="M20 32 L6 6 Q20 0 34 6 Z" fill="currentColor" opacity=".5"/>
      <path d="M13 32 Q20 28 27 32 L25 44 Q20 48 15 44 Z" fill="currentColor"/>
      <ellipse cx="20" cy="45" rx="6" ry="3.4" fill="currentColor"/>
    </svg>`;
  for (let i = 0; i < 9; i++) {
    const d = document.createElement('div');
    d.className = 'drifter';
    d.style.left = `${Math.random() * 100}%`;
    d.style.animationDuration = `${16 + Math.random() * 22}s`;
    d.style.animationDelay = `${-Math.random() * 30}s`;
    d.innerHTML = svg;
    // The drift keyframes own the element transform, so scale the SVG itself.
    d.firstElementChild.style.transform = `scale(${(0.6 + Math.random() * 0.9).toFixed(2)})`;
    el.shuttleField.appendChild(d);
  }
}

/* ── Export ──────────────────────────────────────────────────────────── */

function csvCell(v) {
  const s = String(v ?? '');
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function exportWinners() {
  if (!state.winners.length) { toast('No winners to export yet'); return; }
  const rows = [['#', 'Name', 'Email', 'Prize', 'Drawn at']];
  state.winners.forEach((w, i) => {
    rows.push([i + 1, w.name, w.email, w.prize, new Date(w.ts).toISOString()]);
  });
  const csv = rows.map((r) => r.map(csvCell).join(',')).join('\n');
  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `mo2026fest-winners-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* ── Misc UI ─────────────────────────────────────────────────────────── */

let toastTimer = null;
function toast(msg) {
  el.toast.textContent = msg;
  el.toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.toast.classList.remove('show'), 2600);
}

/* ── Wiring ──────────────────────────────────────────────────────────── */

el.addBtn.addEventListener('click', () => {
  const rows = parseGuests(el.guestInput.value);
  if (!rows.length) { toast('Nothing to add — paste some names first'); return; }
  addGuests(rows);
  el.guestInput.value = '';
});

el.replaceBtn.addEventListener('click', () => {
  const rows = parseGuests(el.guestInput.value);
  if (!rows.length) { toast('Nothing to import — paste some names first'); return; }
  if (state.guests.length && !confirm(`Replace all ${state.guests.length} entrants with ${rows.length} from the box?`)) return;
  addGuests(rows, { replace: true });
  el.guestInput.value = '';
});

el.fileInput.addEventListener('change', async (e) => {
  const file = e.target.files && e.target.files[0];
  if (!file) return;
  const text = await file.text();
  const rows = parseGuests(text);
  if (!rows.length) { toast('Could not read any entrants from that file'); return; }
  addGuests(rows);
  el.fileInput.value = '';
});

// Drag a CSV anywhere onto the page.
document.addEventListener('dragover', (e) => e.preventDefault());
document.addEventListener('drop', async (e) => {
  const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
  if (!file) return;
  e.preventDefault();
  const rows = parseGuests(await file.text());
  if (!rows.length) { toast('Could not read any entrants from that file'); return; }
  addGuests(rows);
});

el.clearBtn.addEventListener('click', () => {
  if (!state.guests.length) return;
  if (!confirm(`Remove all ${state.guests.length} entrants?`)) return;
  state.guests = [];
  save(); render();
});

el.search.addEventListener('input', renderEntrants);

el.dedupe.addEventListener('change', () => { state.dedupe = el.dedupe.checked; save(); });
el.excludeWinners.addEventListener('change', () => {
  state.excludeWinners = el.excludeWinners.checked;
  save(); render();
});

el.prizeInput.addEventListener('input', () => { state.prize = el.prizeInput.value.trim(); save(); render(); });
el.winnerCount.addEventListener('change', () => {
  const n = Math.max(1, Math.min(50, Number(el.winnerCount.value) || 1));
  el.winnerCount.value = n;
  state.winnerCount = n;
  save();
});

el.drawBtn.addEventListener('click', runDraw);
el.redrawBtn.addEventListener('click', () => {
  if (!lastDraw) return;
  if (!confirm('Void the last draw and pick again?')) return;
  undoLastDraw();
});

el.exportBtn.addEventListener('click', exportWinners);
el.resetWinnersBtn.addEventListener('click', () => {
  if (!state.winners.length) return;
  if (!confirm(`Clear the log of ${state.winners.length} winner(s)?`)) return;
  state.winners = [];
  lastDraw = null;
  el.winnerCards.replaceChildren();
  el.reel.classList.remove('revealed');
  el.reelName.textContent = 'Ready';
  setReelScale(1);
  save(); render();
});

el.soundBtn.addEventListener('click', () => {
  state.sound = !state.sound;
  el.soundBtn.setAttribute('aria-pressed', String(state.sound));
  el.soundIcon.textContent = state.sound ? '🔊' : '🔇';
  save();
});

el.stageBtn.addEventListener('click', () => document.body.classList.toggle('stage-mode'));

document.addEventListener('keydown', (e) => {
  const tag = document.activeElement ? document.activeElement.tagName : '';
  const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(tag);
  if (typing) return;
  if (e.code === 'Space' || e.key === 'Enter') {
    if (el.drawBtn.disabled) return;
    e.preventDefault();
    runDraw();
  } else if (e.key === 'f' || e.key === 'F') {
    document.body.classList.toggle('stage-mode');
  }
});

/* ── Boot ────────────────────────────────────────────────────────────── */

load();
el.prizeInput.value = state.prize;
el.winnerCount.value = state.winnerCount;
el.dedupe.checked = state.dedupe;
el.excludeWinners.checked = state.excludeWinners;
el.soundBtn.setAttribute('aria-pressed', String(state.sound));
el.soundIcon.textContent = state.sound ? '🔊' : '🔇';
seedDrifters();
render();
