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
  timeout: 90_000, // walkthrough is now 10 steps; 45s tripped the whole-test budget on a slow prod night (2026-09-02: token mint alone 17s) — the failure surfaced as "Target page ... has been closed" mid-step, not as a step assertion
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
  projects: [
    {
      name: 'chromium',
      use: {
        browserName: 'chromium',
        // ── Why these two flags exist ────────────────────────────────────
        // Without them, on the release host (gpupc) headless Chromium
        // produces NO frames at all: `requestAnimationFrame` callbacks
        // never fire, `document.timeline.currentTime` stays pinned at 0,
        // CSS animations are frozen, and `page.screenshot()` hangs until
        // its timeout.
        //
        // That breaks EVERY `click()`, because Playwright's actionability
        // "stable" check compares an element's bounding box across two
        // consecutive rAF callbacks. With no frames, stability can never
        // be confirmed, so a perfectly healthy button fails with the very
        // misleading
        //     waiting for element to be visible, enabled and stable
        // — which reads like a product/animation bug and is not one. The
        // walkthrough's first click (landing-page "Log in") died this way
        // on every run. Measured, same host, same page:
        //   default                          rAF=0  click=TIMEOUT  screenshot=TIMEOUT
        //   --disable-gpu                    rAF=0  click=TIMEOUT
        //   --disable-software-rasterizer    rAF=0  click=TIMEOUT
        //   both flags together              rAF=5  click=OK 37ms   screenshot=OK 42ms
        // Only killing BOTH the hardware-GL path and the SwiftShader
        // software-GL fallback gets Chromium onto the plain CPU raster
        // path, which does tick. (The host's GPU is an NVIDIA card whose
        // /dev/dri nodes the runner user cannot even open — it is not in
        // the `video`/`render` groups — so the GL path was never usable
        // here to begin with; SwiftShader hanging is what turned a clean
        // fallback into a dead compositor.)
        //
        // Safe to keep unconditionally: this app renders through the DOM
        // (React Flow included) and the repo has no WebGL/three.js/canvas-
        // GL dependency, so nothing under test needs GPU rasterization.
        // Override with E2E_PROD_CHROMIUM_ARGS if a future host wants the
        // GL path back.
        launchOptions: {
          args: (process.env.E2E_PROD_CHROMIUM_ARGS ?? '--disable-gpu,--disable-software-rasterizer')
            .split(',')
            .map((a) => a.trim())
            .filter(Boolean),
        },
      },
    },
  ],
});
