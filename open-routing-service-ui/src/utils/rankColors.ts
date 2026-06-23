/** Rank → CSS variable defined in `styles/globals.css`. */
export const RANK_COLORS: Record<number, string> = {
  1: 'var(--color-rank-1)',
  2: 'var(--color-rank-2)',
  3: 'var(--color-rank-3)',
  4: 'var(--color-rank-4)',
  5: 'var(--color-rank-5)',
  6: 'var(--color-rank-6)',
  7: 'var(--color-rank-7)',
  8: 'var(--color-rank-8)',
  9: 'var(--color-rank-9)',
  10: 'var(--color-rank-10)',
};

export function rankColor(rank: number): string {
  return RANK_COLORS[rank] ?? '#94a3b8';
}
