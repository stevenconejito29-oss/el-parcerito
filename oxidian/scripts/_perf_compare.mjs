import { chromium } from 'playwright-core';
const b = await chromium.launch({headless:true, executablePath:'/home/panzeta/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome', args:['--no-sandbox']});
const url = 'http://192.168.1.32/';

// Test 1: normal (con stack nuevo)
async function measure(label, blockPatterns=[]) {
  const ctx = await b.newContext({viewport:{width:393,height:852}, isMobile:true});
  const page = await ctx.newPage();
  await page.route('**/*', route => {
    const u = route.request().url();
    if (blockPatterns.some(p => u.includes(p))) return route.abort();
    route.continue();
  });
  const t0 = Date.now();
  await page.goto(url, {waitUntil:'networkidle', timeout: 20000});
  const total = Date.now() - t0;
  const fcp = await page.evaluate(() => {
    const p = performance.getEntriesByType('paint').find(e => e.name === 'first-contentful-paint');
    return p ? Math.round(p.startTime) : null;
  });
  await ctx.close();
  return {label, total_ms: total, fcp_ms: fcp};
}

const runs = [];
// 3 runs each to average out
for (const cfg of [
  ['CON stack nuevo (baseline)', []],
  ['SIN stack UI nuevo (bloqueando htmx/alpine/nprogress/motion)', ['htmx.min.js','alpine.min.js','nprogress','motion.css','motion-boot']],
]) {
  const samples = [];
  for (let i = 0; i < 3; i++) samples.push(await measure(cfg[0], cfg[1]));
  const avgTotal = Math.round(samples.reduce((a,s) => a + s.total_ms, 0) / samples.length);
  const avgFcp = Math.round(samples.reduce((a,s) => a + (s.fcp_ms||0), 0) / samples.length);
  runs.push({config: cfg[0], avg_total_ms: avgTotal, avg_fcp_ms: avgFcp, samples: samples.map(s => `${s.total_ms}ms (FCP ${s.fcp_ms})`)});
}
await b.close();
console.log(JSON.stringify(runs, null, 2));
