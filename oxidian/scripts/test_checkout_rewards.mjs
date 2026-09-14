// Ejecutar review_customer_views.py primero: fixtures aisladas, sin envíos reales.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {chromium} from 'playwright-core';
const root=process.cwd();
const browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,headless:true,args:['--no-sandbox']});
try {
  const page=await browser.newPage({viewport:{width:375,height:812},serviceWorkers:'block'});
  let failSelection=false;
  const errors=[], counts={request:0,verify:0,choose:0};
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(route.request().isNavigationRequest())return route.fulfill({contentType:'text/html',body:fs.readFileSync('/tmp/parcerito-customer-pages/checkout.html')});
    if(url.pathname.startsWith('/static/')) {
      const filename=url.pathname.startsWith('/static/vendor/leaflet/')?path.join(root,'node_modules/leaflet/dist',url.pathname.slice('/static/vendor/leaflet/'.length)):path.join(root,url.pathname);
      if(fs.existsSync(filename)&&fs.statSync(filename).isFile())return route.fulfill({path:filename});
    }
    if(url.pathname==='/puntos/solicitar-codigo') {
      counts.request++; await new Promise(resolve=>setTimeout(resolve,200));
      return route.fulfill({json:{ok:true,resend_seconds:95,msg:'Si el número está registrado, recibirá un código por WhatsApp.'}});
    }
    if(url.pathname==='/puntos/verificar-codigo') {
      counts.verify++; await new Promise(resolve=>setTimeout(resolve,200));
      return route.fulfill({json:{ok:true,puntos:200,canjeables:[{id:99,nombre:'Recompensa QA',puntos:100}]}});
    }
    if(url.pathname==='/carrito/set-producto-canje') {
      counts.choose++; await new Promise(resolve=>setTimeout(resolve,200));
      if(failSelection)return route.abort("failed");
    }
    return route.fulfill({json:{ok:true}});
  });
  await page.goto('http://rewards.test/checkout');
  await page.locator('[data-customer-phone]').fill('+34900000000');
  await page.evaluate(()=>setRewardFlow(true));
  await page.evaluate(()=>Promise.all([requestRewardCode(),requestRewardCode()]));
  assert.equal(counts.request,1,'Una pulsación repetida no duplica el OTP');
  await page.evaluate(()=>requestRewardCode(true));
  assert.equal(counts.request,1,'Reenvío respeta el intervalo recibido del servidor');
  assert.match(await page.locator('#reward-code-message').textContent(),/9[45]s/);
  await page.locator('#reward-code').fill('123456');
  await page.evaluate(()=>{window.rewardTestPending=verifyRewardCode();});
  assert.equal(await page.locator('#btn-confirmar').isDisabled(),true,'No se confirma el pedido mientras cambia el canje');
  await page.locator('[data-customer-phone]').fill('+34900000001');
  await page.evaluate(()=>window.rewardTestPending);
  assert.equal(await page.locator('#reward-picker').evaluate(el=>el.hidden),true,'Un teléfono nuevo no hereda la respuesta de verificación anterior');
  await page.evaluate(()=>Promise.all([verifyRewardCode(),verifyRewardCode()]));
  assert.equal(counts.verify,2,'Verificar dos veces genera una sola solicitud adicional');
  await page.evaluate(()=>renderRewardOptions([{id:99,nombre:'Recompensa QA',puntos:100}]));
  await page.evaluate(()=>{const button=document.querySelector('[data-reward-id]');return Promise.all([chooseReward(button),chooseReward(button)]);});
  assert.equal(counts.choose,1,'Selección de recompensa serializada');
  assert.equal(await page.locator('#canje_hidden').inputValue(),'99');
  failSelection=true;
  await page.evaluate(()=>chooseReward(document.querySelector('[data-reward-id]')));
  assert.match(await page.locator('#reward-picker-message').textContent(),/No pudimos conectar/);
  assert.equal(await page.locator('[data-reward-id]').isEnabled(),true,'Un fallo de red permite volver a intentar');
  assert.equal(await page.locator('#canje_hidden').inputValue(),'99','Un fallo conserva la selección confirmada');
  assert.deepEqual(errors,[]);
  console.log('Canje: duplicados, intervalo configurable, cambio de teléfono y selección: OK');
} finally {await browser.close();}
