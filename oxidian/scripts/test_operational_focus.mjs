// Pruebas de interacción en navegador, sin servidor ni datos reales.
import assert from 'node:assert/strict';
import { chromium } from 'playwright-core';

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
  headless: true,
  args: ['--no-sandbox'],
});
try {
  const page = await browser.newPage({ viewport: { width: 375, height: 812 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('http://operational.test/**', route => route.fulfill({ body: '<html></html>', contentType: 'text/html' }));
  await page.goto('http://operational.test/');
  await page.setContent(`<div class="work-area route-board has-active-route">
    <nav data-work-focus data-default-focus="active" hidden>
      <button data-work-focus-button="active">Entregar</button>
      <button data-work-focus-button="ready">Recoger</button>
      <button data-work-focus-button="all">Ver todo</button>
    </nav>
    <section class="route-lane--ready" data-work-focus-panel="ready">Por recoger</section>
    <section class="route-lane--active" data-work-focus-panel="active">Entrega en curso</section>
  </div>`);
  // Sin JS no desaparece trabajo.
  assert.equal(await page.locator('[data-work-focus-panel]:visible').count(), 2);
  await page.addStyleTag({ path: 'static/css/rider-console.css' });
  await page.addStyleTag({ path: 'static/css/operational-roles.css' });
  await page.addScriptTag({ path: 'static/js/operational-roles.js' });
  assert.equal(await page.locator('[data-work-focus-panel="ready"]').isVisible(), false);
  assert.equal(await page.locator('[data-work-focus-panel="active"]').isVisible(), true);
  await page.getByRole('button', { name: 'Recoger', exact: true }).click();
  assert.equal(await page.locator('[data-work-focus-panel="ready"]').isVisible(), true);
  assert.equal(await page.locator('[data-work-focus-panel="active"]').isVisible(), false);
  await page.getByRole('button', { name: 'Ver todo' }).focus();
  await page.keyboard.press('Enter');
  assert.equal(await page.locator('[data-work-focus-panel]:visible').count(), 2);

  const checklist = (keys) => `<article class="work-card">${keys.map(key => `<label class="work-item"><input type="checkbox" data-item-check data-item-key="${key}">${key}</label>`).join('')}
    <form data-prep-confirm-ready data-pedido-id="42"><button data-ready-button disabled>Comprobar</button></form></article>`;
  async function load(keys) {
    await page.setContent(checklist(keys));
    await page.addScriptTag({ path: 'static/js/preparation-checklist.js' });
  }
  await load(['10:1', '11:2']);
  await page.locator('[data-item-key="10:1"]').check();
  assert.equal(await page.locator('[data-ready-button]').isDisabled(), true);
  // Una reordenación no marca por error otro producto.
  await load(['11:2', '10:1']);
  assert.equal(await page.locator('[data-item-key="10:1"]').isChecked(), true);
  assert.equal(await page.locator('[data-item-key="11:2"]').isChecked(), false);
  await page.locator('[data-item-key="11:2"]').check();
  assert.equal(await page.locator('[data-ready-button]').isEnabled(), true);
  await page.evaluate(() => document.querySelector('form').dispatchEvent(new Event('submit', { cancelable: true })));
  await load(['10:1', '11:2']);
  assert.equal(await page.locator('[data-ready-button]').isEnabled(), true);
  // Cambiar la cantidad obliga a comprobar de nuevo esa línea.
  await load(['10:3', '11:2']);
  assert.equal(await page.locator('[data-item-key="10:3"]').isChecked(), false);
  assert.equal(await page.evaluate(() => document.querySelector('form').dispatchEvent(new Event('submit', { cancelable: true }))), false);
  await page.evaluate(() => {
    Storage.prototype.getItem = () => { throw new Error('Storage blocked'); };
    Storage.prototype.setItem = () => { throw new Error('Storage blocked'); };
  });
  await load(['10:1']);
  await page.locator('[data-item-check]').check();
  assert.equal(await page.locator('[data-ready-button]').isEnabled(), true);
  assert.deepEqual(errors, []);
  console.log('OK: etapas, teclado, reordenación, cantidad, reintento y almacenamiento bloqueado.');
} finally {
  await browser.close();
}
