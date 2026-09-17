/* badminton-reels editor.
 *
 * Vanilla JS on purpose: no build step, no CDN, works offline. The server
 * owns every decision that affects the render - in particular the crop path
 * is fetched from the real planner rather than re-implemented here, so the
 * box you see is the box you get.
 */
"use strict";

/* ────────────────────────── helpers ────────────────────────── */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const S = {
  token: "",
  state: null,
  project: null,
  clipId: null,
  plan: null,
  planKey: "",
  pendingClips: new Map(),
  pendingRender: {},
  saveTimer: null,
  saving: false,
  jobTimer: null,
  pickedMedia: null,
  sort: "time",
  loop: false,
  skipGaps: false,
  view: { t0: 0, t1: 60 },
};

function fmtTime(seconds, decimals = 1) {
  const value = Math.max(0, Number(seconds) || 0);
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  const secs = value % 60;
  const body = `${minutes}:${secs.toFixed(decimals).padStart(decimals ? 3 + decimals : 2, "0")}`;
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${secs.toFixed(0).padStart(2, "0")}` : body;
}

function fmtClock(seconds) {
  const value = Math.max(0, Math.round(Number(seconds) || 0));
  return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, "0")}`;
}

function fmtSize(bytes) {
  const units = ["B", "kB", "MB", "GB"];
  let value = Number(bytes) || 0;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) { value /= 1024; index += 1; }
  return `${value < 10 && index ? value.toFixed(1) : Math.round(value)} ${units[index]}`;
}

