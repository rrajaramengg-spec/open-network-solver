import { useEffect } from 'react';
import type { Map as MapLibreMap } from 'maplibre-gl';
import { useSearchStore } from '@/store';

export interface IncidentLayerProps {
  /** Stable ref pointing at the MapLibre instance owned by `useMapLibre`. */
  mapRef: React.RefObject<MapLibreMap | null>;
}

const SOURCE_ID = 'incident-source';
const LAYER_ID = 'incident-layer';

/** Renders the incident marker (a circle). */
export function IncidentLayer({ mapRef }: IncidentLayerProps): null {
  const incident = useSearchStore((s) => s.incident);

  useEffect(() => {
    const map = mapRef.current;
    if (map === null) return;

    const features =
      incident === null
        ? []
        : [
            {
              type: 'Feature' as const,
              geometry: {
                type: 'Point' as const,
                coordinates: [incident.lon, incident.lat],
              },
              properties: {},
            },
          ];

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
            'circle-radius': 8,
            'circle-color': '#0ea5e9',
            'circle-stroke-color': '#ffffff',
            'circle-stroke-width': 2,
          },
        });
      } else {
        src.setData({ type: 'FeatureCollection', features });
      }
    };

    if (map.isStyleLoaded()) apply();
    else map.once('load', apply);

    return undefined;
  }, [incident, mapRef]);

  return null;
}
