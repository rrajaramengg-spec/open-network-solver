/**
 * Routing-service client (`POST /v1/closest-facility`).
 *
 * Adds `X-Request-Id` (UUID) on every call, parses both success and error
 * envelopes, and translates `error_code` values into user-friendly messages.
 *
 * Per design D5 + D6: the UI always calls the routing service for routing,
 * and NEVER the routing service for geocoding (that goes direct to Nominatim).
 */

import type {
  ClosestFacilityRequest,
  ClosestFacilityResponse,
  ErrorResponse,
  FacilityCategoriesResponse,
} from '@/types/routing';

/** Mapping of backend error_code → user-friendly message. */
const ERROR_MESSAGES: Record<string, string> = {
  invalid_request: 'Your request had invalid parameters. Please adjust and try again.',
  incident_off_graph:
    'The incident point is too far from a road (more than 250 m). Move the marker closer.',
  rate_limited: 'Too many requests — please slow down and try again in a moment.',
  routing_db_unavailable: 'The routing database is temporarily unavailable. Please retry.',
  routing_timeout:
    'The routing query took too long. Try a smaller buffer or fewer results.',
};

export class RoutingApiError extends Error {
  /**
   * @param code      The machine-readable error code from the backend envelope.
   * @param message   User-friendly message suitable for surfacing in a toast.
   * @param status    HTTP status code.
   * @param requestId Echoed request id for support correlation.
   */
  constructor(
    public readonly code: string,
    message: string,
    public readonly status: number,
    public readonly requestId: string,
  ) {
    super(message);
    this.name = 'RoutingApiError';
  }
}

/** Crypto-random UUID v4 (browser-native; no extra dependency). */
function newRequestId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) {
    return crypto.randomUUID();
  }
  // Last-resort fallback for ancient browsers; v4-ish.
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
  });
}

export interface ClosestFacilityClient {
  findClosest(
    request: ClosestFacilityRequest,
    options?: { signal?: AbortSignal },
  ): Promise<ClosestFacilityResponse>;
  /** Fetch the precomputed POI-category summary for the data-driven filter. */
  getFacilityCategories(
    options?: { signal?: AbortSignal },
  ): Promise<FacilityCategoriesResponse>;
}

export function createClosestFacilityClient(baseUrl: string): ClosestFacilityClient {
  const trimmed = baseUrl.replace(/\/+$/, '');
  return {
    async findClosest(request, options) {
      const requestId = newRequestId();
      const resp = await fetch(`${trimmed}/v1/closest-facility`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Request-Id': requestId,
          Accept: 'application/json',
        },
        body: JSON.stringify(request),
        signal: options?.signal,
      });

      if (resp.ok) {
        return (await resp.json()) as ClosestFacilityResponse;
      }

      // Try to parse the structured envelope; fall back to a generic message.
      let envelope: ErrorResponse | { detail?: ErrorResponse } | null = null;
      try {
        envelope = (await resp.json()) as ErrorResponse | { detail?: ErrorResponse };
      } catch {
        envelope = null;
      }
      const e: Partial<ErrorResponse> =
        envelope && 'error_code' in envelope
          ? (envelope as ErrorResponse)
          : (envelope as { detail?: ErrorResponse } | null)?.detail ?? {};
      const code =
        e?.error_code ??
        (resp.status === 429 ? 'rate_limited' : `http_${resp.status}`);
      const message =
        ERROR_MESSAGES[code] ?? e?.message ?? `Request failed (HTTP ${resp.status}).`;
      throw new RoutingApiError(code, message, resp.status, e?.request_id ?? requestId);
    },

    async getFacilityCategories(options) {
      const requestId = newRequestId();
      const resp = await fetch(`${trimmed}/v1/facility-categories`, {
        method: 'GET',
        headers: {
          'X-Request-Id': requestId,
          Accept: 'application/json',
        },
        signal: options?.signal,
      });

      if (resp.ok) {
        return (await resp.json()) as FacilityCategoriesResponse;
      }

      let envelope: ErrorResponse | { detail?: ErrorResponse } | null = null;
      try {
        envelope = (await resp.json()) as ErrorResponse | { detail?: ErrorResponse };
      } catch {
        envelope = null;
      }
      const e: Partial<ErrorResponse> =
        envelope && 'error_code' in envelope
          ? (envelope as ErrorResponse)
          : (envelope as { detail?: ErrorResponse } | null)?.detail ?? {};
      const code =
        e?.error_code ??
        (resp.status === 429 ? 'rate_limited' : `http_${resp.status}`);
      const message =
        ERROR_MESSAGES[code] ?? e?.message ?? `Request failed (HTTP ${resp.status}).`;
      throw new RoutingApiError(code, message, resp.status, e?.request_id ?? requestId);
    },
  };
}

/** Fetch `/readyz` for the runbook badge. Returns null on any error. */
export async function fetchReadyz(
  baseUrl: string,
  options?: { signal?: AbortSignal },
): Promise<import('@/types/routing').ReadyzResponse | null> {
  const trimmed = baseUrl.replace(/\/+$/, '');
  try {
    const resp = await fetch(`${trimmed}/readyz`, {
      signal: options?.signal,
      headers: { Accept: 'application/json' },
    });
    // /readyz returns 200 ok or 503 degraded — both have a JSON body.
    return (await resp.json()) as import('@/types/routing').ReadyzResponse;
  } catch {
    return null;
  }
}
