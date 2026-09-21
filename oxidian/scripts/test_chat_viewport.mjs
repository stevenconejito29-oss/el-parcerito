// HTML/CSS reales, red simulada: scroll, ticket y teclado en Chromium/WebKit.
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {chromium,webkit} from 'playwright-core';
const engine=process.env.REVIEW_BROWSER==='webkit'?webkit:chromium;
const browser=await engine.launch({headless:true,...(engine===chromium?{executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,args:['--no-sandbox']}:{})});
try {
 for(const installed of [false,true]) for(const width of [320,375,852]) {
  const page=await browser.newPage({viewport:{width,height:width===852?393:812},serviceWorkers:'block'});
  let next=120;
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',route=>{
   const url=new URL(route.request().url());
   if(route.request().isNavigationRequest())return route.fulfill({contentType:'text/html',body:fs.readFileSync('/tmp/parcerito-customer-pages/chat.html')});
   if(url.pathname.startsWith('/static/')){const file=path.join(process.cwd(),url.pathname);if(fs.existsSync(file)&&fs.statSync(file).isFile())return route.fulfill({path:file});}
   if(url.pathname.endsWith('/state'))return route.fulfill({json:{ok:true,conversation:{id:1,status:'bot',customer_recognised:true},has_older:true,orders:[{id:2,number:'QA-REC',fulfillment_label:'Recogida en el negocio',status_label:'Listo para recoger',tracking_url:'/pedido/2/confirmado'}],messages:url.searchParams.has('before')?[{id:19,sender:'bot',body:'Mensaje antiguo'}]:url.searchParams.has('after')?[{id:next++,sender:'bot',body:'Nuevo mensaje de seguimiento'}]:Array.from({length:100},(_,i)=>({id:i+20,sender:'bot',body:`Mensaje ${i+20}. Información de prueba del pedido para comprobar lectura y desplazamiento.`}))}});
   return route.fulfill({json:{ok:true}});
  });
  await page.goto('http://chat.test/ayuda');
  if(installed)await page.evaluate(()=>{document.documentElement.classList.add('ox-pwa-runtime');window.dispatchEvent(new Event('resize'));});
  const consent=page.locator('#ox-privacy-banner [data-privacy-reject]');if(await consent.isVisible())await consent.click();
  await page.waitForSelector('[data-id="119"]');
  await page.waitForTimeout(400);
  const log=page.locator('#wcp-messages');
  assert.ok(await log.evaluate(el=>el.scrollHeight>el.clientHeight+100),'Mensajes desplazables');
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false,'Sin desbordamiento horizontal');
  const form=page.locator('#wcp-form');
  assert.ok(await form.evaluate(el=>el.getBoundingClientRect().bottom<=innerHeight+1),'Compositor visible');
  assert.equal(await page.getByRole('link',{name:'Ver pedido y ticket'}).getAttribute('href'),'/pedido/2/confirmado');
  await log.evaluate(el=>el.scrollTop=el.scrollHeight/3);
  const top=await log.evaluate(el=>el.scrollTop);
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await page.waitForFunction(()=>document.querySelector('[data-id="120"]'));
  assert.ok(Math.abs(await log.evaluate(el=>el.scrollTop)-top)<3,'Polling conserva posición de lectura');
  await log.evaluate(el=>el.scrollTop=0);
  const anchor=await page.locator('[data-id="20"]').evaluate(el=>el.getBoundingClientRect().top);
  await page.locator('#wcp-history').click();
  await page.waitForSelector('[data-id="19"]');
  assert.ok(Math.abs(await page.locator('[data-id="20"]').evaluate(el=>el.getBoundingClientRect().top)-anchor)<3,'Historial conserva ancla');
  if(width!==852){
   await page.locator('#wcp-input').focus();
   await page.setViewportSize({width,height:430});
   await page.waitForFunction(()=>document.body.classList.contains('ox-keyboard-open'));
   assert.ok(await form.evaluate(el=>el.getBoundingClientRect().bottom<=innerHeight+1),'Compositor con teclado');
   assert.ok(await log.evaluate(el=>el.clientHeight>60),'Lectura con teclado');
  }
  assert.deepEqual(errors,[]);
  console.log('Chat scroll/ticket/viewport',engine.name(),width,installed?'PWA':'web','OK');
  await page.close();
 }
}finally{await browser.close();}
