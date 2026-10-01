// Sample reels. Add a `src` (path or URL to an .mp4) to show a real video;
// without one, an animated gradient placeholder is shown.
const reels = [
  {
    user: "alice.potato", hue: 330, src: "",
    caption: "First reel on the new page 🥔✨ Tap the caption to expand, double-tap the video to like. #potato #reels #hello",
    audio: "alice.potato · Original audio", likes: 12483, comments: 214,
    gradient: "linear-gradient(135deg,#f58529,#dd2a7b,#8134af,#515bd4)",
    sampleComments: [
      { user: "spud.lover", hue: 40, text: "this is so good 😍", likes: 32 },
      { user: "mash_daily", hue: 200, text: "need a part 2!!", likes: 11 },
    ],
  },
  {
    user: "sunny.days", hue: 45, src: "",
    caption: "Golden hour hits different ☀️ #sunset #weekend",
    audio: "lofi beats · Chill mix", likes: 3920, comments: 58,
    gradient: "linear-gradient(160deg,#ff9a3c,#ff6a88,#ff99ac)",
    sampleComments: [{ user: "beachbum", hue: 180, text: "where is this?", likes: 4 }],
  },
  {
    user: "city.walks", hue: 210, src: "",
    caption: "Rainy night in the city 🌧️🌃",
    audio: "city.walks · Original audio", likes: 87210, comments: 1302,
    gradient: "linear-gradient(200deg,#0f2027,#203a43,#2c5364)",
    sampleComments: [],
  },
];

const icons = {
  heart: '<svg viewBox="0 0 24 24"><path d="M12 20s-8-5-8-11a4.5 4.5 0 0 1 8-2.8A4.5 4.5 0 0 1 20 9c0 6-8 11-8 11z"/></svg>',
  comment: '<svg viewBox="0 0 24 24"><path d="M20.7 16.5A9 9 0 1 0 17 20l4 1z"/></svg>',
  share: '<svg viewBox="0 0 24 24"><path d="M21 4 3 11l7 2 2 7z"/><path d="m10 13 5-5"/></svg>',
  save: '<svg viewBox="0 0 24 24"><path d="M19 21l-7-5-7 5V4a1 1 0 0 1 1-1h12a1 1 0 0 1 1 1z"/></svg>',
  more: '<svg viewBox="0 0 24 24"><circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/></svg>',
  music: '<svg viewBox="0 0 24 24"><path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/></svg>',
  mute: '<svg viewBox="0 0 24 24"><path d="M11 5 6 9H2v6h4l5 4zM23 9l-6 6M17 9l6 6"/></svg>',
  sound: '<svg viewBox="0 0 24 24"><path d="M11 5 6 9H2v6h4l5 4zM15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14"/></svg>',
  play: '<svg viewBox="0 0 24 24"><path d="M6 4l14 8-14 8z"/></svg>',
};

