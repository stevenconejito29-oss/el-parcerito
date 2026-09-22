import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import {chromium} from 'playwright-core';
const browser=await chromium.launch({headless:true,args:['--no-sandbox'],executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE});
const report=[];
try {
 for(const [width,height] of [[320,740],[375,812],[768,1024],[852,393]]) {
  for(const name of ['cerrado_menu','cerrado_producto','cerrado_combo','cerrado_carrito','cerrado_checkout','preapertura','configuracion']) {
   const folder=name==='configuracion'?'/tmp/parcerito-role-review':'/tmp/parcerito-customer-pages';
   const page=await browser.newPage({viewport:{width,height},serviceWorkers:'block'});
   const errors=[];page.on('pageerror',error=>errors.push(error.message));
   await page.route('**/*',route=>{
    const url=new URL(route.request().url());
    if(route.request().isNavigationRequest())return route.fulfill({contentType:'text/html',body:fs.readFileSync(`${folder}/${name}.html`)});
    const file=path.join(process.cwd(),url.pathname);
    if(url.pathname.startsWith('/static/')&&fs.existsSync(file))return route.fulfill({path:file});
    return route.fulfill({json:{ok:true}});
   });
   await page.goto('https://responsive.test/'+(name==='configuracion'?'superadmin/config#notificaciones-cliente':''));
   await page.waitForTimeout(150);
   const privacy=page.locator('#ox-privacy-banner [data-privacy-reject]');
   if(await privacy.isVisible())await privacy.click();
   if(name==='cerrado_carrito')assert.notEqual(await page.locator('.cr-panel').evaluate(el=>getComputedStyle(el).position),'fixed');
   if(name==='preapertura')assert.equal(await page.locator('.launch-copy h1').evaluate(el=>getComputedStyle(el).color),'rgb(255, 250, 240)');
   if(name==='configuracion' && width<=760) {
    await page.locator('#notificaciones-cliente').waitFor({state:'visible'});
    await page.locator('.cfg-nav a[href="#acceso"]').click();
    await page.locator('#acceso').waitFor({state:'visible'});
    assert.equal(await page.locator('[name="ACCESO_CLIENTES_REGISTRADOS"]').count(),1);
    await page.evaluate(()=>location.hash='seccion-inexistente');
    await page.locator('#tienda').waitFor({state:'visible'});
   }
   await page.addStyleTag({content:'html {font-size:20px !important}'});
   const clipped=await page.evaluate(()=>[...document.querySelectorAll('.launch-copy h1,.launch-copy p,.launch-name,.cr-store-closed,.pd-store-closed,.ep-card-name,.cfg-section h2')].filter(el=>el.getBoundingClientRect().width&&el.scrollWidth>el.clientWidth+2).map(el=>el.className||el.tagName));
   const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
   report.push({name,width,overflow,clipped,errors});
   if(width===320)await page.screenshot({path:`/tmp/parcerito-${name}-320.png`,fullPage:true});
   await page.close();
  }
 }
 console.log(JSON.stringify(report));
 assert.equal(report.filter(r=>r.overflow||r.clipped.length||r.errors.length).length,0,'Hay texto cortado o errores: ver informe');
}finally{await browser.close();}
