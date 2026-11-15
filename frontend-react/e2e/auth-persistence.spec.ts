import { expect, test } from '@playwright/test';

test('refreshes an expired access token and keeps the session after reload', async ({
  page, request,
}) => {
  const response = await request.post('/api/auth/register', {
    data: {
      email: `refresh-${Date.now()}@test.com`,
      password: 'TestPass123!',
      name: 'Refresh Test',
    },
  });
  expect(response.ok()).toBeTruthy();
  const session = await response.json() as {
    access_token: string;
    refresh_token: string;
  };
  let refreshRequests = 0;
  let meRequests = 0;

  await page.route('**/api/auth/me', async (route) => {
    meRequests += 1;
    if (route.request().headers().authorization === 'Bearer expired-e2e-token') {
      await route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({
          error: { code: 'TOKEN_EXPIRED', details: 'Session expired.' },
        }),
      });
      return;
    }
    await route.continue();
  });
  await page.route('**/api/auth/refresh', async (route) => {
    refreshRequests += 1;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(session),
    });
  });
  await page.addInitScript(() => {
    if (localStorage.getItem('jalora_token')) return;
    localStorage.setItem('jalora_token', 'expired-e2e-token');
    localStorage.setItem('jalora_refresh_token', 'persisted-refresh-token');
  });

  await page.goto('/');
  await expect(page.locator('.sidebar')).toBeVisible();
  await expect.poll(() => page.evaluate(() => localStorage.getItem('jalora_token')))
    .toBe(session.access_token);
  await expect.poll(() => page.evaluate(() => localStorage.getItem('jalora_refresh_token')))
    .toBe(session.refresh_token);
  expect(refreshRequests).toBe(1);
  expect(meRequests).toBeGreaterThanOrEqual(2);

  await page.reload();
  await expect(page.locator('.sidebar')).toBeVisible();
  expect(refreshRequests).toBe(1);
});
