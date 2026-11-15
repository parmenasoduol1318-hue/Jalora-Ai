import { test, expect } from './fixtures/test-fixtures';

test('idea projects can be created, edited, archived, reopened, and survive reload', async ({
  authenticatedPage,
}) => {
  await authenticatedPage.locator('.sidebar .nav-item', { hasText: /idea projects/i }).click();
  await authenticatedPage.getByRole('button', { name: /new idea/i }).click();

  await authenticatedPage.getByTestId('idea-title').fill('Neighborhood garden');
  await authenticatedPage.getByTestId('idea-brief').fill('A shared growing space.');
  await authenticatedPage.getByLabel('Next steps').fill('Ask neighbors to join');
  await authenticatedPage.getByRole('button', { name: /save project/i }).click();

  const projectRow = authenticatedPage.locator('.idea-project-row', {
    hasText: 'Neighborhood garden',
  });
  await expect(projectRow).toBeVisible();
  await authenticatedPage.getByLabel('Next steps').fill('Reserve a location');
  await authenticatedPage.getByRole('button', { name: /save project/i }).click();

  await authenticatedPage.getByRole('button', { name: /^archive$/i }).click();
  await authenticatedPage.getByRole('button', { name: /show archived/i }).click();
  await expect(projectRow).toContainText('Archived');
  await authenticatedPage.getByRole('button', { name: /^reopen$/i }).click();
  await expect(projectRow).not.toContainText('Archived');

  await authenticatedPage.reload();
  await authenticatedPage.locator('.sidebar .nav-item', { hasText: /idea projects/i }).click();
  await expect(authenticatedPage.locator('.idea-project-row', {
    hasText: 'Neighborhood garden',
  })).toBeVisible();
});
