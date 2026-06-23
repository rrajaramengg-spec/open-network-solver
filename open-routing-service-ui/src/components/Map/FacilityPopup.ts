/**
 * Facility info-popup content.
 *
 * Builds the HTML rendered inside MapLibre GL JS's built-in `Popup` when a
 * facility marker (or result row) is clicked. Kept dependency-free and pure so
 * it can be unit-tested without a map instance (the `Popup` instance itself is
 * created by `FacilityLayer`).
 */

import { humanizeCategory } from '@/utils/categoryIcon';

/** Data needed to render a facility popup. */
export interface FacilityPopupData {
  facilityId: number;
  rank: number;
  name?: string | null;
  category?: string | null;
  totalDistanceM: number;
  totalTimeS: number;
  tags?: Record<string, unknown> | null;
}

/** OSM tag keys worth surfacing in the popup, in display order. */
const DISPLAY_TAGS: readonly [string, string][] = [
  ['addr:housenumber', 'Address'],
  ['addr:street', ''],
  ['phone', 'Phone'],
  ['opening_hours', 'Hours'],
  ['operator', 'Operator'],
  ['website', 'Website'],
];

/** Minimal HTML-escape for text interpolated into the popup. */
function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** Render the optional OSM-tag detail rows. */
function tagRows(tags: Record<string, unknown> | null | undefined): string {
  if (tags == null) return '';
  const street = typeof tags['addr:street'] === 'string' ? (tags['addr:street'] as string) : '';
  const num =
    typeof tags['addr:housenumber'] === 'string' ? (tags['addr:housenumber'] as string) : '';
  const rows: string[] = [];
  if (street.length > 0) {
    const addr = num.length > 0 ? `${num} ${street}` : street;
    rows.push(`<div class="cf-popup__row"><span>Address</span><span>${escapeHtml(addr)}</span></div>`);
  }
  for (const [key, label] of DISPLAY_TAGS) {
    if (label.length === 0) continue; // street handled above
    const raw = tags[key];
    if (typeof raw !== 'string' || raw.length === 0) continue;
    rows.push(
      `<div class="cf-popup__row"><span>${escapeHtml(label)}</span><span>${escapeHtml(raw)}</span></div>`,
    );
  }
  return rows.join('');
}

/**
 * Build the popup HTML for a facility.
 *
 * @param d Facility popup data (identity, ranking, distance/time, tags).
 * @returns A self-contained HTML string for `Popup.setHTML`.
 */
export function buildFacilityPopupHTML(d: FacilityPopupData): string {
  const title =
    d.name != null && d.name.length > 0
      ? escapeHtml(d.name)
      : `Facility #${d.facilityId}`;
  const category = escapeHtml(humanizeCategory(d.category ?? 'other'));
  const distance = Math.round(d.totalDistanceM);
  const time = Math.round(d.totalTimeS);
  return (
    `<div class="cf-popup" data-testid="facility-popup">` +
    `<div class="cf-popup__title">${title}</div>` +
    `<div class="cf-popup__category">${category}</div>` +
    `<div class="cf-popup__row"><span>Rank</span><span>#${d.rank}</span></div>` +
    `<div class="cf-popup__row"><span>Distance</span><span>${distance} m</span></div>` +
    `<div class="cf-popup__row"><span>Travel time</span><span>${time} s</span></div>` +
    tagRows(d.tags) +
    `</div>`
  );
}
