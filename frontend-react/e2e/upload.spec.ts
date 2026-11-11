import { test, expect } from './fixtures/test-fixtures';

/** E2E: File upload — upload a document and verify it appears. */
test.describe('File upload', () => {
  test('can upload a file and see it in the attachment list', async ({ authenticatedPage }) => {
    // Find the file input — could be in a toolbar, chat composer, or attachment area.
    const fileInput = authenticatedPage.locator('input[type="file"]').first();

    await fileInput.setInputFiles({
      name: 'upload-test.txt',
      mimeType: 'text/plain',
      buffer: Buffer.from('This is a test file for Playwright e2e upload.'),
    });

    // Wait for the upload to complete and the file to appear.
    await expect(authenticatedPage.locator('.pending-media')).toBeVisible({
      timeout: 15000,
    });
  });
});
