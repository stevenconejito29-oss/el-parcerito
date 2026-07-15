import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright-core';

const root = process.cwd();
const baseUrl = process.env.PWA_AUDIT_URL || 'http://127.0.0.1:5070';
const outputDir = path.join(root, 'docs', 'auditoria_pwa');
const browserPath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
  || '/home/panzeta/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome';

fs.mkdirSync(outputDir, { recursive: true });

const browser = await chromium.launch({
  executablePath: browserPath,
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
  proxy: process.env.PWA_AUDIT_PROXY ? { server: process.env.PWA_AUDIT_PROXY } : undefined,
});

const report = {
  generatedAt: new Date().toISOString(),
  baseUrl,
  views: [],
  manifest: null,
  offline: null,
};

try {
  await captureView('android-menu', {
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
    userAgent: 'Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36',
  });
  await captureView('iphone-pwa', {
    viewport: { width: 393, height: 852 },
    deviceScaleFactor: 3,
    isMobile: true,
    hasTouch: true,
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Version/17.5 Mobile/15E148 Safari/604.1',
  }, true);
  await captureView('desktop-menu', {
    viewport: { width: 1440, height: 1000 },
    deviceScaleFactor: 1,
  });

  const response = await fetch(`${baseUrl}/manifest.webmanifest`);
  report.manifest = {
    status: response.status,
    contentType: response.headers.get('content-type'),
    data: await response.json(),
  };
  report.offline = await auditOffline();
} finally {
  await browser.close();
}

fs.writeFileSync(
  path.join(outputDir, 'report.json'),
  JSON.stringify(report, null, 2),
);
console.log(JSON.stringify(report, null, 2));

async function captureView(name, contextOptions, standalone = false) {
  const context = await browser.newContext({
    locale: 'es-ES',
    colorScheme: 'light',
    serviceWorkers: 'allow',
    ...contextOptions,
  });
  const page = await context.newPage();
  const consoleErrors = [];
  const failedRequests = [];
  page.on('console', (message) => {
    if (message.type() === 'error') consoleErrors.push(message.text());
  });
  page.on('requestfailed', (request) => {
    failedRequests.push({
      url: request.url(),
      error: request.failure()?.errorText || 'unknown',
    });
  });
  if (standalone) {
    await page.addInitScript(() => {
      const nativeMatchMedia = window.matchMedia.bind(window);
      window.matchMedia = (query) => {
        if (query === '(display-mode: standalone)') {
          return {
            matches: true,
            media: query,
            onchange: null,
            addListener() {},
            removeListener() {},
            addEventListener() {},
            removeEventListener() {},
            dispatchEvent() { return false; },
          };
        }
        return nativeMatchMedia(query);
      };
      Object.defineProperty(navigator, 'standalone', { value: true });
    });
  }

  const response = await page.goto(`${baseUrl}/`, { waitUntil: 'networkidle' });
  await page.screenshot({
    path: path.join(outputDir, `${name}.png`),
    fullPage: true,
  });

  const metrics = await page.evaluate(() => {
    const interactive = [...document.querySelectorAll(
      'a[href], button:not([disabled]), input:not([type="hidden"]), select, textarea',
    )].filter((element) => {
      const style = getComputedStyle(element);
      const rect = element.getBoundingClientRect();
      return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
    });
    const smallTargets = interactive
      .map((element) => {
        const rect = element.getBoundingClientRect();
        return {
          tag: element.tagName.toLowerCase(),
          text: (element.getAttribute('aria-label') || element.textContent || '').trim().slice(0, 70),
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        };
      })
      .filter((item) => item.width < 44 || item.height < 44);

    return {
      title: document.title,
      horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
      scrollWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
      products: document.querySelectorAll('.ep-card').length,
      visibleProducts: [...document.querySelectorAll('.ep-card')]
        .filter((card) => card.getBoundingClientRect().height > 0).length,
      smallTargets,
      serviceWorkerControlled: Boolean(navigator.serviceWorker?.controller),
      standalone: window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true,
    };
  });

  // Los filtros de categoría conservan tarjetas ocultas en el DOM. Auditar
  // sólo una acción realmente visible evita falsos fallos por esa estructura.
  const addButton = page.locator('.ep-btn-add:visible').first();
  if (await addButton.count()) {
    await addButton.click();
    await page.waitForTimeout(250);
    await page.screenshot({
      path: path.join(outputDir, `${name}-producto.png`),
      fullPage: false,
    });
  }

  report.views.push({
    name,
    status: response?.status() || null,
    consoleErrors,
    failedRequests,
    ...metrics,
  });
  await context.close();
}

async function auditOffline() {
  const context = await browser.newContext({
    viewport: { width: 390, height: 844 },
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
    locale: 'es-ES',
    serviceWorkers: 'allow',
  });
  const page = await context.newPage();
  await page.goto(`${baseUrl}/`, { waitUntil: 'networkidle' });
  await page.evaluate(async () => {
    await navigator.serviceWorker.ready;
    if (!navigator.serviceWorker.controller) {
      await new Promise((resolve) => {
        navigator.serviceWorker.addEventListener('controllerchange', resolve, { once: true });
      });
    }
  });
  await context.setOffline(true);
  let error = null;
  try {
    await page.reload({ waitUntil: 'domcontentloaded', timeout: 15000 });
  } catch (caught) {
    error = String(caught);
  }
  await page.screenshot({
    path: path.join(outputDir, 'pwa-offline.png'),
    fullPage: true,
  });
  const result = await page.evaluate(() => ({
    title: document.title,
    bodyText: document.body.innerText.slice(0, 300),
    controlled: Boolean(navigator.serviceWorker?.controller),
  })).catch(() => null);
  await context.close();
  return { error, result };
}
