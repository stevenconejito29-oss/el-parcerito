import { chromium } from 'playwright-core';

const baseUrl = (process.env.PWA_AUDIT_URL || 'http://127.0.0.1:5071').replace(/\/$/, '');
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
  || '/home/panzeta/.cache/ms-playwright/chromium-1169/chrome-linux/chrome';
const browser = await chromium.launch({ executablePath, headless: true, args: ['--no-sandbox'] });
const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
await context.addInitScript(() => Object.defineProperty(navigator, 'standalone', { configurable: true, get: () => true }));
const page = await context.newPage();
const errors = [];
page.on('pageerror', error => errors.push(error.message));

try {
  await page.goto(`${baseUrl}/`, { waitUntil: 'domcontentloaded' });
  const reject = page.locator('[data-privacy-reject]').first();
  if (await reject.isVisible()) await reject.click();
  const nav = page.locator('.ox-bottom-nav');
  if (!await nav.isVisible()) throw new Error('Navegación inferior no visible');
  const routerLoaded = await page.locator('script[src*="spa-nav.js"]').count();
  if (routerLoaded) throw new Error('El router SPA incompatible sigue cargado');

  await page.locator('[data-bnav="search"]').click();
  await page.waitForTimeout(350);
  if (new URL(page.url()).hash !== '#buscar') throw new Error('Buscar no actualiza el estado navegable');
  if (!await page.locator('[data-bnav="search"]').evaluate(el => el.classList.contains('is-active') && el.getAttribute('aria-current') === 'page')) {
    throw new Error('Buscar no comunica su estado activo');
  }

  await Promise.all([
    page.waitForURL(url => /\/carrito\/?$/.test(url.pathname)),
    page.locator('[data-bnav="cart"]').click(),
  ]);
  if (!/\/carrito\/?$/.test(new URL(page.url()).pathname)) throw new Error('Carrito no navega');
  await Promise.all([
    page.waitForURL(url => url.pathname === '/'),
    page.locator('[data-bnav="home"]').click(),
  ]);
  if (new URL(page.url()).pathname !== '/') throw new Error('Menú no regresa al catálogo');

  // Repetir una ruta comprueba la navegación nativa, restauración de historial
  // y que el service worker no entregue un documento principal obsoleto.
  const legal = page.locator('a[href*="informacion-legal"]').first();
  if (await legal.count()) {
    await Promise.all([page.waitForURL(/informacion-legal/), legal.click()]);
    await page.goBack({ waitUntil: 'domcontentloaded' });
    await page.waitForURL(url => url.pathname === '/');
    const legalAgain = page.locator('a[href*="informacion-legal"]').first();
    await Promise.all([page.waitForURL(/informacion-legal/), legalAgain.click()]);
    if (!await page.locator('main').count()) throw new Error('La navegación perdió el contenido principal');
    await page.goBack({ waitUntil: 'domcontentloaded' });
    await page.waitForURL(url => url.pathname === '/');
  }
  if (errors.length) throw new Error(`Errores JS: ${errors.join(' | ')}`);
  console.log(JSON.stringify({ ok: true, mode: 'native', steps: ['menu', 'buscar', 'carrito', 'menu', 'legal', 'back', 'legal-repeat', 'back'], finalUrl: page.url() }));
} finally {
  await browser.close();
}
