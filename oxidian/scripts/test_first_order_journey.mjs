// Servidor QA en memoria: REVIEW_PORT=5078 python scripts/serve_flow_review.py.
import assert from 'node:assert/strict';
import {chromium} from 'playwright-core';
const base=process.env.REVIEW_BASE_URL||'http://127.0.0.1:5078';
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
try {
  const context=await browser.newContext({viewport:{width:375,height:820},serviceWorkers:'block'});
  const page=await context.newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base+'/producto/4');
  await page.locator('#ox-privacy-banner [data-privacy-reject]').click();
  await page.locator('.pd-combo-qty-control[data-action=increment]').click({clickCount:2});
  assert.match(await page.locator('.pd-add-btn').textContent(),/Añadir combo/);
  await Promise.all([
    page.waitForResponse(r=>r.url().includes('/carrito')&&r.request().method()==='POST'),
    page.locator('.pd-add-btn').click(),
  ]);
  await page.goto(base+'/checkout');
  await page.locator('[name=telefono_invitado]').fill('+34600000002');
  await page.locator('[name=nombre_invitado]').fill('Cliente QA recorrido');
  await page.locator('[name=tipo_entrega_cliente][value=recogida]').check();
  await page.locator('[name=metodo_pago][value=efectivo]').check();
  await page.locator('[name=acepta_condiciones]').check();
  await page.locator('button[type=submit]').click();
  await page.waitForURL(/\/pedido\/\d+\/confirmado/);
  const ticket=page.url();
  const cta=page.locator('.order-verify-cta');
  assert.equal(new URL(await cta.getAttribute('href'),base).searchParams.get('text'),'si');
  assert.equal(await page.getByRole('link',{name:'💬 Consultar en el chat'}).isVisible(),true);
  await page.screenshot({path:'/tmp/parcerito-primer-pedido-pendiente.png',fullPage:true});
  // Simula exclusivamente la llamada privada que hace el bot al recibir «si».
  const confirmation=await context.request.post(base+'/api/bot/confirmacion/responder',{
    headers:{'X-Bot-Key':'qa-flow-only'},data:{telefono:'+34600000002',respuesta:'si'},
  });
  assert.equal(confirmation.status(),200);
  assert.equal((await confirmation.json()).accion,'confirmado');
  await page.evaluate(()=>window.dispatchEvent(new Event('pageshow')));
  await cta.waitFor({state:'hidden'});
  assert.doesNotMatch(await page.locator('[data-order-title]').textContent(),/Confirma/);
  await page.goto(base+'/ayuda');
  await page.locator('#wcp-input').fill('quiero mi tiket');
  await page.locator('#wcp-form button[type=submit]').click();
  const ticketLink=page.getByRole('link',{name:'Ver pedido y ticket'});
  await ticketLink.waitFor();
  assert.equal(new URL(await ticketLink.getAttribute('href'),base).href,ticket);
  await ticketLink.click();
  assert.equal(page.url(),ticket);
  assert.equal(await page.locator('.order-verification-alert').count(),0);
  assert.deepEqual(errors,[]);
  console.log('OK: armar combo → checkout recogida → ticket pendiente → si por API bot → ticket confirmado → solicitar tiket en chat → reabrir ticket.');
} finally {await browser.close();}