const fmt = n => n >= 1e6 ? (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M"
  : n >= 1e4 ? (n / 1e3).toFixed(1).replace(/\.0$/, "") + "K"
  : n.toLocaleString();

const esc = s => s.replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const feed = document.getElementById("feed");
const toastEl = document.getElementById("toast");
let muted = true;
let activeReel = null;

function toast(msg) {
  toastEl.textContent = msg;
  toastEl.classList.add("show");
  clearTimeout(toast.t);
  toast.t = setTimeout(() => toastEl.classList.remove("show"), 1800);
}

function media(r) {
  return r.src
    ? `<video src="${esc(r.src)}" loop playsinline muted preload="metadata"></video>`
    : `<div class="placeholder" style="background-image:${r.gradient}"><p>${esc(r.user)}<small>Add a video with “Create”, or set <code>src</code> in app.js</small></p></div>`;
}

function render(r, i) {
  const el = document.createElement("section");
  el.className = "reel";
  el.dataset.index = i;
  el.innerHTML = `
    <div class="stage">
      ${media(r)}
      <div class="shade"></div>
      <div class="paused-icon">${icons.play}</div>
      <svg class="burst" viewBox="0 0 24 24"><path d="M12 20s-8-5-8-11a4.5 4.5 0 0 1 8-2.8A4.5 4.5 0 0 1 20 9c0 6-8 11-8 11z"/></svg>
      <button class="mute-btn" aria-label="Toggle sound">${muted ? icons.mute : icons.sound}</button>
      <div class="meta">
        <div class="author">
          <span class="avatar" style="--h:${r.hue}"></span>
          <a href="#">${esc(r.user)}</a> ·
          <button class="follow">Follow</button>
        </div>
        <p class="caption">${esc(r.caption)}</p>
        <div class="audio">${icons.music}<span>${esc(r.audio)}</span></div>
      </div>
      <div class="progress"><i></i></div>
    </div>
    <div class="actions">
      <div class="act like"><button aria-label="Like">${icons.heart}</button><span>${fmt(r.likes)}</span></div>
      <div class="act comment"><button aria-label="Comment">${icons.comment}</button><span>${fmt(r.comments)}</span></div>
      <div class="act share"><button aria-label="Share">${icons.share}</button></div>
      <div class="act save"><button aria-label="Save">${icons.save}</button></div>
      <div class="act"><button aria-label="More">${icons.more}</button></div>
      <span class="audio-thumb avatar" style="--h:${r.hue}"></span>
    </div>`;
  wire(el, r);
  return el;
}

function setLiked(el, r, on) {
  r.liked = on;
  const act = el.querySelector(".act.like");
  act.classList.toggle("liked", on);
  act.querySelector("span").textContent = fmt(r.likes + (on ? 1 : 0));
}

function wire(el, r) {
  const stage = el.querySelector(".stage");
  const video = el.querySelector("video");
  const burst = el.querySelector(".burst");
  let tapTimer = null;

  stage.addEventListener("click", e => {
    if (e.target.closest("button, a, .caption")) return;
    if (tapTimer) { // double tap → like
      clearTimeout(tapTimer); tapTimer = null;
      setLiked(el, r, true);
      burst.classList.remove("show"); void burst.offsetWidth; burst.classList.add("show");
      return;
    }
    tapTimer = setTimeout(() => {
      tapTimer = null;
      if (!video) return;
      if (video.paused) { video.play(); stage.classList.remove("paused"); }
      else { video.pause(); stage.classList.add("paused"); }
    }, 250);
  });

  el.querySelector(".mute-btn").addEventListener("click", () => {
    muted = !muted;
    document.querySelectorAll(".reel video").forEach(v => (v.muted = muted));
    document.querySelectorAll(".mute-btn").forEach(b => (b.innerHTML = muted ? icons.mute : icons.sound));
  });

  el.querySelector(".caption").addEventListener("click", e => e.currentTarget.classList.toggle("open"));

  el.querySelector(".follow").addEventListener("click", e => {
    const b = e.currentTarget;
    const on = b.classList.toggle("following");
    b.textContent = on ? "Following" : "Follow";
  });

  el.querySelector(".act.like button").addEventListener("click", () => setLiked(el, r, !r.liked));
  el.querySelector(".act.save button").addEventListener("click", e => {
    const on = e.currentTarget.parentElement.classList.toggle("saved");
    toast(on ? "Saved to your collection" : "Removed from saved");
  });
  el.querySelector(".act.share button").addEventListener("click", async () => {
    const url = location.href.split("#")[0] + "#reel-" + el.dataset.index;
    try {
      if (navigator.share) await navigator.share({ title: r.user, url });
      else { await navigator.clipboard.writeText(url); toast("Link copied"); }
    } catch { /* share dismissed */ }
  });
  el.querySelector(".act.comment button").addEventListener("click", () => openComments(r, el));

  if (video) {
    const bar = el.querySelector(".progress i");
    video.addEventListener("timeupdate", () => {
      if (video.duration) bar.style.width = (video.currentTime / video.duration) * 100 + "%";
    });
  }
}

// Autoplay the reel that is in view, pause the rest.
const observer = new IntersectionObserver(entries => {
  entries.forEach(({ target, isIntersecting }) => {
    const v = target.querySelector("video");
    if (!v) return;
    if (isIntersecting) { v.muted = muted; v.play().catch(() => {}); target.querySelector(".stage").classList.remove("paused"); }
    else { v.pause(); v.currentTime = 0; }
  });
}, { threshold: 0.6 });

function mount() {
  feed.innerHTML = "";
  reels.forEach((r, i) => {
    const el = render(r, i);
    el.id = "reel-" + i;
    feed.appendChild(el);
    observer.observe(el);
  });
}

// ---------- Comments ----------
const drawer = document.getElementById("comments");
const list = document.getElementById("comment-list");
const form = document.getElementById("comment-form");
const input = document.getElementById("comment-input");
const postBtn = form.querySelector("button");

function renderComments() {
  const r = activeReel.r;
  list.innerHTML = r.sampleComments.length ? "" : '<li class="empty">No comments yet. Start the conversation.</li>';
  r.sampleComments.forEach(c => {
    const li = document.createElement("li");
    li.innerHTML = `
      <span class="avatar" style="--h:${c.hue}"></span>
      <div class="body"><b>${esc(c.user)}</b>${esc(c.text)}
        <div class="sub"><span>${c.time || "1d"}</span><span>${c.likes} likes</span><span>Reply</span></div>
      </div>
      <button class="heart ${c.liked ? "on" : ""}" aria-label="Like comment">${icons.heart}</button>`;
    li.querySelector(".heart").addEventListener("click", () => {
      c.liked = !c.liked; c.likes += c.liked ? 1 : -1; renderComments();
    });
    list.appendChild(li);
  });
}

function openComments(r, el) {
  activeReel = { r, el };
  renderComments();
  drawer.classList.add("open");
  drawer.setAttribute("aria-hidden", "false");
  input.focus();
}

function closeComments() {
  drawer.classList.remove("open");
  drawer.setAttribute("aria-hidden", "true");
}

document.getElementById("close-comments").addEventListener("click", closeComments);
document.addEventListener("keydown", e => { if (e.key === "Escape") closeComments(); });

const syncPost = () => (postBtn.disabled = !input.value.trim());
input.addEventListener("input", syncPost);
syncPost();

form.addEventListener("submit", e => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text || !activeReel) return;
  const { r, el } = activeReel;
  r.sampleComments.unshift({ user: "you", hue: 200, text, likes: 0, time: "now" });
  r.comments += 1;
  el.querySelector(".act.comment span").textContent = fmt(r.comments);
  input.value = ""; syncPost();
  renderComments();
});

// ---------- Upload your own video ----------
const fileInput = document.getElementById("file-input");
document.getElementById("upload-link").addEventListener("click", e => { e.preventDefault(); fileInput.click(); });
fileInput.addEventListener("change", () => {
  const file = fileInput.files[0];
  if (!file) return;
  reels.unshift({
    user: "you", hue: 200, src: URL.createObjectURL(file),
    caption: file.name.replace(/\.[^.]+$/, ""), audio: "you · Original audio",
    likes: 0, comments: 0, sampleComments: [],
  });
  mount();
  feed.scrollTo({ top: 0 });
  toast("Your reel is live");
  fileInput.value = "";
});

mount();
