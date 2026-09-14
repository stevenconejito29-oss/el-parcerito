import assert from 'node:assert/strict';
import { chromium } from 'playwright-core';

const browser = await chromium.launch({ executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE, headless: true, args: ['--no-sandbox'] });
try {
  const page = await browser.newPage();
  await page.setContent(`<body class="ox-body-admin">
    <div data-tool-search hidden><input type="search"><p id="ox-tool-results" role="status"></p></div>
    <nav id="sb-nav">
      <details class="ox-sb-category" open><summary>Operación diaria</summary><a class="ox-sb-item active" href="/pedidos">Pedidos</a></details>
      <details class="ox-sb-category"><summary>Finanzas</summary><a class="ox-sb-item" href="/caja">Caja diaria</a><a class="ox-sb-item" href="/comisiones">Liquidación</a></details>
      <details class="ox-sb-category"><summary>Sin permiso</summary></details>
    </nav></body>`);
  await page.addStyleTag({ path: 'static/css/system-foundation.css' });
  await page.addScriptTag({ path: 'static/js/admin-tool-search.js' });
  const search = page.locator('[data-tool-search] input');
  assert.ok(await search.isVisible());
  await search.fill('liquidacion');
  assert.ok(await page.locator('[href="/comisiones"]').isVisible());
  assert.equal(await page.locator('[href="/pedidos"]').isVisible(), false);
  assert.equal(await page.locator('#sb-nav a:not([hidden])').count(), 1);
  await search.fill('finanzas');
  assert.equal(await page.locator('#sb-nav a:not([hidden])').count(), 2);
  await search.fill('inexistente');
  assert.match(await page.locator('[role=status]').innerText(), /No hay/);
  await search.press('Escape');
  assert.equal(await search.inputValue(), '');
  assert.ok(await page.locator('[href="/pedidos"]').isVisible());
  assert.equal(await page.locator('.ox-sb-category[open]').count(), 1);
  assert.equal(await page.locator('.ox-sb-category[hidden]').count(), 1);
  assert.equal(await page.locator('#sb-nav a').count(), 3);
  await page.locator('.ox-sb-category').nth(1).locator('summary').click();
  assert.ok(await page.locator('[href="/caja"]').isVisible());
  console.log('OK: búsqueda por nombre/categoría, acentos, vacíos y restauración del menú.');
} finally { await browser.close(); }
