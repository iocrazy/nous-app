// e2e-prod/get-token.spec.ts — mint a real production access token.
//
// OPT-IN, and it never prints the token.
//
// Two hygiene rules this file exists to keep:
//   1. It is SKIPPED unless `E2E_MINT_TOKEN` is set. `npm run e2e:prod` runs
//      every spec in this directory, so before the guard a full production
//      walkthrough logged in a second time purely to mint a credential nobody
//      in this repo consumes.
//   2. The token goes to a FILE at mode 0600, not to stdout. A JWT on stdout
//      lands in terminal scrollback, CI logs and any transcript of the run —
//      a bearer credential for the real stack, copied into places nobody
//      chose. Only the path and the expiry are printed.
//
// Usage:
//   E2E_MINT_TOKEN=1 npx playwright test --config e2e-prod/playwright.config.ts get-token
//   E2E_MINT_TOKEN=1 E2E_TOKEN_OUT=/tmp/tok npx playwright test ...   # custom path

import { rmSync, writeFileSync } from 'node:fs';

import { test } from '@playwright/test';

import { loadProdCreds } from './helpers';

const DEFAULT_TOKEN_OUT = '/media/heygo/program/datahub/nous/secrets/claude-debug.token';

/** The `exp` claim, as an ISO string — or null when the token is not a JWT.
 *
 *  Decoding is deliberately narrow: this reads ONE numeric claim so the run
 *  can say how long the file is good for. Nothing else from the payload is
 *  printed, and the signature is not inspected. */
function expiryOf(token: string): string | null {
  try {
    const payload = token.split('.')[1];
    if (!payload) return null;
    const json = Buffer.from(payload.replace(/-/g, '+').replace(/_/g, '/'), 'base64').toString(
      'utf8',
    );
    const exp = (JSON.parse(json) as { exp?: number }).exp;
    return typeof exp === 'number' ? new Date(exp * 1000).toISOString() : null;
  } catch {
    return null;
  }
}

// FILE scope, not inside the body: a skip declared here is decided before the
// fixtures are built, so the default `npm run e2e:prod` does not even launch a
// browser for it. The same call inside the test would boot Chromium first and
// only then decide it had nothing to do.
test.skip(!process.env.E2E_MINT_TOKEN, 'opt-in: set E2E_MINT_TOKEN=1');

test('mint token', async ({ page }) => {
  test.setTimeout(120000);
  const creds = loadProdCreds();
  await page.goto('/login');
  await page.getByText('Log in', { exact: true }).click();
  await page.locator('input[type="email"]').fill(creds.email);
  const pw = page.locator('input[type="password"]');
  await pw.fill(creds.password);
  await pw.locator('..').locator('xpath=following-sibling::button[1]').click();
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 20000 });
  await page.waitForTimeout(2000);
  const tok = await page.evaluate(() => {
    const keys = Object.keys(localStorage).filter((k) => k.includes('auth-token'));
    const raw = keys.length ? localStorage.getItem(keys[0]) : null;
    return raw ? (JSON.parse(raw).access_token as string) : '';
  });
  if (!tok) throw new Error('logged in but no access_token in localStorage');
  const out = process.env.E2E_TOKEN_OUT ?? DEFAULT_TOKEN_OUT;
  // REMOVE first, then create. `mode` is applied only when `open` creates the
  // file — writing over an existing one keeps whatever permissions it already
  // had, so a second mint into a world-readable leftover would silently stay
  // world-readable. Unlinking makes every run a fresh 0600 create.
  rmSync(out, { force: true });
  writeFileSync(out, tok, { mode: 0o600 });
  const exp = expiryOf(tok);
  console.log(`token written to ${out} (mode 0600)${exp ? `, expires ${exp}` : ''}`);
});
