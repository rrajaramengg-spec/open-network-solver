import { describe, it, expect, beforeEach } from 'vitest';
import {
  configureSearchStore,
  useSearchStore,
} from '@/store/searchStore';
import type { ClosestFacilityClient } from '@/api';
import { RoutingApiError } from '@/api';

function makeFakeClient(impl: ClosestFacilityClient['findClosest']): ClosestFacilityClient {
  return { findClosest: impl };
}

function resetStore() {
  useSearchStore.setState({
    incident: null,
    bufferMeters: 152.4,
    k: 1,
    costMode: 'distance',
    facilityType: 'all',
    facilityFilter: {},
    isLoading: false,
    lastError: null,
    results: [],
    cacheHit: false,
    selectedRank: null,
  });
}

describe('searchStore', () => {
  beforeEach(() => {
    resetStore();
  });

  it('setIncident updates the incident and clears selection', () => {
    useSearchStore.getState().setSelectedRank(3);
    useSearchStore.getState().setIncident({ lat: 32.7, lon: -117.1 });
    expect(useSearchStore.getState().incident).toEqual({ lat: 32.7, lon: -117.1 });
    expect(useSearchStore.getState().selectedRank).toBeNull();
  });

  it('setBuffer clamps to [10, 50000]', () => {
    useSearchStore.getState().setBuffer(0);
    expect(useSearchStore.getState().bufferMeters).toBe(10);
    useSearchStore.getState().setBuffer(99999);
    expect(useSearchStore.getState().bufferMeters).toBe(50000);
  });

  it('setK clamps to [1, 10] and rounds', () => {
    useSearchStore.getState().setK(0);
    expect(useSearchStore.getState().k).toBe(1);
    useSearchStore.getState().setK(99);
    expect(useSearchStore.getState().k).toBe(10);
    useSearchStore.getState().setK(3.6);
    expect(useSearchStore.getState().k).toBe(4);
  });

  it('setFacilityType maps to amenity filter', () => {
    useSearchStore.getState().setFacilityType('hospital');
    expect(useSearchStore.getState().facilityFilter).toEqual({ amenity: 'hospital' });
    useSearchStore.getState().setFacilityType('all');
    expect(useSearchStore.getState().facilityFilter).toEqual({});
  });

  it('submit() errors when no incident is set', async () => {
    configureSearchStore(makeFakeClient(async () => ({
      request_id: 'x', results: [], cache_hit: false,
    })));
    await useSearchStore.getState().submit();
    expect(useSearchStore.getState().lastError?.code).toBe('no_incident');
  });

  it('submit() populates results and selects rank 1', async () => {
    configureSearchStore(
      makeFakeClient(async () => ({
        request_id: 'x',
        cache_hit: false,
        results: [
          {
            facility_id: 1,
            rank: 1,
            total_cost: 100,
            total_distance_m: 100,
            total_time_s: 10,
            route_geojson: null,
          },
          {
            facility_id: 2,
            rank: 2,
            total_cost: 200,
            total_distance_m: 200,
            total_time_s: 20,
            route_geojson: null,
          },
        ],
      })),
    );
    useSearchStore.getState().setIncident({ lat: 0, lon: 0 });
    await useSearchStore.getState().submit();
    expect(useSearchStore.getState().results).toHaveLength(2);
    expect(useSearchStore.getState().selectedRank).toBe(1);
    expect(useSearchStore.getState().isLoading).toBe(false);
    expect(useSearchStore.getState().lastError).toBeNull();
  });

  it('submit() does NOT clear previous results on error (spec: error toast UX)', async () => {
    useSearchStore.setState({
      results: [
        {
          facility_id: 99,
          rank: 1,
          total_cost: 0,
          total_distance_m: 0,
          total_time_s: 0,
          route_geojson: null,
        },
      ],
      incident: { lat: 0, lon: 0 },
    });
    configureSearchStore(
      makeFakeClient(async () => {
        throw new RoutingApiError('routing_timeout', 'too slow', 504, 'rid');
      }),
    );
    await useSearchStore.getState().submit();
    expect(useSearchStore.getState().results).toHaveLength(1); // PRESERVED
    expect(useSearchStore.getState().lastError?.code).toBe('routing_timeout');
  });

  it('clear() resets results + incident + error', () => {
    useSearchStore.setState({
      results: [
        {
          facility_id: 1,
          rank: 1,
          total_cost: 0,
          total_distance_m: 0,
          total_time_s: 0,
          route_geojson: null,
        },
      ],
      incident: { lat: 0, lon: 0 },
      lastError: { code: 'x', message: 'm' },
    });
    useSearchStore.getState().clear();
    expect(useSearchStore.getState().results).toEqual([]);
    expect(useSearchStore.getState().incident).toBeNull();
    expect(useSearchStore.getState().lastError).toBeNull();
  });

  it('dismissError() clears the error', () => {
    useSearchStore.setState({ lastError: { code: 'x', message: 'y' } });
    useSearchStore.getState().dismissError();
    expect(useSearchStore.getState().lastError).toBeNull();
  });
});
