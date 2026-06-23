import { test, expect } from '@playwright/test';

/**
 * Phase 4 Playwright E2E for the address geocoder (tasks 4.3/4.7). The
 * `maplibre-gl-geocoder` control is backed by our Photon adapter; both the
 * Photon endpoint and the routing endpoints are mocked with `page.route` so the
 * run is hermetic:
 *   * Photon `/api?q=…`          — drives the suggestion list.
 *   * POST /v1/closest-facility  — returns a result once an incident is set.
 *   * GET  /v1/facility-categories — drives the data-driven dropdown.
 *
 * Library-owned behaviours (debounce, a11y, keyboard nav) are NOT re-tested
 * here (design D10) — only our wiring: suggestion → incident → routing, and
 * graceful degradation when the geocoder is unavailable.
 */

const PHOTON_BODY = {
  type: 'FeatureCollection',
  features: [
    {
      type: 'Feature',
      geometry: { type: 'Point', coordinates: [-117.161, 32.715] },
      properties: {
        name: 'San Diego City Hall',
        housenumber: '202',
        street: 'C St',
        city: 'San Diego',
        state: 'CA',
        postcode: '92101',
        country: 'United States',
        osm_id: 1,
        osm_type: 'N',
      },
    },
  ],
};

const ENRICHED_RESULTS = {
  request_id: 'e2e-cf',
  cache_hit: false,
  results: [
    {
      facility_id: 1000,
      rank: 1,
      total_cost: 420,
      total_distance_m: 418.7,
      total_time_s: 63.2,
      route_geojson: {
        type: 'LineString',
        coordinates: [
          [-117.161, 32.715],
          [-117.158, 32.718],
        ],
      },
      name: 'Mercy General Hospital',
      category: 'hospital',
      tags: { amenity: 'hospital' },
      facility_geojson: {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [-117.1575, 32.7185] },
        properties: { facility_id: 1000, name: 'Mercy General Hospital', category: 'hospital' },
      },
    },
  ],
};

const CATEGORIES = {
  request_id: 'e2e-cats',
  cache_hit: false,
  total: 12,
  categories: [{ category: 'hospital', count: 12 }],
};

test.describe('address geocoder (Photon)', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/v1/facility-categories**', (route) =>
      route.fulfill({ json: CATEGORIES }),
    );
    await page.route('**/v1/closest-facility', (route) =>
      route.fulfill({ json: ENRICHED_RESULTS }),
    );
  });

  test('type → suggestion → incident → run a closest-facility query', async ({ page }) => {
    await page.route(/\/api\?/, (route) => route.fulfill({ json: PHOTON_BODY }));
    await page.goto('/');

    await expect(page.locator('.maplibregl-canvas')).toBeVisible();
    const input = page.locator('.maplibregl-ctrl-geocoder input');
    await expect(input).toBeVisible();
    await input.click();
    await input.pressSequentially('San Diego City Hall', { delay: 40 });

    // Suggestion list (rendered + debounced by the control) appears.
    const suggestion = page.locator('.maplibregl-ctrl-geocoder .suggestions > li').first();
    await expect(suggestion).toBeVisible();
    await suggestion.click();

    const find = page.getByTestId('find-button');
    await expect(find).toBeEnabled();
    await find.click();

    await expect(page.getByText('Mercy General Hospital')).toBeVisible();
  });

  test('graceful degradation: with the geocoder down, map-click + routing still works', async ({
    page,
  }) => {
    // Photon errors out → the adapter yields no suggestions.
    await page.route(/\/api\?/, (route) => route.fulfill({ status: 500, body: 'down' }));
    await page.goto('/');

    await expect(page.locator('.maplibregl-canvas')).toBeVisible();
    const input = page.locator('.maplibregl-ctrl-geocoder input');
    await input.click();
    await input.pressSequentially('nowhere', { delay: 40 });
    // No suggestion list renders.
    await expect(page.locator('.maplibregl-ctrl-geocoder .suggestions > li')).toHaveCount(0);

    // The map-click fallback still sets an incident and routing succeeds.
    await page.getByTestId('map-canvas').click({ position: { x: 420, y: 300 } });
    const find = page.getByTestId('find-button');
    await expect(find).toBeEnabled();
    await find.click();
    await expect(page.getByText('Mercy General Hospital')).toBeVisible();
  });
});
