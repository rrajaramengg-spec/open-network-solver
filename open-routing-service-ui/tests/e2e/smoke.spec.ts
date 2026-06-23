import { test, expect } from '@playwright/test';

/**
 * Live smoke test against the running docker stack.
 *
 * Required env:
 *   E2E_BASE_URL=http://localhost:58081
 *
 * Asserts the regressions we have fixed end-to-end:
 *   1. The map canvas renders at a non-zero size (regression for the
 *      MapLibre ``position:relative`` cascade override).
 *   2. Clicking the map updates the incident state in the search widget.
 *   3. Submitting an off-graph incident surfaces a typed error toast
 *      (proves the API maps ``incident_off_graph`` to a UI message).
 *   4. The API directly returns >=1 result for a San-Jose incident
 *      (proves the closest_facility PL/pgSQL fixes are live on US-West data).
 */

test.describe('live smoke @58081', () => {
  test.beforeEach(async ({ page }) => {
    // The basemap tile CDN is often unreachable behind corporate egress
    // proxies; abort tile requests so the map still initialises (MapLibre
    // creates the WebGL canvas regardless) and the page reaches a settled
    // state without waiting on never-arriving tiles.
    await page.route(/tile\.openstreetmap\.org/, (route) => route.abort());
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('[data-testid="map-canvas"] canvas');
    await page.waitForTimeout(2500);
  });

  test('map renders with non-zero size', async ({ page }) => {
    await expect(page.locator('header')).toContainText('open-network-solver');
    const rect = await page.locator('[data-testid="map-canvas"]').boundingBox();
    expect(rect).not.toBeNull();
    expect(rect!.width).toBeGreaterThan(200);
    expect(rect!.height).toBeGreaterThan(200);
  });

  test('clicking the map sets the incident state', async ({ page }) => {
    const box = await page.locator('[data-testid="map-canvas"]').boundingBox();
    expect(box).not.toBeNull();
    await page.mouse.click(box!.x + box!.width / 2, box!.y + box!.height / 2);

    const widget = page.locator('aside[aria-label="Search widget"]');
    await expect(widget.locator('div.font-mono').first()).toHaveText(
      /-?\d+\.\d+,\s*-?\d+\.\d+/,
    );
  });

  test('off-graph submission surfaces a typed error toast', async ({ page }) => {
    const box = await page.locator('[data-testid="map-canvas"]').boundingBox();
    expect(box).not.toBeNull();
    // Default center is California (-117, 37) — outside any loaded network.
    await page.mouse.click(box!.x + box!.width / 2, box!.y + box!.height / 2);
    await page.getByTestId('find-button').click();
    const toast = page.getByTestId('error-toast');
    await expect(toast).toBeVisible({ timeout: 10_000 });
    await expect(toast).toContainText(/incident|road|too far/i);
  });

  test('API returns results for a San-Jose incident', async ({ page }) => {
    const result = await page.evaluate(async () => {
      const r = await fetch('http://localhost:58000/v1/closest-facility', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Request-Id': 'e2e-smoke' },
        body: JSON.stringify({
          incident: { lat: 37.3382, lon: -121.8863 },
          buffer_m: 2000,
          k: 3,
          cost_mode: 'distance',
          facility_filter: {},
        }),
      });
      return { status: r.status, body: await r.json() };
    });
    expect(result.status).toBe(200);
    expect(result.body.results.length).toBeGreaterThan(0);
    expect(result.body.results[0].total_distance_m).toBeGreaterThan(0);
    expect(result.body.results[0].route_geojson.coordinates.length).toBeGreaterThan(1);
  });
});
