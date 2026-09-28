// Requiere serve_flow_review.py: catálogo y pedidos sintéticos en memoria.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {chromium} from 'playwright-core';
const base = process.env.REVIEW_BASE_URL || 'http://127.0.0.1:5079';
const combo = fs.readFileSync('/tmp/parcerito-flow-qa-combo', 'utf8').trim();
const browser = await chromium.launch({headless:true,args:['--no-sandbox']});
const errors = [];
try {
  for (const standalone of [false,true]) for (const width of [280,320,375,480,768,1024,1440]) {
    const context = await browser.newContext({viewport:{width,height:820},serviceWorkers:'block'});
    if (standalone) await context.addInitScript(() => Object.defineProperty(navigator,'standalone',{get:()=>true}));
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    for (const route of ['/',`/producto/${combo}`,'/ayuda']) {
      await page.goto(base+route);
      const privacy = page.locator('#ox-privacy-banner [data-privacy-reject]');
      if (await privacy.isVisible()) await privacy.click();
      assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth <= innerWidth+1), `Overflow ${route} ${width} pwa=${standalone}`);
      if (route === '/') {
        const failures = await page.locator('.ep-grid .ep-card').evaluateAll(cards => cards.flatMap(card => {
          const bounds = card.getBoundingClientRect();
          return [...card.querySelectorAll('.ep-card-name,.ep-card-price,.ep-btn-detail')].filter(el => {
            const r = el.getBoundingClientRect();
            return !r.width || !r.height || r.right>bounds.right+1 || r.bottom>bounds.bottom+1 || r.left<bounds.left-1 || el.scrollWidth>el.clientWidth+2;
          }).map(el=>el.className);
        }));
        assert.deepEqual(failures, [], `Contenido recortado ${width} pwa=${standalone}`);
        const compressedBadges = await page.locator('.ep-cbadge-label').evaluateAll(labels => labels.filter(el =>
          el.textContent.trim().length > 8 && el.getBoundingClientRect().width < 64
        ).map(el=>el.textContent));
        assert.deepEqual(compressedBadges, [], `Modalidad ilegible ${width} pwa=${standalone}`);
      }
      if (route.includes('/producto/')) {
        const titleWidth = await page.locator('.pd-cstep-meta').first().evaluate(el=>el.clientWidth);
        assert.ok(titleWidth >= 85, `Título combo demasiado estrecho: ${titleWidth} (${width})`);
      }
      if ([320,375,768].includes(width)) await page.screenshot({path:`/tmp/parcerito-review-${standalone?'pwa':'web'}-${width}-${route==='/'?'catalogo':route.includes('producto')?'combo':'chat'}.png`,fullPage:true});
    }
    console.log(`Catálogo, combo y chat ${width}px ${standalone?'PWA simulada':'web'} OK`);
    await context.close();
  }
  assert.deepEqual(errors, []);
} finally { await browser.close(); }
