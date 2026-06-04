const { defineConfig, devices } = require('@playwright/test');
const { REPORT_DIR } = require('./tests/bug-hunter/lib/env');

module.exports = defineConfig({
  testDir: './tests/bug-hunter/specs',
  timeout: 90_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  outputDir: 'test-results/bug-hunter',
  globalSetup: require.resolve('./tests/bug-hunter/lib/global-setup'),
  reporter: [
    ['list'],
    [require.resolve('./tests/bug-hunter/lib/reporter')],
    ['html', { outputFolder: `${REPORT_DIR}/playwright-html`, open: 'never' }],
    ['json', { outputFile: `${REPORT_DIR}/playwright-results.json` }],
  ],
  use: {
    baseURL: process.env.BUG_HUNTER_BASE_URL || 'http://127.0.0.1:8001',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    actionTimeout: 15_000,
    navigationTimeout: 45_000,
    ignoreHTTPSErrors: true,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
