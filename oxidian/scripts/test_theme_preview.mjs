import assert from 'node:assert/strict';
import {chromium} from 'playwright-core';
const browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,headless:true,args:['--no-sandbox']});
try {
  const page=await browser.newPage();
  const errors=[]; page.on('pageerror',error=>errors.push(error.message));
  const colors={COLOR_TEXTO:'#222222',COLOR_SUPERFICIE:'#ffffff',COLOR_TEXTO_SUAVE:'#444444',COLOR_CABECERA_FONDO:'#ffffff',COLOR_CABECERA_TEXTO:'#222222',COLOR_FONDO_APP:'#eeeeee',COLOR_ACENTO:'#245a9a',COLOR_PRIMARIO:'#654321',COLOR_SECUNDARIO:'#da4d40'};
  await page.setContent(`<form>${Object.entries(colors).map(([name,value])=>`<label>${name}<input type="color" name="${name}" value="${value}"></label>`).join('')}<button type="reset">Restaurar</button></form><section data-theme-preview><p data-theme-preview-note></p><div data-theme-preview-header></div><div data-theme-preview-card><span data-theme-preview-secondary></span><p data-theme-preview-price></p><p data-theme-preview-text></p><span data-theme-preview-button>Continuar</span></div><p data-theme-contrast></p></section>`);
  await page.addScriptTag({path:'static/js/theme-preview.js'});
  assert.match(await page.locator('[data-theme-contrast]').innerText(),/buen contraste/);
  await page.locator('[name=COLOR_TEXTO]').evaluate(el=>{el.value='#ffffff';el.dispatchEvent(new Event('input',{bubbles:true}));});
  assert.match(await page.locator('[data-theme-contrast]').innerText(),/texto principal/);
  assert.match(await page.locator('[data-theme-preview-note]').innerText(),/sin guardar/);
  await page.getByText('Restaurar',{exact:true}).click();
  assert.equal(await page.locator('[name=COLOR_TEXTO]').inputValue(),'#222222');
  assert.match(await page.locator('[data-theme-contrast]').innerText(),/buen contraste/);
  assert.doesNotMatch(await page.locator('[data-theme-preview-note]').innerText(),/sin guardar/);
  assert.deepEqual(errors,[]);
  console.log('OK: preview, contraste, cambios pendientes y restauración en Chromium');
} finally {await browser.close();}
