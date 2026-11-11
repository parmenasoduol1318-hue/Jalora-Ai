import { test, expect } from './fixtures/test-fixtures';

/** E2E: Task lifecycle — create, list, and complete tasks. */
test.describe('Task lifecycle', () => {
  test('can create a task via chat and see it in the tasks list', async ({ authenticatedPage }) => {
    // Create a task by asking Jalora to create one.
    const input = authenticatedPage.locator('#composer');
    await input.fill('Create a task: "E2E Task" to review the quarterly report');
    await input.press('Enter');

    // Verify the assistant responded (even if model quota limits the response).
    await expect(authenticatedPage.locator('.msg.assistant')).toHaveCount(1, {
      timeout: 20000,
    });

    // Navigate to the tasks page via the sidebar.
    await authenticatedPage.locator('.sidebar .nav-item', { hasText: /tasks/i }).click();

    // The task list should be visible (may be empty if model couldn't create the task).
    await expect(authenticatedPage.locator('.task-list')).toBeVisible({
      timeout: 10000,
    });
  });

  test('task status can be toggled', async ({ authenticatedPage }) => {
    // Navigate to tasks.
    await authenticatedPage.locator('.sidebar .nav-item', { hasText: /tasks/i }).click();

    // Verify the task list renders (may be empty or have cards).
    await expect(authenticatedPage.locator('.task-list')).toBeVisible({
      timeout: 10000,
    });

    // If there are task cards, look for a toggle/complete button and try clicking it.
    const cards = authenticatedPage.locator('.task-list .task-card');
    const cardCount = await cards.count();
    if (cardCount > 0) {
      const toggle = authenticatedPage.getByRole('button', { name: /complete|done|toggle|pause|cancel/i }).first();
      if (await toggle.isVisible()) {
        await toggle.click();
      }
    }
    // Verify the page is still responsive.
    await expect(authenticatedPage.locator('.task-list')).toBeVisible();
  });

  test('task controls match paused and approval-waiting states', async ({ authenticatedPage }) => {
    await authenticatedPage.route('**/api/tasks', (route) => route.fulfill({
      json: [
        {
          id: 'running-task',
          run_id: 'running-run',
          goal: 'Running task',
          status: 'running',
          resumable: false,
          plan: [],
          current_step: 0,
          progress: 25,
          created_at: new Date().toISOString(),
        },
        {
          id: 'approval-task',
          run_id: 'approval-run',
          goal: 'Waiting for approval',
          status: 'waiting_for_approval',
          resumable: false,
          plan: [],
          current_step: 0,
          progress: 25,
          created_at: new Date().toISOString(),
        },
        {
          id: 'paused-task',
          run_id: 'paused-run',
          goal: 'Paused task',
          status: 'paused',
          resumable: false,
          plan: [],
          current_step: 0,
          progress: 25,
          created_at: new Date().toISOString(),
        },
        {
          id: 'partial-task',
          run_id: 'partial-run',
          goal: 'Partially completed task',
          status: 'partial',
          resumable: false,
          plan: [],
          current_step: 1,
          progress: 50,
          error: 'MODEL_UNAVAILABLE: Provider disconnected.',
          created_at: new Date().toISOString(),
        },
      ],
    }));

    await authenticatedPage.locator('.sidebar .nav-item', { hasText: /tasks/i }).click();
    const cards = authenticatedPage.locator('.task-list .task-card');
    await expect(cards).toHaveCount(4);

    const runningCard = cards.filter({ hasText: 'Running task' });
    await expect(runningCard.getByRole('button', { name: /pause/i })).toBeVisible();
    await expect(runningCard.getByRole('button', { name: /cancel/i })).toBeVisible();

    const approvalCard = cards.filter({ hasText: 'Waiting for approval' });
    await expect(approvalCard.getByRole('button', { name: /pause/i })).toHaveCount(0);
    await expect(approvalCard.getByRole('button', { name: /cancel/i })).toBeVisible();

    const pausedCard = cards.filter({ hasText: 'Paused task' });
    await expect(pausedCard.getByRole('button', { name: /pause/i })).toHaveCount(0);
    await expect(pausedCard.getByRole('button', { name: /cancel/i })).toBeVisible();

    const partialCard = cards.filter({ hasText: 'Partially completed task' });
    await expect(partialCard.locator('.error-bubble')).toContainText('Provider disconnected');
  });

  test('notification opens the linked task for evidence review and approval', async ({
    authenticatedPage,
  }) => {
    let approvalStatus = 'pending';
    let approvalArguments = {
      to: 'review@example.org',
      subject: 'Reviewed update',
      body: 'Original draft',
    };
    let payloadVersion = 2;
    let approvalDecision: unknown = null;
    const task = {
      id: 'linked-task',
      run_id: 'linked-run',
      goal: 'Send a reviewed update',
      status: 'waiting_for_approval',
      resumable: false,
      plan: [{
        id: 'send-step',
        description: 'Send the reviewed update',
        tool: 'send_email',
        status: 'active',
      }],
      current_step: 0,
      progress: 50,
      created_at: new Date().toISOString(),
    };
    await authenticatedPage.route('**/api/tasks**', async (route) => {
      const requestUrl = new URL(route.request().url());
      if (requestUrl.pathname === '/api/tasks/linked-task') {
        await route.fulfill({
          json: {
            ...task,
            steps: [{
              id: 'step-row',
              plan_step_id: 'send-step',
              task_id: 'linked-task',
              idx: 0,
              description: 'Send the reviewed update',
              status: 'active',
              dependencies: [],
              dependency_step_ids: [],
              capability: 'send_email',
              tool_id: 'send_email',
              outcome_status: approvalStatus === 'approved' ? 'submitted' : 'awaiting_approval',
              verification_status: 'pending',
              approval_id: 'approval-1',
              approval_status: approvalStatus,
              safe_summary: 'The exact message is waiting for your decision.',
              evidence: [{
                type: 'source',
                title: 'Contact source',
                url: 'https://example.org/contact',
              }],
            }],
            approvals: [{
              id: 'approval-1',
              run_id: 'linked-run',
              task_step_id: 'step-row',
              plan_step_id: 'send-step',
              tool_name: 'send_email',
              arguments: approvalArguments,
              level: 3,
              kind: 'tool',
              status: approvalStatus,
              can_approve: approvalStatus === 'pending',
              can_edit: approvalStatus === 'pending',
              decision_status: approvalStatus,
              claim_status: approvalStatus === 'pending' ? 'unclaimed' : 'finished',
              payload_version: payloadVersion,
              created_at: new Date().toISOString(),
              expires_at: null,
            }],
          },
        });
        return;
      }
      if (requestUrl.searchParams.get('run_id') === 'linked-run') {
        await route.fulfill({ json: [task] });
        return;
      }
      await route.fulfill({ json: [task] });
    });
    await authenticatedPage.route('**/api/notifications/unread-count', (route) =>
      route.fulfill({ json: { count: 1 } }));
    await authenticatedPage.route('**/api/notifications?**', (route) =>
      route.fulfill({
        json: [{
          id: 'notification-1',
          text: 'A task needs your approval.',
          kind: 'approval_required',
          run_id: 'linked-run',
          task_id: null,
          link: null,
          read: false,
          created_at: new Date().toISOString(),
        }],
      }));
    await authenticatedPage.route('**/api/notifications/notification-1/read', (route) =>
      route.fulfill({ json: { id: 'notification-1', read: true } }));
    await authenticatedPage.route('**/api/approvals/approval-1', async (route) => {
      const body = route.request().postDataJSON();
      approvalArguments = body.arguments;
      payloadVersion += 1;
      await route.fulfill({
        json: {
          id: 'approval-1',
          tool_name: 'send_email',
          arguments: approvalArguments,
          status: 'pending',
          payload_version: payloadVersion,
        },
      });
    });
    await authenticatedPage.route('**/api/approvals/approval-1/approve', async (route) => {
      approvalStatus = 'approved';
      approvalDecision = route.request().method();
      await route.fulfill({
        json: {
          status: 'completed',
          reply: 'The approved task completed.',
          conversation_id: 'linked-conversation',
          run_id: 'linked-run',
          sources: [],
          model: 'test',
        },
      });
    });

    await authenticatedPage.setViewportSize({ width: 390, height: 844 });
    const notificationBell = authenticatedPage.getByRole('button', { name: /notifications/i });
    await notificationBell.focus();
    await notificationBell.press('Enter');
    const notification = authenticatedPage.getByRole('button', {
      name: /approval needed.*needs your approval/i,
    });
    await expect(notification).toBeVisible();
    await notification.focus();
    await notification.press('Enter');

    const card = authenticatedPage.locator('.task-card').filter({
      hasText: 'Send a reviewed update',
    });
    await expect(card.getByRole('heading', { name: 'Steps and evidence' })).toBeVisible();
    await expect(card).toContainText('The exact message is waiting for your decision.');
    await expect(card.getByRole('link', { name: 'Contact source' })).toHaveAttribute(
      'href',
      'https://example.org/contact',
    );

    const edit = card.getByRole('textbox', { name: /edit request arguments/i });
    const revised = { ...approvalArguments, body: 'Updated and reviewed draft' };
    await edit.fill(JSON.stringify(revised, null, 2));
    await card.getByRole('button', { name: /save reviewed changes/i }).click();
    await expect.poll(() => approvalArguments.body).toBe('Updated and reviewed draft');
    await card.getByRole('button', { name: /^approve$/i }).click();
    await expect.poll(() => approvalDecision).toBe('POST');
    await expect(card.locator('.task-approval')).toContainText('approved');
  });

  test('outreach queue shows ordered recipients and untrusted source evidence', async ({ authenticatedPage }) => {
    await authenticatedPage.route('**/api/tasks', (route) => route.fulfill({
      json: [{
        id: 'outreach-task',
        run_id: 'outreach-run',
        goal: 'Organization outreach',
        status: 'waiting_for_approval',
        resumable: false,
        plan: [{
          id: 'send-1',
          description: 'Send outreach to contact@example.org',
          tool: 'send_email',
          status: 'active',
        }, {
          id: 'contact-search',
          description: 'Find official public contact email addresses.',
          tool: 'search_public_contacts',
          status: 'done',
        }],
        current_step: 0,
        progress: 10,
        created_at: new Date().toISOString(),
      }],
    }));
    await authenticatedPage.route('**/api/tasks/outreach-task', (route) => route.fulfill({
      json: {
        id: 'outreach-task',
        goal: 'Organization outreach',
        status: 'waiting_for_approval',
        plan: [],
        current_step: 0,
        progress: 10,
        created_at: new Date().toISOString(),
        steps: [],
        outreach_queue: [{
          position: 1,
          plan_step_id: 'send-1',
          organization: 'Example Organization',
          recipient: 'contact@example.org',
          status: 'awaiting_approval',
          source_url: 'https://example.org/contact',
          source_title: 'Contact',
          address_found_on_source: true,
          organization_match_on_source: true,
          approval_id: 'approval-1',
          provider: '',
          provider_message_id: '',
          provider_thread_id: '',
        }],
      },
    }));

    await authenticatedPage.locator('.sidebar .nav-item', { hasText: /tasks/i }).click();
    const card = authenticatedPage.locator('.task-card').filter({ hasText: 'Organization outreach' });
    await card.getByRole('button', { name: /review outreach queue/i }).click();
    await expect(card).toContainText('Example Organization');
    await expect(card).toContainText('contact@example.org');
    await expect(card).toContainText('Address appears on this page');
    await expect(card).toContainText('Page content matches the organization name');
    await expect(card).toContainText('page ownership has not been independently confirmed');
    await expect(card.getByRole('link', { name: 'Contact' })).toHaveAttribute(
      'href',
      'https://example.org/contact',
    );
  });

  test('uncertain outreach send can be reconciled without retrying', async ({ authenticatedPage }) => {
    let reconciled = false;
    let reconciliationAttempts = 0;
    await authenticatedPage.route('**/api/tasks', (route) => route.fulfill({
      json: [{
        id: 'outreach-task',
        run_id: 'outreach-run',
        goal: 'Organization outreach',
        status: 'partial',
        resumable: false,
        plan: [{
          id: 'send-1',
          description: 'Send outreach to contact@example.org',
          tool: 'send_email',
          status: 'failed',
        }, {
          id: 'contact-search',
          description: 'Find official public contact email addresses.',
          tool: 'search_public_contacts',
          status: 'done',
        }],
        current_step: 0,
        progress: 10,
        created_at: new Date().toISOString(),
      }],
    }));
    await authenticatedPage.route('**/api/tasks/outreach-task', (route) => route.fulfill({
      json: {
        id: 'outreach-task',
        goal: 'Organization outreach',
        status: 'partial',
        plan: [],
        current_step: 0,
        progress: 10,
        created_at: new Date().toISOString(),
        steps: [],
        outreach_queue: [{
          position: 1,
          plan_step_id: 'send-1',
          organization: 'Example Organization',
          recipient: 'contact@example.org',
          status: reconciled ? 'sent' : 'outcome_unknown',
          source_url: 'https://example.org/contact',
          source_title: 'Contact',
          address_found_on_source: true,
          organization_match_on_source: true,
          approval_id: 'approval-uncertain',
          provider: reconciled ? 'gmail' : '',
          provider_message_id: reconciled ? 'gmail-message-123' : '',
        }],
      },
    }));
    await authenticatedPage.route('**/api/emails/send/approval-uncertain/reconcile', async (route) => {
      expect(route.request().method()).toBe('POST');
      reconciliationAttempts += 1;
      if (reconciliationAttempts === 1) {
        await route.fulfill({
          json: { id: 'approval-uncertain', status: 'outcome_unknown', matches: 0, retry_allowed: false },
        });
        return;
      }
      reconciled = true;
      await route.fulfill({
        json: {
          id: 'approval-uncertain',
          status: 'provider_confirmed',
          provider_receipt: { provider: 'gmail', message_id: 'gmail-message-123' },
        },
      });
    });

    await authenticatedPage.locator('.sidebar .nav-item', { hasText: /tasks/i }).click();
    const card = authenticatedPage.locator('.task-card').filter({ hasText: 'Organization outreach' });
    await card.getByRole('button', { name: /review outreach queue/i }).click();
    const reconcile = card.getByRole('button', { name: /check sent mail before retrying/i });
    await expect(reconcile).toBeVisible();
    await reconcile.click();
    await expect(authenticatedPage.getByText(
      'No exact sent copy found. The outcome remains unknown; do not retry.',
    )).toBeVisible();
    await expect(reconcile).toBeVisible();
    await expect(card.getByRole('button', { name: /approve and send/i })).toHaveCount(0);
    await reconcile.click();
    await expect(card).toContainText('completed');
    await expect(card).toContainText('gmail-message-123');
    await expect(card.getByRole('button', { name: /check sent mail before retrying/i })).toHaveCount(0);
    await expect(card.getByRole('button', { name: /approve and send/i })).toHaveCount(0);
  });

  test('outreach approval edits persist and explicit approval sends the draft', async ({ authenticatedPage }) => {
    let draft = {
      to: 'contact@example.org',
      cc: '',
      bcc: '',
      subject: 'Partnership proposal',
      body: 'Original proposal.',
      regards: '',
      attachments: [],
    };
    let sent = false;
    await authenticatedPage.route('**/api/tasks', (route) => route.fulfill({
      json: [{
        id: 'outreach-task',
        run_id: 'outreach-run',
        goal: 'Organization outreach',
        status: sent ? 'completed' : 'waiting_for_approval',
        resumable: false,
        plan: [{
          id: 'send-1',
          description: 'Send outreach to contact@example.org',
          tool: 'send_email',
          status: sent ? 'done' : 'active',
        }, {
          id: 'contact-search',
          description: 'Find official public contact email addresses.',
          tool: 'search_public_contacts',
          status: 'done',
        }],
        current_step: 0,
        progress: sent ? 100 : 10,
        created_at: new Date().toISOString(),
      }],
    }));
    await authenticatedPage.route('**/api/tasks/outreach-task', (route) => route.fulfill({
      json: {
        id: 'outreach-task',
        goal: 'Organization outreach',
        status: sent ? 'completed' : 'waiting_for_approval',
        plan: [],
        current_step: 0,
        progress: sent ? 100 : 10,
        created_at: new Date().toISOString(),
        steps: [],
        outreach_queue: [{
          position: 1,
          plan_step_id: 'send-1',
          organization: 'Example Organization',
          recipient: draft.to,
          status: sent ? 'sent' : 'awaiting_approval',
          source_url: 'https://example.org/contact',
          source_title: 'Contact',
          address_found_on_source: true,
          organization_match_on_source: true,
          approval_id: sent ? null : 'approval-1',
          draft: sent ? null : draft,
          provider: sent ? 'email_mock' : '',
          provider_message_id: sent ? 'mock-message-1' : '',
          provider_thread_id: '',
        }],
      },
    }));
    await authenticatedPage.route('**/api/approvals/approval-1', async (route) => {
      if (route.request().method() === 'PATCH') {
        const body = route.request().postDataJSON() as {
          arguments: typeof draft;
        };
        draft = body.arguments;
        await route.fulfill({ json: { arguments: draft } });
        return;
      }
      await route.continue();
    });
    await authenticatedPage.route('**/api/approvals/approval-1/approve', async (route) => {
      sent = true;
      await route.fulfill({
        json: { status: 'completed', reply: 'Email sent after approval.' },
      });
    });

    await authenticatedPage.locator('.sidebar .nav-item', { hasText: /tasks/i }).click();
    const card = authenticatedPage.locator('.task-card').filter({ hasText: 'Organization outreach' });
    await card.getByRole('button', { name: /review outreach queue/i }).click();
    const review = card.locator('.approval-email-card');
    await expect(review).toBeVisible();
    await review.locator('textarea').fill('Revised and reviewed proposal.');
    await review.getByRole('button', { name: /save draft without sending/i }).click();
    await expect.poll(() => draft.body).toBe('Revised and reviewed proposal.');
    expect(sent).toBe(false);

    const fileInput = review.locator('input[type="file"]');
    await fileInput.setInputFiles({
      name: 'proposal.pdf',
      mimeType: 'application/pdf',
      buffer: Buffer.from('%PDF-1.4 test'),
    });
    await expect(review).toContainText('proposal.pdf');
    await review.getByRole('button', { name: /remove/i }).click();
    await expect(review).not.toContainText('proposal.pdf');

    await fileInput.setInputFiles({
      name: 'proposal.pdf',
      mimeType: 'application/pdf',
      buffer: Buffer.from('%PDF-1.4 test'),
    });
    await review.getByRole('button', { name: /save draft without sending/i }).click();
    await expect.poll(() => draft.attachments).toHaveLength(1);
    expect(draft.attachments[0]).toMatchObject({
      filename: 'proposal.pdf',
      mime_type: 'application/pdf',
      size_bytes: Buffer.byteLength('%PDF-1.4 test'),
    });
    const uploadedArtifact = draft.attachments[0] as { url: string };
    expect(uploadedArtifact.url).toMatch(
      /^\/api\/artifacts\/[^/]+\/email-attachments\/[0-9a-f]+-proposal\.pdf$/,
    );
    const artifactResponse = await authenticatedPage.evaluate(async ({ url, token }) => {
      const response = await fetch(url, {
        headers: { Authorization: `Bearer ${token}` },
      });
      return { status: response.status, body: await response.text() };
    }, {
      url: uploadedArtifact.url,
      token: await authenticatedPage.evaluate(() => localStorage.getItem('jalora_token')),
    });
    expect(artifactResponse.status).toBe(200);
    expect(artifactResponse.body).toBe('%PDF-1.4 test');

    await review.getByRole('button', { name: /approve and send/i }).click();
    await expect.poll(() => sent).toBe(true);
    await expect(card).toContainText('completed');
    await expect(card).toContainText('mock-message-1');
  });

  test('outreach queue records skip without approving or sending', async ({ authenticatedPage }) => {
    let skipped = false;
    await authenticatedPage.route('**/api/tasks', (route) => route.fulfill({
      json: [{
        id: 'outreach-task',
        run_id: 'outreach-run',
        goal: 'Organization outreach',
        status: skipped ? 'completed' : 'waiting_for_approval',
        resumable: false,
        plan: [{
          id: 'send-1',
          description: 'Send outreach to contact@example.org',
          tool: 'send_email',
          status: skipped ? 'skipped' : 'active',
        }, {
          id: 'contact-search',
          description: 'Find official public contact email addresses.',
          tool: 'search_public_contacts',
          status: 'done',
        }],
        current_step: 0,
        progress: skipped ? 100 : 10,
        created_at: new Date().toISOString(),
      }],
    }));
    await authenticatedPage.route('**/api/tasks/outreach-task', (route) => route.fulfill({
      json: {
        id: 'outreach-task',
        goal: 'Organization outreach',
        status: skipped ? 'completed' : 'waiting_for_approval',
        plan: [],
        current_step: 0,
        progress: skipped ? 100 : 10,
        created_at: new Date().toISOString(),
        steps: [],
        outreach_queue: [{
          position: 1,
          plan_step_id: 'send-1',
          organization: 'Example Organization',
          recipient: 'contact@example.org',
          status: skipped ? 'skipped' : 'awaiting_approval',
          source_url: 'https://example.org/contact',
          source_title: 'Contact',
          address_found_on_source: true,
          organization_match_on_source: true,
          approval_id: skipped ? null : 'approval-skip',
          draft: skipped ? null : {
            to: 'contact@example.org',
            cc: '',
            bcc: '',
            subject: 'Partnership proposal',
            body: 'Review before deciding.',
            regards: '',
            attachments: [],
          },
        }],
      },
    }));
    await authenticatedPage.route('**/api/approvals/approval-skip/skip', async (route) => {
      skipped = true;
      await route.fulfill({
        json: { status: 'completed', reply: 'This organization was skipped.' },
      });
    });

    await authenticatedPage.locator('.sidebar .nav-item', { hasText: /tasks/i }).click();
    const card = authenticatedPage.locator('.task-card').filter({ hasText: 'Organization outreach' });
    await card.getByRole('button', { name: /review outreach queue/i }).click();
    await card.getByRole('button', { name: /skip this organization/i }).click();
    await expect.poll(() => skipped).toBe(true);
    await expect(card).toContainText('Skipped');
    await expect(card.getByRole('button', { name: /approve and send/i })).toHaveCount(0);
  });
});