function fmtAgo(epoch) {
  const seconds = Date.now() / 1000 - (Number(epoch) || 0);
  if (seconds < 90) return "just now";
  if (seconds < 5400) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} d ago`;
}

function el(tag, attrs = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key === "html") node.innerHTML = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of [].concat(children)) {
    if (child) node.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
}

let toastTimer = null;
function toast(message, bad = false) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.toggle("bad", !!bad);
  node.classList.add("on");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("on"), bad ? 5200 : 2400);
}

function mediaUrl(path, extra = {}) {
  const url = new URL(path, location.origin);
  url.searchParams.set("t", S.token);
  for (const [key, value] of Object.entries(extra)) url.searchParams.set(key, value);
  return url.toString();
}

async function api(path, options = {}) {
  const { method = "GET", body, query } = options;
  const url = new URL(path, location.origin);
  for (const [key, value] of Object.entries(query || {})) url.searchParams.set(key, value);
  const headers = { "X-BDR-Token": S.token };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const response = await fetch(url, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let data = {};
  try { data = await response.json(); } catch (err) { /* empty body */ }
  if (!response.ok) throw new Error(data.error || `${response.status} ${response.statusText}`);
  return data;
}

/* ────────────────────────── field builder ────────────────────────── */

function readPath(object, path) {
  return path.split(".").reduce((node, key) => (node == null ? undefined : node[key]), object);
}

function buildFields(container, spec, values, onChange, prefix) {
  // The prefix keeps ids unique: the same field can appear both as a project
  // default and as a per-clip override, and duplicate ids would break the
  // <label for> links as well as any selector that expects one match.
  const scope = prefix || container.id || "f";
  container.innerHTML = "";
  for (const group of spec) {
    const row = group.length > 1 ? el("div", { class: "field-row" }) : null;
    for (const field of group) {
      const current = readPath(values, field.key);
      const value = current === undefined || current === null ? field.def : current;
      const id = `${scope}-${field.key.replace(/\./g, "-")}`;
      let input;
      if (field.type === "select") {
        input = el("select", { id });
        for (const option of field.options) {
          input.appendChild(el("option", {
            value: option.value, text: option.label,
            selected: String(option.value) === String(value),
          }));
        }
      } else if (field.type === "check") {
        input = el("input", { type: "checkbox", id, checked: !!value });
      } else if (field.type === "text") {
        input = el("input", { type: "text", id, value: value == null ? "" : value, placeholder: field.placeholder || "" });
      } else {
        input = el("input", {
          type: "number", id, value, step: field.step || 0.1,
          min: field.min, max: field.max,
        });
      }
      let wrap;
      if (field.type === "check") {
        // A checkbox reads as "[x] label", not as a label with a box under it.
        wrap = el("label", { class: "check", for: id }, [
          input,
          el("span", {}, [
            el("span", { text: field.label }),
            field.hint ? el("span", { class: "muted small", text: ` ${field.hint}` }) : null,
          ]),
        ]);
      } else {
        wrap = el("div", { class: "field" }, [
          el("label", { for: id }, [
            el("span", { text: field.label }),
            field.unit ? el("span", { class: "muted", text: field.unit }) : null,
          ]),
          input,
          field.hint ? el("div", { class: "hint", text: field.hint }) : null,
        ]);
      }
      input.addEventListener("change", () => {
        let next;
        if (field.type === "check") next = input.checked;
        else if (field.type === "select" || field.type === "text") next = input.value;
        else {
          next = parseFloat(input.value);
          if (Number.isNaN(next)) next = field.def;
          if (field.min !== undefined) next = Math.max(field.min, next);
          if (field.max !== undefined) next = Math.min(field.max, next);
          input.value = next;
        }
        if (field.type === "select" && field.numeric) next = parseFloat(next);
        onChange(field.key, next, field);
      });
      (row || container).appendChild(wrap);
    }
    if (row) container.appendChild(row);
  }
}

const DETECT_SPEC = [
  [{ key: "sensitivity", label: "Impact sensitivity", def: 1.0, min: 0.3, max: 3, step: 0.1,
     hint: "Above 1 finds quieter hits; below 1 ignores crowd noise." }],
  [{ key: "detect.min_duration", label: "Shortest rally", unit: "s", def: 2.0, min: 0.5, max: 20, step: 0.5 },
   { key: "detect.min_shots", label: "Fewest impacts", def: 2, min: 0, max: 20, step: 1 }],
  [{ key: "detect.pre_roll", label: "Lead-in", unit: "s", def: 0.8, min: 0, max: 5, step: 0.1,
     hint: "Time kept before the serve." },
   { key: "detect.post_roll", label: "Tail", unit: "s", def: 1.2, min: 0, max: 6, step: 0.1,
     hint: "Time kept after the winning shot." }],
  [{ key: "detect.hang", label: "Quiet before a rally ends", unit: "s", def: 1.2, min: 0.2, max: 6, step: 0.1 },
   { key: "detect.merge_gap", label: "Join gaps under", unit: "s", def: 1.5, min: 0, max: 8, step: 0.1 }],
  [{ key: "detect.enter_level", label: "Start level", def: 0.45, min: 0.05, max: 0.95, step: 0.05,
     hint: "Lower finds more rallies." },
   { key: "detect.exit_level", label: "End level", def: 0.25, min: 0.02, max: 0.9, step: 0.05 }],
  [{ key: "detect.max_duration", label: "Split longer than", unit: "s", def: 45, min: 5, max: 600, step: 5 }],
];

/* ────────────────────────── boot ────────────────────────── */

function readToken() {
  const url = new URL(location.href);
  const fromUrl = url.searchParams.get("t");
  if (fromUrl) {
    try { sessionStorage.setItem("bdr-token", fromUrl); } catch (err) { /* private mode */ }
    return fromUrl;
  }
  try { return sessionStorage.getItem("bdr-token") || ""; } catch (err) { return ""; }
}

async function boot() {
  S.token = readToken();
  wireStart();
  wireEditor();
  wireKeys();
  if (!S.token) {
    showFatal("This page needs the access token. Open the URL that `bdr serve` printed in the terminal.");
    return;
  }
  try {
    await refreshState();
  } catch (err) {
    showFatal(`Could not reach the editor: ${err.message}`);
    return;
  }
  const wanted = new URL(location.href).searchParams.get("p");
  const first = S.state.projects[0];
  if (wanted) await openProject(wanted);
  else if (first && first.clips) await openProject(first.id);
  else showStart();
  pollJobs();
}

function showFatal(message) {
  $("#screen-editor").classList.add("hidden");
  $("#screen-start").classList.remove("hidden");
  $(".start-wrap").prepend(el("div", { class: "panel", style: "border-color:#5a2a2a;margin-bottom:16px" }, [
    el("strong", { text: "Editor unavailable" }), el("div", { class: "muted small", text: message }),
  ]));
}

async function refreshState() {
  S.state = await api("/api/state");
  renderMediaList();
  renderProjectList();
  renderProjectPicker();
  if (!$("#analyze-settings").childElementCount) {
    buildFields($("#analyze-settings"), DETECT_SPEC,
      { sensitivity: 1, detect: S.state.defaults.detect },
      (key, value) => { setPath(S.analyzeSettings = S.analyzeSettings || {}, key, value); },
      "analyse");
  }
  if (!S.state.ffmpeg) {
    toast("ffmpeg was not found on this machine, so nothing can be analysed or rendered.", true);
  }
}

function setPath(object, path, value) {
  const parts = path.split(".");
  let node = object;
  while (parts.length > 1) {
    const key = parts.shift();
    node[key] = node[key] || {};
    node = node[key];
  }
  node[parts[0]] = value;
}

function showStart() {
  $("#screen-start").classList.remove("hidden");
  $("#screen-editor").classList.add("hidden");
}

function showEditor() {
  $("#screen-start").classList.add("hidden");
  $("#screen-editor").classList.remove("hidden");
}

/* ────────────────────────── start screen ────────────────────────── */

function wireStart() {
  const input = $("#file-input");
  $("#btn-browse").addEventListener("click", () => input.click());
  input.addEventListener("change", () => { if (input.files[0]) upload(input.files[0]); });

  const zone = $("#dropzone");
  zone.addEventListener("click", (event) => { if (event.target === zone || event.target.closest(".dz-inner") === event.target) input.click(); });
  for (const name of ["dragenter", "dragover"]) {
    zone.addEventListener(name, (event) => { event.preventDefault(); zone.classList.add("over"); });
  }
  for (const name of ["dragleave", "drop"]) {
    zone.addEventListener(name, (event) => { event.preventDefault(); zone.classList.remove("over"); });
  }
  zone.addEventListener("drop", (event) => {
    const file = event.dataTransfer && event.dataTransfer.files[0];
    if (file) upload(file);
  });

  $("#btn-analyze").addEventListener("click", startAnalysis);
  $("#btn-new").addEventListener("click", showStart);
  $("#btn-help").addEventListener("click", showHelp);
  $("#modal-close").addEventListener("click", closeModal);
  $("#modal").addEventListener("click", (event) => { if (event.target.id === "modal") closeModal(); });
  $("#project-picker").addEventListener("change", (event) => {
    if (event.target.value) openProject(event.target.value);
  });
}

function upload(file) {
  const bar = $("#upload-progress");
  const fill = $(".progress-fill", bar);
  bar.classList.remove("hidden");
  fill.style.width = "0%";
  const request = new XMLHttpRequest();
  request.open("POST", mediaUrl("/api/upload", { name: file.name }));
  request.setRequestHeader("X-BDR-Token", S.token);
  request.upload.addEventListener("progress", (event) => {
    if (event.lengthComputable) fill.style.width = `${(event.loaded / event.total) * 100}%`;
  });
  request.addEventListener("load", async () => {
    bar.classList.add("hidden");
    let payload = {};
    try { payload = JSON.parse(request.responseText); } catch (err) { /* ignore */ }
    if (request.status >= 400) { toast(payload.error || "Upload failed", true); return; }
    toast(`${payload.name} added`);
    await refreshState();
    pickMedia(payload.path);
  });
  request.addEventListener("error", () => {
    bar.classList.add("hidden");
    toast("Upload failed", true);
  });
  request.send(file);
}

function pickMedia(path) {
  S.pickedMedia = path;
  const button = $("#btn-analyze");
  button.disabled = !path || !S.state.ffmpeg;
  button.textContent = path ? `Analyse ${path.split("/").pop()}` : "Select a video to analyse";
  for (const row of $$("#media-list .list-row")) {
    row.setAttribute("aria-selected", String(row.dataset.path === path));
  }
}

function renderMediaList() {
  const list = $("#media-list");
  list.innerHTML = "";
  const media = S.state.media || [];
  if (!media.length) {
    list.appendChild(el("div", { class: "list-empty", text: "No videos yet — drop one in above." }));
  }
  for (const item of media.slice(0, 80)) {
    list.appendChild(el("div", {
      class: "list-row", "data-path": item.path, role: "option",
      "aria-selected": String(item.path === S.pickedMedia),
      onclick: () => pickMedia(item.path),
    }, [
      el("span", { class: "name", text: item.name, title: item.path }),
      el("span", { class: "meta", text: `${fmtSize(item.size)} · ${fmtAgo(item.modified)}` }),
    ]));
  }
  const roots = (S.state.workspace.media_roots || []).join(", ");
  $("#media-hint").textContent = `Readable folders: ${roots}`;
}

function renderProjectList() {
  const list = $("#project-list");
  list.innerHTML = "";
  const projects = S.state.projects || [];
  if (!projects.length) {
    list.appendChild(el("div", { class: "list-empty", text: "Nothing analysed yet." }));
    return;
  }
  for (const project of projects) {
    list.appendChild(el("div", { class: "list-row", onclick: () => openProject(project.id) }, [
      el("span", { class: "name", text: project.name, title: project.source }),
      el("span", { class: "meta", text: `${project.kept}/${project.clips} rallies · ${fmtClock(project.kept_duration)} · ${fmtAgo(project.updated)}` }),
      project.source_missing ? el("span", { class: "chip", text: "source moved" }) : null,
      el("button", {
        class: "ghost small", text: "Delete",
        onclick: async (event) => {
          event.stopPropagation();
          if (!confirm(`Delete the project for ${project.name}? Rendered files go too. The source video is left alone.`)) return;
          await api(`/api/projects/${project.id}`, { method: "DELETE" });
          if (S.project && S.project.id === project.id) { S.project = null; showStart(); }
          await refreshState();
          toast("Project deleted");
        },
      }),
    ]));
  }
}

function renderProjectPicker() {
  const picker = $("#project-picker");
  picker.innerHTML = "";
  const projects = S.state.projects || [];
  if (!projects.length) {
    picker.appendChild(el("option", { value: "", text: "No projects yet" }));
    picker.disabled = true;
    return;
  }
  picker.disabled = false;
  for (const project of projects) {
    picker.appendChild(el("option", {
      value: project.id,
      text: project.name,
      selected: S.project && S.project.id === project.id,
    }));
  }
}

async function startAnalysis() {
  if (!S.pickedMedia) return;
  const body = {
    path: S.pickedMedia,
    proxy: $("#opt-proxy").checked,
    settings: S.analyzeSettings || {},
  };
  try {
    const job = await api("/api/analyze", { method: "POST", body });
    S.watchJob = { id: job.id, target: "#analyze-job", onDone: async (done) => {
      await refreshState();
      if (done.result && done.result.project_id) {
        await openProject(done.result.project_id);
        toast(`${done.result.clips} rallies found`);
      }
    } };
    pollJobs(true);
  } catch (err) {
    toast(err.message, true);
  }
}

/* ────────────────────────── jobs ────────────────────────── */

function renderJob(target, job) {
  const node = $(target);
  if (!node) return;
  node.classList.remove("hidden");
  node.dataset.status = job.status;
  const percent = Math.round((job.progress || 0) * 100);
  node.innerHTML = "";
  node.appendChild(el("div", { class: "job-title" }, [
    job.status === "done" ? el("span", { text: "✓" })
      : job.status === "error" ? el("span", { text: "✕" })
      : el("div", { class: "spinner" }),
    el("span", { text: job.title }),
    el("span", { class: "bar-spacer" }),
    el("span", { class: "muted small", text: job.status === "done" ? `${job.elapsed}s` : `${percent}%` }),
  ]));
  if (job.status !== "done") {
    node.appendChild(el("div", { class: "progress" }, [
      el("div", { class: "progress-fill", style: `width:${percent}%` }),
    ]));
  }
  node.appendChild(el("div", { class: "job-log", text: job.error ? job.error : (job.log || []).slice(-4).join("\n") }));
}

async function pollJobs(immediate = false) {
  clearTimeout(S.jobTimer);
  const tick = async () => {
    let jobs = [];
    try {
      jobs = (await api("/api/jobs")).jobs || [];
    } catch (err) { /* server restarting */ }
    const active = jobs.filter((job) => job.status === "queued" || job.status === "running");
    const watched = S.watchJob && jobs.find((job) => job.id === S.watchJob.id);
    if (watched) {
      renderJob(S.watchJob.target, watched);
      if (watched.status === "done" || watched.status === "error") {
        const finished = S.watchJob;
        S.watchJob = null;
        if (watched.status === "error") toast(watched.error || "Job failed", true);
        else if (finished.onDone) await finished.onDone(watched);
      }
    }
    const pill = $("#job-pill");
    if (active.length) {
      pill.classList.remove("hidden");
      pill.textContent = `${active[0].title} ${Math.round(active[0].progress * 100)}%`;
    } else {
      pill.classList.add("hidden");
    }
    S.jobTimer = setTimeout(tick, active.length ? 600 : 4000);
  };
  if (immediate) tick(); else S.jobTimer = setTimeout(tick, 400);
}

/* ────────────────────────── modal ────────────────────────── */

function openModal(title, body) {
  $("#modal-title").textContent = title;
  const host = $("#modal-body");
  host.innerHTML = "";
  host.appendChild(body);
  $("#modal").classList.remove("hidden");
}

function closeModal() {
  $("#modal").classList.add("hidden");
  $("#modal-body").innerHTML = "";
}

function showHelp() {
  const keys = [
    ["space", "play / pause"],
    ["j / k", "next / previous rally"],
    ["l", "loop the selected rally"],
    [", / .", "step one frame"],
    ["← / →", "jump 1 s (hold shift for 5 s)"],
    ["i / o", "set the in / out point at the playhead"],
    ["[ / ]", "nudge the in point by 0.1 s"],
    ["- / =", "nudge the out point by 0.1 s"],
    ["x", "drop this rally"],
    ["s", "keep this rally"],
    ["n", "add a rally around the playhead"],
    ["b", "split this rally at the playhead"],
    ["delete", "remove this rally from the timeline"],
    ["+ / − / 0", "zoom the timeline in / out / fit"],
  ];
  const grid = el("div", { class: "keys" });
  for (const [key, description] of keys) {
    grid.appendChild(el("kbd", { text: key }));
    grid.appendChild(el("span", { text: description }));
  }
  openModal("Keyboard", grid);
}

/* ────────────────────────── project loading ────────────────────────── */

async function openProject(projectId) {
  await flushSave();
  let payload;
  try {
    payload = await api(`/api/projects/${projectId}`);
  } catch (err) {
    toast(err.message, true);
    showStart();
    return;
  }
  S.project = payload;
  S.clipId = payload.clips.length ? payload.clips[0].id : null;
  S.plan = null;
  S.planKey = "";
  const url = new URL(location.href);
  url.searchParams.set("p", projectId);
  history.replaceState(null, "", url);

  const video = $("#video");
  video.src = mediaUrl(`/api/projects/${projectId}/preview`);
  video.load();

  showEditor();
  renderProjectPicker();
  fitView();
  buildInspectorPanels();
  renderAll();
  if (payload.source_missing) {
    toast("The source video has moved, so rendering will fail until it is back.", true);
  }
  if (!payload.has_proxy) {
    $("#reel-note").textContent = "No proxy: scrubbing plays the original file.";
  }
}

function clips() { return (S.project && S.project.clips) || []; }
function selected() { return clips().find((clip) => clip.id === S.clipId) || null; }
function duration() { return (S.project && S.project.source && S.project.source.duration) || 0; }
function keptClips() { return clips().filter((clip) => clip.keep !== false); }

function sortedClips() {
  const list = clips().slice();
  if (S.sort === "score") list.sort((a, b) => (b.score || 0) - (a.score || 0));
  else if (S.sort === "shots") list.sort((a, b) => (b.shots || 0) - (a.shots || 0));
  else list.sort((a, b) => a.start - b.start);
  return list;
}

function selectClip(clipId, { seek = false } = {}) {
  S.clipId = clipId;
  const clip = selected();
  if (clip && seek) {
    $("#video").currentTime = clip.start + 0.02;
    centreView(clip);
  }
  renderAll();
}

function renderAll() {
  renderStrip();
  renderRallyInspector();
  renderOutputs();
  renderFacts();
  drawTimelines();
  refreshPlan();
  updateSummary();
}

function updateSummary() {
  const kept = keptClips();
  const total = kept.reduce((sum, clip) => sum + (clip.end - clip.start), 0);
  const shots = kept.reduce((sum, clip) => sum + (clip.shots || 0), 0);
  $("#strip-summary").textContent =
    `${clips().length} rallies · ${kept.length} kept · ${fmtClock(total)} of footage · ${shots} impacts`;
  $("#duration-readout").textContent = `/ ${fmtTime(duration(), 0)}`;
}

/* ────────────────────────── saving ────────────────────────── */

function markSaveState(state, detail) {
  const pill = $("#save-state");
  pill.dataset.state = state;
  pill.textContent = detail || { idle: "saved", dirty: "unsaved", saving: "saving…", error: "save failed" }[state];
}

function queueClipPatch(clipId, patch) {
  const existing = S.pendingClips.get(clipId) || {};
  S.pendingClips.set(clipId, Object.assign(existing, patch));
  const clip = clips().find((item) => item.id === clipId);
  if (clip) Object.assign(clip, patch);
  S.planKey = "";
  scheduleSave();
}

function queueRenderPatch(patch) {
  Object.assign(S.pendingRender, patch);
  if (S.project) Object.assign(S.project.render, patch);
  S.planKey = "";
  scheduleSave();
}

function scheduleSave() {
  markSaveState("dirty");
  clearTimeout(S.saveTimer);
  S.saveTimer = setTimeout(flushSave, 300);
}

async function flushSave() {
  clearTimeout(S.saveTimer);
  if (!S.project) return;
  if (!S.pendingClips.size && !Object.keys(S.pendingRender).length) {
    markSaveState("idle");
    return;
  }
  const body = {};
  if (S.pendingClips.size) {
    body.clips = Array.from(S.pendingClips.entries()).map(([id, patch]) => Object.assign({ id }, patch));
  }
  if (Object.keys(S.pendingRender).length) body.render = Object.assign({}, S.pendingRender);
  S.pendingClips.clear();
  S.pendingRender = {};
  markSaveState("saving");
  S.saving = true;
  try {
    const payload = await api(`/api/projects/${S.project.id}`, { method: "PUT", body });
    // Only adopt the server's copy if nothing was edited while it was in flight.
    if (!S.pendingClips.size && !Object.keys(S.pendingRender).length) {
      S.project = payload;
      markSaveState("idle");
      // The server now holds the edit, so the crop plan can be refetched.
      S.planKey = "";
      renderAll();
    }
  } catch (err) {
    markSaveState("error", err.message.slice(0, 40));
    toast(err.message, true);
  } finally {
    S.saving = false;
  }
}

/* ────────────────────────── player ────────────────────────── */

function wireEditor() {
  const video = $("#video");
  $("#btn-play").addEventListener("click", togglePlay);
  $("#btn-prev").addEventListener("click", () => stepClip(-1));
  $("#btn-next").addEventListener("click", () => stepClip(1));
  $("#btn-loop").addEventListener("click", () => {
    S.loop = !S.loop;
    $("#btn-loop").setAttribute("aria-pressed", String(S.loop));
    if (S.loop) toast("Looping the selected rally");
  });
  $("#opt-skip").addEventListener("change", (event) => { S.skipGaps = event.target.checked; });
  $("#btn-mark-in").addEventListener("click", () => setEdge("start"));
  $("#btn-mark-out").addEventListener("click", () => setEdge("end"));

  video.addEventListener("loadedmetadata", () => { drawTimelines(); drawReel(); });
  video.addEventListener("timeupdate", onTick);
  video.addEventListener("seeked", () => { drawTimelines(); drawReel(); });
  video.addEventListener("play", () => { $("#btn-play").textContent = "⏸"; pumpFrames(); });
  video.addEventListener("pause", () => { $("#btn-play").textContent = "▶"; });
  video.addEventListener("error", () => {
    const hasH264 = !!video.canPlayType('video/mp4; codecs="avc1.42E01E"');
    $("#reel-note").textContent = hasH264
      ? "This browser could not load the video. Check the file is still there."
      : "This browser has no H.264 decoder, so it cannot show the footage. " +
        "Chrome, Safari, Edge and Firefox can.";
    toast($("#reel-note").textContent, true);
  });

  for (const tab of $$(".tab")) {
    tab.addEventListener("click", () => {
      for (const other of $$(".tab")) other.classList.toggle("active", other === tab);
      for (const panel of $$(".tab-body")) {
        panel.classList.toggle("hidden", panel.dataset.panel !== tab.dataset.tab);
      }
    });
  }

  $("#btn-zoom-in").addEventListener("click", () => zoomView(0.6));
  $("#btn-zoom-out").addEventListener("click", () => zoomView(1 / 0.6));
  $("#btn-zoom-fit").addEventListener("click", fitView);
  $("#strip-sort").addEventListener("change", (event) => { S.sort = event.target.value; renderStrip(); });
  $("#btn-keep-all").addEventListener("click", () => {
    for (const clip of clips()) if (clip.keep === false) queueClipPatch(clip.id, { keep: true });
    renderAll();
  });
  $("#btn-keep-top").addEventListener("click", () => {
    const best = new Set(clips().slice().sort((a, b) => (b.score || 0) - (a.score || 0)).slice(0, 8).map((c) => c.id));
    for (const clip of clips()) queueClipPatch(clip.id, { keep: best.has(clip.id) });
    renderAll();
  });
  $("#btn-renumber").addEventListener("click", async () => {
    await flushSave();
    S.project = await api(`/api/projects/${S.project.id}`, { method: "PUT", body: { renumber: true } });
    renderAll();
    toast("Kept rallies renumbered");
  });
  $("#btn-redetect").addEventListener("click", reDetect);
  $("#btn-render-clips").addEventListener("click", () => startRender("clips"));
  $("#btn-render-reel").addEventListener("click", () => startRender("reel"));
  $("#btn-render-one").addEventListener("click", () => {
    if (!selected()) return;
    startRender("clips", [selected().id]);
  });
  wireTimelines();
  window.addEventListener("resize", drawTimelines);
  window.addEventListener("beforeunload", (event) => {
    if (S.pendingClips.size || Object.keys(S.pendingRender).length) {
      flushSave();
      event.preventDefault();
      event.returnValue = "";
    }
  });
}

function togglePlay() {
  const video = $("#video");
  if (video.paused) video.play().catch(() => toast("This browser will not play that file", true));
  else video.pause();
}

function stepClip(direction) {
  const ordered = clips().slice().sort((a, b) => a.start - b.start);
  if (!ordered.length) return;
  const index = ordered.findIndex((clip) => clip.id === S.clipId);
  const next = ordered[Math.min(ordered.length - 1, Math.max(0, (index < 0 ? 0 : index) + direction))];
  selectClip(next.id, { seek: true });
}

function onTick() {
  const video = $("#video");
  const clip = selected();
  if (S.loop && clip && (video.currentTime >= clip.end || video.currentTime < clip.start - 0.5)) {
    video.currentTime = clip.start;
  } else if (S.skipGaps && !video.paused) {
    const inside = keptClips().find((item) => video.currentTime >= item.start - 0.05 && video.currentTime <= item.end);
    if (!inside) {
      const next = keptClips().filter((item) => item.start > video.currentTime).sort((a, b) => a.start - b.start)[0];
      if (next) { video.currentTime = next.start; selectClip(next.id); }
      else video.pause();
    } else if (inside.id !== S.clipId) {
      selectClip(inside.id);
    }
  }
  $("#time-readout").textContent = fmtTime(video.currentTime);
  drawTimelines();
  if (video.paused) drawReel();
}

function pumpFrames() {
  const video = $("#video");
  const step = () => {
    if (video.paused || video.ended) return;
    drawReel();
    requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

/* ────────────────────────── crop overlay + reel preview ────────────────────────── */

function planKeyFor(clip) {
  const render = (S.project && S.project.render) || {};
  return [clip.id, clip.start, clip.end, clip.layout || render.layout, clip.zoom || render.zoom,
    clip.pan || render.pan, clip.speed || render.speed, render.width, render.height,
    render.pan_tau, render.pan_max_speed, render.center_bias, render.track_y].join("|");
}

async function refreshPlan() {
  const clip = selected();
  if (!clip) { S.plan = null; drawCropLayer(); drawReel(); return; }
  const key = planKeyFor(clip);
  if (key === S.planKey) { drawCropLayer(); drawReel(); return; }
  try {
    const plan = await api(`/api/projects/${S.project.id}/clips/${clip.id}/plan`);
    S.plan = plan;
    S.planKey = key;
    const canvas = $("#reel-canvas");
    const height = Math.round((270 * plan.canvas.height) / plan.canvas.width);
    canvas.width = 270;
    canvas.height = height;
    $("#reel-label").textContent = `${plan.canvas.width}×${plan.canvas.height} · ${plan.layout}`;
  } catch (err) {
    S.plan = null;
  }
  drawCropLayer();
  drawReel();
}

function paneAt(pane, relative) {
  const times = pane.times || [];
  if (!times.length) return { x: 0, y: 0 };
  let index = 0;
  while (index + 1 < times.length && times[index + 1] <= relative) index += 1;
  return { x: pane.xs[index], y: pane.ys[index] };
}

function relativeTime() {
  const clip = selected();
  if (!clip || !S.plan) return 0;
  const speed = S.plan.speed || 1;
  return Math.max(0, ($("#video").currentTime - clip.start) / speed);
}

function drawCropLayer() {
  const layer = $("#crop-layer");
  layer.innerHTML = "";
  const plan = S.plan;
  const video = $("#video");
  // Only the plan and the element's own size matter here, so the box is on
  // screen before the first frame has decoded.
  if (!plan || !video.offsetWidth) return;
  const source = plan.source;
  const width = video.offsetWidth;
  const height = video.offsetHeight;
  const relative = relativeTime();
  plan.panes.forEach((pane) => {
    const covers = pane.crop_w >= source.width - 4 && pane.crop_h >= source.height - 4;
    if (covers) return;   // the "whole court" pane needs no box
    const at = paneAt(pane, relative);
    const box = el("div", {
      class: `crop-box${pane.tracking ? "" : " secondary"}`,
      style: `left:${(at.x / source.width) * width}px;top:${(at.y / source.height) * height}px;` +
             `width:${(pane.crop_w / source.width) * width}px;height:${(pane.crop_h / source.height) * height}px`,
    }, [el("span", { class: "crop-tag", text: pane.tracking ? "tracking" : "fixed" })]);
    layer.appendChild(box);
  });
}

function drawReel() {
  const canvas = $("#reel-canvas");
  const ctx = canvas.getContext("2d");
  const video = $("#video");
  const plan = S.plan;
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!plan || !video.videoWidth) {
    ctx.fillStyle = "#05070a";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    return;
  }
  const scale = canvas.width / plan.canvas.width;
  const k = video.videoWidth / plan.source.width;   // proxy is smaller than the source
  const relative = relativeTime();
  ctx.fillStyle = "#000";
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  if (plan.blur_background) {
    const cover = Math.max(canvas.width / video.videoWidth, canvas.height / video.videoHeight);
    const width = video.videoWidth * cover;
    const height = video.videoHeight * cover;
    ctx.save();
    ctx.filter = "blur(9px) brightness(0.82)";
    try {
      ctx.drawImage(video, (canvas.width - width) / 2, (canvas.height - height) / 2, width, height);
    } catch (err) { /* frame not ready */ }
    ctx.restore();
  }
  for (const pane of plan.panes) {
    const at = paneAt(pane, relative);
    try {
      ctx.drawImage(
        video,
        at.x * k, at.y * k, pane.crop_w * k, pane.crop_h * k,
        pane.dest_x * scale, pane.dest_y * scale, pane.scaled_w * scale, pane.scaled_h * scale,
      );
    } catch (err) { /* frame not ready */ }
  }
  const clip = selected();
  if (clip) {
    const outside = video.currentTime < clip.start - 0.05 || video.currentTime > clip.end + 0.05;
    $("#reel-note").textContent = outside
      ? "playhead is outside this rally"
      : `${fmtTime(clip.end - clip.start)}s · ${plan.layout}${plan.speed !== 1 ? ` · ${plan.speed}× speed` : ""}`;
  }
}

/* ────────────────────────── timelines ────────────────────────── */
/* Two tiers, like any cutting room: an overview of the whole match, and a
 * zoomed detail track where trimming happens with pixel precision. */

const RULER = 16;          // height of the time ruler inside the detail canvas
const HANDLE = 7;          // pixels either side of an edge that grab it
const MIN_DRAG = 3;        // pixels before a click becomes a drag

function canvasMetrics(canvas) {
  const ratio = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || canvas.parentElement.clientWidth || 600;
  const height = parseInt(canvas.getAttribute("height"), 10);
  if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  return { ctx, width, height };
}

function viewSpan() { return Math.max(1, S.view.t1 - S.view.t0); }

function fitView() {
  S.view = { t0: 0, t1: Math.max(5, duration()) };
  drawTimelines();
}

function centreView(clip) {
  const span = Math.min(viewSpan(), Math.max(8, (clip.end - clip.start) * 3));
  const centre = (clip.start + clip.end) / 2;
  setView(centre - span / 2, centre + span / 2);
}

function setView(t0, t1) {
  const total = Math.max(5, duration());
  let span = Math.min(Math.max(t1 - t0, 1.5), total);
  let start = Math.max(0, Math.min(t0, total - span));
  S.view = { t0: start, t1: start + span };
  drawTimelines();
}

function zoomView(factor, anchor) {
  const span = viewSpan();
  const centre = anchor === undefined ? (S.view.t0 + S.view.t1) / 2 : anchor;
  const next = Math.min(Math.max(span * factor, 1.5), Math.max(5, duration()));
  const ratio = (centre - S.view.t0) / span;
  setView(centre - next * ratio, centre - next * ratio + next);
}

function clipColour(clip, isSelected) {
  if (clip.keep === false) return isSelected ? "#7d8796" : "#3b434f";
  return isSelected ? "#56c8f5" : "#2f7f9e";
}

function drawTimelines() {
  drawOverview();
  drawDetail();
  $("#view-readout").textContent =
    `showing ${fmtTime(S.view.t0, 0)}–${fmtTime(S.view.t1, 0)} of ${fmtTime(duration(), 0)}`;
  drawCropLayer();
}

function drawCurve(ctx, width, top, height, t0, t1, thin) {
  const curve = (S.project.analysis && S.project.analysis.curve) || {};
  const scores = curve.score || [];
  if (!scores.length) return;
  const rate = curve.rate || 4;
  const peak = Math.max(0.3, ...scores) || 1;
  ctx.beginPath();
  ctx.moveTo(0, top + height);
  let previous = -1;
  for (let x = 0; x <= width; x += 1) {
    const time = t0 + ((t1 - t0) * x) / width;
    const index = Math.round(time * rate);
    if (index === previous) continue;
    previous = index;
    const value = index >= 0 && index < scores.length ? scores[index] : 0;
    ctx.lineTo(x, top + height - (value / peak) * height);
  }
  ctx.lineTo(width, top + height);
  ctx.closePath();
  ctx.fillStyle = thin ? "rgba(86,200,245,0.16)" : "rgba(86,200,245,0.20)";
  ctx.fill();
  if (!thin) {
    ctx.strokeStyle = "rgba(86,200,245,0.6)";
    ctx.lineWidth = 1;
    ctx.stroke();
    const enter = curve.enter;
    if (enter) {
      const y = top + height - (enter / peak) * height;
      ctx.strokeStyle = "rgba(255,182,77,0.45)";
      ctx.setLineDash([3, 3]);
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }
}

function drawOverview() {
  const canvas = $("#overview");
  const { ctx, width, height } = canvasMetrics(canvas);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#12161c";
  ctx.fillRect(0, 0, width, height);
  if (!S.project) return;
  const total = Math.max(1, duration());
  const x = (time) => (time / total) * width;

  drawCurve(ctx, width, 2, height - 12, 0, total, true);
  for (const clip of clips()) {
    ctx.fillStyle = clipColour(clip, clip.id === S.clipId);
    ctx.fillRect(x(clip.start), height - 9, Math.max(1.5, x(clip.end) - x(clip.start)), 6);
  }
  // The window the detail track is showing.
  ctx.strokeStyle = "rgba(233,237,243,0.75)";
  ctx.lineWidth = 1;
  ctx.strokeRect(x(S.view.t0), 0.5, Math.max(2, x(S.view.t1) - x(S.view.t0)), height - 1);
  ctx.fillStyle = "rgba(233,237,243,0.08)";
  ctx.fillRect(x(S.view.t0), 0, Math.max(2, x(S.view.t1) - x(S.view.t0)), height);

  const time = $("#video").currentTime || 0;
  ctx.fillStyle = "#ffb64d";
  ctx.fillRect(x(time) - 0.5, 0, 1.5, height);
}

function tickStep(span, width) {
  const target = span / Math.max(4, width / 90);
  for (const step of [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1800]) {
    if (step >= target) return step;
  }
  return 3600;
}

function drawDetail() {
  const canvas = $("#detail");
  const { ctx, width, height } = canvasMetrics(canvas);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#12161c";
  ctx.fillRect(0, 0, width, height);
  if (!S.project) return;
  const { t0, t1 } = S.view;
  const span = t1 - t0;
  const x = (time) => ((time - t0) / span) * width;
  const laneTop = 6;
  const laneBottom = height - RULER - 4;
  const laneHeight = laneBottom - laneTop;

  drawCurve(ctx, width, laneTop, laneHeight, t0, t1, false);

  // Impact ticks, once they are far enough apart to read.
  const impacts = (S.project.analysis && S.project.analysis.impact_times) || [];
  if (span < 240 && impacts.length) {
    ctx.fillStyle = "rgba(255,255,255,0.42)";
    for (const time of impacts) {
      if (time < t0 - 1 || time > t1 + 1) continue;
      ctx.fillRect(x(time), laneBottom - 7, 1, 7);
    }
  }

  ctx.font = "11px -apple-system, system-ui, sans-serif";
  for (const clip of clips()) {
    if (clip.end < t0 || clip.start > t1) continue;
    const isSelected = clip.id === S.clipId;
    const left = x(clip.start);
    const right = x(clip.end);
    const boxTop = laneTop + 4;
    const boxHeight = laneHeight - 8;
    ctx.fillStyle = clipColour(clip, isSelected);
    ctx.globalAlpha = clip.keep === false ? 0.55 : 0.82;
    roundRect(ctx, left, boxTop, Math.max(2, right - left), boxHeight, 4);
    ctx.fill();
    ctx.globalAlpha = 1;
    if (isSelected) {
      ctx.strokeStyle = "#e9edf3";
      ctx.lineWidth = 1.5;
      roundRect(ctx, left, boxTop, Math.max(2, right - left), boxHeight, 4);
      ctx.stroke();
      ctx.fillStyle = "#e9edf3";
      ctx.fillRect(left - 1.5, boxTop, 3, boxHeight);
      ctx.fillRect(right - 1.5, boxTop, 3, boxHeight);
    }
    if (right - left > 54) {
      ctx.fillStyle = "rgba(6,12,18,0.85)";
      ctx.fillText(
        `${clip.label || clip.id}${clip.shots ? ` · ${clip.shots}` : ""}`,
        left + 6, boxTop + 14,
      );
    }
  }

  if (S.drag && S.drag.type === "create") {
    const from = Math.min(S.drag.from, S.drag.to);
    const to = Math.max(S.drag.from, S.drag.to);
    ctx.fillStyle = "rgba(67,221,139,0.25)";
    ctx.fillRect(x(from), laneTop + 4, x(to) - x(from), laneHeight - 8);
    ctx.strokeStyle = "#43dd8b";
    ctx.setLineDash([4, 3]);
    ctx.strokeRect(x(from), laneTop + 4, x(to) - x(from), laneHeight - 8);
    ctx.setLineDash([]);
    ctx.fillStyle = "#43dd8b";
    ctx.fillText(`new rally ${fmtTime(to - from)}s`, x(from) + 6, laneTop + 18);
  }

  // Ruler.
  ctx.fillStyle = "#0e1217";
  ctx.fillRect(0, height - RULER, width, RULER);
  ctx.strokeStyle = "rgba(255,255,255,0.08)";
  ctx.beginPath();
  ctx.moveTo(0, height - RULER + 0.5);
  ctx.lineTo(width, height - RULER + 0.5);
  ctx.stroke();
  const step = tickStep(span, width);
  ctx.fillStyle = "#8d99a9";
  ctx.textAlign = "left";
  for (let time = Math.ceil(t0 / step) * step; time <= t1; time += step) {
    const px = x(time);
    ctx.fillRect(px, height - RULER, 1, 4);
    ctx.fillText(fmtTime(time, 0), px + 4, height - 4);
  }

  const time = $("#video").currentTime || 0;
  if (time >= t0 && time <= t1) {
    ctx.fillStyle = "#ffb64d";
    ctx.fillRect(x(time) - 0.5, 0, 1.5, height - RULER);
    ctx.beginPath();
    ctx.moveTo(x(time) - 5, 0);
    ctx.lineTo(x(time) + 5, 0);
    ctx.lineTo(x(time), 7);
    ctx.closePath();
    ctx.fill();
  }
}

function roundRect(ctx, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + width, y, x + width, y + height, r);
  ctx.arcTo(x + width, y + height, x, y + height, r);
  ctx.arcTo(x, y + height, x, y, r);
  ctx.arcTo(x, y, x + width, y, r);
  ctx.closePath();
}

/* ── interaction ───────────────────────────────────────────── */

function detailTimeAt(event) {
  const canvas = $("#detail");
  const rect = canvas.getBoundingClientRect();
  const fraction = (event.clientX - rect.left) / rect.width;
  return S.view.t0 + fraction * viewSpan();
}

function hitTest(event) {
  const canvas = $("#detail");
  const rect = canvas.getBoundingClientRect();
  const y = event.clientY - rect.top;
  const time = detailTimeAt(event);
  const perPixel = viewSpan() / rect.width;
  if (y > rect.height - RULER) return { type: "scrub", time };
  // Prefer the selected clip's handles, then any clip under the cursor.
  const ordered = clips().slice().sort((a, b) => (b.id === S.clipId) - (a.id === S.clipId));
  for (const clip of ordered) {
    const grab = HANDLE * perPixel;
    if (Math.abs(time - clip.start) <= grab) return { type: "trim-start", clip, time };
    if (Math.abs(time - clip.end) <= grab) return { type: "trim-end", clip, time };
    if (time > clip.start && time < clip.end) return { type: "move", clip, time };
  }
  return { type: "create", time };
}

function neighbourBounds(clip) {
  let low = 0;
  let high = duration();
  for (const other of clips()) {
    if (other.id === clip.id) continue;
    if (other.end <= clip.start) low = Math.max(low, other.end);
    if (other.start >= clip.end) high = Math.min(high, other.start);
  }
  return { low, high };
}

function wireTimelines() {
  const detail = $("#detail");
  const overview = $("#overview");
  const minimum = (S.state && S.state.min_clip_duration) || 0.4;

  detail.addEventListener("wheel", (event) => {
    event.preventDefault();
    if (event.ctrlKey || event.metaKey || Math.abs(event.deltaY) > Math.abs(event.deltaX)) {
      zoomView(event.deltaY > 0 ? 1.18 : 1 / 1.18, detailTimeAt(event));
    } else {
      const shift = (event.deltaX / detail.clientWidth) * viewSpan();
      setView(S.view.t0 + shift, S.view.t1 + shift);
    }
  }, { passive: false });

  detail.addEventListener("pointerdown", (event) => {
    detail.setPointerCapture(event.pointerId);
    const hit = hitTest(event);
    S.drag = {
      type: hit.type, clip: hit.clip, from: hit.time, to: hit.time,
      startX: event.clientX, moved: false,
      origin: hit.clip ? { start: hit.clip.start, end: hit.clip.end } : null,
    };
    if (hit.clip) selectClip(hit.clip.id);
    if (hit.type === "scrub") $("#video").currentTime = hit.time;
    drawTimelines();
  });

  detail.addEventListener("pointermove", (event) => {
    if (!S.drag) {
      const hit = hitTest(event);
      detail.style.cursor = hit.type.startsWith("trim") ? "ew-resize"
        : hit.type === "move" ? "grab" : hit.type === "scrub" ? "col-resize" : "crosshair";
      return;
    }
    const time = detailTimeAt(event);
    S.drag.to = time;
    if (Math.abs(event.clientX - S.drag.startX) > MIN_DRAG) S.drag.moved = true;
    const drag = S.drag;
    if (drag.type === "scrub") {
      $("#video").currentTime = Math.max(0, Math.min(duration(), time));
    } else if (drag.clip && drag.moved) {
      const bounds = neighbourBounds(drag.clip);
      const clip = drag.clip;
      if (drag.type === "trim-start") {
        clip.start = Math.max(bounds.low, Math.min(time, clip.end - minimum));
      } else if (drag.type === "trim-end") {
        clip.end = Math.min(bounds.high, Math.max(time, clip.start + minimum));
      } else {
        const shift = time - drag.from;
        const length = drag.origin.end - drag.origin.start;
        let start = Math.max(bounds.low, Math.min(drag.origin.start + shift, bounds.high - length));
        clip.start = start;
        clip.end = start + length;
      }
      drawTimelines();
      drawReel();
    } else if (drag.type === "create") {
      drawTimelines();
    }
  });

  const finish = (event) => {
    const drag = S.drag;
    if (!drag) return;
    S.drag = null;
    detail.style.cursor = "crosshair";
    if (drag.type === "create") {
      const from = Math.max(0, Math.min(drag.from, drag.to));
      const to = Math.min(duration(), Math.max(drag.from, drag.to));
      if (drag.moved && to - from >= minimum) addClip(from, to);
      else $("#video").currentTime = from;
      drawTimelines();
      return;
    }
    if (drag.clip && drag.moved) {
      queueClipPatch(drag.clip.id, {
        start: Number(drag.clip.start.toFixed(3)),
        end: Number(drag.clip.end.toFixed(3)),
      });
      renderStrip();
      renderRallyInspector();
      updateSummary();
    } else if (drag.clip && !drag.moved) {
      selectClip(drag.clip.id, { seek: true });
    }
    drawTimelines();
  };
  detail.addEventListener("pointerup", finish);
  detail.addEventListener("pointercancel", finish);

  const overviewSeek = (event) => {
    const rect = overview.getBoundingClientRect();
    const fraction = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    const time = fraction * duration();
    const span = viewSpan();
    setView(time - span / 2, time + span / 2);
    $("#video").currentTime = time;
  };
  overview.addEventListener("pointerdown", (event) => {
    overview.setPointerCapture(event.pointerId);
    S.overviewDrag = true;
    overviewSeek(event);
  });
  overview.addEventListener("pointermove", (event) => { if (S.overviewDrag) overviewSeek(event); });
  overview.addEventListener("pointerup", () => { S.overviewDrag = false; });
  overview.addEventListener("pointercancel", () => { S.overviewDrag = false; });
}

/* ────────────────────────── clip operations ────────────────────────── */

async function addClip(start, end) {
  try {
    const payload = await api(`/api/projects/${S.project.id}/clips`, {
      method: "POST", body: { start, end },
    });
    S.project = payload.project;
    S.clipId = payload.clip.id;
    S.planKey = "";
    renderAll();
    toast(`Added ${payload.clip.label || payload.clip.id}`);
  } catch (err) {
    toast(err.message, true);
  }
}

async function splitHere() {
  const clip = selected();
  if (!clip) return;
  const at = $("#video").currentTime;
  await flushSave();
  try {
    const payload = await api(`/api/projects/${S.project.id}/clips/${clip.id}/split`, {
      method: "POST", body: { at },
    });
    S.project = payload.project;
    S.clipId = payload.tail.id;
    S.planKey = "";
    renderAll();
    toast("Split");
  } catch (err) {
    toast(err.message, true);
  }
}

async function deleteClip() {
  const clip = selected();
  if (!clip) return;
  await flushSave();
  try {
    const payload = await api(`/api/projects/${S.project.id}/clips/${clip.id}`, { method: "DELETE" });
    S.project = payload.project;
    const remaining = clips();
    S.clipId = remaining.length ? remaining[0].id : null;
    S.planKey = "";
    renderAll();
    toast("Removed from the timeline");
  } catch (err) {
    toast(err.message, true);
  }
}

function setEdge(which) {
  const clip = selected();
  if (!clip) return;
  const time = $("#video").currentTime;
  const minimum = (S.state && S.state.min_clip_duration) || 0.4;
  const patch = {};
  if (which === "start") patch.start = Math.min(time, clip.end - minimum);
  else patch.end = Math.max(time, clip.start + minimum);
  queueClipPatch(clip.id, patch);
  drawTimelines();
  renderStrip();
  renderRallyInspector();
}

function nudge(which, delta) {
  const clip = selected();
  if (!clip) return;
  const minimum = (S.state && S.state.min_clip_duration) || 0.4;
  const patch = {};
  if (which === "start") {
    patch.start = Math.max(0, Math.min(clip.start + delta, clip.end - minimum));
  } else {
    patch.end = Math.min(duration(), Math.max(clip.end + delta, clip.start + minimum));
  }
  queueClipPatch(clip.id, patch);
  drawTimelines();
  renderStrip();
  renderRallyInspector();
}

function setKeep(clipId, keep) {
  queueClipPatch(clipId, { keep });
  renderStrip();
  renderRallyInspector();
  drawTimelines();
  updateSummary();
}

/* ────────────────────────── inspector ────────────────────────── */

const LAYOUT_OPTIONS = [
  { value: "follow", label: "follow — tracked 9:16 crop" },
  { value: "stack", label: "stack — court above, close-up below" },
  { value: "fit", label: "fit — whole court, blurred bed" },
];

const FRAMING_SPEC = [
  [{ key: "layout", label: "Layout", type: "select", options: LAYOUT_OPTIONS, def: "follow" }],
  [{ key: "zoom", label: "Zoom", def: 1.0, min: 0.5, max: 3, step: 0.05,
     hint: "Above 1 crops tighter; below 1 goes wider than 9:16 and letterboxes." },
   { key: "pan", label: "Pan", type: "select", def: "smooth",
     options: [{ value: "smooth", label: "follow the action" }, { value: "none", label: "hold still" }] }],
  [{ key: "pan_tau", label: "Pan smoothing", unit: "s", def: 0.7, min: 0.1, max: 4, step: 0.1,
     hint: "Higher is calmer." },
   { key: "pan_max_speed", label: "Max pan speed", def: 0.6, min: 0.1, max: 2, step: 0.05,
     hint: "Crop widths per second." }],
  [{ key: "center_bias", label: "Centre pull", def: 0.15, min: 0, max: 1, step: 0.05 },
   { key: "track_y", label: "Track vertically", type: "check", def: true }],
];

const RENDER_SPEC = [
  [{ key: "size", label: "Canvas", type: "select", def: "1080x1920", options: [
    { value: "1080x1920", label: "Reels / TikTok — 1080×1920" },
    { value: "1080x1350", label: "Feed post — 1080×1350" },
    { value: "1080x1080", label: "Square — 1080×1080" },
    { value: "720x1280", label: "Small — 720×1280" },
  ] }],
  [{ key: "fps", label: "Frame rate", type: "select", numeric: true, def: 30,
     options: [{ value: 30, label: "30" }, { value: 60, label: "60" }] },
   { key: "crf", label: "Quality (CRF)", def: 20, min: 14, max: 30, step: 1,
     hint: "Lower is better and bigger." }],
  [{ key: "speed", label: "Speed", type: "select", numeric: true, def: 1, options: [
      { value: 1, label: "normal" }, { value: 0.5, label: "half — slow motion" },
      { value: 0.75, label: "0.75×" }, { value: 1.5, label: "1.5×" }] },
   { key: "fade", label: "Fade", unit: "s", def: 0.12, min: 0, max: 1, step: 0.02 }],
  [{ key: "blur_sigma", label: "Background blur", def: 26, min: 0, max: 60, step: 2,
     hint: "0 gives plain black bars." },
   { key: "music_volume", label: "Music level", def: 0.35, min: 0, max: 1, step: 0.05 }],
  [{ key: "music", label: "Music bed", type: "text", def: "",
     placeholder: "path to an audio file in your media folder" }],
  [{ key: "mute", label: "Drop the original audio", type: "check", def: false }],
];

function buildInspectorPanels() {
  const render = (S.project && S.project.render) || {};
  buildFields($("#framing-fields"), FRAMING_SPEC, render, (key, value) => {
    queueRenderPatch({ [key]: value });
    S.planKey = "";
    refreshPlan();
  }, "framing");
  const values = Object.assign({}, render, { size: `${render.width}x${render.height}` });
  buildFields($("#render-fields"), RENDER_SPEC, values, (key, value) => {
    if (key === "size") {
      const [width, height] = String(value).split("x").map(Number);
      queueRenderPatch({ width, height });
    } else {
      queueRenderPatch({ [key]: value });
    }
  }, "render");
  const settings = (S.project.analysis && S.project.analysis.settings) || {};
  buildFields($("#detect-fields"), DETECT_SPEC, settings, (key, value) => {
    setPath(S.redetect = S.redetect || {}, key, value);
  }, "detect");
}

function renderRallyInspector() {
  const host = $("#rally-inspector");
  const clip = selected();
  host.innerHTML = "";
  if (!clip) {
    host.appendChild(el("div", { class: "muted small", text:
      "No rally selected. Drag across empty timeline space to add one." }));
    return;
  }
  const render = S.project.render || {};
  host.appendChild(el("div", { class: "field" }, [
    el("label", { text: "Name" }),
    el("input", {
      type: "text", value: clip.label || "",
      onchange: (event) => {
        queueClipPatch(clip.id, { label: event.target.value.slice(0, 80) });
        renderStrip();
        drawTimelines();
      },
    }),
  ]));

  host.appendChild(el("div", { class: "chips", style: "margin:10px 0" }, [
    el("span", { class: `chip ${clip.keep === false ? "drop" : "keep"}`,
                 text: clip.keep === false ? "dropped" : "kept" }),
    el("span", { class: "chip", text: `${clip.shots || 0} impacts` }),
    el("span", { class: "chip", text: `score ${(clip.score || 0).toFixed(2)}` }),
    el("span", { class: "chip", text: `${(clip.end - clip.start).toFixed(1)}s` }),
  ]));

  for (const [which, label] of [["start", "In"], ["end", "Out"]]) {
    host.appendChild(el("div", { class: "row gap", style: "margin:6px 0" }, [
      el("span", { class: "muted small", style: "width:26px", text: label }),
      el("div", { class: "nudge" }, [
        el("button", { text: "−1", title: "one second earlier", onclick: () => nudge(which, -1) }),
        el("button", { text: "−", onclick: () => nudge(which, -0.1) }),
        el("span", { class: "val", text: fmtTime(clip[which], 2) }),
        el("button", { text: "+", onclick: () => nudge(which, 0.1) }),
        el("button", { text: "+1", onclick: () => nudge(which, 1) }),
      ]),
      el("button", { class: "ghost small", text: "Set", title: "use the playhead",
                     onclick: () => setEdge(which) }),
    ]));
  }

  host.appendChild(el("div", { class: "row gap", style: "margin-top:10px" }, [
    el("button", {
      class: clip.keep === false ? "primary" : "ghost",
      style: "flex:1",
      text: clip.keep === false ? "Keep" : "Drop",
      onclick: () => setKeep(clip.id, clip.keep === false),
    }),
    el("button", { class: "ghost", text: "Play", onclick: () => {
      $("#video").currentTime = clip.start;
      $("#video").play().catch(() => {});
    } }),
  ]));
  host.appendChild(el("div", { class: "row gap", style: "margin-top:6px" }, [
    el("button", { class: "ghost", style: "flex:1", text: "Split at playhead", onclick: splitHere }),
    el("button", { class: "ghost", text: "Remove", onclick: deleteClip }),
  ]));

  host.appendChild(el("h2", { text: "Just this rally" }));
  const overrides = el("div", { class: "fields" });
  buildFields(overrides, [
    [{ key: "layout", label: "Layout", type: "select", def: "",
       options: [{ value: "", label: `project default (${render.layout})` }].concat(LAYOUT_OPTIONS) }],
    [{ key: "zoom", label: "Zoom", type: "text", def: "", placeholder: String(render.zoom) },
     { key: "speed", label: "Speed", type: "text", def: "", placeholder: String(render.speed) }],
    [{ key: "pan", label: "Pan", type: "select", def: "", options: [
      { value: "", label: `project default (${render.pan})` },
      { value: "smooth", label: "follow the action" },
      { value: "none", label: "hold still" }] }],
  ], clip, (key, value) => {
    let next = value;
    if (key === "zoom" || key === "speed") {
      next = String(value).trim() === "" ? null : parseFloat(value);
      if (next !== null && !(next > 0)) { toast("Enter a number", true); return; }
    }
    queueClipPatch(clip.id, { [key]: next === "" ? null : next });
  }, "clip");
  host.appendChild(overrides);
}

function renderFacts() {
  const host = $("#analysis-facts");
  const analysis = S.project.analysis || {};
  const source = S.project.source || {};
  const region = analysis.roi;
  const rows = [
    ["File", (source.path || "").split("/").pop()],
    ["Size", `${source.display_width || source.width}×${source.display_height || source.height}`],
    ["Frame rate", `${(source.fps || 0).toFixed(2)} fps`],
    ["Length", fmtTime(source.duration, 0)],
    ["HDR", source.hdr ? "yes — tone mapped" : "no"],
    ["Audio", source.has_audio ? "yes" : "none (motion only)"],
    ["Impacts found", analysis.impacts != null ? String(analysis.impacts) : "—"],
    ["Court region", region ? region.map((v) => `${Math.round(v * 100)}%`).join(" ") : "whole frame"],
    ["Proxy", S.project.has_proxy ? "yes" : "no"],
  ];
  host.innerHTML = "";
  for (const [key, value] of rows) {
    host.appendChild(el("dt", { text: key }));
    host.appendChild(el("dd", { text: value }));
  }
}

/* ────────────────────────── rally strip ────────────────────────── */

function renderStrip() {
  const strip = $("#strip");
  strip.innerHTML = "";
  if (!clips().length) {
    strip.appendChild(el("div", { class: "list-empty", text:
      "No rallies. Drag across the timeline to add one, or re-detect with a lower start level." }));
    return;
  }
  for (const clip of sortedClips()) {
    const url = mediaUrl(`/api/projects/${S.project.id}/clips/${clip.id}/thumb`, { v: clip.start });
    const card = el("div", {
      class: `card${clip.keep === false ? " dropped" : ""}`,
      "aria-selected": String(clip.id === S.clipId),
      onclick: () => selectClip(clip.id, { seek: true }),
    }, [
      el("div", { class: "thumb" }, [
        el("img", { src: url, alt: "", loading: "lazy" }),
        el("div", { class: "badge", text: clip.label || clip.id }),
        el("div", { class: "dur", text: `${(clip.end - clip.start).toFixed(1)}s` }),
      ]),
      el("div", { class: "body" }, [
        el("div", { class: "title", text: `${clip.shots || 0} impacts · score ${(clip.score || 0).toFixed(2)}` }),
        el("div", { class: "actions" }, [
          el("button", {
            text: clip.keep === false ? "Keep" : "Drop",
            onclick: (event) => { event.stopPropagation(); setKeep(clip.id, clip.keep === false); },
          }),
          el("button", {
            text: "▶",
            onclick: (event) => {
              event.stopPropagation();
              selectClip(clip.id, { seek: true });
              $("#video").play().catch(() => {});
            },
          }),
        ]),
      ]),
    ]);
    strip.appendChild(card);
  }
}

/* ────────────────────────── render + detect ────────────────────────── */

async function startRender(mode, only) {
  await flushSave();
  const body = { mode, labels: mode === "reel" };
  if (only) body.only = only;
  if (mode === "reel") body.max_duration = 90;
  try {
    const job = await api(`/api/projects/${S.project.id}/render`, { method: "POST", body });
    S.watchJob = { id: job.id, target: "#render-job", onDone: async () => {
      S.project = await api(`/api/projects/${S.project.id}`);
      renderOutputs();
      toast("Render finished");
    } };
    for (const tab of $$(".tab")) tab.classList.toggle("active", tab.dataset.tab === "render");
    for (const panel of $$(".tab-body")) panel.classList.toggle("hidden", panel.dataset.panel !== "render");
    pollJobs(true);
  } catch (err) {
    toast(err.message, true);
  }
}

async function reDetect() {
  if (!confirm("Re-detect rallies? The current rally list, including manual trims, is replaced.")) return;
  await flushSave();
  try {
    const job = await api(`/api/projects/${S.project.id}/detect`, {
      method: "POST", body: { detect: (S.redetect && S.redetect.detect) || {} },
    });
    S.watchJob = { id: job.id, target: "#detect-job", onDone: async (done) => {
      await openProject(S.project.id);
      toast(`${(done.result && done.result.clips) || 0} rallies`);
    } };
    pollJobs(true);
  } catch (err) {
    toast(err.message, true);
  }
}

function renderOutputs() {
  const host = $("#outputs");
  host.innerHTML = "";
  const outputs = (S.project && S.project.outputs) || [];
  if (!outputs.length) {
    host.appendChild(el("div", { class: "list-empty", text: "Nothing rendered yet." }));
    return;
  }
  for (const output of outputs) {
    host.appendChild(el("div", { class: "list-row" }, [
      el("span", { class: "name", text: output.name }),
      el("span", { class: "meta", text: fmtSize(output.size) }),
      el("button", { class: "ghost small", text: "View", onclick: () => {
        const video = el("video", {
          src: mediaUrl(`/api/projects/${S.project.id}/outputs/${encodeURIComponent(output.name)}`),
          controls: true, autoplay: true,
        });
        openModal(output.name, video);
      } }),
      el("a", {
        class: "link small",
        href: mediaUrl(`/api/projects/${S.project.id}/outputs/${encodeURIComponent(output.name)}`, { download: 1 }),
        download: output.name, text: "Save",
      }),
    ]));
  }
}

/* ────────────────────────── keyboard ────────────────────────── */

function wireKeys() {
  document.addEventListener("keydown", (event) => {
    const tag = (event.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "select" || tag === "textarea" || event.metaKey || event.ctrlKey) return;
    if ($("#screen-editor").classList.contains("hidden")) return;
    const video = $("#video");
    const frame = 1 / ((S.project && S.project.source && S.project.source.fps) || 30);
    const handlers = {
      " ": () => togglePlay(),
      j: () => stepClip(1),
      k: () => stepClip(-1),
      l: () => $("#btn-loop").click(),
      i: () => setEdge("start"),
      o: () => setEdge("end"),
      x: () => S.clipId && setKeep(S.clipId, false),
      s: () => S.clipId && setKeep(S.clipId, true),
      n: () => {
        const at = video.currentTime;
        addClip(Math.max(0, at - 3), Math.min(duration(), at + 5));
      },
      b: () => splitHere(),
      Delete: () => deleteClip(),
      Backspace: () => deleteClip(),
      "[": () => nudge("start", -0.1),
      "]": () => nudge("start", 0.1),
      "-": () => nudge("end", -0.1),
      "=": () => nudge("end", 0.1),
      ",": () => { video.pause(); video.currentTime = Math.max(0, video.currentTime - frame); },
      ".": () => { video.pause(); video.currentTime = Math.min(duration(), video.currentTime + frame); },
      ArrowLeft: () => { video.currentTime = Math.max(0, video.currentTime - (event.shiftKey ? 5 : 1)); },
      ArrowRight: () => { video.currentTime = Math.min(duration(), video.currentTime + (event.shiftKey ? 5 : 1)); },
      "+": () => zoomView(0.6),
      0: () => fitView(),
      "?": () => showHelp(),
      Escape: () => closeModal(),
    };
    const handler = handlers[event.key];
    if (handler) { event.preventDefault(); handler(); }
  });
}

/* A deliberate, read-only hook for the browser tests: pure helpers and the
 * live state, so UI behaviour can be asserted without a decoded frame. */
window.__bdr = { S, paneAt, planKeyFor, fmtTime, fmtSize, drawCropLayer, hitTest, api };

boot();
