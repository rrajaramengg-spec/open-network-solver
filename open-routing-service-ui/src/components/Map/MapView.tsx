import { useEffect } from 'react';
import { fitToPoints, useMapLibre } from '@/hooks';
import { useSearchStore } from '@/store';
import { IncidentLayer } from './IncidentLayer';
import { FacilityLayer } from './FacilityLayer';
import { RouteLayer } from './RouteLayer';
import type { Map as MapLibreMap } from 'maplibre-gl';

export interface MapViewProps {
  /** Tile URL template (raster basemap). */
  tileUrl: string;
}

/** Top-level map container. Wires the MapLibre instance to the Zustand store. */
export function MapView({ tileUrl }: MapViewProps): JSX.Element {
  const setIncident = useSearchStore((s) => s.setIncident);
  const results = useSearchStore((s) => s.results);
  const incident = useSearchStore((s) => s.incident);

  const { containerRef, mapRef } = useMapLibre({
    tileUrl,
    onClick: (lat, lon) => setIncident({ lat, lon }),
  });

  // Fit bounds to all features (incident + every route start/end) after each
  // successful search — spec scenario "fit-bounds-to-results on submit".
  useEffect(() => {
    const map = mapRef.current as MapLibreMap | null;
    if (map === null) return;
    const pts: [number, number][] = [];
    if (incident !== null) pts.push([incident.lon, incident.lat]);
    for (const r of results) {
      if (r.route_geojson !== null) {
        pts.push(...(r.route_geojson.coordinates as [number, number][]));
      }
    }
    fitToPoints(map, pts);
  }, [results, incident, mapRef]);

  return (
    <>
      <div
        ref={containerRef}
        data-testid="map-canvas"
        aria-label="Map"
        role="application"
        style={{ position: 'absolute', top: 0, right: 0, bottom: 0, left: 0 }}
      />
      <RouteLayer mapRef={mapRef} />
      <FacilityLayer mapRef={mapRef} />
      <IncidentLayer mapRef={mapRef} />
    </>
  );
}
