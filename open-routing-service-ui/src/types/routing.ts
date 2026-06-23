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

/** GeoJSON Point geometry (RFC 7946) for the facility location. */
export interface FacilityGeometry {
  type: 'Point';
  coordinates: [number, number];
}

/**
 * GeoJSON `Feature` wrapping the facility point + its properties.
 *
 * `properties` carries `facility_id`, `name`, `category` and the raw OSM
 * `tags` so the map can render a category-specific marker and a click-through
 * popup directly from the feature.
 */
export interface FacilityFeature {
  type: 'Feature';
  geometry: FacilityGeometry;
  properties: Record<string, unknown>;
}

/**
 * A single result row from `closest_facility()`.
 *
 * The facility identity/geometry fields (`name`, `category`, `tags`,
 * `facility_geojson`) are additive — older payloads may omit them, so they are
 * optional on the client and rendered with fallbacks.
 */
export interface FacilityResult {
  facility_id: number;
  rank: number;
  total_cost: number;
  total_distance_m: number;
  total_time_s: number;
  route_geojson: RouteGeoJSON | null;
  /** Human-readable facility name (OSM `name` tag), or null. */
  name?: string | null;
  /** Stable lowercase category from `facility_category(tags)` (e.g. `hospital`). */
  category?: string;
  /** Raw OSM tags for the facility. */
  tags?: Record<string, unknown>;
  /** GeoJSON Feature (Point) for the facility, or null when unavailable. */
  facility_geojson?: FacilityFeature | null;
}

/** 200 response body. */
export interface ClosestFacilityResponse {
  request_id: string;
  results: FacilityResult[];
  cache_hit: boolean;
}

/** One `{category, count}` row of the live POI catalog summary. */
export interface FacilityCategory {
  category: string;
  count: number;
}

/** 200 response body for `GET /v1/facility-categories` (precomputed summary). */
export interface FacilityCategoriesResponse {
  request_id: string;
  categories: FacilityCategory[];
  total: number;
  cache_hit: boolean;
}

/** Stable error-envelope shape for non-2xx responses. */
export interface ErrorResponse {
  request_id: string;
  error_code: string;
  message: string;
}

/**
 * Facility-type filter surfaced in the UI dropdown.
 *
 * Data-driven: the `ALL_FACILITIES` sentinel (`'all'`) means no filter, and any
 * other value is a category string returned by `GET /v1/facility-categories`
 * (e.g. `hospital`, `fire_station`, `pharmacy`). Kept as a widened `string` so
 * the dropdown surfaces every ingested POI category, not a hardcoded subset.
 */
export type FacilityType = string;

/** Sentinel `FacilityType` value meaning "no category filter". */
export const ALL_FACILITIES = 'all';

/** Readiness payload from the service `/readyz` endpoint. */
export interface ReadyzResponse {
  status: 'ok' | 'degraded';
  primary_pg: 'ok' | 'error';
  replica_pg: 'ok' | 'error';
  pgr_version: 'ok' | 'error';
  redis: 'ok' | 'degraded';
}
