/**
 * Pure unit conversions for buffer-distance input.
 *
 * Defaults per spec: incident search buffer is 500 ft by default; user can
 * toggle to metres. Internally everything stored in metres.
 */

export const FEET_TO_METRES = 0.3048;
export const METRES_TO_FEET = 1 / FEET_TO_METRES;

export function feetToMetres(ft: number): number {
  return ft * FEET_TO_METRES;
}

export function metresToFeet(m: number): number {
  return m * METRES_TO_FEET;
}

/** Round to N decimals — handy for UI display so we don't show 152.39999... */
export function round(value: number, digits = 1): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}
