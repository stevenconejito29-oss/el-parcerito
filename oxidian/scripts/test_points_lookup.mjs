// Requiere las páginas aisladas de review_customer_views.py. Sin envíos reales.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {chromium} from 'playwright-core';
const root=process.cwd();
const browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,headless:true,args:['--no-sandbox']});
try {
  const page=await browser.newPage({viewport:{width:375,height:812},serviceWorkers:'block'});
  const errors=[]; let requests=0, verifications=0;
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*', async route=>{
    const url=new URL(route.request().url());
    if(route.request().isNavigationRequest())return route.fulfill({contentType:'text/html',body:fs.readFileSync('/tmp/parcerito-customer-pages/club.html')});
    if(url.pathname.startsWith('/static/')) {
      const file=path.join(root,url.pathname);
      if(fs.existsSync(file)&&fs.statSync(file).isFile())return route.fulfill({path:file});
    }
    if(url.pathname==='/puntos/solicitar-codigo') {
      requests++; await new Promise(r=>setTimeout(r,150));
      return route.fulfill({json:{ok:true,resend_seconds:95}});
    }
    if(url.pathname==='/puntos/verificar-saldo') {
      verifications++; await new Promise(r=>setTimeout(r,150));
      return route.fulfill({json:{ok:true,puntos:200}});
    }
    return route.fulfill({json:{ok:true}});
  });
  await page.goto('http://points.test/club');
  const privacy=page.locator('#ox-privacy-banner [data-privacy-reject]');
  if(await privacy.isVisible())await privacy.click();
  await page.locator('#points-phone').fill('+34610000001');
  await page.evaluate(()=>{const f=document.querySelector('#points-lookup-form');f.requestSubmit();f.requestSubmit();});
  await page.waitForFunction(()=>!document.querySelector('[data-points-code-step]').hidden);
  assert.equal(requests,1);
  await page.locator('[data-points-resend]').click();
  assert.equal(requests,1);
  assert.match(await page.locator('#points-lookup-status').textContent(),/Espera/);
  await page.locator('#points-code').fill('123456');
  await page.locator('[data-points-verify]').click();
  await page.locator('#points-phone').fill('+34610000002');
  await page.waitForTimeout(250);
  assert.equal(await page.locator('[data-points-balance]').isVisible(),false,'Cambio de teléfono descarta respuesta anterior');
  await page.locator('[data-points-request]').click();
  await page.locator('[data-points-verify]').waitFor({state:'visible'});
  await page.locator('#points-code').fill('123456');
  await page.locator('[data-points-verify]').click();
  await page.locator('[data-points-balance]').waitFor({state:'visible'});
  assert.equal(await page.locator('[data-points-value]').textContent(),'200');
  assert.equal(verifications,2);
  assert.deepEqual(errors,[]);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
  console.log('Club: OTP único, reenvío limitado, respuesta antigua descartada y saldo visible: OK');
} finally {await browser.close();}
