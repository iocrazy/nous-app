import { defineConfig } from '@playwright/test';

// e2e-prod/playwright.config.ts — deliberately separate from the repo's
// main `playwright.config.ts` (testDir: './e2e'). This project targets the
// REAL production stack (real Supabase auth, real backend, real data) —
// it must never be picked up by `npm run test:e2e` / CI's regular e2e run,
// and it has no `webServer` block (there's nothing to build; it points at
// whatever is already deployed). See e2e-prod/README.md for the "why" and
// how to run it: `npm run e2e:prod`.
export default defineConfig({
  testDir: '.',
  timeout: 45_000,
  // Real network + real backend — a single flaky retry absorbs transient
  // CDN/gateway blips without masking a genuine break (CLAUDE.md「验收纪律」:
  // this walkthrough exists precisely so a genuine break is NEVER masked).
  retries: 1,
  use: {
    baseURL: process.env.PROD_BASE_URL || 'https://app.nous.ink',
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
    // Real production latency (cross-region gateway hop) — longer than the
    // stubbed suite's default.
    navigationTimeout: 30_000,
    actionTimeout: 15_000,
  },
  projects: [{ name: 'chromium', use: { browserName: 'chromium' } }],
});
