// Servidor QA con franjas activas y al menos una salida futura con cupo.
import assert from 'node:assert/strict';
import {chromium} from 'playwright-core';
const base = process.env.REVIEW_BASE_URL || 'http://127.0.0.1:5079';
const browser = await chromium.launch();
try {
  const page = await browser.newPage({viewport:{width:393,height:852}});
  await page.goto(base+'/producto/1');
  await page.locator('#ox-privacy-banner [data-privacy-reject]').click();
  await page.locator('[data-flavor-option]').first().click();
  await Promise.all([page.waitForResponse(r=>r.url().includes('/carrito')&&r.request().method()==='POST'),page.locator('.pd-add-btn').click()]);
  await page.goto(base+'/checkout');
  await page.locator('#franjas-slot-container input').first().waitFor();
  const selected = await page.locator('#franjas-slot-id').inputValue();
  assert.ok(selected);
  assert.match(await page.locator('#franjas-slot-selected').innerText(),/Franja elegida/);
  for (const width of [280,320,393,768]) {
    await page.setViewportSize({width,height:852});
    await page.evaluate(()=>document.documentElement.style.fontSize='20px');
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),`franjas ${width}px`);
  }
  await page.setViewportSize({width:393,height:852});
  await page.locator('#franjas-slot-block').screenshot({path:'/tmp/parcerito-franjas-checkout.png'});
  await page.locator('[name=tipo_entrega_cliente][value=recogida]').check();
  assert.equal(await page.locator('#franjas-slot-id').inputValue(),'');
  assert.equal(await page.locator('#franjas-slot-block').isVisible(),false);
  await page.locator('[name=tipo_entrega_cliente][value=delivery]').check();
  assert.equal(await page.locator('#franjas-slot-id').inputValue(),selected);
  console.log('Franjas: sugerencia, 280–768px, recogida sin reserva y retorno conservando franja OK');
} finally {await browser.close();}
