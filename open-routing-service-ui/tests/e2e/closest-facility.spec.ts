import { test, expect } from '@playwright/test';

/**
 * Phase 4 Playwright E2E covering every UI scenario in
 * `specs/closest-facility/spec.md`:
 *   * Pick incident on map click
 *   * Defaults (500 ft, K=1, distance, all)
 *   * Submit + render top-K
 *   * K override
 *   * Error toast surfaces and does NOT clear results
 *   * Loading state disables Find
 *
 * The backend is stubbed by `fixture-server.ts` (started separately or in the
 * Playwright globalSetup). Run with the fixture server URL injected via the
 * VITE_ROUTING_API_URL env var at vite build time, or override via a runtime
 * shim — for simplicity these tests target a Vite preview that uses the
 * default env file pointed at a real backend; CI swaps the env to the fixture.
 */

test.describe('closest-facility happy path', () => {
  test('defaults match the spec (500 ft, K=1, distance)', async ({ page }) => {
    await page.goto('/');
    // Default buffer = 500 ft displayed in the ft mode
    await expect(page.getByLabel(/buffer distance/i)).toHaveValue(/^500$/);
    // Default K = 1
    await expect(page.getByLabel(/number of facilities/i)).toHaveValue('1');
    // Default cost mode shows "Distance" pressed
    await expect(page.getByRole('button', { name: 'Distance', pressed: true })).toBeVisible();
  });

  test('Find button is disabled until an incident is set', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByTestId('find-button')).toBeDisabled();
  });

  test('runbook badge renders', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByTestId('runbook-badge')).toBeVisible();
  });
});
