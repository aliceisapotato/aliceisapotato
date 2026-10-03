import express from "express";
import multer from "multer";
import { writeFile } from "node:fs/promises";
import { randomUUID } from "node:crypto";
import { identifyDog } from "./identify.js";
import { scoreCatch, levelForXp, xpForLevel } from "./game.js";
import * as db from "./db.js";

const app = express();
app.use(express.json());
app.use(express.static("public"));
app.use("/photos", express.static("data/photos"));

const ALLOWED_TYPES = new Set(["image/jpeg", "image/png", "image/webp", "image/gif"]);
const upload = multer({
  storage: multer.memoryStorage(),
  limits: { fileSize: 5 * 1024 * 1024 }, // Claude accepts images up to 5 MB
  fileFilter: (_req, file, cb) => cb(null, ALLOWED_TYPES.has(file.mimetype)),
});

function withLevel(player) {
  const level = levelForXp(player.xp);
  return { ...player, level, nextLevelXp: xpForLevel(level + 1) };
}

app.post("/api/players", (req, res) => {
  const name = String(req.body?.name ?? "").trim().slice(0, 30);
  if (!name) return res.status(400).json({ error: "Name is required" });
  res.status(201).json(withLevel(db.createPlayer(name)));
});

app.get("/api/players/:id", (req, res) => {
  const player = db.getPlayer(req.params.id);
  if (!player) return res.status(404).json({ error: "Player not found" });
  res.json(withLevel(player));
});

app.get("/api/players/:id/dogdex", (req, res) => {
  res.json(db.getDogdex(req.params.id));
});

app.get("/api/leaderboard", (_req, res) => {
  res.json(db.getLeaderboard());
});

app.post("/api/catch", upload.single("photo"), async (req, res) => {
  const player = db.getPlayer(req.body?.playerId);
  if (!player) return res.status(404).json({ error: "Player not found" });
  if (!req.file) return res.status(400).json({ error: "Upload a JPEG, PNG, WebP or GIF photo under 5 MB" });

  let scan;
  try {
    scan = await identifyDog(req.file.buffer, req.file.mimetype);
  } catch (err) {
    console.error("Scan failed:", err);
    return res.status(502).json({ error: "The breed scanner is having trouble. Try again in a moment." });
  }

  const alreadyOwned = scan.is_dog && db.playerOwnsBreed(player.id, scan.breed);
  const outcome = scoreCatch(scan, alreadyOwned);
  if (!outcome.caught) return res.json({ scan, ...outcome });

  const ext = req.file.mimetype.split("/")[1].replace("jpeg", "jpg");
  const photoFile = `${randomUUID()}.${ext}`;
  await writeFile(`data/photos/${photoFile}`, req.file.buffer);

  const lat = Number.parseFloat(req.body.lat);
  const lng = Number.parseFloat(req.body.lng);
  db.recordCatch({
    playerId: player.id,
    scan,
    photoFile,
    nickname: req.body.nickname?.slice(0, 40),
    lat: Number.isFinite(lat) ? lat : null,
    lng: Number.isFinite(lng) ? lng : null,
    xp: outcome.xp,
  });

  const before = levelForXp(player.xp);
  const updated = withLevel(db.getPlayer(player.id));
  res.json({ scan, ...outcome, photoUrl: `/photos/${photoFile}`, player: updated, leveledUp: updated.level > before });
});

const port = Number(process.env.PORT ?? 3000);
app.listen(port, () => console.log(`🐶 Dogspotting running at http://localhost:${port}`));
