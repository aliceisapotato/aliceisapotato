import { test } from "node:test";
import assert from "node:assert/strict";
import { scoreCatch, levelForXp, xpForLevel } from "../src/game.js";

const scan = { is_dog: true, is_live_photo: true, breed: "Shiba Inu", confidence: 0.9, rarity: "rare" };

test("first catch of a breed earns double XP", () => {
  assert.deepEqual(scoreCatch(scan, false), { caught: true, newBreed: true, xp: 120 });
});

test("repeat catch earns half XP", () => {
  assert.deepEqual(scoreCatch(scan, true), { caught: true, newBreed: false, xp: 30 });
});

test("rejects non-dogs, screen photos and low confidence", () => {
  assert.equal(scoreCatch({ ...scan, is_dog: false }, false).caught, false);
  assert.equal(scoreCatch({ ...scan, is_live_photo: false }, false).caught, false);
  assert.equal(scoreCatch({ ...scan, confidence: 0.2 }, false).caught, false);
});

test("levels follow 50 * (n-1)^2", () => {
  assert.equal(levelForXp(0), 1);
  assert.equal(levelForXp(49), 1);
  assert.equal(levelForXp(50), 2);
  assert.equal(levelForXp(200), 3);
  assert.equal(xpForLevel(3), 200);
});
