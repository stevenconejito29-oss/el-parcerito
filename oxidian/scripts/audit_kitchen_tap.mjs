import { chromium } from 'playwright-core';

const base = (process.env.KITCHEN_AUDIT_URL || 'http://127.0.0.1:5071').replace(/\/$/, '');
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
  || '/home/panzeta/.cache/ms-playwright/chromium-1169/chrome-linux/chrome';
const browser = await chromium.launch({ executablePath, headless: true, args: ['--no-sandbox'] });
try {
  const context = await browser.newContext({ viewport: { width: 393, height: 852 }, hasTouch: true });
  const page = await context.newPage();
  await page.goto(`${base}/auth/login`, { waitUntil: 'domcontentloaded' });
  await page.locator('input[name="email"]').fill(process.env.KITCHEN_AUDIT_EMAIL || 'cocina@oxidian.com');
  await page.locator('input[name="password"]').fill(process.env.KITCHEN_AUDIT_PASSWORD || 'qa-roles-2026');
  await Promise.all([page.waitForLoadState('domcontentloaded'), page.locator('button[type="submit"]').click()]);
  if (page.url().includes('/auth/login')) throw new Error('No se pudo iniciar sesión en cocina');
  await page.goto(`${base}/preparador/pedidos`, { waitUntil: 'domcontentloaded' });
  const row = page.locator('.work-item--tap').first();
  try {
    await row.waitFor({ state: 'visible', timeout: 8000 });
  } catch (_) {
    throw new Error(`No se encontró una fila de preparación: url=${page.url()} cards=${await page.locator('.work-card').count()} texto=${(await page.locator('body').innerText()).slice(0, 1500)}`);
  }
  const checkbox = row.locator('[data-item-check]');
  if (await checkbox.isChecked()) await row.click({ position: { x: 180, y: 24 } });
  await row.click({ position: { x: 180, y: 24 } });
  if (!(await checkbox.isChecked())) throw new Error('La fila completa no marcó el producto');
  const box = await row.boundingBox();
  console.log(JSON.stringify({ ok: true, wholeRowTap: true, rowHeight: Math.round(box?.height || 0) }));
  await context.close();
} finally {
  await browser.close();
}
