/**
 * Deterministic fixture server for Playwright e2e tests.
 *
 * Listens on http://localhost:5174 and responds to:
 *   * POST /v1/closest-facility — returns canned results computed from the
 *     request body so the assertions are byte-stable.
 *   * GET  /readyz             — returns the "all ok" payload.
 *   * GET  /search             — Nominatim stub.
 *
 * Boot from the e2e tests with `node tests/e2e/fixture-server.ts &`.
 */

import { createServer } from 'node:http';

const PORT = Number(process.env.FIXTURE_PORT ?? 5174);

const server = createServer(async (req, res) => {
  const reqId = req.headers['x-request-id'] ?? 'fixture-rid';
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Headers', '*');
  res.setHeader('X-Request-Id', String(reqId));

  if (req.method === 'OPTIONS') {
    res.writeHead(204).end();
    return;
  }

  const url = new URL(req.url ?? '/', `http://localhost:${PORT}`);

  if (url.pathname === '/healthz') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ status: 'ok' }));
    return;
  }

  if (url.pathname === '/readyz') {
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(
      JSON.stringify({
        status: 'ok',
        primary_pg: 'ok',
        replica_pg: 'ok',
        pgr_version: 'ok',
        redis: 'ok',
      }),
    );
    return;
  }

  if (url.pathname === '/search') {
    const q = url.searchParams.get('q') ?? '';
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(
      JSON.stringify([
        { display_name: `${q} (fixture)`, lat: '32.71500', lon: '-117.16100' },
      ]),
    );
    return;
  }

  if (url.pathname === '/v1/closest-facility' && req.method === 'POST') {
    let body = '';
    req.on('data', (chunk) => (body += chunk));
    req.on('end', () => {
      const payload = JSON.parse(body) as {
        incident: { lat: number; lon: number };
        k: number;
      };
      const results = Array.from({ length: Math.min(payload.k, 3) }, (_, i) => ({
        facility_id: 1000 + i,
        rank: i + 1,
        total_cost: 100 * (i + 1),
        total_distance_m: 100 * (i + 1),
        total_time_s: 10 * (i + 1),
        route_geojson: {
          type: 'LineString',
          coordinates: [
            [payload.incident.lon, payload.incident.lat],
            [payload.incident.lon + 0.001 * (i + 1), payload.incident.lat + 0.001 * (i + 1)],
          ],
        },
      }));
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(
        JSON.stringify({
          request_id: String(reqId),
          cache_hit: false,
          results,
        }),
      );
    });
    return;
  }

  res.writeHead(404).end();
});

server.listen(PORT, () => {
  console.log(`fixture server listening on http://localhost:${PORT}`);
});
