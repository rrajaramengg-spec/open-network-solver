import { describe, it, expect, vi } from 'vitest';

// `FacilityLayer` imports the maplibre-gl runtime default for its `Popup`.
// The functions under test are pure, so stub the module to keep jsdom happy.
vi.mock('maplibre-gl', () => ({
  default: { Popup: class {} },
}));

import { buildFacilityFeatures } from '@/components/Map/FacilityLayer';
import { buildAccessLegFeatures } from '@/components/Map/AccessLegLayer';
import type { FacilityResult } from '@/types/routing';

function makeResult(over: Partial<FacilityResult> = {}): FacilityResult {
  return {
    facility_id: 1,
    rank: 1,
    total_cost: 100,
    total_distance_m: 123.4,
    total_time_s: 45.6,
    route_geojson: { type: 'LineString', coordinates: [[-71.05, 42.36], [-71.06, 42.37]] },
    name: 'Engine 7',
    category: 'fire_station',
    tags: { amenity: 'fire_station', 'addr:street': 'Main St' },
    facility_geojson: {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [-71.061, 42.371] },
      properties: { facility_id: 1 },
    },
    ...over,
  };
}

describe('buildFacilityFeatures', () => {
  it('sources the marker from the real facility_geojson Point', () => {
    const [f] = buildFacilityFeatures([makeResult()]);
    expect(f.geometry.type).toBe('Point');
    expect(f.geometry.coordinates).toEqual([-71.061, 42.371]);
    expect(f.properties.icon).toBe('cat-fire_station');
    expect(f.properties.category).toBe('fire_station');
    expect(f.properties.name).toBe('Engine 7');
  });

  it('serialises tags as JSON in the feature properties', () => {
    const [f] = buildFacilityFeatures([makeResult()]);
    expect(JSON.parse(f.properties.tags as string)).toEqual({
      amenity: 'fire_station',
      'addr:street': 'Main St',
    });
  });

  it('falls back to the route terminal coordinate when geometry is missing', () => {
    const [f] = buildFacilityFeatures([makeResult({ facility_geojson: null })]);
    expect(f.geometry.coordinates).toEqual([-71.06, 42.37]);
  });

  it('uses the fallback icon for an unknown/missing category', () => {
    const [f] = buildFacilityFeatures([
      makeResult({ category: undefined, facility_geojson: makeResult().facility_geojson }),
    ]);
    expect(f.properties.icon).toBe('cat-other');
  });

  it('skips results that have neither geometry nor a route', () => {
    const features = buildFacilityFeatures([
      makeResult({ facility_geojson: null, route_geojson: null }),
    ]);
    expect(features).toHaveLength(0);
  });
});

describe('buildAccessLegFeatures', () => {
  it('connects the route terminal to the facility point with a LineString', () => {
    const [leg] = buildAccessLegFeatures([makeResult()]);
    expect(leg.geometry.type).toBe('LineString');
    expect(leg.geometry.coordinates).toEqual([
      [-71.06, 42.37],
      [-71.061, 42.371],
    ]);
    expect(leg.properties.rank).toBe(1);
  });

  it('omits the leg when the endpoints coincide', () => {
    const coincident = makeResult({
      route_geojson: { type: 'LineString', coordinates: [[-71.061, 42.371]] },
    });
    expect(buildAccessLegFeatures([coincident])).toHaveLength(0);
  });

  it('omits the leg when geometry or route is missing', () => {
    expect(buildAccessLegFeatures([makeResult({ facility_geojson: null })])).toHaveLength(0);
    expect(buildAccessLegFeatures([makeResult({ route_geojson: null })])).toHaveLength(0);
  });
});
