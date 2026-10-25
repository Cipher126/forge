import { defineConfig, devices } from '@playwright/test';
const baseURL = process.env.FORGE_E2E_URL || 'http://127.0.0.1:5173';
export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  timeout: 45000,
  reporter: [['list'], ['html', { open: 'never' }]],
  use: { baseURL, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  projects: [
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        channel: process.env.FORGE_BROWSER_CHANNEL || undefined,
      },
    },
  ],
  webServer: process.env.FORGE_E2E_URL
    ? undefined
    : {
        command: 'npm run dev',
        url: baseURL,
        reuseExistingServer: !process.env.CI,
        timeout: 90000,
      },
});
