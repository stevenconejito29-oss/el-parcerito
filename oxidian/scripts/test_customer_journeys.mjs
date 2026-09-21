// Regresiones de interacción con red simulada y scripts de producción.
import assert from 'node:assert/strict';
import { chromium } from 'playwright-core';

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
  headless: true, args: ['--no-sandbox'],
});
try {
  const page = await browser.newPage({viewport:{width:375,height:812}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const messages = Array.from({length:100}, (_, i) => ({id:i+20,sender:'bot',body:`Mensaje ${i+20}`}));
  messages[0].created_at = '2026-09-10T10:30:00Z';
  messages[1].created_at = 'invalid-date';
  messages[2].body = '<img src=x onerror="alert(1)">';
  const posts = [];
  let failOnce = true;
  const payload = () => ({ok:true,conversation:{status:'bot'},messages,orders:[],has_older:true});
  await page.route('http://customer.test/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith('/messages')) {
      const data = route.request().postDataJSON(); posts.push(data);
      if (failOnce) { failOnce = false; return route.abort('failed'); }
      return route.fulfill({json:{...payload(),messages:[{id:120,sender:'client',body:data.message}]}});
    }
    if (url.pathname.endsWith('/state')) {
      if (url.searchParams.has('before')) return route.fulfill({json:{...payload(),messages:[{id:19,sender:'bot',body:'Más antiguo'}],has_older:false}});
      if (url.searchParams.has('after')) return route.fulfill({json:{...payload(),messages:[{id:121,sender:'bot',body:'Nuevo aviso'}]}});
      return route.fulfill({json:payload()});
    }
    return route.fulfill({contentType:'text/html',body:'<html></html>'});
  });
  await page.goto('http://customer.test/');
  await page.setContent(`<style>#wcp-messages{height:180px;overflow:auto}.wcp-message{height:35px}</style>
    <p id="wcp-status"></p><button id="wcp-history" hidden>Historial</button><button id="wcp-latest" hidden>Nuevos</button><button id="wcp-retry" hidden>Reintentar</button>
    <div id="wcp-messages"></div><div id="wcp-orders"></div><div id="wcp-reorder"></div>
    <div id="wcp-handoff"><p id="wcp-handoff-copy"></p><button id="wcp-agent">Equipo</button><button id="wcp-resume">Asistente</button></div>
    <button data-wcp-quick="horario">Horario</button><form id="wcp-form"><textarea id="wcp-input"></textarea><button type="submit">Enviar</button></form>`);
  await page.addScriptTag({path:'static/js/web-chat-page.js'});
  await page.waitForSelector('[data-id="20"] time');
  assert.equal(await page.locator('[data-id="20"] .wcp-message-meta span').textContent(), 'Asistente');
  assert.equal(await page.locator('[data-id="20"] time').getAttribute('datetime'), '2026-09-10T10:30:00.000Z');
  assert.equal(await page.locator('[data-id="21"] time').count(), 0);
  assert.equal(await page.locator('[data-id="22"] .wcp-message-body').textContent(), messages[2].body);
  assert.equal(await page.locator('[data-id="22"] img').count(), 0);
  await page.waitForFunction(() => document.querySelectorAll('[data-id]').length === 100);
  await page.locator('#wcp-input').fill('Primer mensaje');
  await page.locator('#wcp-form').evaluate(form => form.requestSubmit());
  await page.locator('#wcp-input').fill('Segundo borrador');
  await page.locator('#wcp-form').evaluate(form => form.requestSubmit());
  await page.waitForFunction(() => !document.getElementById('wcp-retry').hidden);
  assert.equal(await page.locator('#wcp-input').inputValue(), 'Segundo borrador');
  await page.locator('#wcp-retry').click();
  await page.waitForFunction(() => document.querySelector('[data-id="120"]'));
  assert.equal(posts.length, 2);
  assert.equal(posts[0].nonce, posts[1].nonce, 'Reintentar conserva identidad del envío');
  assert.equal(await page.locator('#wcp-input').inputValue(), 'Segundo borrador');
  await page.locator('#wcp-messages').evaluate(log => {log.scrollTop=0;});
  await page.waitForFunction(() => document.querySelector('[data-id="121"]'));
  assert.equal(await page.locator('#wcp-messages').evaluate(log=>log.scrollTop),0,'Polling respeta lectura anterior');
  assert.equal(await page.locator('#wcp-latest').isVisible(),true);
  await page.locator('#wcp-history').click();
  await page.waitForFunction(() => document.querySelector('[data-id="19"]'));
  assert.equal(await page.locator('#wcp-messages > :first-child').getAttribute('data-id'),'19');
  await page.close();

  const cart = await browser.newPage();
  cart.on('pageerror', error => errors.push(error.message));
  let saved;
  await cart.route('http://customer.test/**', route => {
    if(route.request().method()==='POST') saved = new URLSearchParams(route.request().postData());
    return route.fulfill({contentType:'text/html',body:'<html>Guardado</html>'});
  });
  await cart.goto('http://customer.test/cart');
  await cart.setContent(`<form action="/carrito/actualizar" method="post" id="cr-form-qty" data-max-quantity="10">
    <input type="hidden" id="cr-continuar" name="continuar"><div class="cr-item" data-line-key="a" data-price="5">
    <input class="cr-qty-input" type="number" id="qty_a" name="cantidad_a" value="1" min="0" max="10">
    <button type="button" data-cart-qty="1" data-cart-line-key="a">Más</button><span id="subtotal_a"></span></div>
    <div id="cr-update-row" hidden></div></form><span id="total-display"></span><span id="total-estimado"></span><a id="btn-checkout" href="/checkout">Continuar</a>`);
  await cart.addScriptTag({path:'static/js/storefront-cart.js'});
  await cart.getByText('Más', {exact:true}).click();
  await cart.getByText('Más', {exact:true}).click();
  assert.equal(await cart.locator('#total-display').textContent(),'€15.00');
  await cart.locator('#btn-checkout').click();
  await cart.waitForURL('**/carrito/actualizar');
  assert.equal(saved.get('cantidad_a'),'3');
  assert.equal(saved.get('continuar'),'checkout');
  assert.deepEqual(errors,[]);
  console.log('OK: historial, lectura, borrador concurrente, reintento idempotente y carrito antes de checkout.');
} finally { await browser.close(); }
