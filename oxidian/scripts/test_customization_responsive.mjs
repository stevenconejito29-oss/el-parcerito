// Servidor QA: REVIEW_RICH_CATALOG=1 REVIEW_PORT=5077 serve_flow_review.py
import assert from 'node:assert/strict';
import {chromium} from 'playwright-core';
import fs from 'node:fs';
const base=process.env.REVIEW_BASE_URL||'http://127.0.0.1:5077';
const comboId=fs.readFileSync('/tmp/parcerito-flow-qa-combo','utf8').trim();
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
async function contained(page, selector) {
 const failures=await page.locator(selector).evaluateAll(elements=>elements.filter(el=>{
  const r=el.getBoundingClientRect();
  return r.width&&r.height&&(r.left<0||r.right>innerWidth+1||el.scrollWidth>el.clientWidth+2);
 }).map(el=>({class:el.className,text:el.textContent.slice(0,80)})));
 assert.deepEqual(failures,[],selector);
}
try {
 for(const standalone of [false,true]) for(const viewport of [{width:280,height:740},{width:375,height:812},{width:852,height:393},{width:1280,height:800}]) {
  const context=await browser.newContext({viewport,serviceWorkers:'block'});
  if(standalone) await context.addInitScript(()=>Object.defineProperty(navigator,'standalone',{get:()=>true}));
  const page=await context.newPage();
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(base);
  const privacy=page.locator('#ox-privacy-banner [data-privacy-reject]');if(await privacy.isVisible()) await privacy.click();
  await page.evaluate(()=>document.documentElement.style.fontSize='20px');
  if(standalone) await page.evaluate(()=>{
   for(const [key,value] of Object.entries({'--theme-surface':'#18212a','--theme-surface-alt':'#263441','--theme-text':'#f4f5f7','--theme-muted':'#bac7d4'})) document.body.style.setProperty(key,value);
  });
  const data=await page.locator('#ep-data').evaluate(el=>JSON.parse(el.textContent));
  const configurable=Object.values(data).find(p=>p.option_groups?.length===2);
  assert.ok(configurable,'Producto con sabores y extras');
  for(const id of [configurable.id, Number(comboId),Object.values(data).find(p=>p.nombre?.startsWith('Combo familiar')).id,Object.values(data).find(p=>p.nombre?.startsWith('Bebida natural')).id]) {
   await page.locator(`[data-product-card="${id}"] .ep-card-img`).click();
   await page.locator('#ep-modal').waitFor({state:'visible'});
   await contained(page,'.ep-modal-name,.ep-modal-desc,.ep-modal-combo-item,.ep-modal-choice-summary,.ep-modal-options section,.ep-modal-pres-pill,.ep-modal-detail-btn,.ep-modal-add-btn');
   if(id===configurable.id) {
    assert.match(await page.locator('#ep-modal-options').textContent(),/Mango.*maracuyá/);
    assert.match(await page.locator('#ep-modal-options').textContent(),/Extra de queso.*1.50/);
    if(standalone) assert.equal(await page.locator('#ep-modal-options h3').first().evaluate(el=>getComputedStyle(el).color),'rgb(244, 245, 247)');
   }
   if(data[id].nombre.startsWith('Bebida natural')) {
    await page.locator('.ep-modal-pres-pill').last().click();
    assert.equal(await page.locator('#ep-modal-price').textContent(),'€7.00');
    assert.equal(await page.locator('#ep-modal-pres-val').inputValue(),'grande');
   }
   if(data[id].nombre.startsWith('Combo familiar')) assert.equal(await page.locator('.ep-modal-combo-item').count(),4);
   await page.locator('#ep-modal-detail-btn').scrollIntoViewIfNeeded();
   if(viewport.width===375&&!standalone) await page.screenshot({path:`/tmp/parcerito-modal-${id}.png`});
   await page.keyboard.press('Escape');
   await page.locator('#ep-modal').waitFor({state:'hidden'});
  }
  await page.goto(base+'/producto/'+comboId);
  await page.evaluate(()=>document.documentElement.style.fontSize='20px');
  await contained(page,'.pd-combo-guide,.pd-combo-guide-copy,.pd-combo-guide-step,.pd-flavor-chip,.pd-combo-unit-presentation,.pd-combo-unit-flavor,.pd-combo-opcion,.pd-combo-opt-name,.pd-combo-opt-desc,.pd-combo-qty-selector');
  for (const option of await page.locator('.pd-combo-opcion:has(.pd-combo-qty-selector)').all()) {
   const overlap=await option.evaluate(el=>{
    const body=el.querySelector('.pd-combo-opt-body').getBoundingClientRect();
    const qty=el.querySelector('.pd-combo-qty-selector').getBoundingClientRect();
    return Math.min(body.bottom,qty.bottom)>Math.max(body.top,qty.top)+1 && Math.min(body.right,qty.right)>Math.max(body.left,qty.left)+1;
   });
   assert.equal(overlap,false,'La cantidad no invade el nombre del componente');
   await option.locator('[data-action=increment]').click();
   assert.equal(await option.locator('.pd-combo-qty-input').inputValue(),'1');
  }
  const guide=page.locator('#pd-combo-guide');
  assert.equal(await guide.evaluate(el=>getComputedStyle(el).position),'static');
  assert.ok(await page.locator('.pd-combo-guide-steps').evaluate(el=>el.clientWidth>=el.parentElement.clientWidth-50),'Los pasos usan el ancho de la guía');
  for(const step of await page.locator('.pd-combo-guide-step').all()) {
   const box=await step.boundingBox();assert.ok(box.height>=44);
   await step.click();
  }
  if(viewport.width===375&&!standalone) await guide.screenshot({path:'/tmp/parcerito-guia-combo-responsive.png',style:'.ox-header-public,.ox-bottom-nav,.pd-add-bar{visibility:hidden!important}'});
  await page.goto(base+'/producto/'+configurable.id);
  await page.evaluate(()=>document.documentElement.style.fontSize='20px');
  await contained(page,'.pd-extra-group,.pd-flavor-chip,.pd-flavor-chip__name,.pd-extra-option,.pd-extra-stepper');
  await page.locator('[data-flavor-option]').first().click();
  assert.equal(await page.locator('[data-flavor-option]').first().getAttribute('aria-pressed'),'true');
  assert.equal(await page.locator('.pd-extra-stepper').first().evaluate(el=>getComputedStyle(el).flexDirection),'row');
  await page.locator('[data-extra-step="+1"]').first().click();
  assert.equal(await page.locator('.pd-extra-qty').first().inputValue(),'1');
  if(viewport.width===375&&!standalone) await page.locator('.pd-extras').screenshot({path:'/tmp/parcerito-sabores-extras-responsive.png',style:'.ox-header-public,.ox-bottom-nav,.pd-add-bar{visibility:hidden!important}'});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  assert.deepEqual(errors,[]);
  console.log(`Modal, progreso, sabores y extras ${viewport.width}px ${standalone?'PWA':'web'} OK`);
  await context.close();
 }
} finally {await browser.close();}
