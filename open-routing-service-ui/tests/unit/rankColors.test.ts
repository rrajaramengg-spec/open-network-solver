import { describe, it, expect } from 'vitest';
import { rankColor, RANK_COLORS } from '@/utils/rankColors';

describe('rankColors', () => {
  it.each(Array.from({ length: 10 }, (_, i) => i + 1))(
    'has a colour for rank %i',
    (r) => {
      expect(RANK_COLORS[r]).toBeDefined();
      expect(rankColor(r)).toBe(RANK_COLORS[r]);
    },
  );

  it('falls back to grey for an out-of-range rank', () => {
    expect(rankColor(99)).toMatch(/^#/);
  });
});
