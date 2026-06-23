import { describe, it, expect } from 'vitest';
import {
  FALLBACK_CATEGORY,
  iconIdForCategory,
  humanizeCategory,
} from '@/utils/categoryIcon';

describe('iconIdForCategory', () => {
  it('maps a curated category to its `cat-<key>` image id', () => {
    expect(iconIdForCategory('hospital')).toBe('cat-hospital');
    expect(iconIdForCategory('fire_station')).toBe('cat-fire_station');
    expect(iconIdForCategory('pharmacy')).toBe('cat-pharmacy');
  });

  it('is case-insensitive', () => {
    expect(iconIdForCategory('Hospital')).toBe('cat-hospital');
    expect(iconIdForCategory('FIRE_STATION')).toBe('cat-fire_station');
  });

  it('falls back to the marker pin for unknown categories', () => {
    expect(iconIdForCategory('definitely_not_a_category')).toBe(
      `cat-${FALLBACK_CATEGORY}`,
    );
  });

  it('falls back for null/undefined/empty', () => {
    expect(iconIdForCategory(null)).toBe(`cat-${FALLBACK_CATEGORY}`);
    expect(iconIdForCategory(undefined)).toBe(`cat-${FALLBACK_CATEGORY}`);
    expect(iconIdForCategory('')).toBe(`cat-${FALLBACK_CATEGORY}`);
  });

  it('resolves shared-glyph aliases to a registered id', () => {
    // clinic/ambulance_station share the hospital glyph but keep their own id.
    expect(iconIdForCategory('ambulance_station')).toBe('cat-ambulance_station');
    expect(iconIdForCategory('clinic')).toBe('cat-clinic');
  });
});

describe('humanizeCategory', () => {
  it('title-cases snake_case category keys', () => {
    expect(humanizeCategory('fire_station')).toBe('Fire Station');
    expect(humanizeCategory('place_of_worship')).toBe('Place Of Worship');
    expect(humanizeCategory('hospital')).toBe('Hospital');
  });

  it('renders the `all` sentinel as a friendly label', () => {
    expect(humanizeCategory('all')).toBe('All facilities');
  });
});
