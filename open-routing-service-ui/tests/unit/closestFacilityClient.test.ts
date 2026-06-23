import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import {
  createClosestFacilityClient,
  RoutingApiError,
} from '@/api/closestFacilityClient';

const BASE = 'http://routing.test';

describe('closestFacilityClient', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn();
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('POSTs JSON to /v1/closest-facility with X-Request-Id', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({ request_id: 'abc', results: [], cache_hit: false }),
        { status: 200, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    const client = createClosestFacilityClient(BASE);
    const resp = await client.findClosest({
      incident: { lat: 0, lon: 0 },
      buffer_m: 100,
      k: 1,
      cost_mode: 'distance',
      facility_filter: {},
    });

    expect(resp.results).toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(`${BASE}/v1/closest-facility`);
    expect(init.method).toBe('POST');
    expect(init.headers['Content-Type']).toBe('application/json');
    expect(typeof init.headers['X-Request-Id']).toBe('string');
    expect(init.headers['X-Request-Id'].length).toBeGreaterThan(0);
  });

  it('throws RoutingApiError with code for envelope errors', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          detail: {
            request_id: 'rid',
            error_code: 'incident_off_graph',
            message: 'too far',
          },
        }),
        { status: 422, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    const client = createClosestFacilityClient(BASE);
    await expect(
      client.findClosest({
        incident: { lat: 0, lon: 0 },
        buffer_m: 100,
        k: 1,
        cost_mode: 'distance',
        facility_filter: {},
      }),
    ).rejects.toMatchObject({
      code: 'incident_off_graph',
      status: 422,
    });
  });

  it('translates 429 to rate_limited code', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response('{}', { status: 429, headers: { 'Content-Type': 'application/json' } }),
    );
    const client = createClosestFacilityClient(BASE);
    try {
      await client.findClosest({
        incident: { lat: 0, lon: 0 },
        buffer_m: 100,
        k: 1,
        cost_mode: 'distance',
        facility_filter: {},
      });
      expect.fail('expected RoutingApiError');
    } catch (err) {
      expect(err).toBeInstanceOf(RoutingApiError);
      expect((err as RoutingApiError).code).toBe('rate_limited');
    }
  });

  it('propagates AbortSignal', async () => {
    const controller = new AbortController();
    fetchMock.mockImplementationOnce(
      (_url: string, init: { signal?: AbortSignal }) =>
        new Promise((_resolve, reject) => {
          init.signal?.addEventListener('abort', () =>
            reject(new DOMException('aborted', 'AbortError')),
          );
        }),
    );
    const client = createClosestFacilityClient(BASE);
    const p = client.findClosest(
      {
        incident: { lat: 0, lon: 0 },
        buffer_m: 100,
        k: 1,
        cost_mode: 'distance',
        facility_filter: {},
      },
      { signal: controller.signal },
    );
    controller.abort();
    await expect(p).rejects.toBeInstanceOf(DOMException);
  });

  it('trims trailing slash on base URL', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify({ request_id: 'x', results: [], cache_hit: false }),
        { status: 200 },
      ),
    );
    const client = createClosestFacilityClient(`${BASE}///`);
    await client.findClosest({
      incident: { lat: 0, lon: 0 },
      buffer_m: 100,
      k: 1,
      cost_mode: 'distance',
      facility_filter: {},
    });
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE}/v1/closest-facility`);
  });
});
