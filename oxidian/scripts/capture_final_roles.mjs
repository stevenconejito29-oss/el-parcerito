import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright-core';

const output = path.join(process.cwd(), 'docs', 'auditoria_roles', 'final');
fs.mkdirSync(output, { recursive: true });

const selected = new Set(
  String(process.env.CAPTURE_ROLES || '').split(',').map((value) => value.trim()).filter(Boolean),
);
const jobs = [
  ['superadmin', process.env.COOKIE_SUPER, '/superadmin/chatbot'],
  ['preparacion', process.env.COOKIE_PREP, '/preparador/pedidos'],
  ['proveedor-pedidos', process.env.COOKIE_PROV, '/proveedor/pedidos'],
  ['proveedor-inventario', process.env.COOKIE_PROV, '/proveedor/inventario'],
].filter(([name]) => !selected.size || selected.has(name));

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
    || '/home/panzeta/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome',
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
});

try {
  for (const [name, cookie, route] of jobs) {
    if (!cookie) throw new Error(`Falta cookie para ${name}`);
    const context = await browser.newContext({
      viewport: { width: 393, height: 851 },
      isMobile: true,
      hasTouch: true,
      serviceWorkers: 'allow',
    });
    await context.addCookies([{
      name: process.env.VISUAL_SESSION_COOKIE || '__Host-oxidian_session',
      value: cookie,
      domain: 'elparcerito.com',
      path: '/',
      secure: true,
      httpOnly: true,
      sameSite: 'Lax',
    }]);
    const page = await context.newPage();
    const response = await page.goto(`https://elparcerito.com${route}`, {
      waitUntil: 'networkidle',
    });
    await page.screenshot({
      path: path.join(output, `${name}.png`),
      fullPage: true,
      animations: 'disabled',
    });
    console.log(`${name}: ${response?.status()} ${page.url()}`);
    await context.close();
  }
} finally {
  await browser.close();
}
