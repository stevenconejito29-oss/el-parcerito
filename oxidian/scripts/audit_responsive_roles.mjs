import fs from 'node:fs';
import path from 'node:path';
import { chromium } from 'playwright-core';

const base = process.env.VISUAL_BASE_URL || 'http://127.0.0.1:5071';
const browserPath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
  || '/home/panzeta/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome';
const out = process.env.RESPONSIVE_AUDIT_OUTPUT
  || path.join(process.cwd(), 'docs', 'auditoria_roles', 'responsive-latest');
const selectedRoles = new Set(String(process.env.AUDIT_ROLES || '').split(',').map(v => v.trim()).filter(Boolean));
const roles = [
  ['admin', '/admin/dashboard'],
  ['cocina', '/preparador/pedidos'],
  ['preparacion', '/preparador/pedidos'],
  ['repartidor', '/repartidor/ruta'],
].map(([role, route]) => ({
  role, route,
  email: process.env[`AUDIT_${role.toUpperCase()}_EMAIL`],
  password: process.env[`AUDIT_${role.toUpperCase()}_PASSWORD`],
})).filter(item => item.email && item.password && (!selectedRoles.size || selectedRoles.has(item.role)));
const viewports = [
  ['movil', 393, 851], ['horizontal', 851, 393], ['escritorio', 1440, 900],
];

fs.rmSync(out, { recursive: true, force: true });
fs.mkdirSync(out, { recursive: true });
const report = { generatedAt: new Date().toISOString(), base, results: [] };
const browser = await chromium.launch({ executablePath: browserPath, headless: true, args: ['--no-sandbox'] });

try {
  for (const account of roles) {
    // El SW se valida por separado; bloquearlo evita que controllerchange
    // recargue la página a mitad de una captura y falsee el resultado visual.
    const context = await browser.newContext({
      viewport: { width: 393, height: 851 }, locale: 'es-ES', serviceWorkers: 'block',
    });
    const page = await context.newPage();
    await page.goto(`${base}/auth/login`, { waitUntil: 'domcontentloaded' });
    await page.locator('input[name=email]').fill(account.email);
    await page.locator('input[name=password]').fill(account.password);
    await Promise.all([page.waitForLoadState('domcontentloaded'), page.locator('button[type=submit]').click()]);
    for (const [mode, width, height] of viewports) {
      const errors = [];
      const onError = error => errors.push(error.message || String(error));
      page.on('pageerror', onError);
      await page.setViewportSize({ width, height });
      const response = await page.goto(`${base}${account.route}`, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(350);
      const metrics = await page.evaluate(() => {
        const interactive = [...document.querySelectorAll('button,a,input,select,textarea')]
          .filter(el => { const r = el.getBoundingClientRect(); return r.width && r.height; });
        const tooSmall = interactive.filter(el => {
          const r = el.getBoundingClientRect();
          return (r.width < 36 || r.height < 36) && !el.closest('.ox-sidebar');
        });
        return {
          path: location.pathname,
          overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 3,
          scrollWidth: document.documentElement.scrollWidth,
          clientWidth: document.documentElement.clientWidth,
          navItems: document.querySelectorAll('.ox-admin-bnav-item').length,
          sidebarItems: document.querySelectorAll('#sb-nav .ox-sb-item').length,
          tooSmallControls: tooSmall.length,
          tooSmallSamples: tooSmall.slice(0, 8).map(el => {
            const r = el.getBoundingClientRect();
            return `${el.tagName.toLowerCase()} ${el.getAttribute('aria-label') || el.textContent.trim().slice(0, 28) || el.name || el.type} ${Math.round(r.width)}x${Math.round(r.height)}`;
          }),
        };
      });
      const entry = { role: account.role, mode, width, height, status: response?.status(), errors, ...metrics };
      report.results.push(entry);
      await page.screenshot({ path: path.join(out, `${account.role}-${mode}.png`), fullPage: false, animations: 'disabled' });
      console.log(`${entry.overflow || entry.path !== account.route ? '✗' : '✓'} ${account.role}/${mode} ${entry.status} ${entry.path}`);
      page.off('pageerror', onError);
    }
    await context.close();
  }
} finally {
  await browser.close();
}
fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
if (report.results.some(item => item.status >= 400 || item.path !== roles.find(r => r.role === item.role)?.route || item.overflow || item.errors.length)) process.exitCode = 1;
