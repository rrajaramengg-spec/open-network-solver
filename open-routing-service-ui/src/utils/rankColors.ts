/**
 * Rank → concrete hex colour.
 *
 * These mirror the `--color-rank-N` CSS variables in `styles/globals.css` so
 * the results list (DOM, which resolves `var()`) and the map layers (MapLibre
 * WebGL, which does **not** understand `var()`) share one palette. Always use
 * concrete hex here — passing a `var(--…)` string to a MapLibre paint property
 * silently fails to parse and every feature falls back to the default colour.
 */
export const RANK_COLORS: Record<number, string> = {
  1: '#ef4444', // red-500
  2: '#f97316', // orange-500
  3: '#eab308', // yellow-500
  4: '#84cc16', // lime-500
  5: '#14b8a6', // teal-500
  6: '#0ea5e9', // sky-500
  7: '#6366f1', // indigo-500
  8: '#a855f7', // purple-500
  9: '#ec4899', // pink-500
  10: '#f43f5e', // rose-500
};

export function rankColor(rank: number): string {
  return RANK_COLORS[rank] ?? '#94a3b8';
}
