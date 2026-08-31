import { chromium } from 'playwright-core';
const b = await chromium.launch({headless:true, executablePath:'/home/panzeta/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome', args:['--no-sandbox']});
const results = [];
for (const [name, url] of [
  ['home',   'http://192.168.1.32/'],
  ['favor',  'http://192.168.1.32/favor'],
  ['producto', 'http://192.168.1.32/producto/1'],
  ['carrito','http://192.168.1.32/carrito'],
]) {
  const ctx = await b.newContext({viewport:{width:393,height:852}, isMobile:true, hasTouch:true, locale:'es-CO'});
  const page = await ctx.newPage();
  const resources = [];
  page.on('response', async r => {
    try {
      const headers = r.headers();
      const size = parseInt(headers['content-length'] || '0');
      resources.push({url: r.url().replace('http://192.168.1.32',''), status: r.status(), size, type: headers['content-type']||''});
    } catch {}
  });
  const t0 = Date.now();
  await page.goto(url, {waitUntil:'networkidle', timeout: 20000});
  const totalMs = Date.now() - t0;
  const perf = await page.evaluate(() => {
    const nav = performance.getEntriesByType('navigation')[0] || {};
    const paint = performance.getEntriesByType('paint');
    const fcp = paint.find(p => p.name === 'first-contentful-paint');
    // Total JS/CSS from resource timing
    const res = performance.getEntriesByType('resource');
    const jsSize = res.filter(r => r.initiatorType === 'script').reduce((a,r) => a + (r.transferSize || r.encodedBodySize || 0), 0);
    const cssSize = res.filter(r => r.name.includes('.css')).reduce((a,r) => a + (r.transferSize || r.encodedBodySize || 0), 0);
    const imgSize = res.filter(r => r.initiatorType === 'img').reduce((a,r) => a + (r.transferSize || r.encodedBodySize || 0), 0);
    return {
      ttfb_ms: Math.round(nav.responseStart || 0),
      domContentLoaded_ms: Math.round(nav.domContentLoadedEventEnd || 0),
      loadEvent_ms: Math.round(nav.loadEventEnd || 0),
      fcp_ms: fcp ? Math.round(fcp.startTime) : null,
      resource_count: res.length,
      js_kb: Math.round(jsSize/1024),
      css_kb: Math.round(cssSize/1024),
      img_kb: Math.round(imgSize/1024),
      transferred_total_kb: Math.round((jsSize+cssSize+imgSize)/1024),
    };
  });
  // Sort resources by size, top 8 heaviest
  const heaviest = resources.filter(r => r.size > 0).sort((a,b) => b.size - a.size).slice(0, 8).map(r => `${(r.size/1024).toFixed(0)}KB ${r.url.slice(0,60)}`);
  results.push({name, url, total_load_ms: totalMs, ...perf, top_assets: heaviest});
  await ctx.close();
}
await b.close();
console.log(JSON.stringify(results, null, 2));
