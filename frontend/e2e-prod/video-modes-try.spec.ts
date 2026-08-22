// TEMP: real end-to-end try of the new video modes (First&Last + resolution).
import { expect, test } from '@playwright/test';
import { loadProdCreds } from './helpers';

test('video frames mode real dispatch', async ({ page }) => {
  test.setTimeout(420000);
  const genPayloads: string[] = [];
  page.on('request', (r) => {
    if (r.url().includes('/generations') && r.method() === 'POST') {
      genPayloads.push(r.postData() ?? '');
    }
  });
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

  // ensure the media node has TWO images: upload a second tiny png into it
  const addBtn = page.getByTestId('media-node-add');
  console.log('TRY media-add=', await addBtn.count());
  const thumbsBefore = await page.locator('[data-testid^="media-node-thumb"]').count();
  console.log('TRY thumbs-before=', thumbsBefore);
  if (thumbsBefore < 2 && (await addBtn.count())) {
    const [chooser] = await Promise.all([page.waitForEvent('filechooser'), addBtn.click()]);
    const png = Buffer.from(
      'iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAYAAADED76LAAAAFElEQVR4nGP8z8Dwn4EIwESMolGFAAhKAhGjAAZLAAAAAElFTkSuQmCC',
      'base64',
    );
    await chooser.setFiles({ name: 'probe2.png', mimeType: 'image/png', buffer: png });
    await page.waitForTimeout(6000);
  }
  console.log('TRY thumbs-now=', await page.locator('[data-testid^="media-node-thumb"]').count());

  // select the media node → attached composer appears
  await page.locator('[data-testid="smart-media-node"], [data-testid^="media-node-grid"]').first().click({ force: true });
  await page.waitForTimeout(1500);
  const composer = page.getByTestId('attached-composer');
  console.log('TRY composer=', await composer.count());
  if (!(await composer.count())) {
    await page.locator('.react-flow__node[data-id]').filter({ hasText: 'MEDIA' }).first().click({ force: true });
    await page.waitForTimeout(1500);
  }
  await page.getByTestId('composer-kind-video').click();
  await page.waitForTimeout(800);
  // fit the canvas so the popover stays inside the viewport
  await page.locator('.react-flow__controls-fitview').click().catch(() => {});
  await page.waitForTimeout(600);
  // open duration pill → pick 720p + First & Last
  await page.getByTestId('pill-duration').click();
  await page.waitForTimeout(500);
  await page.locator('[data-testid="video-resolution-option"]').first().click({ force: true }); // 720p closes popover
  await page.getByTestId('pill-duration').click();
  await page.getByText('First & Last').click({ force: true });
  await page.waitForTimeout(500);
  // prompt + run
  await page.getByLabel('Attached prompt').fill('smooth morph between the two frames');
  await page.getByTestId('composer-run').click();
  await page.waitForTimeout(5000);
  console.log('TRY payloads=', genPayloads.length);
  for (const p of genPayloads) console.log('TRY payload:', p.slice(0, 400));

  // wait up to 5 min for a video output node
  const deadline = Date.now() + 300000;
  let videoCount = 0;
  while (Date.now() < deadline) {
    videoCount = await page.locator('[data-testid="output-video-preview"]').count();
    if (videoCount > 0) break;
    const failed = await page.locator('text=Run failed').count();
    if (failed > 0) { console.log('TRY run-failed-banner'); break; }
    await page.waitForTimeout(10000);
  }
  console.log('TRY video-outputs=', videoCount);
  expect(genPayloads.length).toBeGreaterThan(0);
});
