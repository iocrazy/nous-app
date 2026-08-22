// TEMP: nous composer survey for side-by-side with the IC shots.
import { test } from '@playwright/test';
import { loadProdCreds } from './helpers';

const OUT = '/tmp/claude-1000/-media-heygo-program-projects-code-repos-nous-app/3dd6aa9b-ad2c-4ddc-91fc-416657e99cdd/scratchpad/cmp';

test('nous survey', async ({ page }) => {
  test.setTimeout(240000);
  const creds = loadProdCreds();
  await page.goto('/login');
  await page.getByText('Log in', { exact: true }).click();
  await page.locator('input[type="email"]').fill(creds.email);
  const pw = page.locator('input[type="password"]');
  await pw.fill(creds.password);
  await pw.locator('..').locator('xpath=following-sibling::button[1]').click();
  await page.waitForURL((u) => !u.pathname.startsWith('/login'), { timeout: 20000 });
  await page.waitForTimeout(2500);
  const teamId = page.url().match(/team\/(\d+)/)?.[1] ?? '';
  const canvasId = await page.evaluate(async () => {
    const keys = Object.keys(localStorage).filter((k) => k.includes('auth-token'));
    const raw = keys.length ? localStorage.getItem(keys[0]) : null;
    const token = raw ? (JSON.parse(raw).access_token as string) : null;
    const H = { Authorization: `Bearer ${token}` };
    const pr = await fetch('https://api.nous.ink/api/v1/projects', { headers: H });
    const pj = await pr.json();
    for (const p of pj.data ?? pj) {
      const cr = await fetch(`https://api.nous.ink/api/v1/projects/${p.id}/canvases`, { headers: H });
      const cj = await cr.json();
      for (const c of cj.data ?? cj) if (c.name === 'DblClick Repro') return c.id as string;
    }
    return null;
  });
  await page.goto(`/team/${teamId}/canvas/${canvasId}`);
  await page.waitForTimeout(6000);
  await page.locator('.react-flow__controls-fitview').click().catch(() => {});
  await page.waitForTimeout(800);
  // select media node → composer (image kind)
  await page.locator('[data-testid="media-node-grid"]').first().click({ force: true });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: `${OUT}/nous-21-composer-image.png` });
  await page.getByTestId('composer-kind-video').click();
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${OUT}/nous-22-composer-video.png` });
  console.log('SURVEY composer=', await page.getByTestId('attached-composer').count(),
    'engine-select=', await page.getByLabel('Engine').count(),
    'library-btn=', await page.getByTestId('composer-library').count(),
    'mode-frames=', await page.getByTestId('composer-mode-frames').count(),
    'vres-pill=', await page.getByTestId('pill-vres').count());
});
