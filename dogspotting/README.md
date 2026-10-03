# 🐾 Dogspotting: Gotta Spot 'Em All

A Pokémon-style game where you photograph dogs you meet on the street. AI identifies the breed from the photo, and the dog goes into your **Dogdex**. You earn XP, level up, and chase rare breeds.

This folder has a working starter app plus a step-by-step guide for growing it into a full game.

---

## 1. How it works

```
 📱 Phone (web app)                 ☁️ Server (Node.js)                 🤖 Claude (vision AI)
 ───────────────────               ─────────────────────               ────────────────────
 Take photo with camera  ──POST──▶  /api/catch                ──────▶  Looks at the dog's attributes:
 Shrink to ~1568px JPEG             • checks the player                 head, muzzle, ears, coat,
 Attach GPS (optional)              • sends photo to Claude    ◀──────  colours, size, tail
                                    • scores the catch                  Returns JSON: breed, mix,
 Show the "card" with     ◀──JSON── • saves photo and catch             confidence, rarity, reasoning
 breed, rarity and XP                 to SQLite
```

| File | What it does |
|---|---|
| `src/identify.js` | **The AI part.** Sends the photo to Claude and gets back structured JSON (breed, possible mix, confidence, rarity, attributes, reasoning, fun fact). Also flags photos of screens or printouts so players can't cheat with Google Images. |
| `src/game.js` | **Game rules.** Decides if a scan counts as a catch and how much XP it's worth: rarity points, double XP for a new breed, the level curve. |
| `src/db.js` | **Storage.** SQLite tables for players and catches, plus Dogdex and leaderboard queries. |
| `src/server.js` | **API.** Express routes the phone talks to. |
| `public/` | **The app.** A mobile web app (PWA): camera button, scanning animation, catch card, Dogdex grid, leaderboard. |

### Why these choices

- **A web app (PWA) before a native app.** `<input type="file" accept="image/*" capture="environment">` opens the phone's rear camera on iOS and Android. You get one codebase, no app-store review, and you can "Add to Home Screen" so it feels like an app. Move to native (step 6) once the game is fun.
- **A vision LLM instead of training your own model.** A classic classifier only knows the ~120 breeds it was trained on and can't explain itself. Claude can name any breed, describe mixes, say *why* ("wedge-shaped head, erect triangular ears, curled tail → Shiba Inu"), judge rarity, and reject non-dogs and photos of screens, all in one call. Structured outputs force the reply to match the schema in `identify.js`, so the game never parses free text.
- **SQLite.** It's built into Node 22+, needs no setup, and is plenty for thousands of players. You can swap in Postgres later.

---

## 2. Run it on your computer

