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

## 2. Install and play

### Quickest: play online, nothing to install

Dogspotting is hosted as a web page on claude.ai: **https://claude.ai/artifact/TriTYor1LqTjzWHibkncKF**

Open it in a web browser on your phone or computer and sign in to claude.ai. The page source is `web/dogspotting.html`.

- **No server and no API key.** Each scan runs on the player's own Claude account, so the first scan asks you to **Allow** it.
- **Your Dogdex and the leaderboard are saved online** and follow you between phone and computer, because they're tied to your claude.ai account.
- **On a phone:** tap the red ball to open the camera. To get an app icon, use Add to Home Screen as described in [Step 3](#step-3-install-it-on-your-phones-home-screen).
- **Playing with friends:** the page is private until you share it from its **Share** menu. Friends need a claude.ai account. On a personal plan, invite them by email as **Editor** so their catches save. Anyone with less access can still scan in guest mode, but their catches aren't saved.

To run your own copy on your own server instead, follow the steps below.


The game has two parts: a **server** that runs on your computer, and the **app** you open in a web browser on your computer or phone. Set up the server once, then play from any device that can reach it.

### Step 1: Set up the server on your computer (one time)

1. **Install Node.js 22.13 or newer.** Download the "LTS" version from https://nodejs.org and run the installer. To check it worked, open a terminal (Mac: *Terminal*, Windows: *PowerShell*) and run:
   ```bash
   node -v        # should print v22.13.0 or higher
   ```
2. **Get the code.** Either run `git clone https://github.com/aliceisapotato/aliceisapotato.git`, or on GitHub click **Code → Download ZIP** and unzip it.
3. **Get an Anthropic API key** at https://console.anthropic.com (Settings → API Keys). The AI breed scanner uses it. Each scan costs a small amount, so add a spending limit in the console.
4. **Save your key.** In the `dogspotting` folder, copy `.env.example` to a new file named `.env` and paste your key in:
   ```
   ANTHROPIC_API_KEY=sk-ant-your-key-here
   PORT=3000
   ```
   `.env` is in `.gitignore`, so your key is never uploaded to GitHub.
5. **Install and start:**
   ```bash
   cd dogspotting
   npm install     # only needed the first time
   npm start
   ```
   When you see `🐶 Dogspotting running at http://localhost:3000`, the server is ready. Leave this terminal open while you play. Press `Ctrl+C` to stop it.

### Step 2a: Play on your computer

Open **http://localhost:3000** in Chrome, Safari, Edge or Firefox.

On a computer, the **Spot a dog!** button opens a file picker instead of a camera. Choose a dog photo you took, for example one copied from your phone. Screenshots and photos of a screen are rejected by design.

### Step 2b: Play on your phone

Pick whichever option fits:

**Option A: Same Wi-Fi (quickest, at home)**
1. Connect your phone to the same Wi-Fi as the computer running the server.
2. Find your computer's local IP address:
   - Mac: `ipconfig getifaddr en0`
   - Windows: `ipconfig` and look for "IPv4 Address"
   - Linux: `hostname -I`
3. On your phone, open `http://<that-address>:3000`, e.g. `http://192.168.1.23:3000`.
4. If it doesn't load, allow Node.js through your computer's firewall when asked, or check that both devices are on the same network.

The camera works this way. Location tagging doesn't, because phones only share GPS with `https://` sites. Catches are saved without a map location.

**Option B: Anywhere, with GPS (secure tunnel)**

In a second terminal on the computer (keep `npm start` running in the first):
```bash
npx cloudflared tunnel --url http://localhost:3000
```
It prints a link like `https://random-words.trycloudflare.com`. Open it on your phone, even on mobile data, and you get camera plus GPS. The link changes each time you restart the tunnel, and only works while your computer and the server are on.

**Option C: Always on, for friends.** Put the server online (see [Step 5: Deploy to the cloud](#step-5-deploy-to-the-cloud)) to get a permanent `https://` link that works without your computer.

### Step 3: Install it on your phone's home screen

This makes Dogspotting open full-screen like a normal app, with its own icon:

- **iPhone (Safari):** open the game link → tap the **Share** button (square with an arrow) → **Add to Home Screen** → **Add**.
- **Android (Chrome):** open the game link → tap the **⋮** menu → **Add to Home screen** (or **Install app**) → **Install**.

On a computer, Chrome and Edge show an **Install** icon at the right of the address bar that does the same.

> Your progress is tied to the browser you play on, so your phone and your computer are separate players. Pick one device for your main Dogdex. Real accounts that sync across devices are [Step 1 of the roadmap](#step-1-accounts-do-this-before-sharing-with-friends).

### How to play

1. **Pick a spotter name** the first time you open the game.
2. **Find a dog** on the street, in the park, or at a friend's house. Ask the owner if it's OK to take a photo.
3. **Tap the big 📸 Spot a dog! button.** Your camera opens. Take a clear photo of the whole dog. You can type the dog's name first if the owner tells you.
4. **Wait for the scan.** The AI studies the dog's head, ears, coat, colours, size and tail, then shows a card with:
   - the **breed** (or likely mix) and how sure it is
   - its **rarity**: ★ common · ★★ uncommon · ★★★ rare · ★★★★ legendary
   - what features gave the breed away, plus a fun fact
   - the **XP** you earned
5. **Fill your Dogdex** (📖 tab). Each breed you catch gets an entry with your best photo and how many times you've spotted it.
6. **Climb the ranks** (🏆 tab) and compare XP and breed counts with other players on the same server.

**Scoring**

| Rarity | Example breeds | New breed | Repeat breed |
|---|---|---|---|
| ★ Common | Labrador, Golden Retriever, Doodles | 20 XP | 5 XP |
| ★★ Uncommon | Corgi, Shiba Inu | 50 XP | 13 XP |
| ★★★ Rare | Borzoi, Komondor | 120 XP | 30 XP |
| ★★★★ Legendary | Azawakh, Mudi, Lagotto | 300 XP | 75 XP |

Hunting for **new** breeds is the fastest way to level up. Level 2 needs 50 XP, level 3 needs 200, level 4 needs 450, and so on.

**"It got away…": why a catch can fail**

| Message | What to do |
|---|---|
| No dog detected | Get closer, so the dog fills more of the photo. |
| Looks like a photo of a screen or picture | Only real dogs in front of you count. |
| The scanner couldn't get a clear read | Hold steady, use good light, and show the dog's whole body and face. |
| The breed scanner is having trouble | Check the server terminal. Usually the API key is missing or wrong in `.env`, or the computer is offline. |

**Tips for better scans:** side-on shots showing the full body and head work best. Avoid heavy shadows, and avoid dogs wearing coats or costumes that hide their fur.

### Troubleshooting

- **`npm start` says the port is in use:** change `PORT=3000` in `.env` to e.g. `3001`, and use that number in the links.
- **The phone can't open the page:** use Option B (tunnel). It avoids Wi-Fi and firewall problems.
- **The camera doesn't open on the phone:** check the browser is allowed to use the camera (iPhone: Settings → Safari → Camera; Android: Chrome → Site settings → Camera).
- **Run the game-rule tests:** `npm test`.

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
