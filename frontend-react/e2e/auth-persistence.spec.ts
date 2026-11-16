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

test('restores a session when only the refresh token survived', async ({
  page, request,
}) => {
  const response = await request.post('/api/auth/register', {
    data: {
      email: `refresh-only-${Date.now()}@test.com`,
      password: 'TestPass123!',
      name: 'Refresh Only Test',
    },
  });
  expect(response.ok()).toBeTruthy();
  const session = await response.json() as {
    access_token: string;
    refresh_token: string;
  };
  let refreshRequests = 0;
  await page.route('**/api/auth/refresh', async (route) => {
    refreshRequests += 1;
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(session),
    });
  });
  await page.addInitScript((refreshToken: string) => {
    localStorage.removeItem('jalora_token');
    localStorage.setItem('jalora_refresh_token', refreshToken);
  }, session.refresh_token);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.locator('.sidebar')).toBeVisible();
  await expect(page.locator('.msg-list')).toBeVisible();
  await expect.poll(() => page.evaluate(() => localStorage.getItem('jalora_token')))
    .toBe(session.access_token);
  expect(refreshRequests).toBe(1);
});

test('explicit sign-out revokes the persisted refresh token', async ({
  page, request,
}) => {
  const response = await request.post('/api/auth/register', {
    data: {
      email: `logout-ui-${Date.now()}@test.com`,
      password: 'TestPass123!',
      name: 'Logout UI Test',
    },
  });
  expect(response.ok()).toBeTruthy();
  const session = await response.json() as {
    access_token: string;
    refresh_token: string;
  };
  await page.addInitScript((tokens) => {
    localStorage.setItem('jalora_token', tokens.access_token);
    localStorage.setItem('jalora_refresh_token', tokens.refresh_token);
  }, session);

  await page.goto('/');
  await expect(page.locator('#btn-logout')).toBeVisible();
  const revocation = page.waitForResponse((response) =>
    response.url().endsWith('/api/auth/logout') && response.request().method() === 'POST',
  );
  await page.locator('#btn-logout').click();
  expect((await revocation).ok()).toBeTruthy();
  await expect(page.locator('.auth-screen')).toBeVisible();
  await expect.poll(() => page.evaluate(() => localStorage.getItem('jalora_token')))
    .toBeNull();
  await expect.poll(() => page.evaluate(() => localStorage.getItem('jalora_refresh_token')))
    .toBeNull();

  const refresh = await request.post('/api/auth/refresh', {
    data: { refresh_token: session.refresh_token },
  });
  expect(refresh.status()).toBe(401);
});
