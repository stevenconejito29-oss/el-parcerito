import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {chromium, webkit} from 'playwright-core';
const root=process.cwd(), out='/tmp/parcerito-customer-pages';
const engine=process.env.REVIEW_BROWSER === 'webkit' ? webkit : chromium;
const browser=await engine.launch({headless:true,...(engine===chromium ? {executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,args:['--no-sandbox']} : {})});
const report=[];
try {
 for(const [mode,width,height] of [['mobile',375,812],['small',320,740],['desktop',1280,900],['tablet',768,1024],['landscape',852,393],['browser',375,812],['dark',375,812]]) {
  for(const name of ['menu','carrito','chat','checkout','producto','club','legal','seguimiento','franjas','combo','combo_carrito'].filter(name=>!process.env.REVIEW_VIEWS || process.env.REVIEW_VIEWS.split(',').includes(name))) {
   const context=await browser.newContext({viewport:{width,height},serviceWorkers:'block',colorScheme:mode==='dark'?'dark':'light'});
   if(mode!=='browser') await context.addInitScript(()=>{
    const original=window.matchMedia;
    window.matchMedia=function(query){const result=original.call(this,query);if(query.includes('display-mode')&&query.includes('standalone'))Object.defineProperty(result,'matches',{value:true});return result;};
   });
   const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
   await page.route('**/*',route=>{
    const url=new URL(route.request().url());
    if(route.request().isNavigationRequest())return route.fulfill({contentType:'text/html',body:fs.readFileSync(`${out}/${name}.html`)});
    if(url.pathname.startsWith('/static/vendor/leaflet/')) {
      const filename=path.join(root,'node_modules/leaflet/dist',url.pathname.split('/').pop());
      if(fs.existsSync(filename))return route.fulfill({path:filename});
    }
    if(url.pathname.startsWith('/static/')) {
      const filename=path.join(root,url.pathname);
      if(fs.existsSync(filename)&&fs.statSync(filename).isFile())return route.fulfill({path:filename});
    }
    if(url.pathname==='/pedido/1/estado')return route.fulfill({json:{ok:true,active:true,status:'pendiente'}});
    if(route.request().headers()['hx-request'])return route.fulfill({status:204});
    if(url.pathname==='/api/web-chat/state')return route.fulfill({json:{ok:true,conversation:{status:'bot'},messages:[{id:1,sender:'bot',body:'Hola. Puedo ayudarte con tu compra, entrega o pago.'}],orders:[]}});
    return route.fulfill({json:{ok:true}});
   });
   await page.goto('http://pwa.test'+({menu:'/',carrito:'/carrito',chat:'/ayuda',checkout:'/checkout',producto:'/producto/1',club:'/club',legal:'/informacion-legal',seguimiento:'/pedido/1/confirmado',franjas:'/delivery/preview',combo:'/producto/7',combo_carrito:'/carrito'}[name]), {waitUntil:'domcontentloaded'});
   const privacy=page.locator('#ox-privacy-banner [data-privacy-reject]');
   if(await privacy.isVisible())await privacy.click();
   await page.waitForTimeout(250);
   if(name==='combo') {
    const selection=page.locator('.combo-sel-qty:not(:disabled)').first();
    await selection.fill('2'); await selection.dispatchEvent('input');
    assert.equal(await page.evaluate(()=>pdComboState().find(s=>s.key!=='_fija').complete),false,'Respeta el mínimo de tres');
    await selection.fill('3'); await selection.dispatchEvent('input');
    const state=await page.evaluate(()=>{const s=pdComboState().find(s=>s.key!=='_fija');return {complete:s.complete,selectionText:s.selectionText};});
    assert.equal(state.complete,true);
    assert.match(state.selectionText,/Bebida de prueba/,'El resumen conserva el nombre de la opción');
   }
   if(name==='combo_carrito') {
    assert.equal(await page.locator('.cr-combo[open]').count(),1);
    assert.match(await page.locator('.cr-combo').innerText(),/3×/);
   }
   if(name==='carrito') {
    const readable=await page.locator('.cr-total-val').evaluate(el=>{
      const rgb=getComputedStyle(el).color.match(/[\d.]+/g).slice(0,3).map(Number);
      const bg=getComputedStyle(el.closest('.cr-summary')).backgroundColor.match(/[\d.]+/g).slice(0,3).map(Number);
      const lum=values=>values.map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
      return (Math.max(lum(rgb),lum(bg))+.05)/(Math.min(lum(rgb),lum(bg))+.05);
    });
    assert.ok(readable>=4.5, `Cart total contrast ${readable} in ${mode}`);
   }
   if(name==='menu') {
    assert.ok(await page.locator('#catalogo').evaluate(el => Boolean(el.compareDocumentPosition(document.querySelector('.ep-mc-destacados-wrap')) & Node.DOCUMENT_POSITION_FOLLOWING)));
    const canje=page.locator('.ep-cat[data-category="_canje"]');
    if(await canje.count()) {
      await canje.click();
      assert.ok(await page.locator('[data-storefront-empty]').isVisible());
      await page.locator('.ep-cat[data-category=""]').click();
      assert.equal(await page.locator('[data-storefront-empty]').isVisible(),false);
    }
    const opener=page.locator('[data-modal-open]').first();
    if(await opener.count()) {
      await opener.click();
      await page.locator('#ep-modal').waitFor({state:'visible'});
      await page.locator('[data-modal-close]').click();
      await page.locator('#ep-modal').waitFor({state:'hidden'});
    }
    await page.evaluate(()=>scrollTo(0,0));
   }
   const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
   report.push({name,mode,overflow,errors});
   console.log(name,mode,overflow,errors);
   if(name==='chat'){
    assert.equal(await page.locator('.wcp-help').getAttribute('open'),null);
    const form=await page.locator('#wcp-form').boundingBox();const nav=await page.locator('.ox-bottom-nav').boundingBox();
    assert.ok(form.height>=44);
    if(nav&&width<768)assert.ok(form.y+form.height<=nav.y+1,`Chat composer overlaps navigation: ${mode}`);
   }
   if(['mobile','desktop'].includes(mode) && ['menu','carrito','chat','checkout','club','combo','combo_carrito'].includes(name)) await page.screenshot({animations:'disabled',timeout:10000,path:`${out}/${name}-${mode}.png`,fullPage:true});
   await context.close();
  }
 }
 fs.writeFileSync(`${out}/report.json`,JSON.stringify(report,null,2));
 console.log(JSON.stringify(report));
 assert.ok(report.every(row=>!row.overflow&&row.errors.length===0));
}finally{await browser.close();}
