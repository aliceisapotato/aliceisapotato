// SQLite storage using Node's built-in driver (no native packages to compile).
import { DatabaseSync } from "node:sqlite";
import { mkdirSync } from "node:fs";
import { randomUUID } from "node:crypto";

mkdirSync("data/photos", { recursive: true });
const db = new DatabaseSync(process.env.DB_PATH ?? "data/dogspotting.db");

db.exec(`
  CREATE TABLE IF NOT EXISTS players (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    xp         INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
  );
  CREATE TABLE IF NOT EXISTS catches (
    id          TEXT PRIMARY KEY,
    player_id   TEXT NOT NULL REFERENCES players(id),
    breed       TEXT NOT NULL,
    rarity      TEXT NOT NULL,
    confidence  REAL NOT NULL,
    scan_json   TEXT NOT NULL,
    photo_file  TEXT NOT NULL,
    nickname    TEXT,
    lat         REAL,
    lng         REAL,
    xp_awarded  INTEGER NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
  );
  CREATE INDEX IF NOT EXISTS catches_by_player ON catches(player_id, breed);
`);

export function createPlayer(name) {
  const id = randomUUID();
  db.prepare("INSERT INTO players (id, name) VALUES (?, ?)").run(id, name);
  return getPlayer(id);
}

export function getPlayer(id) {
  return db.prepare("SELECT id, name, xp FROM players WHERE id = ?").get(id);
}

export function playerOwnsBreed(playerId, breed) {
  return !!db
    .prepare("SELECT 1 FROM catches WHERE player_id = ? AND breed = ? COLLATE NOCASE LIMIT 1")
    .get(playerId, breed);
}

export function recordCatch({ playerId, scan, photoFile, nickname, lat, lng, xp }) {
  const id = randomUUID();
  db.exec("BEGIN");
  try {
    db.prepare(
      `INSERT INTO catches (id, player_id, breed, rarity, confidence, scan_json, photo_file, nickname, lat, lng, xp_awarded)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).run(id, playerId, scan.breed, scan.rarity, scan.confidence, JSON.stringify(scan), photoFile,
      nickname ?? null, lat ?? null, lng ?? null, xp);
    db.prepare("UPDATE players SET xp = xp + ? WHERE id = ?").run(xp, playerId);
    db.exec("COMMIT");
  } catch (err) {
    db.exec("ROLLBACK");
    throw err;
  }
  return id;
}

// One entry per breed: how many times caught, best photo, first spotted.
export function getDogdex(playerId) {
  return db
    .prepare(
      `SELECT breed, rarity, COUNT(*) AS times_caught, MIN(created_at) AS first_spotted,
              (SELECT photo_file FROM catches c2 WHERE c2.player_id = c.player_id AND c2.breed = c.breed COLLATE NOCASE
               ORDER BY confidence DESC LIMIT 1) AS photo_file
       FROM catches c WHERE player_id = ?
       GROUP BY breed COLLATE NOCASE ORDER BY first_spotted DESC`,
    )
    .all(playerId);
}

export function getLeaderboard(limit = 20) {
  return db
    .prepare(
      `SELECT p.name, p.xp, COUNT(DISTINCT c.breed) AS breeds
       FROM players p LEFT JOIN catches c ON c.player_id = p.id
       GROUP BY p.id ORDER BY p.xp DESC LIMIT ?`,
    )
    .all(limit);
}
