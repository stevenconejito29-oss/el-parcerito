import { chromium } from 'playwright-core';
const b = await chromium.launch({headless:true, executablePath:'/home/panzeta/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome', args:['--no-sandbox']});
const ctx = await b.newContext({viewport:{width:393,height:852}, isMobile:true, hasTouch:true});
const page = await ctx.newPage();
const errors = [];
const failed = [];
page.on('pageerror', e => errors.push(String(e.message)));
page.on('requestfailed', r => failed.push(`${r.method()} ${r.url()}`));

await page.goto('http://192.168.1.32/', {waitUntil:'networkidle', timeout:15000});
await page.waitForTimeout(2000);

// Scroll a fondo para forzar carga de imágenes lazy
await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
await page.waitForTimeout(3000);
await page.evaluate(() => window.scrollTo(0, 0));
await page.waitForTimeout(1000);

// Contar imágenes cargadas vs rotas tras scroll completo
const imgs = await page.evaluate(() => {
  const all = document.querySelectorAll('img');
  const loaded = [...all].filter(i => i.complete && i.naturalWidth > 0).length;
  const broken = [...all].filter(i => i.complete && i.naturalWidth === 0 && i.src).length;
  const pending = [...all].filter(i => !i.complete).length;
  const emptySrc = [...all].filter(i => !i.src || i.src === location.href).length;
  return {total: all.length, loaded, broken, pending, emptySrc};
});

// Test navegación: click en primer link visible del menú
const navResult = {};
try {
  const links = await page.$$eval('a[href^="/"]', els => els.map(a => ({href:a.href, text:a.textContent?.trim().slice(0,40)})).filter(l => l.text && !l.href.includes('#')).slice(0,10));
  navResult.top_links = links;
  // Intentar click en un link específico de menú
  const menuLink = await page.$('a[href*="menu"], a[href="/menu"], a[href*="carta"]');
  if (menuLink) {
    const t0 = Date.now();
    await menuLink.click();
    await page.waitForLoadState('networkidle', {timeout:8000});
    navResult.menu_click_ms = Date.now() - t0;
    navResult.menu_url = page.url();
    navResult.menu_title = await page.title();
  }
} catch(e) { navResult.error = String(e.message); }

await page.screenshot({path:'/tmp/after_scroll.png', fullPage:true});
await b.close();
console.log(JSON.stringify({imgs, navResult, errors, failed: failed.slice(0,10)}, null, 2));
