// Comprueba la preferencia de accesibilidad sobre el CSS real compartido.
import assert from 'node:assert/strict';
import { chromium } from 'playwright-core';

const browser = await chromium.launch({
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
  headless: true,
  args: ['--no-sandbox'],
});
try {
  const page = await browser.newPage();
  await page.setContent(`<div class="skeleton">Cargando</div>
    <div class="motion-pulse">Estado</div>
    <div class="hx-animate htmx-added">Pedido</div>
    <details class="favor-cancel" open><summary>Acciones</summary><div>Confirmación</div></details>
    <details class="sa-disclosure" open><summary>Configuración</summary><div class="sa-disclosure-body">Opciones</div></details>`);
  await page.addStyleTag({ path: 'static/css/motion.css' });
  const selectors = ['.skeleton', '.motion-pulse', '.htmx-added', '.favor-cancel > div', '.sa-disclosure-body'];
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  for (const selector of selectors) {
    assert.notEqual(await page.locator(selector).evaluate(el => getComputedStyle(el).animationName), 'none', selector);
  }
  await page.emulateMedia({ reducedMotion: 'reduce' });
  for (const selector of selectors) {
    assert.equal(await page.locator(selector).evaluate(el => getComputedStyle(el).animationName), 'none', selector);
    assert.ok(await page.locator(selector).isVisible(), selector);
  }
  console.log('OK: skeleton, estados, HTMX y desplegables respetan movimiento reducido.');
} finally {
  await browser.close();
}
