// Web Push simulado: comprueba permisos y estados de UI, sin suscripción real.
import assert from 'node:assert/strict';
import {chromium} from 'playwright-core';
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
try {
  for (const scenario of ['active','denied','server-error','ios-browser']) {
    const page=await browser.newPage(scenario==='ios-browser'?{userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)'}:{});
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.route('https://push.test/**',route=>{
      const pathname=new URL(route.request().url()).pathname;
      if(pathname==='/api/push/vapid-key')return route.fulfill({json:{public_key:'AQID'}});
      if(pathname==='/api/push/subscribe')return route.fulfill(scenario==='server-error'?{status:503,json:{ok:false,error:'Servidor no disponible'}}:{json:{ok:true}});
      return route.fulfill({contentType:'text/html',body:'<html></html>'});
    });
    await page.goto('https://push.test/');
    await page.setContent('<meta name="ox-push-eligible" content="1"><button data-push-activate aria-label="Activar avisos">🔔<span>Avisos</span></button><section id="ox-pwa-sheet" hidden></section>');
    await page.evaluate(scenario=>{
      const subscription={options:{applicationServerKey:new Uint8Array([1,2,3]).buffer},toJSON:()=>({endpoint:'https://push.invalid/qa',keys:{}})};
      const registration=Object.assign(new EventTarget(),{update:async()=>{},pushManager:{getSubscription:async()=>subscription}});
      Object.defineProperty(navigator,'serviceWorker',{value:Object.assign(new EventTarget(),{ready:Promise.resolve(registration),register:async()=>registration})});
      if(scenario==='ios-browser') { delete window.Notification; delete window.PushManager; }
      else {
        window.Notification={permission:scenario==='denied'?'denied':'default',requestPermission:async()=>{window.Notification.permission='granted';return 'granted';}};
        window.PushManager=function(){};
      }
    },scenario);
    await page.addScriptTag({path:'static/js/pwa-manager.js'});
    await page.locator('[data-push-activate]').click();
    if(scenario==='active') {
      await page.waitForFunction(()=>document.querySelector('[data-push-activate]').getAttribute('aria-pressed')==='true');
      assert.equal(await page.locator('[data-push-activate]').getAttribute('aria-label'),'Avisos activos');
      assert.equal(await page.locator('[data-push-activate] span').textContent(),'Avisos activos');
    } else if(scenario==='denied') {
      assert.equal(await page.locator('[data-push-activate]').getAttribute('aria-pressed'),'false');
      assert.match(await page.locator('#ox-toasts').textContent(),/bloqueados/);
    } else if(scenario==='server-error') {
      await page.waitForFunction(()=>document.querySelector('[data-push-activate]').textContent.includes('Reintentar'));
      assert.equal(await page.locator('[data-push-activate]').isDisabled(),false);
      assert.equal(await page.locator('[data-push-activate]').getAttribute('aria-pressed'),'false');
    } else {
      assert.equal(await page.locator('#ox-pwa-sheet').getAttribute('hidden'),null);
      assert.match(await page.locator('#ox-toasts').textContent(),/instala primero/);
    }
    assert.deepEqual(errors,[]);
    console.log(`Avisos: ${scenario} OK`);
    await page.close();
  }
} finally {await browser.close();}
