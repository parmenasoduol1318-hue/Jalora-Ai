import { test, expect } from './fixtures/test-fixtures';

/** E2E: Calendar view — month grid renders and an event can be created. */
test.describe('Calendar view', () => {
  test('renders the month grid', async ({ authenticatedPage }) => {
    await authenticatedPage
      .locator('.sidebar .nav-item', { hasText: /calendar/i })
      .click();

    await expect(
      authenticatedPage.locator('.view-header h2', { hasText: /calendar/i })
    ).toBeVisible({ timeout: 10000 });
    await expect(authenticatedPage.locator('.cal-grid').first()).toBeVisible();
    await expect(authenticatedPage.locator('.cal-cell').first()).toBeVisible();
  });

  test('can create an event and see it on the grid', async ({ authenticatedPage }) => {
    await authenticatedPage
      .locator('.sidebar .nav-item', { hasText: /calendar/i })
      .click();
    await expect(authenticatedPage.locator('.cal-grid').first()).toBeVisible({
      timeout: 10000,
    });

    // Open the New event form and fill it in.
    await authenticatedPage.getByRole('button', { name: /new event/i }).click();
    const form = authenticatedPage.locator('form.schedule-form');
    await expect(form).toBeVisible();

    const now = new Date();
    const day = String(Math.min(now.getDate() + 1, 28)).padStart(2, '0');
    const start = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${day}T10:00`;

    await form.locator('input').first().fill('E2E Calendar Event');
    await form.locator('input[type="datetime-local"]').first().fill(start);
    await form.getByRole('button', { name: /create/i }).click();

    // The event should appear on the month grid after the list reloads.
    await expect(
      authenticatedPage.locator('.cal-event', { hasText: 'E2E Calendar Event' })
    ).toBeVisible({ timeout: 10000 });
  });
});
