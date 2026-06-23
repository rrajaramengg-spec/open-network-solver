import { useEffect } from 'react';
import type { Map as MapLibreMap } from 'maplibre-gl';
import { useSearchStore } from '@/store';
import { rankColor } from '@/utils/rankColors';

export interface FacilityLayerProps {
  mapRef: React.RefObject<MapLibreMap | null>;
}

const SOURCE_ID = 'facilities-source';
const LAYER_ID = 'facilities-layer';
const LABEL_LAYER_ID = 'facilities-labels';

/** Renders ranked facility markers with `1..K` badges. */
export function FacilityLayer({ mapRef }: FacilityLayerProps): null {
  const results = useSearchStore((s) => s.results);

  useEffect(() => {
    const map = mapRef.current;
    if (map === null) return;

    const features = results
      .filter((r) => r.route_geojson !== null && r.route_geojson.coordinates.length > 0)
      .map((r) => {
        const coords = r.route_geojson!.coordinates;
        const last = coords[coords.length - 1] as [number, number];
        return {
          type: 'Feature' as const,
          geometry: { type: 'Point' as const, coordinates: last },
          properties: {
            rank: r.rank,
            color: rankColor(r.rank),
            facility_id: r.facility_id,
          },
        };
      });

    const apply = () => {
      const src = map.getSource(SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
      if (src === undefined) {
        map.addSource(SOURCE_ID, {
          type: 'geojson',
          data: { type: 'FeatureCollection', features },
        });
        map.addLayer({
          id: LAYER_ID,
          type: 'circle',
          source: SOURCE_ID,
          paint: {
            'circle-radius': 12,
            'circle-color': ['get', 'color'],
            'circle-stroke-color': '#ffffff',
            'circle-stroke-width': 2,
          },
        });
        map.addLayer({
          id: LABEL_LAYER_ID,
          type: 'symbol',
          source: SOURCE_ID,
          layout: {
            'text-field': ['to-string', ['get', 'rank']],
            'text-size': 12,
            'text-allow-overlap': true,
          },
          paint: {
            'text-color': '#ffffff',
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
