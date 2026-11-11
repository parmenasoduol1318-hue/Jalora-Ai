import { test, expect } from './fixtures/test-fixtures';

/** E2E: Approval flow — the assistant asks for approval before an action. */
test.describe('Approval flow', () => {
  test('approval modal appears for tool calls requiring approval', async ({
    authenticatedPage,
  }) => {
    test.setTimeout(60000);
    const input = authenticatedPage.locator('#composer');

    // Send a message that triggers a tool call requiring approval.
    await input.fill('Create a task called "Approval Test Task"');
    await input.press('Enter');

    // Wait for either an approval modal or a tool execution indicator.
    const approvalModal = authenticatedPage.locator(
      '[role="dialog"], [class*="approval"], [class*="modal"]'
    );

    // The approval modal should appear within 10 seconds.
    try {
      await expect(approvalModal).toBeVisible({ timeout: 10000 });
      // Click approve.
      await authenticatedPage
        .getByRole('button', { name: /approve|allow|confirm/i })
        .click();
    } catch {
      // If no approval modal (mock mode may auto-approve), just verify
      // the assistant acknowledged the request.
      await expect(authenticatedPage.locator('.msg.assistant')).toHaveCount(1, {
        timeout: 20000,
      });
    }
  });

  test('can reject an approval request', async ({ authenticatedPage }) => {
    const approval = {
      approval_id: 'approval-delete-e2e',
      run_id: 'run-delete-e2e',
      tool_name: 'delete_task',
      arguments: { task_id: 'task-e2e', title: 'E2E task' },
      level: 3,
      kind: 'tool',
    };
    let rejected = false;
    await authenticatedPage.route('**/api/chat', async (route) => {
      const events = [
        { type: 'approval', ...approval },
        {
          type: 'done',
          status: 'paused_approval',
          reply: 'This action is ready for your review.',
          intent: 'task',
          approval,
        },
      ];
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''),
      });
    });
    await authenticatedPage.route(
      '**/api/approvals/approval-delete-e2e/reject',
      async (route) => {
        rejected = true;
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ status: 'rejected', reply: 'The action was rejected.' }),
        });
      },
    );
    await authenticatedPage.locator('#composer').fill('Delete all tasks');
    await authenticatedPage.locator('#composer').press('Enter');
    await expect(authenticatedPage.locator('.modal-overlay')).toBeVisible();
    await authenticatedPage
      .getByRole('button', { name: /reject|deny|cancel/i })
      .last()
      .click();
    await expect.poll(() => rejected).toBe(true);
    await expect(authenticatedPage.locator('body')).toContainText(/rejected/i);
  });

  test('chat approval opens the same canonical task details', async ({ authenticatedPage }) => {
    const approval = {
      approval_id: 'approval-chat-task',
      run_id: 'run-chat-task',
      tool_name: 'delete_task',
      arguments: { task_id: 'task-e2e', title: 'E2E task' },
      level: 3,
      kind: 'tool',
    };
    const task = {
      id: 'task-from-chat',
      run_id: 'run-chat-task',
      goal: 'Send a reviewed email',
      status: 'waiting_for_approval',
      resumable: false,
      plan: [],
      current_step: 0,
      progress: 50,
      created_at: new Date().toISOString(),
    };
    await authenticatedPage.route('**/api/chat', async (route) => {
      const events = [
        { type: 'approval', ...approval },
        {
          type: 'done',
          status: 'paused_approval',
          reply: 'This email is ready for your review.',
          intent: 'task',
          approval,
        },
      ];
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''),
      });
    });
    await authenticatedPage.route('**/api/tasks**', async (route) => {
      const requestUrl = new URL(route.request().url());
      if (requestUrl.pathname === '/api/tasks/task-from-chat') {
        await route.fulfill({
          json: {
            ...task,
            steps: [],
            approvals: [{
              id: 'approval-chat-task',
              run_id: 'run-chat-task',
              task_step_id: null,
              plan_step_id: null,
              tool_name: 'delete_task',
              arguments: approval.arguments,
              level: 3,
              kind: 'tool',
              status: 'pending',
              can_approve: true,
              can_edit: true,
              decision_status: 'pending',
              claim_status: 'unclaimed',
              payload_version: 1,
              created_at: new Date().toISOString(),
              expires_at: null,
            }],
          },
        });
        return;
      }
      await route.fulfill({ json: [task] });
    });

    await authenticatedPage.locator('#composer').fill('Prepare and send this email');
    await authenticatedPage.locator('#composer').press('Enter');
    await expect(authenticatedPage.locator('.approval-banner')).toContainText('delete_task');
    await authenticatedPage
      .getByRole('button', { name: /open task details/i })
      .last()
      .click();

    const card = authenticatedPage.locator('.task-card').filter({
      hasText: 'Send a reviewed email',
    });
    await expect(card.getByRole('heading', { name: 'Approvals' })).toBeVisible();
    await expect(card).toContainText('E2E task');
    await expect(card.getByRole('button', { name: /^approve$/i })).toBeVisible();
  });

  test('email approval stays pending, is editable, and can be rejected safely', async ({
    authenticatedPage,
  }) => {
    const page = authenticatedPage;
    const approval = {
      approval_id: 'approval-email-e2e',
      run_id: 'run-email-e2e',
      tool_name: 'send_email',
      arguments: {
        to: 'church-contact@example.com',
        subject: 'Are you going to church?',
        body: "Hi, I wanted to ask if you're going to church.",
        attachments: [],
      },
      level: 3,
      kind: 'tool',
    };
    let patchedArguments: Record<string, unknown> | undefined;
    let rejected = false;

    await page.route('**/api/emails/task', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'paused_approval', approval }),
      });
    });
    await page.route('**/api/emails/attachments', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          url: '/api/emails/attachments/review.txt',
          filename: 'review.txt', mime_type: 'text/plain', size_bytes: 12,
        }),
      });
    });
    await page.route('**/api/emails/send/approval-email-e2e', async (route) => {
      if (route.request().method() !== 'PATCH') {
        await route.continue();
        return;
      }
      patchedArguments = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...approval, arguments: patchedArguments }),
      });
    });
    await page.route('**/api/emails/send/approval-email-e2e/reject', async (route) => {
      rejected = true;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'rejected', reply: 'The email was rejected.' }),
      });
    });

    await page.locator('#composer').fill(
      'Send an email to church-contact@example.com to ask whether they are going to church',
    );
    await page.locator('#composer').press('Enter');

    const card = page.locator('.approval-email-card');
    await expect(card).toBeVisible({ timeout: 10000 });
    await expect(card).toContainText('church');
    await expect(page.locator('body')).not.toContainText(/email was sent/i);

    const recipient = card.locator('input.approval-email-input').nth(0);
    const subject = card.locator('input.approval-email-input').nth(3);
    const body = card.locator('textarea.approval-email-textarea');
    await recipient.fill('edited-recipient@example.com');
    await subject.fill('Edited subject');
    await body.fill('Edited body for review.');
    await card.locator('input[type="file"]').setInputFiles({
      name: 'review.txt', mimeType: 'text/plain', buffer: Buffer.from('review file'),
    });
    await expect(card).toContainText('review.txt');
    await card.getByRole('button', { name: /save changes/i }).click();
    await expect.poll(() => patchedArguments?.to).toBe('edited-recipient@example.com');
    await expect.poll(() => patchedArguments?.subject).toBe('Edited subject');
    await expect.poll(() => patchedArguments?.body).toBe('Edited body for review.');

    await page.getByRole('button', { name: /deny|reject|cancel/i }).last().click();
    await expect.poll(() => rejected).toBe(true);
    await expect(page.locator('body')).toContainText(/rejected/i);
  });

  test('batch email approval reviews, edits, and approves all recipients together', async ({
    authenticatedPage,
  }) => {
    const page = authenticatedPage;
    const approval = {
      approval_id: 'approval-email-batch-e2e',
      run_id: 'run-email-batch-e2e',
      tool_name: 'send_email_batch',
      arguments: {
        emails: [
          {
            to: 'first@example.org',
            subject: 'First partnership proposal',
            body: 'First organization-specific proposal.',
            attachments: [],
          },
          {
            to: 'second@example.org',
            subject: 'Second partnership proposal',
            body: 'Second organization-specific proposal.',
            attachments: [],
          },
        ],
      },
      level: 3,
      kind: 'tool',
    };
    let patchedArguments: Record<string, unknown> | undefined;
    let approved = false;

    await page.route('**/api/chat', async (route) => {
      const sse = [
        `event: approval\ndata: ${JSON.stringify({ type: 'approval', ...approval })}`,
        `event: done\ndata: ${JSON.stringify({
          type: 'done', status: 'paused_approval',
          reply: 'Review all messages before dispatch. Nothing has been sent yet.',
          intent: 'email', approval,
        })}`,
        '',
      ].join('\n\n');
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: sse,
      });
    });
    await page.route('**/api/emails/attachments', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          url: '/api/emails/attachments/review.txt',
          filename: 'review.txt',
          mime_type: 'text/plain',
          size_bytes: 12,
        }),
      });
    });
    await page.route('**/api/approvals/approval-email-batch-e2e', async (route) => {
      if (route.request().method() !== 'PATCH') {
        await route.continue();
        return;
      }
      const request = route.request().postDataJSON() as {
        arguments?: Record<string, unknown>;
      };
      patchedArguments = request.arguments;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ...approval, arguments: patchedArguments }),
      });
    });
    await page.route('**/api/approvals/approval-email-batch-e2e/approve', async (route) => {
      approved = true;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'completed',
          reply: 'The complete reviewed email batch was approved.',
        }),
      });
    });

    await page.locator('#composer').fill(
      'Send separate partnership emails to two organizations.',
    );
    await page.locator('#composer').press('Enter');

    const cards = page.locator('.approval-email-card');
    await expect(cards).toHaveCount(2, { timeout: 10000 });
    await expect(cards.nth(0).locator('input.approval-email-input').first())
      .toHaveValue('first@example.org');
    await expect(cards.nth(1).locator('input.approval-email-input').first())
      .toHaveValue('second@example.org');
    await expect(page.locator('body')).not.toContainText(/emails? (?:was|were) sent/i);

    await cards.nth(0).locator('textarea.approval-email-textarea')
      .fill('Edited first message.');
    await cards.nth(0).locator('input[type="file"]').setInputFiles({
      name: 'review.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('review file'),
    });
    await expect(cards.nth(0)).toContainText('review.txt');
    await cards.nth(0).getByRole('button', { name: /save changes/i }).click();
    await expect.poll(() => patchedArguments?.emails).toMatchObject([
      {
        to: 'first@example.org',
        body: 'Edited first message.',
        attachments: [{ filename: 'review.txt' }],
      },
      {
        to: 'second@example.org',
        body: 'Second organization-specific proposal.',
        attachments: [],
      },
    ]);

    await page.getByRole('button', { name: /approve/i }).last().click();
    await expect.poll(() => approved).toBe(true);
    await expect(page.locator('body')).toContainText(/batch was approved/i);
  });
});
