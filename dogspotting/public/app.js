const $ = (sel) => document.querySelector(sel);
const RARITY_STARS = { common: "★", uncommon: "★★", rare: "★★★", legendary: "★★★★" };

let player = null;

// ---------- navigation ----------
function show(screen) {
  document.querySelectorAll(".screen").forEach((s) => (s.hidden = s.id !== screen));
  document.querySelectorAll("#tabs button").forEach((b) => b.classList.toggle("active", b.dataset.screen === screen));
  if (screen === "dogdex") loadDogdex();
  if (screen === "leaderboard") loadLeaderboard();
}
document.querySelectorAll("#tabs button").forEach((b) => b.addEventListener("click", () => show(b.dataset.screen)));

function renderBadge() {
  const pct = Math.min(100, Math.round((player.xp / player.nextLevelXp) * 100));
  $("#player-badge").hidden = false;
  $("#player-badge").innerHTML = `
    <strong>${escapeHtml(player.name)}</strong> · Lv ${player.level}
    <div class="xp-bar"><div style="width:${pct}%"></div></div>`;
}

// ---------- player ----------
async function start() {
  const id = safeStorage("get", "playerId");
  if (id) {
    const res = await fetch(`/api/players/${id}`);
    if (res.ok) player = await res.json();
  }
  if (!player) return show("onboarding");
  renderBadge();
  $("#tabs").hidden = false;
  show("spot");
}

$("#name-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const res = await fetch("/api/players", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name: $("#name-input").value }),
  });
  player = await res.json();
  safeStorage("set", "playerId", player.id);
  start();
});

// ---------- catching ----------
$("#photo-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  e.target.value = "";
  if (!file) return;

  show("result");
  $("#card").hidden = true;
  $("#again-button").hidden = true;
  $("#scanning").hidden = false;
  $("#preview").src = URL.createObjectURL(file);

  try {
    const [photo, coords] = await Promise.all([shrinkImage(file), getLocation()]);
    const form = new FormData();
    form.append("photo", photo, "dog.jpg");
    form.append("playerId", player.id);
    form.append("nickname", $("#nickname-input").value);
    if (coords) {
      form.append("lat", coords.latitude);
      form.append("lng", coords.longitude);
    }
    const res = await fetch("/api/catch", { method: "POST", body: form });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error);
    renderResult(data);
    if (data.player) {
      player = data.player;
      renderBadge();
    }
  } catch (err) {
    $("#card").innerHTML = `<div class="card miss"><h3>Oops!</h3><p>${escapeHtml(err.message)}</p></div>`;
    $("#card").hidden = false;
  } finally {
    $("#scanning").hidden = true;
    $("#again-button").hidden = false;
    $("#nickname-input").value = "";
  }
});

$("#again-button").addEventListener("click", () => show("spot"));

function renderResult({ scan, caught, reason, newBreed, xp, photoUrl, leveledUp, player: updated }) {
  if (!caught) {
    $("#card").innerHTML = `<div class="card miss"><h3>It got away…</h3><p>${escapeHtml(reason)}</p></div>`;
  } else {
    const a = scan.attributes;
    const mix = scan.possible_mix.length ? `<p class="mix">Possible mix: ${scan.possible_mix.map(escapeHtml).join(", ")}</p>` : "";
    $("#card").innerHTML = `
      <div class="card ${scan.rarity}">
        ${newBreed ? '<div class="ribbon">NEW!</div>' : ""}
        <div class="card-top"><h3>${escapeHtml(scan.breed)}</h3><span class="stars">${RARITY_STARS[scan.rarity]}</span></div>
        <img src="${photoUrl}" alt="${escapeHtml(scan.breed)}" />
        <p class="rarity-label">${scan.rarity} · ${Math.round(scan.confidence * 100)}% match</p>
        ${mix}
        <dl>
          <dt>Size</dt><dd>${escapeHtml(a.size)}</dd>
          <dt>Coat</dt><dd>${escapeHtml(a.coat)}</dd>
          <dt>Colors</dt><dd>${a.colors.map(escapeHtml).join(", ")}</dd>
          <dt>Ears</dt><dd>${escapeHtml(a.ears)}</dd>
          <dt>Tail</dt><dd>${escapeHtml(a.tail)}</dd>
        </dl>
        <p class="reasoning">🔍 ${escapeHtml(scan.reasoning)}</p>
        <p class="fun-fact">💡 ${escapeHtml(scan.fun_fact)}</p>
        <p class="xp">+${xp} XP${leveledUp ? ` · LEVEL UP! Lv ${updated.level}` : ""}</p>
      </div>`;
  }
  $("#card").hidden = false;
}

// ---------- dogdex & leaderboard ----------
async function loadDogdex() {
  const entries = await (await fetch(`/api/players/${player.id}/dogdex`)).json();
  $("#dex-count").textContent = `${entries.length} breeds`;
  $("#dex-grid").innerHTML = entries.length
    ? entries
        .map(
          (e) => `
      <div class="dex-entry ${e.rarity}">
        <img src="/photos/${e.photo_file}" alt="" loading="lazy" />
        <strong>${escapeHtml(e.breed)}</strong>
        <small>${RARITY_STARS[e.rarity]} · ×${e.times_caught}</small>
      </div>`,
        )
        .join("")
    : "<p>No dogs yet. Go outside and spot one!</p>";
}

async function loadLeaderboard() {
  const rows = await (await fetch("/api/leaderboard")).json();
  $("#leaderboard-list").innerHTML = rows
    .map((r) => `<li><strong>${escapeHtml(r.name)}</strong> — ${r.xp} XP · ${r.breeds} breeds</li>`)
    .join("");
}

// ---------- helpers ----------
// Phone photos are often 5-12 MB. Shrink to ~1568px JPEG before uploading:
// faster uploads, under the 5 MB API limit, and no loss in breed accuracy.
async function shrinkImage(file, maxSide = 1568) {
  const bitmap = await createImageBitmap(file);
  const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.85));
}

function getLocation() {
  return new Promise((resolve) => {
    if (!navigator.geolocation) return resolve(null);
    navigator.geolocation.getCurrentPosition((p) => resolve(p.coords), () => resolve(null), { timeout: 5000 });
  });
}

function safeStorage(op, key, value) {
  try {
    return op === "get" ? localStorage.getItem(key) : localStorage.setItem(key, value);
  } catch {
    return null;
  }
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

start();
