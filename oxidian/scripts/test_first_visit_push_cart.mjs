// Datos QA. Solo se simula Web Push; suscripción, carrito y CSRF son reales.
import assert from 'node:assert/strict';
import {chromium,webkit} from 'playwright-core';
const base=process.env.REVIEW_BASE_URL||'http://127.0.0.1:5077';
const engine=process.env.REVIEW_BROWSER==='webkit'?webkit:chromium;
const browser=await engine.launch({headless:true});
const pushEndpoint='https://push.example.test/first-visit-'+crypto.randomUUID();
try {
 const context=await browser.newContext({viewport:{width:1280,height:1000},serviceWorkers:'block'});
 await context.addInitScript(endpoint=>{
  Object.defineProperty(navigator,'standalone',{get:()=>true});
  const subscription={options:{applicationServerKey:new Uint8Array([1,2,3]).buffer},toJSON:()=>({endpoint,keys:{p256dh:'qa_public_key',auth:'qa_auth'}})};
  const reg=Object.assign(new EventTarget(),{update:async()=>{},pushManager:{getSubscription:async()=>subscription}});
  Object.defineProperty(navigator,'serviceWorker',{value:Object.assign(new EventTarget(),{ready:Promise.resolve(reg),register:async()=>reg})});
  window.Notification={permission:'default',requestPermission:async()=>{window.Notification.permission='granted';return 'granted';}};
  window.PushManager=function(){};
 },pushEndpoint);
 await context.route('**/api/push/vapid-key',route=>route.fulfill({json:{ok:true,public_key:'AQID'}}));
 const page=await context.newPage();
 await page.goto(base+'/ayuda');
 await page.locator('#ox-privacy-banner [data-privacy-reject]').click();
 assert.equal(await page.locator('meta[name=ox-push-eligible]').getAttribute('content'),'1');
 await page.locator('.wcp-notify').click();
 await page.locator('.wcp-notify[aria-pressed=true]').waitFor();
 assert.equal((await context.request.get(base+'/api/push/status?endpoint='+encodeURIComponent(pushEndpoint))).status(),200);
 assert.equal((await (await context.request.get(base+'/api/push/status?endpoint='+encodeURIComponent(pushEndpoint))).json()).this_device_active,true);
 await page.goto(base+'/producto/4');
 await page.locator('.pd-combo-qty-control[data-action=increment]').click({clickCount:2});
 await Promise.all([page.waitForResponse(r=>r.url().includes('/carrito')&&r.request().method()==='POST'),page.locator('.pd-add-btn').click()]);
 await page.goto(base+'/producto/1');
 await page.locator('[data-flavor-option]').first().click();
 await page.locator('[data-extra-step="+1"]').first().click();
 await Promise.all([page.waitForResponse(r=>r.url().includes('/carrito')&&r.request().method()==='POST'),page.locator('.pd-add-btn').click()]);
 for(const route of ['/','/carrito']) {
  await page.goto(base+route);
  for(const width of [280,320,375,393,430,768]) {
   await page.setViewportSize({width,height:852});
   await page.evaluate(()=>document.documentElement.style.fontSize='20px');
   const selectors=route==='/'?'.ep-btn-detail,.ep-cbadge-label,.ep-card-cart-state':'.cr-item-name,.cr-tag,.cr-choice-chip,.cr-combo-toggle,.cr-item-footer,.cr-qty,.cr-combo-row';
   const bad=await page.locator(selectors).evaluateAll(es=>es.filter(el=>{
    const r=el.getBoundingClientRect();return r.width&&r.height&&(r.right>innerWidth+1||r.left<0||el.scrollWidth>el.clientWidth+2);
   }).map(el=>({cls:el.className,text:el.textContent.slice(0,70)})));
   assert.deepEqual(bad,[],`${route} ${width}px`);
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
   if(route==='/') {
    for(const button of await page.locator('.ep-card-combo .ep-btn-detail,.ep-card-configurable .ep-btn-detail').all()) assert.ok((await button.boundingBox()).width>140,await button.evaluate(el=>JSON.stringify({text:el.textContent,width:el.clientWidth,card:el.closest('.ep-card').className}))); 
    assert.ok(await page.locator('.ep-cbadge-label').first().isVisible());
   }
   if(width===393) await page.screenshot({path:`/tmp/parcerito-${engine.name()}-${route==='/'?'menu':'carrito'}-revisado.png`,fullPage:true});
   console.log(`${engine.name()} ${route} ${width}px texto ampliado OK`);
  }
 }
 await page.setViewportSize({width:393,height:852});
 await page.goto(base+'/checkout');
 for (const width of [280,320,393,768]) {
  await page.setViewportSize({width,height:852});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`checkout ${width}px`);
 }
 await page.setViewportSize({width:393,height:852});
 await page.screenshot({path:`/tmp/parcerito-${engine.name()}-checkout-revisado.png`,fullPage:true});
 await page.locator('[name=telefono_invitado]').fill('+346'+String(Math.floor(Math.random()*1e8)).padStart(8,'0'));
 await page.locator('[name=nombre_invitado]').fill('Cliente QA avisos previos');
 await page.locator('[name=tipo_entrega_cliente][value=recogida]').check();
 await page.locator('[name=metodo_pago][value=efectivo]').check();
 await page.locator('[name=notas]').fill('Portal 12, llamar al llegar');
 await page.locator('[name=acepta_condiciones]').check();
 await page.locator('button[type=submit]').click();
 await page.waitForURL(/\/pedido\/\d+\/confirmado/);
 assert.match(await page.locator('main').innerText(), /Portal 12, llamar al llegar/);
 const detailText = (await page.locator('.order-item-details').allTextContents()).join(' ');
 assert.match(detailText, /Contenido por combo/);
 assert.match(detailText, /Extras por unidad/);
 for (const width of [280,320,393,768]) {
  await page.setViewportSize({width,height:852});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`ticket ${width}px`);
 }
 await page.setViewportSize({width:393,height:852});
 await page.screenshot({path:`/tmp/parcerito-${engine.name()}-ticket-revisado.png`,fullPage:true});
 assert.doesNotMatch(await page.locator('main').innerText(), /\[Combo \d+|\d+#[a-f0-9]{12}/);
 assert.equal((await (await context.request.get(base+'/api/push/status?endpoint='+encodeURIComponent(pushEndpoint))).json()).this_device_active,true);
 const stranger=await browser.newContext();
 const statusUrl=page.url().replace('/confirmado','/estado');
 assert.equal((await stranger.request.get(statusUrl)).status(),403);
 await stranger.close();
 console.log('Avisos antes del primer pedido → vínculo con checkout; ticket sin claves de carrito y acceso ajeno bloqueado.');
 console.log('Avisos activados antes del primer pedido; combo y producto con sabores/extras añadidos al carrito.');
} finally {await browser.close();}
