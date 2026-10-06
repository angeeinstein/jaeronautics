/**
 * End-to-end tests: the built front end in Chromium against the real Flask
 * app, on a throwaway database with sample people in it
 * (scripts/visual/snapshot.py serve). `npm run build` first.
 *
 * @playwright/test is pinned to the version whose Chromium is preinstalled in
 * the development environment; CI downloads the same build.
 */
import { defineConfig, devices } from '@playwright/test';

const PORT = 8777;

export default defineConfig({
  testDir: './e2e',
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['list']] : 'list',
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'], viewport: { width: 1366, height: 820 } } },
    { name: 'phone', use: { ...devices['Pixel 7'] } },
  ],
  webServer: {
    command: `${process.env.PYTHON ?? 'python3'} ../scripts/visual/snapshot.py serve`,
    url: `http://127.0.0.1:${PORT}/__health`,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    env: { SNAPSHOT_PORT: String(PORT) },
  },
});
