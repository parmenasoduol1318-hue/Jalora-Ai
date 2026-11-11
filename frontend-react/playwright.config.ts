import { defineConfig, devices } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const pythonExecutable =
  process.env.JALORA_E2E_PYTHON || process.env.PYTHON || 'python';
const requestedBrowserChannel = process.env.JALORA_E2E_BROWSER_CHANNEL;
const browserChannel =
  requestedBrowserChannel === 'chrome' || requestedBrowserChannel === 'msedge'
    ? requestedBrowserChannel
    : undefined;
if (requestedBrowserChannel && !browserChannel) {
  throw new Error(
    'JALORA_E2E_BROWSER_CHANNEL must be either "chrome" or "msedge".',
  );
}
const backendPort = process.env.JALORA_E2E_BACKEND_PORT || '8000';
const frontendPort = process.env.JALORA_E2E_FRONTEND_PORT || '5173';
const frontendURL = `http://127.0.0.1:${frontendPort}`;
const e2eDataDir =
  process.env.JALORA_E2E_DATA_DIR || join(tmpdir(), `jalora-e2e-${process.pid}`);
const e2eDatabaseUrl =
  process.env.JALORA_E2E_DATABASE_URL
  || `sqlite:///${join(e2eDataDir, 'e2e-test.db').replace(/\\/g, '/')}`;
mkdirSync(e2eDataDir, { recursive: true });

/**
 * Playwright e2e config for Jalora AI frontend.
 *
 * The backend (FastAPI) and frontend dev server are started automatically via
 * `webServer`. Port, Python executable, and test database can be overridden
 * when running beside an existing local instance.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  // The temporary E2E database is shared across tests; parallel workers race
  // on auth/session/calendar/task state.
  workers: 1,
  reporter: 'html',
  use: {
    baseURL: frontendURL,
    trace: 'on-first-attempt',
    screenshot: 'only-on-failure',
    video: 'off',
  },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        ...(browserChannel ? { channel: browserChannel } : {}),
      },
    },
  ],
  // Start the backend API and frontend dev server before running tests.
  webServer: [
    {
      command: `"${pythonExecutable}" -m uvicorn app.main:app --host 127.0.0.1 --port ${backendPort}`,
      cwd: '../backend',
      url: `http://127.0.0.1:${backendPort}/api/health`,
      timeout: 120_000,
      reuseExistingServer: !process.env.CI,
      env: {
        JALORA_ENV: 'test',
        JALORA_JWT_SECRET: 'test-secret-for-e2e-only-32chars!',
        JALORA_DATABASE_URL: e2eDatabaseUrl,
        JALORA_UPLOAD_DIR:
          process.env.JALORA_E2E_UPLOAD_DIR || join(e2eDataDir, 'uploads'),
        JALORA_OUTPUTS_DIR:
          process.env.JALORA_E2E_OUTPUTS_DIR || join(e2eDataDir, 'outputs'),
        JALORA_WORKSPACES_DIR:
          process.env.JALORA_E2E_WORKSPACES_DIR || join(e2eDataDir, 'workspaces'),
        JALORA_CHROMA_DIR:
          process.env.JALORA_E2E_CHROMA_DIR || join(e2eDataDir, 'chroma'),
        JALORA_RATE_LIMIT_AUTH_PER_MIN: '1000',
        JALORA_RATE_LIMIT_GLOBAL_PER_MIN: '0',
        JALORA_EMBEDDING_PROVIDER: 'local',
        JALORA_MEMORY_VECTORSTORE: 'sql',
        JALORA_BROWSER_DRIVER: 'fake',
        JALORA_TRACING_ENABLED: 'false',
      },
    },
    {
      command: 'npm run dev -- --host 127.0.0.1',
      cwd: '.',
      url: frontendURL,
      timeout: 60_000,
      reuseExistingServer: !process.env.CI,
      env: {
        VITE_API_PROXY_TARGET: `http://127.0.0.1:${backendPort}`,
        VITE_DEV_PORT: frontendPort,
      },
    },
  ],
});
