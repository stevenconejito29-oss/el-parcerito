// Requiere REVIEW_RICH_CATALOG=1 REVIEW_PORT=5077 serve_flow_review.py.
import assert from 'node:assert/strict';
import {chromium} from 'playwright-core';
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
const base=process.env.REVIEW_BASE_URL||'http://127.0.0.1:5077';
try {
 for(const scheme of ['light','dark']) for(const viewport of [{width:280,height:740},{width:375,height:820},{width:852,height:393},{width:1024,height:768}]) {
  const page=await browser.newPage({viewport,colorScheme:scheme,serviceWorkers:'block'});
  await page.goto(base);
  await page.locator('#ox-privacy-banner [data-privacy-reject]').click();
  // La tienda usa colores configurables, independientes del tema del sistema.
  // Probamos también una paleta oscura sobre las mismas variables de marca.
  if(scheme==='dark') await page.evaluate(()=>{
    for(const [key,value] of Object.entries({'--theme-surface':'#18212a','--theme-surface-alt':'#263441','--theme-text':'#f4f5f7','--theme-muted':'#bac7d4'})) document.body.style.setProperty(key,value);
  });
  const combo=page.locator('.ep-card').filter({has:page.locator('.ep-card-name').filter({hasText:'Combo familiar'})});
  assert.equal(await combo.locator('.combo-included-list li').count(),4);
  assert.match(await combo.locator('.combo-included-list li').last().textContent(),/4×.*Incluido 4/);
  assert.equal(await combo.locator('.ep-card-price').textContent(),'€17.50');
  assert.match(await combo.locator('.ep-card-combo-copy').textContent(),/Elige entre 2 y 3/);
  assert.doesNotMatch(await combo.textContent(),/NO MOSTRAR|\+ .*más/);
  const sized=page.locator('.ep-card').filter({has:page.locator('.ep-card-name').filter({hasText:'Bebida natural en varios tamaños'})});
  assert.equal(await sized.locator('.ep-card-price').textContent(),'Desde €4.00');
  assert.equal(await sized.locator('.ep-card-size-chip').count(),2);
  // Texto ampliado: los datos críticos conservan espacio y no quedan tapados.
  await page.evaluate(()=>document.documentElement.style.fontSize='20px');
  const failures=await page.locator('.ep-card:not(.is-hidden)').evaluateAll(cards=>cards.flatMap(card=>{
    const outer=card.getBoundingClientRect();
    return [...card.querySelectorAll('.ep-card-name,.ep-card-price,.ep-btn-detail,.ep-cbadge,.combo-included-list li,.ep-card-combo-copy,.ep-card-size-chip')].filter(el=>{
      const r=el.getBoundingClientRect();
      return r.width&&r.height&&(r.right>outer.right+1||r.left<outer.left-1||r.bottom>outer.bottom+1||el.scrollWidth>el.clientWidth+2);
    }).map(el=>el.className);
  }));
  assert.deepEqual(failures,[],`${scheme} ${viewport.width}px texto ampliado`);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  if(viewport.width===375) await combo.screenshot({path:`/tmp/parcerito-combo-${scheme}.png`,style:'.ox-header-public,.ox-bottom-nav{visibility:hidden!important}'});
  await page.goto(new URL(await combo.locator('.ep-card-title-link').getAttribute('href'),base).href);
  assert.doesNotMatch(await page.locator('.pd-card').textContent(),/NO MOSTRAR/);
  assert.equal(await page.locator('#pd-unit-price').textContent(),'€17.50');
  console.log(`Contenido, precio y texto ampliado: ${scheme} ${viewport.width}px OK`);
  await page.close();
 }
} finally {await browser.close();}
