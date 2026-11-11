import { test, expect } from './fixtures/test-fixtures';

/** E2E: Login flow — register via UI, then log in. */
test.describe('Authentication', () => {
  test('user can register and log in via the UI', async ({ page }) => {
    const email = `login-${Date.now()}@test.com`;
    const password = 'LoginPass123!';

    await page.goto('/');
    // Look for a sign-up / register link.
    const signUpLink = page.getByRole('link', { name: /sign up|register/i });
    if (await signUpLink.isVisible()) {
      await signUpLink.click();
    }

    await page.fill('input[name="name"]', 'Login User');
    await page.fill('input[name="email"]', email);
    await page.fill('input[name="password"]', password);
    await page.getByRole('button', { name: /sign up|register|create/i }).click();

    // After registration, we should see the chat input or be redirected to login.
    await expect(page).toHaveURL(/\/$/);
    // The chat input should eventually appear.
    await page.waitForSelector('input, textarea', { timeout: 10000 });
  });

  test('login with valid credentials works', async ({ page, request, testEmail, testPassword }) => {
    // Register the user via API first so login has valid credentials.
    await request.post('/api/auth/register', {
      data: { email: testEmail, password: testPassword, name: 'E2E Test' },
    });

    await page.goto('/');

    // Click sign-in if on landing page.
    const signInBtn = page.getByRole('button', { name: /sign in|log in/i }).last();
    if (await signInBtn.isVisible()) {
      await signInBtn.click();
    }

    await page.fill('input[name="email"]', testEmail);
    await page.fill('input[name="password"]', testPassword);
    await signInBtn.click();

    // Wait for the chat input to appear, confirming login succeeded.
    await page.waitForSelector(
      '#composer, textarea[placeholder*="Ask Jalora"], input[placeholder*="Message"], textarea, input[type="text"]',
      {
      timeout: 15000,
      },
    );
  });

  test('login with wrong password shows error', async ({ page }) => {
    await page.goto('/');
    const signInBtn = page.getByRole('button', { name: /sign in|log in/i });
    if (await signInBtn.isVisible()) {
      await signInBtn.click();
    }

    await page.fill('input[name="email"]', 'wrong@test.com');
    await page.fill('input[name="password"]', 'wrongpassword');
    await page.getByRole('button', { name: /sign in|log in/i }).click();

    // Should see an error message.
    await expect(page.locator('text=/invalid|error|wrong/i')).toBeVisible({ timeout: 10000 });
  });
});
