import { chromium } from 'playwright-core';

const baseUrl = (process.env.PWA_AUDIT_URL || 'http://127.0.0.1:5071').replace(/\/$/, '');
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
  || '/home/panzeta/.cache/ms-playwright/chromium-1169/chrome-linux/chrome';
const viewportWidth = Number.parseInt(process.env.PWA_AUDIT_WIDTH || '375', 10);
const browser = await chromium.launch({ executablePath, headless: true, args: ['--no-sandbox'] });
const context = await browser.newContext({ viewport: { width: viewportWidth, height: 812 }, isMobile: true, hasTouch: true });
await context.addInitScript(() => {
  Object.defineProperty(navigator, 'standalone', { configurable: true, get: () => true });
});
const page = await context.newPage();

try {
  await page.goto(`${baseUrl}/`, { waitUntil: 'domcontentloaded' });
  const rejectPrivacy = page.locator('[data-privacy-reject]').first();
  if (await rejectPrivacy.isVisible()) await rejectPrivacy.click();
  const bar = page.locator('.ep-search-wrap');
  const input = bar.locator('input[name="q"]');
  const submit = bar.locator('button[type="submit"]');
  if (!await bar.isVisible()) throw new Error('La barra de búsqueda no es visible');
  const metrics = await bar.evaluate((node) => {
    const rect = node.getBoundingClientRect();
    return {
      width: rect.width,
      viewport: window.innerWidth,
      overflow: document.documentElement.scrollWidth > window.innerWidth,
    };
  });
  if (metrics.overflow || metrics.width > metrics.viewport + 1) throw new Error('La barra causa overflow horizontal');
  const recommendations = page.locator('.ep-banner-section,.ep-mc-destacados-wrap').first();
  if (await recommendations.count()) {
    const overlap = await page.evaluate(() => {
      const search = document.querySelector('.ep-search-wrap')?.getBoundingClientRect();
      const rec = document.querySelector('.ep-banner-section,.ep-mc-destacados-wrap')?.getBoundingClientRect();
      if (!search || !rec) return false;
      return search.bottom > rec.top + 1 && search.top < rec.bottom - 1;
    });
    if (overlap) throw new Error('La barra se superpone a productos recomendados');
    await page.evaluate(() => window.scrollTo(0, 320));
    await page.waitForTimeout(120);
    const position = await bar.evaluate((node) => getComputedStyle(node).position);
    if (position === 'sticky' || position === 'fixed') throw new Error('La barra sigue flotando al desplazar el catálogo');
    await page.evaluate(() => window.scrollTo(0, 0));
  }

  const navSearch = page.locator('.ox-bnav-item[data-bnav="search"]');
  if (!await navSearch.count()) throw new Error('La navegación no incluye Buscar');
  await navSearch.click();
  await page.waitForTimeout(400);
  if (!await input.evaluate((node) => document.activeElement === node)) {
    throw new Error('Buscar no enfoca la barra responsive');
  }
  await input.fill('combo');
  await Promise.all([page.waitForURL(/(?:\?|&)q=combo(?:&|$)/), submit.click()]);
  if (!page.url().includes('q=combo')) throw new Error('La consulta no llegó al catálogo');
  console.log(JSON.stringify({ ok: true, ...metrics, finalUrl: new URL(page.url()).pathname + new URL(page.url()).search }));
} finally {
  await browser.close();
}
