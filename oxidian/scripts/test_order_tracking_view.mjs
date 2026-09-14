import assert from 'node:assert/strict';
import { chromium } from 'playwright-core';

const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE, headless: true, args: ['--no-sandbox'] });
try {
  const page = await browser.newPage();
  let failed = false;
  let hold = false;
  let release;
  let requests = 0;
  let state = { ok:true, active:true, status:'pendiente', status_label:'Esperando confirmación', presentation:{title:'Confirma tu pedido',description:'Confirma por WhatsApp.',confirmation_pending:true} };
  await page.route('http://tracking.test/**', async route => {
    requests++;
    if (hold) await new Promise(resolve => { release = resolve; });
    await route.fulfill(failed ? {status:503,body:''} : {json:state});
  });
  await page.goto('http://tracking.test/');
  await page.setContent(`<main data-order-state-url="http://tracking.test/estado">
    <h1 data-order-title></h1><p data-order-description></p><span data-order-status></span>
    <span data-order-connection></span><span data-order-eyebrow></span><span data-order-validation></span>
    <section class="order-verification-alert">Confirma</section>
    <span data-order-payment-label>Bizum</span><span data-order-payment-status>Pendiente</span>
    <section data-order-payment-instructions>Enviar Bizum</section>
    <div data-order-progress="1"><div><b>1</b></div><div><b>2</b></div><div><b>3</b></div><div><b>4</b></div></div>
  </main>`);
  await page.addScriptTag({path:'static/js/order-status.js'});
  await page.waitForFunction(()=>document.querySelector('[data-order-title]').textContent==='Confirma tu pedido');
  state = {...state,status:'listo',status_label:'Listo',presentation:{title:'Listo para recoger',description:'Pedido preparado.',confirmation_pending:false,payment_label:'Bizum',payment_status:'Pago confirmado',payment_confirmed:true}};
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await page.waitForFunction(()=>document.querySelector('[data-order-title]').textContent==='Listo para recoger');
  assert.equal(await page.locator('.order-verification-alert').isVisible(),false);
  assert.equal(await page.locator('[data-order-progress]').getAttribute('data-order-progress'),'3');
  assert.equal(await page.locator('[data-order-payment-instructions]').isVisible(),false);
  assert.equal(await page.locator('[data-order-payment-status]').textContent(),'Pago confirmado');
  hold = true;
  await page.evaluate(()=>document.dispatchEvent(new Event('visibilitychange')));
  await new Promise(resolve => {
    const check = () => release ? resolve() : setTimeout(check, 10);
    check();
  });
  const before = requests;
  await page.evaluate(()=>{ for(let i=0;i<5;i++) document.dispatchEvent(new Event('visibilitychange')); });
  assert.equal(requests,before,'No se solapan consultas de seguimiento');
  failed = true;
  hold = false;
  release();
  await page.waitForFunction(()=>document.querySelector('[data-order-connection]').textContent.includes('Sin actualizar'));
  assert.equal(await page.locator('[data-order-title]').textContent(),'Listo para recoger');
  console.log('OK: confirmación, pago, recogida, consultas serializadas y pérdida de conexión.');
} finally { await browser.close(); }
