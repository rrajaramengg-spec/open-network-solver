import { test, expect } from '@playwright/test';

/**
 * Phase 3 Playwright E2E for the enriched facility rendering + data-driven
 * filter (tasks 3.3–3.9). Both backend endpoints are mocked with `page.route`
 * so the run is hermetic and deterministic:
 *   * GET  /v1/facility-categories — drives the facility-type dropdown.
 *   * POST /v1/closest-facility    — returns an enriched result carrying the
 *     real `facility_geojson` Point, `name`, `category` and `tags`.
 *
 * Canvas-layer details (the category symbol, the dashed access-leg geometry and
 * the popup HTML) are exhaustively covered by the Vitest unit suite
 * (`facilityFeatures.test.ts`, `facilityPopup.test.ts`); here we assert the
 * end-to-end, DOM-observable behaviour: the dropdown is populated from the
 * categories endpoint and a map-click → Find renders the enriched result.
 */

const CATEGORIES = {
  request_id: 'e2e-cats',
  cache_hit: false,
  total: 47,
  categories: [
    { category: 'pharmacy', count: 30 },
    { category: 'hospital', count: 12 },
    { category: 'fire_station', count: 5 },
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
      tags: { amenity: 'hospital', 'addr:street': 'Medical Center Dr' },
      facility_geojson: {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [-117.1575, 32.7185] },
        properties: { facility_id: 1000, name: 'Mercy General Hospital', category: 'hospital' },
      },
    },
  ],
};

test.describe('enriched facility rendering + data-driven filter', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/v1/facility-categories**', (route) =>
      route.fulfill({ json: CATEGORIES, headers: { 'X-Request-Id': 'e2e-cats' } }),
    );
    await page.route('**/v1/closest-facility', (route) =>
      route.fulfill({ json: ENRICHED_RESULTS, headers: { 'X-Request-Id': 'e2e-cf' } }),
    );
  });

  test('the facility-type dropdown is populated from the categories endpoint', async ({
    page,
  }) => {
    await page.goto('/');
    const select = page.getByLabel('Facility type');
    // Always-present sentinel + one option per ingested category (data-driven).
    await expect(select.getByRole('option', { name: 'All facilities' })).toBeAttached();
    await expect(select.getByRole('option', { name: 'Pharmacy (30)' })).toBeAttached();
    await expect(select.getByRole('option', { name: 'Hospital (12)' })).toBeAttached();
    await expect(select.getByRole('option', { name: 'Fire Station (5)' })).toBeAttached();
  });

  test('map-click → Find renders the enriched result (name + category)', async ({ page }) => {
    await page.goto('/');

    // Wait for the MapLibre canvas, then click it to drop an incident.
    await expect(page.locator('.maplibregl-canvas')).toBeVisible();
    await page.getByTestId('map-canvas').click({ position: { x: 420, y: 300 } });

    const find = page.getByTestId('find-button');
    await expect(find).toBeEnabled();
    await find.click();

    // The enriched result row surfaces the facility name and humanised category.
    await expect(page.getByText('Mercy General Hospital')).toBeVisible();
    await expect(page.getByText('Hospital', { exact: true })).toBeVisible();
  });
});
