import { chromium } from 'playwright-core';
const b = await chromium.launch({headless:true, executablePath:'/home/panzeta/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome', args:['--no-sandbox']});
const results = [];
const routes = [
  ['home', 'http://192.168.1.32/', 393, 852, true],
  ['favor', 'http://192.168.1.32/favor', 393, 852, true],
  ['carrito', 'http://192.168.1.32/carrito', 393, 852, true],
  ['home-desktop', 'http://192.168.1.32/', 1440, 900, false],
];
for (const [name, url, w, h, mobile] of routes) {
  const ctx = await b.newContext({viewport:{width:w,height:h}, isMobile:mobile, hasTouch:mobile, locale:'es-CO'});
  const page = await ctx.newPage();
  const errors = [];
  const failed = [];
  page.on('pageerror', e => errors.push(String(e.message)));
  page.on('requestfailed', r => failed.push(`${r.method()} ${r.url()}`));
  page.on('response', r => { if (r.status() >= 400) failed.push(`${r.status()} ${r.url()}`); });
  await page.goto(url, {waitUntil:'networkidle', timeout: 15000});
  await page.waitForTimeout(1000);
  // Check that HTMX+Alpine+NProgress are loaded (via window globals)
  const globals = await page.evaluate(() => ({
    htmx: typeof window.htmx === 'object' && typeof window.htmx.trigger === 'function',
    Alpine: typeof window.Alpine === 'object' && typeof window.Alpine.start === 'function',
    NProgress: typeof window.NProgress === 'object' && typeof window.NProgress.start === 'function',
    fadeInCards: document.querySelectorAll('.motion-fade-in').length,
    viewTransitionCards: document.querySelectorAll('[style*="view-transition-name"]').length,
    motionCssLoaded: !!Array.from(document.styleSheets).find(s => s.href && s.href.includes('motion.css')),
  }));
  results.push({name, url, viewport:`${w}x${h}`, mobile, globals, errors, failed: failed.slice(0,10)});
  await ctx.close();
}
await b.close();
console.log(JSON.stringify(results, null, 2));
