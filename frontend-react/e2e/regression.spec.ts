import { test, expect, Page } from '@playwright/test';

/**
 * E2E regressions for the reported issues:
 *   1. History page is reachable on desktop AND on narrow screens
 *      (the sidebar used to be display:none <= 820px with no fallback).
 *   2. The Workspace view has a "Start coding" button that sends the
 *      requirements description to the coding agent and keeps the run in the
 *      Workspace agent pane (it must not hand off to the Chat view).
 *   3. The Schedules view renders content (used to be a blank screen on the
 *      stale production build).
 */
async function loginFromApi(page: Page, request: any, tag: string) {
  const email = `${tag}-${Date.now()}@test.com`;
  const resp = await request.post('/api/auth/register', {
    data: { email, password: 'TestPass123!', name: 'E2E' },
  });
  const body = await resp.json();
  if (!body.access_token) throw new Error('register failed: ' + JSON.stringify(body));
  await page.addInitScript((tk: string) => {
    localStorage.setItem('jalora_token', tk);
  }, body.access_token);
  await page.goto('/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('.sidebar, #composer, textarea', { timeout: 20000 });
}

test.describe('History page reachability', () => {
  test('integrations shows capability scope, approval, and verification limits', async ({
    page, request,
  }) => {
    await loginFromApi(page, request, 'capability-scope');
    await page.locator('.sidebar .nav-item', { hasText: /integrations/i }).click();

    const catalog = page.locator('.panel', {
      has: page.getByRole('heading', { name: 'Supported capability scope' }),
    });
    await expect(catalog).toBeVisible();
    await expect(catalog).toContainText(
      'A registered capability or successful local test does not prove a live provider action',
    );

    const emailCard = catalog.locator('details.capability-scope', {
      has: page.getByText('Email', { exact: true }),
    });
    await expect(emailCard).toBeVisible();
    await emailCard.locator('summary').click();
    await expect(emailCard).toContainText('send_email');
    await expect(emailCard).toContainText('approval');
    await expect(emailCard).toContainText('Live provider behavior is not verified');
    await expect(emailCard).toContainText('tests/test_phase_e_email.py');
  });

  test('device access has a first-class view with accurate browser companion limits', async ({
    page, request,
  }) => {
    await loginFromApi(page, request, 'device-access');
    await page.locator('.sidebar .nav-item', { hasText: /devices & access/i }).click();

    await expect(page.locator('.view-header h2')).toHaveText('Devices & access');
    await expect(page.locator('.device-connection-guide')).toContainText(
      'not necessarily the device displaying this page',
    );
    await expect(page.locator('.device-connection-guide')).toContainText(
      'It cannot inspect a screen, inventory installed apps,',
    );
    const devicePanels = page.locator('.device-access-panel');
    await expect(devicePanels).toHaveCount(2);
    await expect(devicePanels.nth(0)).toBeVisible();
    await expect(devicePanels.nth(1)).toBeVisible();
  });

  test('desktop shows the sidebar and History page via nav', async ({ page, request }) => {
    await loginFromApi(page, request, 'nav-desk');
    await page.setViewportSize({ width: 1280, height: 800 });
    await expect(page.locator('.sidebar')).toBeVisible();
    // History is now a standalone page reached via the sidebar nav.
    await page.locator('.sidebar .nav-item', { hasText: /history/i }).click();
    await expect(page.locator('.view-header h2', { hasText: /history/i })).toBeVisible();
    await expect(page.locator('.recents-view')).toBeVisible();
    await expect(page.locator('.recents-view .conv-list, .recents-view .conv-empty').first()).toBeVisible();
  });

  test('narrow screens can open the History page via the menu toggle', async ({
    page, request,
  }) => {
    await loginFromApi(page, request, 'nav-mob');
    await page.setViewportSize({ width: 390, height: 844 });

    // Sidebar is tucked away on mobile; the menu toggle is shown instead.
    await expect(page.locator('.sidebar.open')).toBeHidden();
    await expect(page.locator('.nav-toggle')).toBeVisible();

    // Open the drawer and navigate to the History page.
    await page.locator('.nav-toggle').click();
    await expect(page.locator('.sidebar.open')).toBeVisible();
    await page.locator('.sidebar .nav-item', { hasText: /history/i }).click();
    await expect(page.locator('.view-header h2', { hasText: /history/i })).toBeVisible();

    // Navigating closes the drawer — re-open it to reach workspace.
    await page.locator('.nav-toggle').click();
    await page.locator('.sidebar .nav-item', { hasText: /workspace/i }).click();
    await expect(page.locator('.sidebar.open')).toBeHidden();
  });

  test('searches, renames, and archives a persisted conversation', async ({ page, request }) => {
    const email = `chat-management-${Date.now()}@test.com`;
    const registered = await request.post('/api/auth/register', {
      data: { email, password: 'TestPass123!', name: 'E2E' },
    });
    const token = (await registered.json()).access_token;
    const headers = { Authorization: `Bearer ${token}` };
    const created = await request.post('/api/conversations', {
      headers, data: { title: 'Quarterly planning' },
    });
    expect(created.ok()).toBeTruthy();

    await page.addInitScript((tk: string) => localStorage.setItem('jalora_token', tk), token);
    await page.goto('/');
    await page.waitForSelector('.sidebar');
    await page.locator('.sidebar .nav-item', { hasText: /history/i }).click();
    await expect(page.locator('.conv-item', { hasText: 'Quarterly planning' })).toBeVisible();
    await page.getByLabel('Search conversations').fill('Quarterly');
    await expect(page.locator('.conv-item', { hasText: 'Quarterly planning' })).toHaveCount(1);

    page.once('dialog', (dialog) => void dialog.accept('Quarterly delivery'));
    await page.getByLabel('Rename Quarterly planning').click();
    await expect(page.locator('.conv-item', { hasText: 'Quarterly delivery' })).toBeVisible();
    await page.getByLabel('Archive Quarterly delivery').click();
    await expect(page.locator('.conv-item', { hasText: 'Quarterly delivery' })).toHaveCount(0);
  });
});

test.describe('Workspace start-coding button', () => {
  test('sends the requirements description to the coding agent', async ({ page, request }) => {
    await loginFromApi(page, request, 'ws-send');
    await page.locator('.sidebar .nav-item', { hasText: /workspace/i }).click();

    const emptyWorkspaceCreate = page.locator('.ws-empty-full .btn.primary');
    if (await emptyWorkspaceCreate.isVisible().catch(() => false)) {
      page.once('dialog', (dialog) => void dialog.accept('E2E coding project'));
      await emptyWorkspaceCreate.click();
      const approvalBanner = page.locator('.approval-banner');
      await expect(approvalBanner).toBeVisible();
      await approvalBanner.locator('.btn.primary').click();
    }

    const textarea = page.locator('.ws-requirements-input');
    await expect(textarea).toBeVisible({ timeout: 15000 });

    const startBtn = page.locator('.ws-requirements-actions button', { hasText: /start coding/i });
    await expect(startBtn).toBeVisible();
    await expect(startBtn).toBeDisabled();

    await textarea.fill('Build a simple todo web app with React and a FastAPI backend.');
    await expect(startBtn).toBeEnabled();
    await startBtn.click();

    // The run stays in the Workspace agent pane — no hand-off to the Chat view.
    await expect(page.locator('.ws-msg.user')).toHaveCount(1, { timeout: 15000 });
    await expect(page.locator('.ws-bubble.user')).toContainText('todo');
    await expect(page.locator('.ws-agent-messages')).toBeVisible();
    await expect(page.locator('#composer')).toHaveCount(0);
  });
});

test.describe('Workspace project lifecycle', () => {
  test('workspace approval opens the canonical task details', async ({ page, request }) => {
    await loginFromApi(page, request, 'ws-task-details');
    await page.locator('.sidebar .nav-item', { hasText: /workspace/i }).click();

    page.once('dialog', (dialog) => void dialog.accept('E2E task details project'));
    await page.locator('.ws-empty-full .btn.primary').click();

    const approvalBanner = page.locator('.approval-banner');
    await expect(approvalBanner).toBeVisible();
    await expect(approvalBanner).toContainText('E2E task details project');
    await expect(approvalBanner).toContainText('Provider or device limits still apply');
    const openTask = approvalBanner.getByRole('button', { name: /open task details/i });
    await expect(openTask).toBeVisible();
    await openTask.click();

    await expect(page.locator('.view-header h2')).toHaveText('Tasks');
    await expect(page.locator('.task-card')).toBeVisible();
    await expect(page.locator('.task-detail')).toBeVisible();
  });

  test('creates, renames, and archives a project through shared approvals', async ({
    page, request,
  }) => {
    await loginFromApi(page, request, 'ws-project-lifecycle');
    await page.locator('.sidebar .nav-item', { hasText: /workspace/i }).click();

    const projectSelect = page.locator('.ws-project-select');
    const initialTitle = `Stage 7 project ${Date.now()}`;
    const renamedTitle = `${initialTitle} renamed`;

    page.once('dialog', (dialog) => void dialog.accept(initialTitle));
    await page.locator('.ws-empty-full .btn.primary').click();
    const createApproval = page.locator('.approval-banner');
    await expect(createApproval).toBeVisible();
    await createApproval.locator('.btn.primary').click();
    await expect(projectSelect.locator('option', { hasText: initialTitle })).toHaveCount(1);

    page.once('dialog', (dialog) => void dialog.accept(renamedTitle));
    await page.getByRole('button', { name: /rename/i }).click();
    const renameApproval = page.locator('.approval-banner');
    await expect(renameApproval).toBeVisible();
    await renameApproval.locator('.btn.primary').click();
    await expect(projectSelect.locator('option', { hasText: renamedTitle })).toHaveCount(1);

    page.once('dialog', (dialog) => void dialog.accept());
    await page.locator('.ws-topbar button.danger').click();
    const archiveApproval = page.locator('.approval-banner');
    await expect(archiveApproval).toBeVisible();
    await archiveApproval.locator('.btn.primary').click();
    await expect(projectSelect.locator('option', { hasText: renamedTitle })).toHaveCount(0);
    await expect(page.locator('.ws-empty-full')).toBeVisible();
  });
});

test.describe('Schedules view rendering', () => {
  test('schedules view renders a header instead of a blank screen', async ({
    page, request,
  }) => {
    await loginFromApi(page, request, 'sched-view');
    await page.locator('.sidebar .nav-item', { hasText: /schedules/i }).click();
    await expect(page.locator('.view-header h2', { hasText: /schedules/i })).toBeVisible({
      timeout: 10000,
    });
  });

  test('calendar view renders a header instead of a blank screen', async ({
    page, request,
  }) => {
    await loginFromApi(page, request, 'cal-view');
    await page.locator('.sidebar .nav-item', { hasText: /calendar/i }).click();
    await expect(page.locator('.view-header h2', { hasText: /calendar/i })).toBeVisible({
      timeout: 10000,
    });
  });
});
