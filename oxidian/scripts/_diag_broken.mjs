import { chromium } from 'playwright-core';
const b = await chromium.launch({headless:true, executablePath:'/home/panzeta/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome', args:['--no-sandbox']});
const ctx = await b.newContext({viewport:{width:393,height:852}, isMobile:true, hasTouch:true, locale:'es-CO'});
const page = await ctx.newPage();
const errors = [];
const warns = [];
const failed = [];
const cspViols = [];
page.on('pageerror', e => errors.push(String(e.message)));
page.on('console', m => {
  const t = m.type();
  const text = m.text();
  if (t === 'error') errors.push(`[console] ${text.slice(0,300)}`);
  if (t === 'warning') warns.push(`[warn] ${text.slice(0,200)}`);
  if (text.toLowerCase().includes('content security')) cspViols.push(text.slice(0,300));
});
page.on('requestfailed', r => failed.push(`FAIL ${r.method()} ${r.url()} → ${r.failure()?.errorText}`));
page.on('response', r => { if (r.status() >= 400) failed.push(`${r.status()} ${r.url()}`); });

await page.goto('http://192.168.1.32/', {waitUntil:'networkidle', timeout: 15000});
await page.waitForTimeout(2000);

// Check navigation elements
const state = await page.evaluate(() => {
  const imgs = document.querySelectorAll('img');
  const brokenImgs = [...imgs].filter(i => i.naturalWidth === 0 && i.src && !i.src.startsWith('data:')).map(i => i.src.slice(-80));
  const navs = document.querySelectorAll('nav, header, [role="navigation"], .ox-header, .ep-nav, .ox-nav');
  const links = document.querySelectorAll('a[href]');
  const linksVisible = [...links].filter(a => {
    const r = a.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  });
  return {
    total_imgs: imgs.length,
    broken_imgs: brokenImgs.slice(0,10),
    broken_count: brokenImgs.length,
    nav_elements: navs.length,
    total_links: links.length,
    visible_links: linksVisible.length,
    body_bg: getComputedStyle(document.body).backgroundColor,
    body_text: document.body.innerText.slice(0,300),
    header_html: document.querySelector('header')?.outerHTML?.slice(0,500) || 'NO HEADER',
    document_hidden: document.hidden,
  };
});

await page.screenshot({path:'/tmp/broken_home.png', fullPage:false});
await b.close();
console.log(JSON.stringify({state, errors, warns: warns.slice(0,5), failed: failed.slice(0,15), cspViols: cspViols.slice(0,5)}, null, 2));
