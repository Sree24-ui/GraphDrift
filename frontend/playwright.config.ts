import { defineConfig, devices } from '@playwright/test'

// The accessibility suite drives the real pages against a real backend, so the
// rows, ring members and charts it scans are the ones production serves.
// Start the backend first (see tests/README-a11y.md or the CI workflow).
const BASE_URL = process.env.A11Y_BASE_URL ?? 'http://127.0.0.1:5174'

export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: process.env.CI ? 'list' : 'line',
  use: { baseURL: BASE_URL, headless: true, ...devices['Desktop Chrome'] },
  webServer: process.env.A11Y_BASE_URL
    ? undefined
    : {
        command: 'npm run dev -- --port 5174 --strictPort --host 127.0.0.1',
        url: BASE_URL,
        reuseExistingServer: !process.env.CI,
        timeout: 60_000,
      },
})
