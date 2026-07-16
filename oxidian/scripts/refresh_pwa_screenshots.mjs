import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const target = (process.env.PWA_SCREENSHOT_URL || 'https://elparcerito.com').replace(/\/$/, '');
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
  || '/home/panzeta/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome';

const browser = await chromium.launch({
  executablePath,
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
});

try {
  await capture('pwa-screenshot-mobile.png', {
    viewport: { width: 390, height: 844 },
    isMobile: true,
    hasTouch: true,
    userAgent: 'Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 Chrome/136 Mobile Safari/537.36',
  });
  await capture('pwa-screenshot-wide.png', {
    viewport: { width: 1280, height: 720 },
  });
} finally {
  await browser.close();
}

async function capture(filename, options) {
  const context = await browser.newContext({
    locale: 'es-ES',
    colorScheme: 'light',
    deviceScaleFactor: 1,
    serviceWorkers: 'block',
    ...options,
  });
  const page = await context.newPage();
  const response = await page.goto(`${target}/`, { waitUntil: 'networkidle', timeout: 30000 });
  if (!response?.ok()) throw new Error(`${target}/ respondió ${response?.status()}`);
  await page.addStyleTag({ content: `
    #ox-pwa-sheet, #ox-push-prompt, .ox-toast-wrap { display:none !important }
    * { caret-color: transparent !important }
  ` });
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({
    path: path.join(root, 'static', filename),
    fullPage: false,
    animations: 'disabled',
  });
  await context.close();
  console.log(`${filename}: ${options.viewport.width}x${options.viewport.height} desde ${target}`);
}
