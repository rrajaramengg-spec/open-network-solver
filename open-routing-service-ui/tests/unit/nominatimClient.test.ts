import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { createNominatimClient } from '@/api/nominatimClient';

describe('nominatimClient', () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    fetchMock = vi.fn();
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });
  afterEach(() => vi.restoreAllMocks());

  it('returns empty array for short queries', async () => {
    const c = createNominatimClient('https://nom.test');
    expect(await c.search('ab')).toEqual([]);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('hits the search endpoint with format=jsonv2', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(
        JSON.stringify([{ display_name: 'X', lat: '32.7', lon: '-117.1' }]),
        { status: 200 },
      ),
    );
    const c = createNominatimClient('https://nom.test');
    const res = await c.search('san diego');
    expect(res).toHaveLength(1);
    const url = fetchMock.mock.calls[0][0] as string;
    expect(url).toContain('format=jsonv2');
    expect(url).toContain('q=san+diego');
  });

  it('returns [] on non-2xx', async () => {
    fetchMock.mockResolvedValueOnce(new Response('boom', { status: 500 }));
    const c = createNominatimClient('https://nom.test');
    expect(await c.search('san diego')).toEqual([]);
  });

  it('returns [] when the response is not an array', async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ error: 'bad' }), { status: 200 }),
    );
    const c = createNominatimClient('https://nom.test');
    expect(await c.search('san diego')).toEqual([]);
  });

  it('propagates AbortSignal', async () => {
    const controller = new AbortController();
    fetchMock.mockImplementationOnce(
      (_u: string, init: { signal?: AbortSignal }) =>
        new Promise((_r, rej) => {
          init.signal?.addEventListener('abort', () =>
            rej(new DOMException('aborted', 'AbortError')),
          );
        }),
    );
    const c = createNominatimClient('https://nom.test');
    const p = c.search('san diego', { signal: controller.signal });
    controller.abort();
    await expect(p).rejects.toBeInstanceOf(DOMException);
  });
});
