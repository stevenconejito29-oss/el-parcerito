// Interacción real de los scripts de cocina; backend simulado sin pedidos reales.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { chromium } from 'playwright-core';

const server = createServer((request, response) => {
  if (request.method === 'POST') {
    response.writeHead(303, {location: '/preparador/pedidos?print_after=42'});
    response.end();
  } else {
    response.writeHead(200, {'Content-Type': 'text/html; charset=utf-8'});
    response.end('<p>Aviso del servidor e impresión</p>');
  }
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
  headless: true, args: ['--no-sandbox'],
});
try {
  const page = await browser.newPage({viewport: {width: 375, height: 812}});
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  const markup = `<main data-slotops>
    <button data-turn="1" data-state="activa" data-pending="1">Salida activa</button>
    <button data-turn="2">Otra salida</button><div data-focusbar></div><div data-no-turn>Elige</div>
    <div data-panel="1"><article class="work-card">
      <label class="work-item"><input type="checkbox" data-item-check data-item-key="7:2">Dos artículos</label>
      <form method="post" action="/listo" data-slot-action data-prep-confirm-ready data-pedido-id="42">
        <button type="submit" data-ready-button disabled>Comprobar</button>
      </form></article></div><div data-panel="2">Sin pedidos</div></main>`;
  async function load() {
    await page.setContent(markup);
    await page.addScriptTag({path: 'static/js/preparation-checklist.js'});
    await page.addScriptTag({path: 'static/js/preparation-slots.js'});
  }
  await load();
  assert.equal(await page.locator('[data-panel="2"]').isVisible(), false);
  await page.locator('[data-turn="2"]').click();
  assert.equal(await page.locator('[data-turn="2"]').getAttribute('aria-pressed'), 'true');
  assert.match(await page.locator('[data-focusbar]').innerText(), /Sin pedidos/);
  await page.locator('[data-turn="1"]').click();
  assert.equal(await page.locator('[data-ready-button]').isDisabled(), true);
  await page.locator('[data-item-check]').check();
  await load();
  assert.equal(await page.locator('[data-item-check]').isChecked(), true);
  await page.locator('[data-ready-button]').click();
  await page.waitForURL('**/preparador/pedidos?print_after=42');
  assert.match(await page.locator('body').innerText(), /Aviso del servidor/);
  console.log('OK: selección, accesibilidad, comprobación persistente y redirección nativa.');
} finally {
  await browser.close();
  await new Promise(resolve => server.close(resolve));
}
