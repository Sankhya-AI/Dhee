// Client-side replica of engram/core/decay.py:calculate_decayed_strength
const SML_RATE = 0.15;
const LML_RATE = 0.02;
const ACCESS_DAMPENING = 0.5;
export const FORGET_THRESHOLD = 0.1;
export const PROMOTE_THRESHOLD = 0.7;
export const PROMOTE_ACCESS_THRESHOLD = 3;

export function projectDecay(
  currentStrength: number,
  accessCount: number,
  layer: "sml" | "lml",
  days: number
): number {
  const rate = layer === "sml" ? SML_RATE : LML_RATE;
  const dampening = 1 + ACCESS_DAMPENING * Math.log1p(accessCount);
  const projected = currentStrength * Math.exp((-rate * days) / dampening);
  return Math.max(0, Math.min(1, projected));
}

export function decayProjectionSeries(
  currentStrength: number,
  accessCount: number,
  layer: "sml" | "lml",
  totalDays = 30
): { day: number; strength: number }[] {
  const points: { day: number; strength: number }[] = [];
  for (let d = 0; d <= totalDays; d++) {
    points.push({ day: d, strength: projectDecay(currentStrength, accessCount, layer, d) });
  }
  return points;
}