You need **Node.js 22.13 or newer** and an **Anthropic API key** (get one at https://console.anthropic.com).

```bash
cd dogspotting
npm install
export ANTHROPIC_API_KEY=sk-ant-...      # Windows PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."
npm start
```

Open http://localhost:3000, pick a name, and upload a dog photo.

Run the game-rule tests with `npm test`.

### Try it on your phone

Phone browsers only allow camera and GPS on **HTTPS** pages (or localhost). The quickest way to get HTTPS is a tunnel:

```bash
npx cloudflared tunnel --url http://localhost:3000
```

Open the `https://….trycloudflare.com` URL it prints on your phone. Tap **Spot a dog!** and the camera opens.

---

## 3. What happens when you catch a dog

1. The phone shrinks the photo to 1568px (`shrinkImage` in `app.js`). Phone photos are often 5–12 MB; this keeps uploads fast and under the API's 5 MB image limit without hurting accuracy.
2. `POST /api/catch` receives the photo and calls `identifyDog()`.
3. Claude returns something like:
   ```json
   {
     "is_dog": true, "is_live_photo": true,
     "breed": "Pembroke Welsh Corgi", "possible_mix": [], "confidence": 0.93,
     "rarity": "uncommon",
     "attributes": { "size": "small", "coat": "medium double coat",
                     "colors": ["red", "white"], "ears": "large, erect",
                     "tail": "docked", "distinguishing_features": ["short legs", "fox-like face"] },
     "reasoning": "Long low body on very short legs, large upright ears and a fox-like head with red-and-white coat.",
     "fun_fact": "Corgis were bred to herd cattle by nipping at their heels."
   }
   ```
4. `scoreCatch()` applies the rules:
   - Not a dog, a photo of a screen, or confidence below 40% → **"It got away…"**
   - Points by rarity: common 10 · uncommon 25 · rare 60 · legendary 150
   - **New breed = 2× XP.** A repeat catch earns ½ XP.
   - Level *n* needs `50 × (n−1)²` XP (0, 50, 200, 450, 800…)
5. The photo is saved, the catch is stored, and the phone shows a trading-card-style result.

**Tweak the game** by editing the numbers in `src/game.js`. **Tweak the AI** by editing `SYSTEM_PROMPT` and the schema descriptions in `src/identify.js`. Claude follows those descriptions closely.

---

## 4. Roadmap: from starter to full game

Build these one at a time, playtesting after each.

### Step 1: Accounts (do this before sharing with friends)
Right now a player is just an ID in `localStorage`. If you clear the browser, you lose your Dogdex. Add real sign-in with a hosted auth service (Supabase Auth, Firebase Auth, or Clerk), then replace `playerId` in requests with the signed-in user from a session token, checked on the server.

### Step 2: Anti-cheat
- ✅ Already done: rejects photos of screens and printouts (`is_live_photo`).
- Accept only photos taken **just now**: read the EXIF timestamp with the `exifr` npm package and reject photos older than ~10 minutes. You can also use an in-app camera (`getUserMedia`) instead of the file picker, so gallery uploads aren't possible.
- Detect duplicates: store a perceptual hash of each photo (e.g. the `sharp-phash` package) and refuse the same picture twice.
- Rate-limit `/api/catch` per player (e.g. `express-rate-limit`), which also caps your API bill.

### Step 3: More Pokémon-style features
- **Dogdex completion:** add a `breeds` table (the ~200 AKC/FCI breeds with group and rarity) and show silhouettes for breeds you haven't caught yet. Ask Claude to match its answer to your list by adding `z.enum(BREED_NAMES)` to the schema.
- **Map:** you already store `lat`/`lng`. Show catches on a Leaflet map ("Dogs spotted near you"). Round coordinates to ~100 m before showing them to *other* players so you don't reveal where someone walks their dog every day.
- **Daily quests:** "Spot 3 terriers", "Find a dog with a curly coat". The attributes JSON already has what you need to check these.
- **Badges:** First Legendary, 10 Herding Dogs, Spotted in 3 cities.
- **Social:** friends, trading cards, weekly leaderboard reset.

### Step 4: Make the AI better and cheaper
- **Measure accuracy:** collect ~50 photos of dogs whose breed you know for sure, run them through `identifyDog()`, and track the % correct whenever you change the prompt.
- **Let players dispute a call:** add a "Not a Corgi?" button and keep the corrections. They're your best test data.
- **Cost:** each scan is one API call with one image. Most of the cost is the image, so keep the 1568px resize. The call already uses `effort: "low"`; check whether accuracy holds at that level with your test set before changing it.

### Step 5: Deploy to the cloud
Pick one:
- **Render / Railway / Fly.io:** connect the GitHub repo, set `ANTHROPIC_API_KEY` as a secret, and attach a persistent disk for `data/`. This is the easiest option for this exact code.
- **Bigger scale:** move photos to object storage (S3 / Cloudflare R2 / Supabase Storage) and the database to Postgres (Supabase / Neon). Only `db.js` and the `writeFile` line in `server.js` need to change.

Never put the API key in the frontend. It stays on the server, which is why the phone talks to *your* server and not straight to Claude.

### Step 6: Native app (optional)
When you want push notifications ("A rare Borzoi was spotted nearby!") or app-store presence, rebuild `public/` in **React Native + Expo** (`expo-camera`, `expo-location`). The server and API stay exactly the same.

---

## 5. Be a good spotter 🐕
- Ask the owner before taking photos, and never chase or approach a dog that seems nervous.
- Don't show other players exact locations. Owners' daily routines are private.
- Photos may include people in the background. Tell users that in your privacy policy, and consider blurring faces before storing images.
