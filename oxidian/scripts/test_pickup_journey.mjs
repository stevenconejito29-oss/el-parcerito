// Fixture de review_customer_views.py y review_role_views.py, sin pedidos reales.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {chromium,webkit} from 'playwright-core';
const root=process.cwd(), engine=process.env.REVIEW_BROWSER==='webkit'?webkit:chromium;
const browser=await engine.launch({headless:true,...(engine===chromium?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,args:['--no-sandbox']}:{})});
try {
 for(const width of [320,375,852]) {
  const page=await browser.newPage({viewport:{width,height:812},serviceWorkers:'block'});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  let state='armando';
  await page.route('**/*',route=>{
    const url=new URL(route.request().url());
    if(route.request().isNavigationRequest()) {
      const file=url.pathname==='/checkout'?'/tmp/parcerito-customer-pages/checkout.html':url.pathname.startsWith('/preparador')?'/tmp/parcerito-role-review/cocina.html':'/tmp/parcerito-customer-pages/recogida.html';
      return route.fulfill({contentType:'text/html',body:fs.readFileSync(file)});
    }
    if(url.pathname.startsWith('/static/')) {
      const file=url.pathname.startsWith('/static/vendor/leaflet/')?path.join(root,'node_modules/leaflet/dist',url.pathname.slice('/static/vendor/leaflet/'.length)):path.join(root,url.pathname);
      if(fs.existsSync(file)&&fs.statSync(file).isFile())return route.fulfill({path:file});
    }
    if(url.pathname==='/pedido/2/estado')return route.fulfill({json:{ok:true,status:state,status_label:state==='listo'?'Listo para recoger':state==='entregado'?'Recogido':'En preparación',active:state!=='entregado',presentation:{title:state==='listo'?'Listo para recoger':state==='entregado'?'Pedido recogido':'Estamos preparando tu pedido',description:'Información de recogida en el negocio.',stage:state==='armando'?2:state==='listo'?3:4,pickup_ready:state==='listo',payment_confirmed:state==='entregado',confirmation_pending:false}}});
    if(route.request().headers()['hx-request'])return route.fulfill({status:204});
    return route.fulfill({json:{ok:true,signature:'qa',franjas:[]}});
  });
  await page.goto('http://pickup.test/checkout');
  const privacy=page.locator('#ox-privacy-banner [data-privacy-reject]');if(await privacy.isVisible())await privacy.click();
  await page.locator('[data-fulfillment-mode][value=recogida]').check();
  assert.equal(await page.locator('#direccion_input').evaluate(el=>el.required),false);
  assert.equal(await page.locator('#delivery-address-block').isVisible(),false);
  assert.equal(await page.locator('#pickup-info-block').isVisible(),true);
  assert.doesNotMatch(await page.locator('#pickup-info-block').textContent(),/WhatsApp/);
  assert.equal(await page.locator('#direccion_lat_input').inputValue(),'');
  assert.equal(await page.locator('#zona_id_input').inputValue(),'');
  await page.locator('[data-fulfillment-mode][value=delivery]').check();
  assert.equal(await page.locator('#direccion_input').evaluate(el=>el.required),true);
  assert.equal(await page.locator('#pickup-info-block').isVisible(),false);
  await page.goto('http://pickup.test/pedido/2/confirmado');
  assert.equal(new URL(await page.locator('[data-order-state-url]').getAttribute('data-order-state-url'),'http://pickup.test').search,'');
  assert.doesNotMatch(await page.locator('[data-order-progress]').textContent(),/En reparto/);
  state='listo';await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await page.waitForFunction(()=>document.querySelector('[data-order-title]').textContent==='Listo para recoger');
  assert.match(await page.locator('[data-pickup-instruction]').textContent(),/Ya puedes venir/);
  assert.equal(await page.locator('[data-order-progress]').getAttribute('data-order-progress'),'3');
  assert.equal(await page.locator('a').filter({hasText:'Cómo llegar con Maps'}).count(),1);
  state='entregado';await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await page.waitForFunction(()=>document.querySelector('[data-order-title]').textContent==='Pedido recogido');
  assert.match(page.url(),/pedido\/2\/confirmado$/);
  assert.equal(await page.locator('[data-order-progress]').getAttribute('data-order-progress'),'4');
  await page.goto('http://pickup.test/preparador/pedidos');
  const counter=page.locator('#recogidas-listas');await counter.waitFor({state:'visible'});
  await counter.locator('summary').click();
  assert.equal(await counter.locator('.work-pickup-handoff form').evaluate(el=>el.checkValidity()),false);
  await counter.locator('[name=cobro_recibido]').check();
  assert.equal(await counter.locator('.work-pickup-handoff form').evaluate(el=>el.checkValidity()),true);
  assert.equal(await counter.locator('[name=codigo_confirmacion]').count(),0);
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
  assert.deepEqual(errors,[]);
  console.log('Recogida: checkout, seguimiento sin token, listo, recogido y mostrador',width,'OK');
  await page.close();
 }
} finally {await browser.close();}
