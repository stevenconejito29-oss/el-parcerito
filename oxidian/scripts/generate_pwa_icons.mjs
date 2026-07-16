import path from 'node:path';
import fs from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const staticDir = path.join(root, 'static');
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
  || '/home/panzeta/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome';
const browser = await chromium.launch({ executablePath, headless: true, args: ['--no-sandbox'] });

try {
  for (const [filename, size, opaque] of [
    ['pwa-icon-192.png', 192, false],
    ['pwa-icon-512.png', 512, false],
    ['pwa-icon-512-maskable.png', 512, true],
    ['apple-touch-icon.png', 180, true],
    ['favicon-64.png', 64, false],
    ['favicon-32.png', 32, false],
  ]) {
    await render('pwa-icon.svg', filename, size, opaque);
  }
  await render('pwa-badge.svg', 'pwa-badge-96.png', 96);
} finally {
  await browser.close();
}

async function render(source, target, size, opaque = false) {
  const context = await browser.newContext({ viewport: { width: size, height: size }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  const svg = await fs.readFile(path.join(staticDir, source), 'utf8');
  await page.setContent(`<!doctype html><style>*{box-sizing:border-box}html,body,svg{width:100%;height:100%;margin:0;display:block;overflow:hidden}body{background:${opaque ? '#F4C542' : 'transparent'}}</style>${svg}`);
  await page.screenshot({ path: path.join(staticDir, target), omitBackground: !opaque, animations: 'disabled' });
  await context.close();
  console.log(`${target}: ${size}x${size}`);
}
