// QA con serve_flow_review.py. Añade un combo real; no confirma pedidos.
import assert from 'node:assert/strict';
import {chromium, webkit} from 'playwright-core';
const base=process.env.REVIEW_BASE_URL||'http://127.0.0.1:5077';
const engine=process.env.REVIEW_BROWSER==='webkit'?webkit:chromium;
const browser=await engine.launch({headless:true});
try {
 for(const standalone of [false,true]) {
  const context=await browser.newContext({viewport:{width:1280,height:1000},serviceWorkers:'block',isMobile:true,hasTouch:true});
  if(standalone) await context.addInitScript(()=>Object.defineProperty(navigator,'standalone',{get:()=>true}));
  const page=await context.newPage();
  await page.goto(base+'/producto/4');
  await page.locator('#ox-privacy-banner [data-privacy-reject]').click();
  await page.locator('.pd-combo-qty-control[data-action=increment]').click({clickCount:2});
  await page.locator('[data-pd-qty="1"]').click({clickCount:2});
  await Promise.all([page.waitForResponse(r=>r.url().includes('/carrito')&&r.request().method()==='POST'),page.locator('.pd-add-btn').click()]);
  await page.goto(base);
  const card=page.locator('[data-product-card="4"]');
  assert.equal(await card.locator('.ep-card-cart-qty').textContent(),'3');
  for(const width of [280,320,393,430,768]) {
   await page.setViewportSize({width,height:852});
   await card.scrollIntoViewIfNeeded();
   const result=await card.evaluate(el=>{
    const rect=x=>{const r=x.getBoundingClientRect();return {width:r.width,height:r.height,x:r.x,y:r.y,right:r.right,bottom:r.bottom};};
    const badge=el.querySelector('.ep-card-cart-qty'),img=el.querySelector('.ep-card-img'),button=el.querySelector('.ep-btn-detail');
    return {badge:rect(badge),image:rect(img),button:rect(button),body:rect(el.querySelector('.ep-card-body')),textOverflows:button.scrollWidth>button.clientWidth+1};
   });
   if(width===393) await card.screenshot({path:`/tmp/parcerito-combo-en-canasta-${engine.name()}-${standalone?'pwa':'web'}.png`,style:'.ox-header-public,.ox-bottom-nav,#ox-toast-stack,#ox-toasts{visibility:hidden!important}'});
   assert.ok(result.badge.height<=48&&result.badge.width<=64,`Contador estirado ${width}px: ${JSON.stringify(result)}`);
   assert.ok(result.badge.x>=result.image.x&&result.badge.right<=result.image.right+1,'Contador dentro de foto');
   assert.ok(result.button.width>=result.body.width-35&&!result.textOverflows,'Botón completo');
   if(width<600) assert.ok(result.image.bottom<=result.body.y+1,'Combo apilado');
   console.log(`Combo x3 en canasta ${width}px ${standalone?'PWA':'web'} OK`);
  }
  await context.close();
 }
} finally {await browser.close();}
