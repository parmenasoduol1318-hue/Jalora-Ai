import { test as base, expect, Page } from '@playwright/test';

// Re-export expect for convenience.
export { expect } from '@playwright/test';

interface TestFixtures {
  testEmail: string;
  testPassword: string;
  authenticatedPage: Page;
}

export const test = base.extend<TestFixtures>({
  // Generate a unique email/password pair for each test.
  testEmail: [async ({}, use) => { await use(`e2e-${Date.now()}@test.com`); }, { scope: 'test' }],
  testPassword: [async ({}, use) => { await use('TestPass123!'); }, { scope: 'test' }],

  // Register + log in once per test, returning an authenticated page.
  authenticatedPage: [
    async ({ page, request, testEmail, testPassword }, use) => {
      // Register via API.
      await request.post('/api/auth/register', {
        data: { email: testEmail, password: testPassword, name: 'E2E Test' },
      });

      // Log in through the UI.
      await page.goto('/');
      const signInBtn = page.getByRole('button', { name: /sign in|log in|login/i });
      if (await signInBtn.isVisible()) {
        await signInBtn.click();
      }

      await page.fill('input[name="email"]', testEmail);
      await page.fill('input[name="password"]', testPassword);
      const submitBtn = page.getByRole('button', { name: /sign in|log in/i }).last();
      await submitBtn.click();

      // Wait until the chat input is visible.
      await page.waitForSelector(
        '#composer, textarea[placeholder*="Ask Jalora"], input[placeholder*="Message"], textarea, input[type="text"]',
        {
        timeout: 15000,
        },
      );
      await use(page);
    },
    { scope: 'test' },
  ],
});
