import { useEffect } from 'react';
import maplibregl, { type Map as MapLibreMap } from 'maplibre-gl';
import MaplibreGeocoder, {
  type CarmenGeojsonFeature,
} from '@maplibre/maplibre-gl-geocoder';
import '@maplibre/maplibre-gl-geocoder/dist/maplibre-gl-geocoder.css';
import { createPhotonGeocoderApi } from '@/api';
import { useSearchStore } from '@/store';

export interface GeocoderControlProps {
  mapRef: React.RefObject<MapLibreMap | null>;
  /** `true` once the map has loaded — gates control attachment. */
  mapReady: boolean;
  /** Photon base URL (browser-direct, design D6). */
  photonUrl: string;
}

/** Extract `[lon, lat]` from a Carmen result (geometry first, `center` fallback). */
function resultLngLat(result: CarmenGeojsonFeature): [number, number] | null {
  const geom = result.geometry;
  if (geom != null && geom.type === 'Point' && Array.isArray(geom.coordinates)) {
    const [lon, lat] = geom.coordinates;
    if (typeof lon === 'number' && typeof lat === 'number') return [lon, lat];
  }
  const center = (result as { center?: unknown }).center;
  if (Array.isArray(center) && typeof center[0] === 'number' && typeof center[1] === 'number') {
    return [center[0], center[1]];
  }
  return null;
}

/**
 * Mounts `@maplibre/maplibre-gl-geocoder` (ISC) as a map control backed by the
 * Photon adapter and forwards the selected `result` to `setIncident`
 * (design D5/D10). The control owns debounce, accessibility, keyboard
 * navigation, the suggestion list and map fly-to; we only wire selection and
 * the OSM (ODbL) attribution for the geocoding data.
 */
export function GeocoderControl({ mapRef, mapReady, photonUrl }: GeocoderControlProps): null {
  useEffect(() => {
    const map = mapRef.current;
    if (!mapReady || map === null) return;

    const geocoder = new MaplibreGeocoder(createPhotonGeocoderApi(photonUrl), {
      maplibregl,
      marker: false,
      showResultsWhileTyping: true,
      placeholder: 'Search address or place…',
      collapsed: false,
    });

    const onResult = (e: { result: CarmenGeojsonFeature }): void => {
      const lngLat = resultLngLat(e.result);
      if (lngLat === null) return;
      useSearchStore.getState().setIncident({ lat: lngLat[1], lon: lngLat[0] });
    };
    geocoder.on('result', onResult);

    map.addControl(geocoder, 'top-left');
    const attribution = new maplibregl.AttributionControl({
      customAttribution:
        'Address search © OpenStreetMap contributors (ODbL), via Photon',
    });
    map.addControl(attribution, 'bottom-right');

    return () => {
      map.removeControl(geocoder);
      map.removeControl(attribution);
    };
  }, [mapRef, mapReady, photonUrl]);

  return null;
}
