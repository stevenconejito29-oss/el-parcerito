import { chromium } from 'playwright-core';

const baseUrl = (process.env.RIDER_AUDIT_URL || 'http://127.0.0.1:5071').replace(/\/$/, '');
const email = process.env.RIDER_AUDIT_EMAIL || 'repartidor@oxidian.com';
const password = process.env.RIDER_AUDIT_PASSWORD;
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
  || '/home/panzeta/.cache/ms-playwright/chromium-1169/chrome-linux/chrome';

if (!password) throw new Error('Falta RIDER_AUDIT_PASSWORD');

const browser = await chromium.launch({ executablePath, headless: true, args: ['--no-sandbox'] });
try {
  const context = await browser.newContext({
    viewport: { width: 393, height: 852 },
    permissions: ['geolocation'],
    geolocation: { latitude: 37.4712, longitude: -5.6421, accuracy: 8 },
  });
  const page = await context.newPage();
  await page.goto(`${baseUrl}/auth/login`, { waitUntil: 'domcontentloaded' });
  await page.locator('input[name="email"]').fill(email);
  await page.locator('input[name="password"]').fill(password);
  await Promise.all([
    page.waitForLoadState('domcontentloaded'),
    page.locator('button[type="submit"]').click(),
  ]);
  await page.goto(`${baseUrl}/repartidor/ruta`, { waitUntil: 'domcontentloaded' });
  const panel = page.locator('[data-rider-tracking]');
  await panel.waitFor({ state: 'visible' });
  await page.locator('[data-rider-tracking][data-state="active"]').waitFor({
    state: 'visible',
    timeout: 10000,
  });
  const routeButton = page.locator('[data-open-maps-route="en_ruta"]').first();
  if (await routeButton.count()) {
    await routeButton.click();
    await page.locator('[data-route-map-panel]:not([hidden])').waitFor({ state: 'visible', timeout: 10000 });
    await page.locator('[data-route-map] .leaflet-marker-icon').first().waitFor({ state: 'visible', timeout: 10000 });
  }
  console.log(JSON.stringify({
    ok: true,
    state: await panel.getAttribute('data-state'),
    message: await panel.locator('[data-rider-tracking-copy]').innerText(),
    embeddedMap: await page.locator('[data-route-map-panel]:not([hidden])').count() > 0,
  }));
  await context.close();
} finally {
  await browser.close();
}
