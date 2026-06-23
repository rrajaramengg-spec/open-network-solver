import { useEffect } from 'react';
import type { Map as MapLibreMap } from 'maplibre-gl';
import { useSearchStore } from '@/store';
import { rankColor } from '@/utils/rankColors';

export interface RouteLayerProps {
  mapRef: React.RefObject<MapLibreMap | null>;
}

const SOURCE_ID = 'routes-source';
const LAYER_ID = 'routes-layer';
const SELECTED_LAYER_ID = 'routes-selected-layer';

/** Renders one polyline per result rank with the spec colour palette. */
export function RouteLayer({ mapRef }: RouteLayerProps): null {
  const results = useSearchStore((s) => s.results);
  const selectedRank = useSearchStore((s) => s.selectedRank);

  useEffect(() => {
    const map = mapRef.current;
    if (map === null) return;

    const features = results
      .filter((r) => r.route_geojson !== null)
      .map((r) => ({
        type: 'Feature' as const,
        geometry: r.route_geojson!,
        properties: {
          rank: r.rank,
          color: rankColor(r.rank),
        },
      }));

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
            'line-width': 4,
            'line-opacity': 0.7,
          },
        });
        map.addLayer({
          id: SELECTED_LAYER_ID,
          type: 'line',
          source: SOURCE_ID,
          filter: ['==', ['get', 'rank'], -1],
          paint: {
            'line-color': ['get', 'color'],
            'line-width': 8,
            'line-opacity': 1,
          },
        });
      } else {
        src.setData({ type: 'FeatureCollection', features });
      }

      // Highlight selected route via filter mutation.
      if (map.getLayer(SELECTED_LAYER_ID) !== undefined) {
        map.setFilter(SELECTED_LAYER_ID, [
          '==',
          ['get', 'rank'],
          selectedRank ?? -1,
        ]);
      }
    };

    if (map.isStyleLoaded()) apply();
    else map.once('load', apply);

    return undefined;
  }, [results, selectedRank, mapRef]);

  return null;
}
