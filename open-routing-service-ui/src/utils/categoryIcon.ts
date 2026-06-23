/**
 * Facility-category → map-icon glue.
 *
 * Maps the backend's `facility_category(tags)` taxonomy onto a curated subset
 * of the **Maki** icon set (Mapbox, public-domain CC0) and registers each as a
 * MapLibre image so a symbol layer can resolve `icon-image` per facility
 * category. Categories with no curated glyph fall back to the generic
 * `marker` pin (design D7).
 *
 * Icons are vendored from the `@mapbox/maki` package (CC0 1.0 Universal) and
 * imported as raw SVG strings via Vite's `?raw` loader.
 */

import type { Map as MapLibreMap } from 'maplibre-gl';

import fireStationSvg from '@mapbox/maki/icons/fire-station.svg?raw';
import hospitalSvg from '@mapbox/maki/icons/hospital.svg?raw';
import policeSvg from '@mapbox/maki/icons/police.svg?raw';
import schoolSvg from '@mapbox/maki/icons/school.svg?raw';
import collegeSvg from '@mapbox/maki/icons/college.svg?raw';
import pharmacySvg from '@mapbox/maki/icons/pharmacy.svg?raw';
import fuelSvg from '@mapbox/maki/icons/fuel.svg?raw';
import bankSvg from '@mapbox/maki/icons/bank.svg?raw';
import restaurantSvg from '@mapbox/maki/icons/restaurant.svg?raw';
import fastFoodSvg from '@mapbox/maki/icons/fast-food.svg?raw';
import worshipSvg from '@mapbox/maki/icons/place-of-worship.svg?raw';
import townHallSvg from '@mapbox/maki/icons/town-hall.svg?raw';
import librarySvg from '@mapbox/maki/icons/library.svg?raw';
import parkingSvg from '@mapbox/maki/icons/parking.svg?raw';
import doctorSvg from '@mapbox/maki/icons/doctor.svg?raw';
import dentistSvg from '@mapbox/maki/icons/dentist.svg?raw';
import veterinarySvg from '@mapbox/maki/icons/veterinary.svg?raw';
import grocerySvg from '@mapbox/maki/icons/grocery.svg?raw';
import convenienceSvg from '@mapbox/maki/icons/convenience.svg?raw';
import busSvg from '@mapbox/maki/icons/bus.svg?raw';
import markerSvg from '@mapbox/maki/icons/marker.svg?raw';

/** Image-id prefix for category pins registered on the map. */
const ICON_PREFIX = 'cat-';

/** Category key used when a facility has no curated glyph. */
export const FALLBACK_CATEGORY = 'other';

/**
 * Curated category → raw Maki SVG map. Keys are values produced by the backend
 * `facility_category(tags)` helper (lowercased OSM `amenity`/`shop`/`tourism`
 * values, plus `ambulance_station` and the `other` fallback). Several related
 * categories intentionally share a glyph.
 */
const CATEGORY_SVG: Readonly<Record<string, string>> = {
  fire_station: fireStationSvg,
  hospital: hospitalSvg,
  clinic: hospitalSvg,
  ambulance_station: hospitalSvg,
  doctors: doctorSvg,
  dentist: dentistSvg,
  veterinary: veterinarySvg,
  pharmacy: pharmacySvg,
  police: policeSvg,
  school: schoolSvg,
  kindergarten: schoolSvg,
  college: collegeSvg,
  university: collegeSvg,
  library: librarySvg,
  fuel: fuelSvg,
  charging_station: fuelSvg,
  bank: bankSvg,
  bureau_de_change: bankSvg,
  restaurant: restaurantSvg,
  cafe: restaurantSvg,
  fast_food: fastFoodSvg,
  place_of_worship: worshipSvg,
  townhall: townHallSvg,
  community_centre: townHallSvg,
  parking: parkingSvg,
  supermarket: grocerySvg,
  convenience: convenienceSvg,
  bus_station: busSvg,
  [FALLBACK_CATEGORY]: markerSvg,
};

