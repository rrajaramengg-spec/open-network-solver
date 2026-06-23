/**
 * Zustand store for the closest-facility search UI.
 *
 * Per task 4.3:
 *   * state:   incident, bufferMeters, k, costMode, facilityType / facilityFilter,
 *              results, isLoading, lastError
 *   * actions: setIncident, setBuffer, setK, setCostMode, setFacilityType,
 *              setFacilityFilter, submit, clear
 *   * NO localStorage persistence in v1 (per the task description).
 *
 * The store depends on a `ClosestFacilityClient` injected at module load via
 * `configureSearchStore` so tests can substitute a deterministic fake.
 */

import { create } from 'zustand';
import {
  createClosestFacilityClient,
  RoutingApiError,
  type ClosestFacilityClient,
} from '@/api';
import type {
  CostMode,
  FacilityResult,
  FacilityType,
  IncidentPoint,
} from '@/types/routing';

export interface ToastError {
  code: string;
  message: string;
}

export interface SearchState {
  // -- inputs --
  incident: IncidentPoint | null;
  bufferMeters: number;
  k: number;
  costMode: CostMode;
  facilityType: FacilityType;
  facilityFilter: Record<string, string>;
  // -- async state --
  isLoading: boolean;
  lastError: ToastError | null;
  // -- results --
  results: FacilityResult[];
  cacheHit: boolean;
  selectedRank: number | null;
  // -- actions --
  setIncident: (p: IncidentPoint | null) => void;
  setBuffer: (metres: number) => void;
  setK: (k: number) => void;
  setCostMode: (m: CostMode) => void;
  setFacilityType: (t: FacilityType) => void;
  setFacilityFilter: (filter: Record<string, string>) => void;
  setSelectedRank: (rank: number | null) => void;
  submit: () => Promise<void>;
  clear: () => void;
  dismissError: () => void;
}

let _client: ClosestFacilityClient | null = null;

/** Inject the API client at module load (see `main.tsx`). Tests call this with a fake. */
export function configureSearchStore(client: ClosestFacilityClient): void {
  _client = client;
}

function ensureClient(): ClosestFacilityClient {
  if (_client === null) {
    throw new Error(
      'searchStore: client not configured — call configureSearchStore() at app start.',
    );
  }
  return _client;
}

function filterFromType(type: FacilityType): Record<string, string> {
  return type === 'all' ? {} : { amenity: type };
}

// Defaults per spec (`closest-facility` UI defaults):
//   buffer 500 ft → 152.4 m, K=1, distance, all facilities
const DEFAULT_BUFFER_M = 152.4;

export const useSearchStore = create<SearchState>((set, get) => ({
  incident: null,
  bufferMeters: DEFAULT_BUFFER_M,
  k: 1,
  costMode: 'distance',
  facilityType: 'all',
  facilityFilter: {},
  isLoading: false,
  lastError: null,
  results: [],
  cacheHit: false,
  selectedRank: null,

  setIncident: (p) => set({ incident: p, selectedRank: null }),
  setBuffer: (metres) =>
    set({ bufferMeters: Math.max(10, Math.min(50_000, metres)) }),
  setK: (k) => set({ k: Math.max(1, Math.min(10, Math.round(k))) }),
  setCostMode: (m) => set({ costMode: m }),
  setFacilityType: (t) => set({ facilityType: t, facilityFilter: filterFromType(t) }),
  setFacilityFilter: (filter) => set({ facilityFilter: filter }),
  setSelectedRank: (rank) => set({ selectedRank: rank }),

  async submit() {
    const state = get();
    if (state.incident === null) {
      set({
        lastError: {
          code: 'no_incident',
          message: 'Pick an incident on the map or search for an address first.',
        },
      });
      return;
    }
    set({ isLoading: true, lastError: null });
    try {
      const client = ensureClient();
      const resp = await client.findClosest({
        incident: state.incident,
        buffer_m: state.bufferMeters,
        k: state.k,
        cost_mode: state.costMode,
        facility_filter: state.facilityFilter,
      });
      set({
        results: resp.results,
        cacheHit: resp.cache_hit,
        selectedRank: resp.results.length > 0 ? resp.results[0].rank : null,
        isLoading: false,
      });
    } catch (err) {
      // Do NOT clear previous results — spec scenario "loading + error UX".
      const code = err instanceof RoutingApiError ? err.code : 'network_error';
      const message =
        err instanceof Error ? err.message : 'Request failed — try again.';
      set({
        isLoading: false,
        lastError: { code, message },
      });
    }
  },

  clear() {
    set({
      incident: null,
      results: [],
      cacheHit: false,
      selectedRank: null,
      lastError: null,
    });
  },

  dismissError() {
    set({ lastError: null });
  },
}));

/** Default-export the create-client helper so `main.tsx` can wire it. */
export function bootstrapClientFromEnv(env: ImportMetaEnv): void {
  configureSearchStore(createClosestFacilityClient(env.VITE_ROUTING_API_URL));
}
