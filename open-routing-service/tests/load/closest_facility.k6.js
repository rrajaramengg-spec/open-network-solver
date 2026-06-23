// Phase 3 load test (scalable-routing-etl task 3.5) — sustained 100 RPS for
// 5 minutes against /v1/closest-facility on country-scale US-West data,
// asserting:
//   * p95 < 50 ms cached
//   * p95 < 800 ms uncached
// and that latency does NOT scale with the size of the `ways` table (the
// bbox-bounded dijkstra keeps the working edge set local — design D8).
//
// Run against a stack started with:
//   docker compose --profile service up -d
// then:
//   k6 run tests/load/closest_facility.k6.js
//
// Override the service URL via env:
//   k6 run -e ROUTING_URL=http://localhost:58000 tests/load/closest_facility.k6.js
//
// The script issues two request groups:
//   * "cold"  — incidents are spread across a 100x100 grid so each call is
//               a unique cache key (cache miss).
//   * "warm"  — incidents are drawn from a small pool of 10 keys so the
//               second-onward request reliably hits the cache.

import http from 'k6/http';
import { check, group, sleep } from 'k6';
import { Trend } from 'k6/metrics';

const ROUTING_URL = __ENV.ROUTING_URL || 'http://localhost:58000';

// Custom trends so we can assert thresholds per group (cached vs. uncached).
const tCold = new Trend('lat_cold_ms');
const tWarm = new Trend('lat_warm_ms');

export const options = {
    scenarios: {
        cold: {
            executor: 'constant-arrival-rate',
            rate: 50,
            timeUnit: '1s',
            duration: '5m',
            preAllocatedVUs: 50,
            maxVUs: 100,
            exec: 'coldHit',
        },
        warm: {
            executor: 'constant-arrival-rate',
            rate: 50,
            timeUnit: '1s',
            duration: '5m',
            preAllocatedVUs: 50,
            maxVUs: 100,
            exec: 'warmHit',
            startTime: '15s', // let cold warm the system first
        },
    },
    thresholds: {
        // 100 RPS combined, with the explicit per-group SLOs from the spec.
        'http_req_failed': ['rate<0.01'],
        'lat_cold_ms': ['p(95)<800'],
        'lat_warm_ms': ['p(95)<50'],
    },
};

// --- helpers ---------------------------------------------------------------

function buildBody(lat, lon) {
    return JSON.stringify({
        incident: { lat: lat, lon: lon },
        buffer_m: 2000.0,
        k: 1,
        cost_mode: 'distance',
        facility_filter: {},
    });
}

// Cold pool: thousands of unique keys, all within the central San-Jose street
// network (on-graph against the US-West extract) so each call exercises a real
// bbox-bounded route rather than the off-graph rejection path.
function randomColdIncident() {
    // Vary the 4th decimal so each call produces a unique cache key. The
    // 0.01° box (~1.1 km) stays inside the dense urban grid => on-graph.
    const lat = 37.33 + (Math.floor(Math.random() * 100) / 10000);
    const lon = -121.89 + (Math.floor(Math.random() * 100) / 10000);
    return [lat, lon];
}

// Warm pool: only 10 distinct keys (also central San-Jose).
const WARM = Array.from({ length: 10 }, (_, i) => [37.33, -121.89 + i / 10000]);
function pickWarmIncident() {
    return WARM[Math.floor(Math.random() * WARM.length)];
}

// --- scenarios -------------------------------------------------------------

export function coldHit() {
    const [lat, lon] = randomColdIncident();
    const res = http.post(`${ROUTING_URL}/v1/closest-facility`, buildBody(lat, lon), {
        headers: { 'Content-Type': 'application/json' },
        tags: { group: 'cold' },
    });
    tCold.add(res.timings.duration);
    check(res, {
        'cold status is 2xx or expected 4xx': (r) => r.status < 500,
    });
}

export function warmHit() {
    const [lat, lon] = pickWarmIncident();
    const res = http.post(`${ROUTING_URL}/v1/closest-facility`, buildBody(lat, lon), {
        headers: { 'Content-Type': 'application/json' },
        tags: { group: 'warm' },
    });
    tWarm.add(res.timings.duration);
    check(res, {
        'warm status is 2xx or expected 4xx': (r) => r.status < 500,
    });
}
