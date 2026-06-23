import { useEffect } from 'react';
import type { Map as MapLibreMap } from 'maplibre-gl';
import { useSearchStore } from '@/store';
import { rankColor } from '@/utils/rankColors';
import type { FacilityResult } from '@/types/routing';

export interface AccessLegLayerProps {
  mapRef: React.RefObject<MapLibreMap | null>;
}

const SOURCE_ID = 'access-legs-source';
const LAYER_ID = 'access-legs-layer';

/** Squared-distance threshold (deg²) below which the access leg is omitted. */
const MIN_LEG_SQ = 1e-12;

type LineFeature = GeoJSON.Feature<GeoJSON.LineString, Record<string, unknown>>;

/**
 * Build dashed "access leg" connectors from each route's terminal coordinate
 * (the snapped graph vertex) to the facility's real point geometry, which can
 * be up to the snap radius away (design D9). Client-side only, from data the
 * results already carry.
 *
 * @param results The ranked closest-facility results.
 * @returns One LineString feature per result that has both endpoints and a
 *          non-degenerate gap between them.
 */
export function buildAccessLegFeatures(results: FacilityResult[]): LineFeature[] {
  const features: LineFeature[] = [];
  for (const r of results) {
    const routeEnd = routeTerminal(r);
    const facility = facilityPoint(r);
    if (routeEnd === null || facility === null) continue;
    const dx = routeEnd[0] - facility[0];
    const dy = routeEnd[1] - facility[1];
    if (dx * dx + dy * dy < MIN_LEG_SQ) continue; // identical points — no leg
    features.push({
      type: 'Feature',
      geometry: { type: 'LineString', coordinates: [routeEnd, facility] },
      properties: { rank: r.rank, color: rankColor(r.rank) },
    });
  }
  return features;
}

function routeTerminal(r: FacilityResult): [number, number] | null {
  const coords = r.route_geojson?.coordinates;
  if (coords != null && coords.length > 0) {
    return coords[coords.length - 1] as [number, number];
  }
  return null;
}

function facilityPoint(r: FacilityResult): [number, number] | null {
  const geo = r.facility_geojson;
  if (geo != null && Array.isArray(geo.geometry?.coordinates)) {
    const [lon, lat] = geo.geometry.coordinates;
    if (typeof lon === 'number' && typeof lat === 'number') return [lon, lat];
  }
  return null;
}

/** Renders the dashed access-leg connectors beneath the facility markers. */
export function AccessLegLayer({ mapRef }: AccessLegLayerProps): null {
  const results = useSearchStore((s) => s.results);

  useEffect(() => {
    const map = mapRef.current;
    if (map === null) return;

    const features = buildAccessLegFeatures(results);

    const apply = () => {
      const src = map.getSource(SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
      if (src === undefined) {
        map.addSource(SOURCE_ID, {
          type: 'geojson',
          data: { type: 'FeatureCollection', features },
        });
        map.addLayer({
          id: LAYER_ID,
          type: 'line',
          source: SOURCE_ID,
          paint: {
            'line-color': ['get', 'color'],
            'line-width': 2,
            'line-opacity': 0.9,
            'line-dasharray': [2, 2],
          },
        });
      } else {
        src.setData({ type: 'FeatureCollection', features });
      }
    };

    if (map.isStyleLoaded()) apply();
    else map.once('load', apply);

    return undefined;
  }, [results, mapRef]);

  return null;
}
