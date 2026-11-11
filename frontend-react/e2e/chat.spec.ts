import { test, expect } from './fixtures/test-fixtures';

/** E2E: Chat + streaming — send a message and see a streamed response. */
test.describe('Chat & streaming', () => {
  test('can send a message and receive a (possibly streamed) response', async ({
    authenticatedPage,
  }) => {
    await authenticatedPage.route('**/api/chat', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: 'event: done\ndata: {"type":"done","success":true,"reply":"A deterministic test response.","intent":"chat","sources":[]}\n\n',
      });
    });
    const input = authenticatedPage.locator('#composer');

    await input.fill('Hello, this is a test message');
    await input.press('Enter');

    await expect(authenticatedPage.locator('.msg.user')).toContainText(
      'Hello, this is a test message',
    );
    await expect(authenticatedPage.locator('.msg.assistant')).toContainText(
      'A deterministic test response.',
    );
  });

  test('message input is cleared after sending', async ({ authenticatedPage }) => {
    const input = authenticatedPage.locator('#composer');
    await input.fill('Another test message');
    await input.press('Enter');

    await expect(input).toHaveValue('', { timeout: 15000 });
  });

  test('can copy Jalora replies and user tasks', async ({ authenticatedPage }) => {
    await authenticatedPage.context().grantPermissions(['clipboard-read', 'clipboard-write']);
    await authenticatedPage.route('**/api/chat', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: 'event: done\ndata: {"type":"done","success":true,"reply":"A copyable answer.","intent":"chat","sources":[]}\n\n',
      });
    });

    const task = 'Please summarize this policy.';
    const input = authenticatedPage.locator('#composer');
    await input.fill(task);
    await input.press('Enter');
    await expect(authenticatedPage.getByText('A copyable answer.', { exact: true })).toBeVisible();

    await authenticatedPage.getByRole('button', { name: 'Copy response' }).click();
    await expect.poll(() =>
      authenticatedPage.evaluate(() => navigator.clipboard.readText()),
    ).toBe('A copyable answer.');

    await authenticatedPage.getByRole('button', { name: 'Copy task' }).click();
    await expect.poll(() =>
      authenticatedPage.evaluate(() => navigator.clipboard.readText()),
    ).toBe(task);
  });

  test('copies math replies exactly, including LaTeX and whitespace', async ({
    authenticatedPage,
  }) => {
    await authenticatedPage.context().grantPermissions(['clipboard-read', 'clipboard-write']);
    const reply = [
      '',
      '### Step 1: Calculate the subtotal',
      '',
      '* **Formula:**',
      '  $$\\text{Subtotal} = \\text{Quantity} \\times \\text{Price per ton}$$',
      '* **Calculation:**',
      '  $$14.5 \\times 110,000 = 1,595,000$$',
      '',
      '> **Answer:** KES 1,850,200 (Inclusive of 16% VAT)',
      '',
    ].join('\n');
    await authenticatedPage.route('**/api/chat', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `event: done\ndata: ${JSON.stringify({
          type: 'done',
          success: true,
          reply,
          intent: 'math',
          sources: [],
        })}\n\n`,
      });
    });

    const input = authenticatedPage.locator('#composer');
    await input.fill('Calculate the total cost including VAT.');
    await input.press('Enter');
    await expect(authenticatedPage.getByRole('button', { name: 'Copy response' })).toBeVisible();
    await authenticatedPage.getByRole('button', { name: 'Copy response' }).click();

    await expect.poll(() =>
      authenticatedPage.evaluate(() => navigator.clipboard.readText()),
    ).toBe(process.platform === 'win32' ? reply.replace(/\n/g, '\r\n') : reply);
  });

  test('renders markdown tables with clear borders and copies equation source', async ({
    authenticatedPage,
  }) => {
    await authenticatedPage.context().grantPermissions(['clipboard-read', 'clipboard-write']);
    const reply = [
      '| Team | Hours |',
      '| --- | ---: |',
      '| Team A | 12 |',
      '',
      'The limit is $$12 \\le 16$$.',
    ].join('\n');
    await authenticatedPage.route('**/api/chat', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `event: done\ndata: ${JSON.stringify({
          type: 'done',
          success: true,
          reply,
          intent: 'math',
          sources: [],
        })}\n\n`,
      });
    });

    const input = authenticatedPage.locator('#composer');
    await input.fill('Check these hours.');
    await input.press('Enter');
    const table = authenticatedPage.locator('.msg .content table');
    await expect(table).toBeVisible();
    await expect(table.locator('thead th')).toHaveText(['Team', 'Hours']);
    await expect(table.locator('tbody td')).toHaveText(['Team A', '12']);
    await expect(table.locator('th').first()).toHaveCSS('border-bottom-style', 'solid');

    const equationCopy = authenticatedPage.locator('.math-copy');
    await expect(equationCopy).toBeVisible();
    await equationCopy.click();
    await expect.poll(() =>
      authenticatedPage.evaluate(() => navigator.clipboard.readText()),
    ).toBe('12 \\le 16');
  });

  test('home shortcuts select capability intents and send the user text unchanged', async ({
    authenticatedPage,
  }) => {
    const requests: Array<{
      message: string;
      intent_hint?: string;
      capability_hint?: string;
    }> = [];
    await authenticatedPage.route('**/api/chat', async route => {
      requests.push(route.request().postDataJSON() as {
        message: string;
        intent_hint?: string;
        capability_hint?: string;
      });
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: 'event: done\ndata: {"type":"done","success":true,"reply":"Shortcut accepted.","intent":"chat","sources":[]}\n\n',
      });
    });

    const shortcuts = [
      { label: /Search Web/, intent: 'web_search', category: 'research' },
      { label: /Write an Email/, intent: 'email', category: 'email' },
      { label: /Do Mathematics/, intent: 'math', category: 'math' },
      { label: /Create a Plan/, intent: 'task', category: 'internal' },
      { label: /Summarise/, intent: 'research', category: 'research' },
      { label: /Code/, intent: 'coding', category: 'coding' },
      { label: /Job Search/, intent: 'research', category: 'jobs' },
    ];

    for (const [index, shortcut] of shortcuts.entries()) {
      if (index > 0) {
        await authenticatedPage.locator('#btn-new-chat').click();
        await expect(authenticatedPage.locator('.suggestions-grid')).toBeVisible();
      }
      await authenticatedPage.getByRole('button', { name: shortcut.label }).click();
      await expect(authenticatedPage.locator('.quick-action-context')).toContainText(
        (await authenticatedPage.getByRole('button', { name: shortcut.label }).innerText())
          .split('\n')[0],
      );
      await expect(authenticatedPage.locator('.prompt-dialog')).toHaveCount(0);
      const input = authenticatedPage.locator('#composer');
      await input.fill('Explain this request in plain language.');
      await input.press('Enter');
      await expect(authenticatedPage.getByText('Shortcut accepted.', { exact: true })).toBeVisible();
      expect(requests[index]).toMatchObject({
        message: 'Explain this request in plain language.',
        intent_hint: shortcut.intent,
        capability_hint: shortcut.category,
      });
    }

    await authenticatedPage.locator('#btn-new-chat').click();
    await authenticatedPage.getByRole('button', { name: /More/ }).click();
    for (const [index, shortcut] of [
      { label: /Call/, intent: 'task', category: 'comms' },
      { label: /Translate/, intent: 'task', category: 'translate' },
      { label: /Analyze Document/, intent: 'task', category: 'documents' },
    ].entries()) {
      if (index > 0) {
        await authenticatedPage.locator('#btn-new-chat').click();
        await authenticatedPage.getByRole('button', { name: /More/ }).click();
      }
      await authenticatedPage.getByRole('button', { name: shortcut.label }).click();
      await expect(authenticatedPage.locator('.prompt-backdrop')).toHaveCount(0);
      await authenticatedPage.locator('#composer').fill('Explain this request in plain language.');
      await authenticatedPage.locator('#composer').press('Enter');
      await expect(authenticatedPage.getByText('Shortcut accepted.', { exact: true })).toBeVisible();
      expect(requests[index + shortcuts.length]).toMatchObject({
        message: 'Explain this request in plain language.',
        intent_hint: shortcut.intent,
        capability_hint: shortcut.category,
      });
    }
  });

  test('composer returns to its compact height after sending a multiline task', async ({
    authenticatedPage,
  }) => {
    await authenticatedPage.route('**/api/chat', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: 'event: done\ndata: {"type":"done","success":true,"reply":"Task received.","intent":"task","sources":[]}\n\n',
      });
    });

    const input = authenticatedPage.locator('#composer');
    await input.fill('Line one\nLine two\nLine three\nLine four');
    expect(await input.evaluate((element) => parseFloat((element as HTMLTextAreaElement).style.height)))
      .toBeGreaterThan(32);
    await input.press('Enter');
    await expect(input).toHaveValue('');
    await expect.poll(() =>
      input.evaluate((element) => parseFloat((element as HTMLTextAreaElement).style.height)),
    ).toBeLessThanOrEqual(40);
  });

  test('multi-intent task reaches chat intact without a shortcut override', async ({
    authenticatedPage,
  }) => {
    let requestBody: Record<string, unknown> | undefined;
    await authenticatedPage.route('**/api/chat', async route => {
      requestBody = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: 'event: done\ndata: {"type":"done","success":true,"reply":"Combined task received.","intent":"task","sources":[]}\n\n',
      });
    });

    const task = 'Search the latest Kenya inflation rate, calculate a 10% adjustment, then draft a short report.';
    await authenticatedPage.locator('#composer').fill(task);
    await authenticatedPage.locator('#composer').press('Enter');
    await expect(authenticatedPage.getByText('Combined task received.', { exact: true })).toBeVisible();
    expect(requestBody?.message).toBe(task);
    expect(requestBody?.intent_hint).toBeUndefined();
  });

  test('can edit a prior task and resend it as a new message', async ({
    authenticatedPage,
  }) => {
    const sentMessages: string[] = [];
    let responseNumber = 0;
    await authenticatedPage.route('**/api/chat', async route => {
      const request = route.request().postDataJSON() as { message: string };
      sentMessages.push(request.message);
      responseNumber += 1;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: `event: done\ndata: ${JSON.stringify({
          type: 'done',
          success: true,
          reply: `Response ${responseNumber}.`,
          intent: 'chat',
          sources: [],
        })}\n\n`,
      });
    });

    const input = authenticatedPage.locator('#composer');
    await input.fill('Find laptop options.');
    await input.press('Enter');
    await expect(authenticatedPage.getByText('Response 1.', { exact: true })).toBeVisible();

    await authenticatedPage.getByRole('button', { name: 'Edit task' }).click();
    await expect(input).toHaveValue('Find laptop options.');
    await expect(authenticatedPage.getByText('Editing task')).toBeVisible();
    await input.fill('Find laptop options under KSh 80,000.');
    await authenticatedPage.locator('#btn-send').click();

    await expect(authenticatedPage.getByText('Response 2.', { exact: true })).toBeVisible();
    expect(sentMessages).toEqual([
      'Find laptop options.',
      'Find laptop options under KSh 80,000.',
    ]);
    await expect(authenticatedPage.getByText('Find laptop options.', { exact: true })).toBeVisible();
    await expect(authenticatedPage.getByText(
      'Find laptop options under KSh 80,000.',
      { exact: true },
    )).toBeVisible();
    await expect(authenticatedPage.getByText('Editing task')).not.toBeVisible();
  });

  test('done event reply remains authoritative after streaming cleanup', async ({ authenticatedPage }) => {
    await authenticatedPage.route('**/api/chat', async route => {
      const body = [
        'event: token\ndata: {"type":"token","text":"model draft"}\n\n',
        'event: done\ndata: {"type":"done","success":true,"reply":"authoritative final","intent":"chat","sources":[]}\n\n',
      ].join('');
      await route.fulfill({ status: 200, contentType: 'text/event-stream', body });
    });
    const input = authenticatedPage.locator('#composer');
    await input.fill('test terminal reply');
    await input.press('Enter');
    await expect(authenticatedPage.getByText('authoritative final', { exact: true })).toBeVisible({ timeout: 10000 });
    await expect(authenticatedPage.getByText('model draft', { exact: true })).not.toBeVisible();
  });

  test('multi-step task with an email dispatch uses the agent workflow', async ({
    authenticatedPage,
  }) => {
    let emailTaskCalled = false;
    let chatRequest: Record<string, unknown> | undefined;
    await authenticatedPage.route('**/api/emails/task', async route => {
      emailTaskCalled = true;
      await route.fulfill({ status: 500, body: 'Must use the agent workflow' });
    });
    await authenticatedPage.route('**/api/chat', async route => {
      chatRequest = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: 'event: done\ndata: {"type":"done","success":true,"reply":"Workflow started.","intent":"research","sources":[]}\n\n',
      });
    });

    const message = [
      'Please execute the following fully dependent sequence:',
      'Step 1: Search the current steel price in Kenya.',
      'Step 2: Calculate a budget from the retrieved price.',
      'Step 3: Generate a procurement report.',
      'Step 4: Create a calendar event, then send an email to parmenas@example.com with the report.',
    ].join('\n');
    const input = authenticatedPage.locator('#composer');
    await input.fill(message);
    await input.press('Enter');

    await expect(authenticatedPage.getByText('Workflow started.', { exact: true })).toBeVisible();
    expect(emailTaskCalled).toBe(false);
    expect(chatRequest?.message).toBe(message);
  });

  test('standalone email request still uses the editable email draft flow', async ({
    authenticatedPage,
  }) => {
    let chatCalled = false;
    await authenticatedPage.route('**/api/emails/task', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'paused_approval',
          approval: {
            id: 'email-approval-test',
            tool_name: 'send_email',
            arguments: {
              to: 'junior@example.com',
              subject: 'A quick update',
              body: 'I am ready to share the update.',
              regards: 'Regards,',
              attachments: [],
            },
            level: 3,
            status: 'pending',
            standalone: true,
          },
        }),
      });
    });
    await authenticatedPage.route('**/api/chat', async route => {
      chatCalled = true;
      await route.fulfill({ status: 500, body: 'Standalone email must not use chat' });
    });

    const input = authenticatedPage.locator('#composer');
    await input.fill('Send an email to junior@example.com saying I am ready to share the update.');
    await input.press('Enter');

    await expect(authenticatedPage.getByText(
      'Review and edit the email below. It will only be sent if you approve.',
      { exact: true },
    ).last()).toBeVisible();
    expect(chatCalled).toBe(false);
  });

  test('first message keeps its text visible when a date separator is shown', async ({
    authenticatedPage,
  }) => {
    await authenticatedPage.route('**/api/chat', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: 'event: done\ndata: {"type":"done","success":true,"reply":"Acknowledged.","intent":"chat","sources":[]}\n\n',
      });
    });

    const message = 'First message of the day stays visible';
    const input = authenticatedPage.locator('#composer');
    await input.fill(message);
    await input.press('Enter');

    const userContent = authenticatedPage.locator('.msg.user .content');
    await expect(userContent).toContainText(message);
    const dateSeparator = authenticatedPage.locator('.date-separator').first();
    await expect(dateSeparator).toBeVisible();
    const insideMessageRow = await dateSeparator.evaluate(
      element => element.parentElement?.classList.contains('msg') ?? false,
    );
    expect(insideMessageRow).toBe(false);

    const firstBounds = await userContent.first().boundingBox();
    expect(firstBounds?.width).toBeGreaterThan(100);

    await expect(authenticatedPage.getByText('Acknowledged.', { exact: true })).toBeVisible();
    await input.fill('A second message on the same date');
    await input.press('Enter');
    await expect(userContent).toHaveCount(2);
    await expect(userContent.nth(1)).toContainText('A second message on the same date');
    const secondBounds = await userContent.nth(1).boundingBox();
    expect(secondBounds?.width).toBeGreaterThan(100);
    await expect(authenticatedPage.locator('.date-separator')).toHaveCount(1);
  });

  test('invalid saved timezone does not crash chat date rendering', async ({
    authenticatedPage,
  }) => {
    const pageErrors: string[] = [];
    authenticatedPage.on('pageerror', error => pageErrors.push(error.message));
    await authenticatedPage.route('**/api/settings', async route => {
      const response = await route.fetch();
      const settings = await response.json();
      await route.fulfill({ response, json: { ...settings, timezone: 'UTC+3' } });
    });
    await authenticatedPage.route('**/api/chat', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: 'event: done\ndata: {"type":"done","success":true,"reply":"Timezone fallback works.","intent":"chat","sources":[]}\n\n',
      });
    });
    const settingsResponse = authenticatedPage.waitForResponse(response =>
      response.url().endsWith('/api/settings'),
    );
    await authenticatedPage.reload();
    await settingsResponse;

    const input = authenticatedPage.locator('#composer');
    await input.waitFor({ state: 'visible' });
    await input.fill('Check message dates with a legacy timezone');
    await input.press('Enter');

    await expect(authenticatedPage.getByText('Timezone fallback works.', { exact: true })).toBeVisible();
    await expect(authenticatedPage.locator('.date-separator')).toBeVisible();
    expect(pageErrors).toEqual([]);
  });
});