/**
 * Resolve the registered MapLibre image id for a facility category.
 *
 * Always returns an id that {@link registerCategoryIcons} registers — unknown
 * or missing categories resolve to the fallback `marker` pin.
 *
 * @param category Facility category (case-insensitive), or null/undefined.
 * @returns The `cat-<key>` image id.
 */
export function iconIdForCategory(category: string | null | undefined): string {
  const key = (category ?? FALLBACK_CATEGORY).toLowerCase();
  return ICON_PREFIX + (key in CATEGORY_SVG ? key : FALLBACK_CATEGORY);
}

/**
 * Human-readable label for a category (e.g. `fire_station` → `Fire station`).
 * The `'all'` sentinel renders as `All facilities`.
 *
 * @param category Category string.
 * @returns Title-cased, space-separated label.
 */
export function humanizeCategory(category: string): string {
  if (category === 'all') return 'All facilities';
  return category
    .split('_')
    .map((w) => (w.length > 0 ? w[0].toUpperCase() + w.slice(1) : w))
    .join(' ');
}

/** Compose a transparent 40×40 image of the Maki glyph in white, centred. */
function glyphSvg(makiSvg: string): string {
  const inner = makiSvg.replace(/<\?xml[^>]*\?>/i, '').trim();
  // Maki icons use a 15×15 viewBox; scale ~1.7 and centre inside 40×40 so the
  // white glyph reads clearly on top of the rank-coloured disc.
  return (
    '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 40 40">' +
    '<g transform="translate(7.25 7.25) scale(1.7)" fill="#ffffff">' +
    inner +
    '</g>' +
    '</svg>'
  );
}

/** Register a single category glyph image, resolving once it has loaded. */
function registerOne(map: MapLibreMap, id: string, makiSvg: string): Promise<void> {
  if (typeof window === 'undefined' || typeof window.Image === 'undefined') {
    return Promise.resolve();
  }
  if (map.hasImage(id)) return Promise.resolve();
  const url =
    'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(glyphSvg(makiSvg));
  return new Promise<void>((resolve) => {
    const img = new window.Image(40, 40);
    img.onload = () => {
      if (!map.hasImage(id)) map.addImage(id, img, { pixelRatio: 2 });
      resolve();
    };
    img.onerror = () => resolve();
    img.src = url;
  });
}

/** Tracks in-flight lazy registrations so `styleimagemissing` doesn't re-fire. */
const _inflight = new Set<string>();

/**
 * Lazily register a single category glyph by its registered image id (e.g.
 * `cat-parking`). Wire this to MapLibre's `styleimagemissing` event so a
 * symbol layer resolves its icon even when it was added before
 * {@link registerCategoryIcons} finished loading. Idempotent and race-safe.
 *
 * @param map The MapLibre map instance.
 * @param id  The missing image id (must start with the `cat-` prefix).
 */
export function ensureCategoryIcon(map: MapLibreMap, id: string): void {
  if (!id.startsWith(ICON_PREFIX) || map.hasImage(id) || _inflight.has(id)) return;
  const key = id.slice(ICON_PREFIX.length);
  const svg = CATEGORY_SVG[key] ?? CATEGORY_SVG[FALLBACK_CATEGORY];
  _inflight.add(id);
  void registerOne(map, id, svg).finally(() => _inflight.delete(id));
}

/**
 * Register every curated category pin on the map (idempotent).
 *
 * Safe to call repeatedly — already-registered images are skipped. In
 * environments without a DOM `Image` (e.g. jsdom unit tests) this is a no-op.
 *
 * @param map The MapLibre map instance.
 */
export async function registerCategoryIcons(map: MapLibreMap): Promise<void> {
  await Promise.all(
    Object.entries(CATEGORY_SVG).map(([key, svg]) =>
      registerOne(map, ICON_PREFIX + key, svg),
    ),
  );
}
