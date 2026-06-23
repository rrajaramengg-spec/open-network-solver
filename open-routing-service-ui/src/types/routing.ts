/**
 * Shared TypeScript types for the closest-facility API.
 *
 * Mirrors the Pydantic v2 models in
 * `open-routing-service/src/open_routing_service/models/api.py`.
 */

/** WGS-84 lat/lon pair (matches backend `IncidentPoint`). */
export interface IncidentPoint {
  /** Latitude in decimal degrees, [-90, 90]. */
  lat: number;
  /** Longitude in decimal degrees, [-180, 180]. */
  lon: number;
}

/** Cost mode for routing — distance (m) or travel time (s). */
export type CostMode = 'distance' | 'time';

/** Request body for `POST /v1/closest-facility`. */
export interface ClosestFacilityRequest {
  incident: IncidentPoint;
  /** Search radius in metres, [10, 50000]. */
  buffer_m: number;
  /** Number of results to return, [1, 10]. */
  k: number;
  cost_mode: CostMode;
  /** OSM tag filter (e.g. `{ "amenity": "fire_station" }`). */
  facility_filter: Record<string, string>;
}

/** GeoJSON LineString — the route from incident to a facility. */
export interface RouteGeoJSON {
  type: 'LineString';
  coordinates: [number, number][];
}

/** A single result row from `closest_facility()`. */
export interface FacilityResult {
  facility_id: number;
  rank: number;
  total_cost: number;
  total_distance_m: number;
  total_time_s: number;
  route_geojson: RouteGeoJSON | null;
}

/** 200 response body. */
export interface ClosestFacilityResponse {
  request_id: string;
  results: FacilityResult[];
  cache_hit: boolean;
}

/** Stable error-envelope shape for non-2xx responses. */
export interface ErrorResponse {
  request_id: string;
  error_code: string;
  message: string;
}

/** Curated facility-type options surfaced in the UI dropdown. */
export type FacilityType =
  | 'all'
  | 'fire_station'
  | 'hospital'
  | 'police'
  | 'school';

/** Readiness payload from the service `/readyz` endpoint. */
export interface ReadyzResponse {
  status: 'ok' | 'degraded';
  primary_pg: 'ok' | 'error';
  replica_pg: 'ok' | 'error';
  pgr_version: 'ok' | 'error';
  redis: 'ok' | 'degraded';
}
