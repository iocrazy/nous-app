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
  // Auto-build + serve the production bundle.
  //
  // VITE_SUPABASE_URL is pinned so the app derives a deterministic auth storage
  // key (`sb-e2e-auth-token`) that the stub harness seeds the session under.
  // VITE_API_URL is pinned same-origin so every backend call is a relative
  // /api/v1 path the stub intercepts — nothing can reach a real backend.
  //
  // ⚠️ reuseExistingServer reuses a server already on :4173 — that server MUST
  // have been built with these same pinned env vars, or the seeded session key
  // won't match and auth will silently fail. Don't point BASE_URL at a normal
  // dev server.
  webServer: {
    command:
      'VITE_SUPABASE_URL=https://e2e.supabase.co VITE_SUPABASE_ANON_KEY=sb_e2e_anon_key VITE_API_URL=http://localhost:4173 npm run build && npm run preview -- --port 4173 --strictPort',
    url: 'http://localhost:4173',
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
