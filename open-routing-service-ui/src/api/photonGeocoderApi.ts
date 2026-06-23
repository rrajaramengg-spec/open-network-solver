/**
 * Photon → `maplibre-gl-geocoder` adapter (design D5/D6).
 *
 * `@maplibre/maplibre-gl-geocoder` is provider-agnostic: it drives an input,
 * owns debounce / accessibility / keyboard-navigation / the suggestion list,
 * and calls a `geocoderApi.forwardGeocode(config)` that must resolve to
 * **Carmen GeoJSON**. This module is the only glue we own — it calls
 * [Photon](https://github.com/komoot/photon) (Apache-2.0, search-as-you-type
 * over OSM data) and maps its GeoJSON `FeatureCollection` onto Carmen GeoJSON.
 *
 * Calls are browser-direct (design D6 preserved); Photon is OSM/Nominatim-
 * derived, so the data provenance is unchanged. On any non-2xx response or
 * malformed body the adapter returns an empty feature list so the control
 * degrades gracefully (the map-click flow keeps working).
 */

import type {
  CarmenGeojsonFeature,
  MaplibreGeocoderApi,
  MaplibreGeocoderApiConfig,
  MaplibreGeocoderFeatureResults,
} from '@maplibre/maplibre-gl-geocoder';

/** A single Photon result feature (subset of the fields we consume). */
interface PhotonFeature {
  type: 'Feature';
  geometry: { type: 'Point'; coordinates: [number, number] };
  properties: {
    name?: string;
    housenumber?: string;
    street?: string;
    city?: string;
    district?: string;
    state?: string;
    postcode?: string;
    country?: string;
    osm_id?: number;
    osm_type?: string;
    osm_key?: string;
    osm_value?: string;
    type?: string;
    extent?: [number, number, number, number];
    [key: string]: unknown;
  };
}

/** Options forwarded to Photon (and the underlying `fetch`). */
export interface PhotonForwardOptions {
  /** Cancellation signal from the caller. */
  signal?: AbortSignal;
  /** Max suggestions to request. */
  limit?: number;
  /** Location-bias point `[lon, lat]` — nearer results score higher. */
  proximity?: [number, number];
  /** IETF language tag for result text. */
  language?: string;
}

/** Compose a human-readable single-line label from Photon properties. */
function placeName(p: PhotonFeature['properties']): string {
  const line1 =
    p.housenumber != null && p.street != null
      ? `${p.housenumber} ${p.street}`
      : (p.street ?? p.name ?? '');
  const parts = [
    line1 || p.name,
    p.city ?? p.district,
    p.state,
    p.postcode,
    p.country,
  ].filter((s): s is string => typeof s === 'string' && s.length > 0);
  return parts.join(', ');
}

/** Map one Photon feature onto a Carmen GeoJSON feature. */
function toCarmen(f: PhotonFeature): CarmenGeojsonFeature {
  const center = f.geometry.coordinates;
  const name = placeName(f.properties);
  return {
    type: 'Feature',
    id: `photon.${f.properties.osm_type ?? ''}${f.properties.osm_id ?? ''}`,
    geometry: f.geometry,
    // Carmen extension fields consumed by the geocoder control:
    place_name: name,
    place_type: ['place'],
    center,
    text: f.properties.name ?? name,
    bbox: f.properties.extent,
    properties: f.properties,
  } as unknown as CarmenGeojsonFeature;
}

/**
 * Query Photon and map the response to Carmen GeoJSON features.
 *
 * @param baseUrl Photon base URL (no trailing slash needed), e.g.
 *                `https://photon.komoot.io`.
 * @param query   The user's search string.
 * @param options Forward/cancellation options (incl. `AbortSignal`).
 * @returns Carmen GeoJSON features, or `[]` on error / malformed body.
 */
export async function fetchPhotonFeatures(
  baseUrl: string,
  query: string,
  options: PhotonForwardOptions = {},
): Promise<CarmenGeojsonFeature[]> {
  const trimmed = baseUrl.replace(/\/+$/, '');
  const params = new URLSearchParams({ q: query });
  if (options.limit != null) params.set('limit', String(options.limit));
  if (options.language != null) params.set('lang', options.language);
  if (options.proximity != null) {
    params.set('lon', String(options.proximity[0]));
    params.set('lat', String(options.proximity[1]));
  }

  try {
    const resp = await fetch(`${trimmed}/api?${params.toString()}`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      signal: options.signal,
    });
    if (!resp.ok) return [];
    const body = (await resp.json()) as { features?: unknown };
    if (!Array.isArray(body.features)) return [];
    return (body.features as PhotonFeature[])
      .filter((f) => f?.geometry?.type === 'Point' && Array.isArray(f.geometry.coordinates))
      .map(toCarmen);
  } catch {
    // Aborted, network failure, or malformed body — degrade to no suggestions.
    return [];
  }
}

/**
 * Build a `MaplibreGeocoderApi` backed by Photon for use with
 * `new MaplibreGeocoder(api, { maplibregl })`.
 *
 * @param baseUrl Photon base URL.
 * @returns A geocoder API whose `forwardGeocode` returns Carmen GeoJSON.
 */
export function createPhotonGeocoderApi(baseUrl: string): MaplibreGeocoderApi {
  return {
    async forwardGeocode(
      config: MaplibreGeocoderApiConfig,
    ): Promise<MaplibreGeocoderFeatureResults> {
      const query = typeof config.query === 'string' ? config.query : '';
      const features =
        query.length === 0
          ? []
          : await fetchPhotonFeatures(baseUrl, query, {
              limit: config.limit,
              language: config.language,
              proximity:
                Array.isArray(config.proximity) && config.proximity.length === 2
                  ? [config.proximity[0], config.proximity[1]]
                  : undefined,
            });
      return { type: 'FeatureCollection', features };
    },
  };
}
