import { chromium } from 'playwright-core';
const browser = await chromium.launch({headless:true, executablePath:'/home/panzeta/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome', args:['--no-sandbox']});
const results = [];
for (const [name, url] of [['home','http://192.168.1.32/'], ['favor','http://192.168.1.32/favor'], ['carrito','http://192.168.1.32/carrito']]) {
  const context = await browser.newContext({viewport:{width:393,height:852}, isMobile:true, hasTouch:true, locale:'es-CO'});
  const page = await context.newPage();
  const errors = [];
  const failed = [];
  const console_msgs = [];
  page.on('pageerror', e => errors.push(String(e.message || e)));
  page.on('console', m => { if (m.type()==='error' || m.type()==='warning') console_msgs.push(`[${m.type()}] ${m.text().slice(0,200)}`); });
  page.on('requestfailed', r => failed.push(`${r.method()} ${r.url()} → ${r.failure()?.errorText}`));
  page.on('response', r => { if (r.status() >= 400) failed.push(`${r.status()} ${r.url()}`); });
  const t0 = Date.now();
  try {
    const resp = await page.goto(url, {waitUntil:'networkidle', timeout: 15000});
    await page.waitForTimeout(1500);
    const title = await page.title();
    const bodyEmpty = await page.evaluate(() => !document.body || document.body.innerText.trim().length < 50);
    results.push({name, url, status: resp?.status(), title, bodyEmpty, loadMs: Date.now()-t0, errors, console_msgs: console_msgs.slice(0,10), failed: failed.slice(0,15)});
  } catch(e) {
    results.push({name, url, exception: String(e.message), errors, failed});
  }
  await context.close();
}
await browser.close();
console.log(JSON.stringify(results, null, 2));
