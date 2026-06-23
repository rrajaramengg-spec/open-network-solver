import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  createPhotonGeocoderApi,
  fetchPhotonFeatures,
} from '@/api/photonGeocoderApi';

const PHOTON_URL = 'https://photon.example';

function photonResponse(features: unknown[]): Response {
  return {
    ok: true,
    json: async () => ({ type: 'FeatureCollection', features }),
  } as unknown as Response;
}

const SAMPLE = {
  type: 'Feature',
  geometry: { type: 'Point', coordinates: [-71.06, 42.36] },
  properties: {
    name: 'City Hall',
    housenumber: '1',
    street: 'Main St',
    city: 'Boston',
    state: 'MA',
    postcode: '02201',
    country: 'USA',
    osm_id: 123,
    osm_type: 'N',
  },
};

describe('fetchPhotonFeatures', () => {
  beforeEach(() => vi.restoreAllMocks());
  afterEach(() => vi.restoreAllMocks());

  it('maps a Photon FeatureCollection to Carmen GeoJSON features', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(photonResponse([SAMPLE])));
    const [f] = await fetchPhotonFeatures(PHOTON_URL, 'city hall');

    expect(f.type).toBe('Feature');
    expect(f.geometry).toEqual({ type: 'Point', coordinates: [-71.06, 42.36] });
    // Carmen extension fields the geocoder control reads:
    const c = f as unknown as {
      place_name: string;
      center: [number, number];
      text: string;
      id: string;
    };
    expect(c.center).toEqual([-71.06, 42.36]);
    expect(c.text).toBe('City Hall');
    expect(c.id).toBe('photon.N123');
    expect(c.place_name).toContain('1 Main St');
    expect(c.place_name).toContain('Boston');
    expect(c.place_name).toContain('MA');
    expect(c.place_name).toContain('02201');
    expect(c.place_name).toContain('USA');
  });

  it('builds the query URL with limit and proximity bias', async () => {
    const mock = vi.fn().mockResolvedValue(photonResponse([SAMPLE]));
    vi.stubGlobal('fetch', mock);
    await fetchPhotonFeatures(PHOTON_URL, 'main st', {
      limit: 5,
      proximity: [-71.0, 42.3],
      language: 'en',
    });
    const url = mock.mock.calls[0][0] as string;
    expect(url).toContain('/api?');
    expect(url).toContain('q=main+st');
    expect(url).toContain('limit=5');
    expect(url).toContain('lon=-71');
    expect(url).toContain('lat=42.3');
    expect(url).toContain('lang=en');
  });

  it('forwards the AbortSignal to fetch', async () => {
    const mock = vi.fn().mockResolvedValue(photonResponse([SAMPLE]));
    vi.stubGlobal('fetch', mock);
    const controller = new AbortController();
    await fetchPhotonFeatures(PHOTON_URL, 'x', { signal: controller.signal });
    expect(mock.mock.calls[0][1]).toMatchObject({ signal: controller.signal });
  });

  it('returns an empty list on a non-2xx response', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false } as Response));
    expect(await fetchPhotonFeatures(PHOTON_URL, 'x')).toEqual([]);
  });

  it('returns an empty list on a malformed body', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) } as unknown as Response),
    );
    expect(await fetchPhotonFeatures(PHOTON_URL, 'x')).toEqual([]);
  });

  it('returns an empty list when fetch rejects (abort/network)', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('aborted')));
    expect(await fetchPhotonFeatures(PHOTON_URL, 'x')).toEqual([]);
  });

  it('skips non-Point features', async () => {
    const poly = { type: 'Feature', geometry: { type: 'Polygon', coordinates: [] }, properties: {} };
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(photonResponse([poly])));
    expect(await fetchPhotonFeatures(PHOTON_URL, 'x')).toEqual([]);
  });
});

describe('createPhotonGeocoderApi', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('forwardGeocode returns a Carmen FeatureCollection', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(photonResponse([SAMPLE])));
    const api = createPhotonGeocoderApi(PHOTON_URL);
    const res = await api.forwardGeocode({ query: 'city hall' });
    expect(res.type).toBe('FeatureCollection');
    expect(res.features).toHaveLength(1);
  });

  it('forwardGeocode short-circuits an empty query without calling fetch', async () => {
    const mock = vi.fn();
    vi.stubGlobal('fetch', mock);
    const api = createPhotonGeocoderApi(PHOTON_URL);
    const res = await api.forwardGeocode({ query: '' });
    expect(res.features).toEqual([]);
    expect(mock).not.toHaveBeenCalled();
  });
});
