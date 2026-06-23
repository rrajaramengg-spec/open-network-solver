import { describe, it, expect } from 'vitest';
import {
  feetToMetres,
  metresToFeet,
  round,
  FEET_TO_METRES,
} from '@/utils/units';

describe('units', () => {
  it('feetToMetres uses the IEC factor', () => {
    expect(feetToMetres(1)).toBeCloseTo(FEET_TO_METRES, 10);
  });

  it('500 ft default matches 152.4 m', () => {
    expect(feetToMetres(500)).toBeCloseTo(152.4, 4);
  });

  it('round-trips ft <-> m within float precision', () => {
    const m = 1000;
    expect(metresToFeet(feetToMetres(metresToFeet(m)))).toBeCloseTo(metresToFeet(m), 6);
  });

  it('round() applies digit precision', () => {
    expect(round(1.23456, 2)).toBe(1.23);
    expect(round(1.5, 0)).toBe(2);
  });
});
