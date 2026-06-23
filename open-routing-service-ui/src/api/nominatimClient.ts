/**
 * Nominatim address-search client (browser-direct, design D6).
 *
 * The UI calls Nominatim itself — the routing service never proxies geocode
 * requests. This means a Nominatim outage degrades the address-search box
 * but does NOT take routing offline.
 *
 * Each call accepts an `AbortSignal` so the caller (the debounced
 * `<AddressSearch>` component) can cancel in-flight requests when the user
 * keeps typing.
 */

export interface NominatimResult {
  display_name: string;
  lat: string;
  lon: string;
  place_id?: number;
  type?: string;
  importance?: number;
}

export interface NominatimClient {
  /**
   * @param query   Free-text search string.
   * @param limit   Max results, default 5, max 10.
   * @param signal  Abort signal for in-flight cancellation.
   */
  search(
    query: string,
    options?: { limit?: number; signal?: AbortSignal },
  ): Promise<NominatimResult[]>;
}

export function createNominatimClient(baseUrl: string): NominatimClient {
  const trimmed = baseUrl.replace(/\/+$/, '');
  return {
    async search(query, options) {
      const trimmedQuery = query.trim();
      if (trimmedQuery.length < 3) return [];

      const limit = Math.min(Math.max(options?.limit ?? 5, 1), 10);
      const params = new URLSearchParams({
        q: trimmedQuery,
        format: 'jsonv2',
        addressdetails: '0',
        limit: String(limit),
      });

      const resp = await fetch(`${trimmed}/search?${params}`, {
        signal: options?.signal,
        headers: { Accept: 'application/json' },
      });
      if (!resp.ok) {
        // Soft failure — caller treats an empty list as "no results".
        return [];
      }
      const data = (await resp.json()) as NominatimResult[];
      return Array.isArray(data) ? data : [];
    },
  };
}
