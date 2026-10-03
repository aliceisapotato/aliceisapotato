// Pure game rules - no I/O, so they're easy to test and tweak.

export const MIN_CONFIDENCE = 0.4;

const BASE_POINTS = { common: 10, uncommon: 25, rare: 60, legendary: 150 };

// XP needed to reach level n is 50 * (n-1)^2 -> 0, 50, 200, 450, 800, ...
export function levelForXp(xp) {
  return Math.floor(Math.sqrt(xp / 50)) + 1;
}

export function xpForLevel(level) {
  return 50 * (level - 1) ** 2;
}

/**
 * Decide whether a scan counts as a catch and how much XP it is worth.
 * @param {object} scan          result of identifyDog()
 * @param {boolean} alreadyOwned true if the player already has this breed
 */
export function scoreCatch(scan, alreadyOwned) {
  if (!scan.is_dog) {
    return { caught: false, reason: "No dog detected. Get closer and try again!" };
  }
  if (!scan.is_live_photo) {
    return { caught: false, reason: "That looks like a photo of a screen or picture. Spot a real dog!" };
  }
  if (scan.confidence < MIN_CONFIDENCE) {
    return { caught: false, reason: "The scanner couldn't get a clear read. Try a sharper photo of the whole dog." };
  }

  const base = BASE_POINTS[scan.rarity] ?? BASE_POINTS.common;
  const newBreed = !alreadyOwned;
  // First catch of a breed is worth double; repeats still earn a little.
  const xp = newBreed ? base * 2 : Math.ceil(base / 2);
  return { caught: true, newBreed, xp };
}
