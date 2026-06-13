import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: process.env.BASE_URL || 'http://localhost:4173',
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  // Auto-build + serve the production bundle. If BASE_URL already points at a
  // running env, Playwright reuses it (reuseExistingServer) and skips this.
  //
  // VITE_SUPABASE_URL is pinned so the app derives a deterministic auth storage
  // key (`sb-e2e-auth-token`) that the stub harness seeds the session under.
  // The backend never runs: page.route intercepts /rest/v1 and /api/v1.
  webServer: {
    command:
      'VITE_SUPABASE_URL=https://e2e.supabase.co VITE_SUPABASE_ANON_KEY=sb_e2e_anon_key npm run build && npm run preview -- --port 4173 --strictPort',
    url: 'http://localhost:4173',
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
