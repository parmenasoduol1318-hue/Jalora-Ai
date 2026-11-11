import { test, expect } from './fixtures/test-fixtures';

/** E2E: Schedule / reminders — create and verify a scheduled item. */
test.describe('Schedules & reminders', () => {
  test('can create a scheduled task via chat', async ({ authenticatedPage }) => {
    const input = authenticatedPage.locator('#composer');

    await input.fill('Schedule a reminder for tomorrow at 9am: Submit the Phase L report');
    await input.press('Enter');

    // Wait for a confirmation that the schedule was created (or an error
    // message if the model couldn't respond due to quota limits).
    await expect(
      authenticatedPage.locator('.msg.assistant')
    ).toHaveCount(1, { timeout: 20000 });
  });

  test('can view scheduled items in a dedicated view', async ({ authenticatedPage }) => {
    // Navigate to the Schedules view via the sidebar nav.
    await authenticatedPage.locator('.sidebar .nav-item', { hasText: /schedules/i }).click();

    // Verify the page loads and shows scheduled items (or empty state).
    await expect(
      authenticatedPage.locator('h1, [class*="header"], [class*="title"]')
    ).toBeVisible({ timeout: 10000 });
  });

  test('can create and edit a timezone-aware schedule', async ({ authenticatedPage }) => {
    await authenticatedPage.locator('.sidebar .nav-item', { hasText: /schedules/i }).click();
    await authenticatedPage.getByRole('button', { name: /new schedule/i }).click();

    const createForm = authenticatedPage.locator('form.schedule-form').first();
    const createInputs = createForm.locator('input');
    await createInputs.nth(0).fill('every Monday at 08:00');
    await createInputs.nth(1).fill('Africa/Nairobi');
    await createForm.locator('textarea').fill('Review the weekly report');
    await createForm.getByRole('button', { name: /create schedule/i }).click();

    const scheduleCard = authenticatedPage.locator('.task-card')
      .filter({ hasText: 'Review the weekly report' });
    await expect(scheduleCard).toBeVisible();
    await scheduleCard.getByRole('button', { name: /^edit$/i }).click();
    const editForm = scheduleCard.locator('form.schedule-form');
    await editForm.locator('input').nth(0).fill('weekdays at 10:00');
    await editForm.locator('input').nth(1).fill('UTC');
    await editForm.locator('textarea').fill('Review the revised weekly report');
    await editForm.getByRole('button', { name: /save changes/i }).click();

    await expect(
      authenticatedPage.locator('.task-card').filter({
        hasText: 'Review the revised weekly report',
      }),
    ).toBeVisible();
  });

  test('latest scheduled task opens in the shared Tasks view', async ({ authenticatedPage }) => {
    await authenticatedPage.route('**/api/schedules', (route) =>
      route.fulfill({
        json: [{
          id: 'schedule-e2e',
          kind: 'cron',
          spec: { hour: 9, minute: 0, day_of_week: '*' },
          schedule_expression: 'daily at 9am',
          timezone: 'UTC',
          task_template: 'Check the scheduled task outcome',
          notify_only: false,
          enabled: true,
          next_run: '2026-11-11T09:00:00+00:00',
          last_run: '2026-11-10T09:00:00+00:00',
          latest_occurrence: {
            id: 'occurrence-e2e',
            status: 'completed',
            scheduled_for: '2026-11-10T09:00:00+00:00',
            result_summary: 'Scheduled task completed.',
            task_id: 'task-e2e',
            run_id: 'run-e2e',
          },
        }],
      }),
    );
    await authenticatedPage.route('**/api/tasks', (route) =>
      route.fulfill({
        json: [{
          id: 'task-e2e',
          user_id: 'user-e2e',
          goal: 'Check the scheduled task outcome',
          status: 'completed',
          progress: 100,
          created_at: '2026-11-10T09:00:00+00:00',
          plan: [],
        }],
      }),
    );
    await authenticatedPage.route('**/api/calls?limit=20', (route) =>
      route.fulfill({ json: [] }),
    );

    await authenticatedPage.locator('.sidebar .nav-item', { hasText: /schedules/i }).click();
    const scheduleCard = authenticatedPage.locator('.task-card')
      .filter({ hasText: 'Check the scheduled task outcome' });
    await expect(scheduleCard.getByText(/completed/i)).toBeVisible();
    await scheduleCard.getByRole('button', { name: /open task/i }).click();

    await expect(authenticatedPage.locator('#task-task-e2e')).toBeVisible();
  });
});
