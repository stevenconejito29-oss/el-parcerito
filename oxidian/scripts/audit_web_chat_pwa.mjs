import { chromium } from 'playwright-core';

const target = (process.env.PWA_AUDIT_URL || 'https://elparcerito.com').replace(/\/$/, '');
const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || '/home/panzeta/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome';
const browser = await chromium.launch({ executablePath, headless:true, args:['--no-sandbox','--disable-dev-shm-usage'] });
try {
  for (const viewport of [{width:393,height:852},{width:402,height:874},{width:440,height:956},{width:852,height:393}]) {
    const context = await browser.newContext({viewport,isMobile:true,hasTouch:true,locale:'es-ES'});
    const page = await context.newPage();
    await page.addInitScript(() => {
      const native = window.matchMedia.bind(window);
      window.matchMedia = query => query === '(display-mode: standalone)'
        ? {matches:true,media:query,addListener(){},removeListener(){},addEventListener(){},removeEventListener(){},dispatchEvent(){return false}}
        : native(query);
      Object.defineProperty(navigator,'standalone',{value:true});
    });
    await page.goto(target,{waitUntil:'networkidle'});
    const privacy = page.locator('[data-privacy-reject]:visible').first();
    if (await privacy.count()) await privacy.click();
    const navBefore = await page.locator('.ox-bottom-nav').boundingBox();
    await page.locator('[data-bnav="chat"]').click();
    await page.waitForURL(/\/ayuda(?:\?|$)/);
    const navAfter = await page.locator('.ox-bottom-nav').boundingBox();
    if (!navBefore || !navAfter || Math.abs(navBefore.x-navAfter.x)>2 || Math.abs(navBefore.y-navAfter.y)>2 || Math.abs(navBefore.width-navAfter.width)>2) {
      const navState = await page.locator('.ox-bottom-nav').evaluate(el => ({display:getComputedStyle(el).display,body:document.body.className,active:document.activeElement?.id||document.activeElement?.tagName}));
      throw new Error(`La navegación cambió de posición al entrar al chat ${JSON.stringify({viewport,navBefore,navAfter,navState})}`);
    }
    await page.locator('#wcp-input').fill('¿Cómo funcionan los cafecitos?');
    const composer = await page.locator('.wcp-compose').boundingBox();
    if (!composer || composer.x < -2 || composer.x + composer.width > viewport.width + 2 || composer.y + composer.height > viewport.height + 2) throw new Error(`Compositor fuera del viewport ${JSON.stringify({viewport,composer})}`);
    const before = page.url();
    await page.locator('#wcp-form button[type="submit"]').click();
    await page.locator('.wcp-message.is-bot', {hasText:/cafecitos/i}).last().waitFor();
    if (page.url() !== before) throw new Error(`El chat navegó fuera de su vista: ${before} -> ${page.url()}`);
    const box = await page.locator('.wcp-page').boundingBox();
    if (!box || box.x < -3 || box.y < -3 || box.x + box.width > viewport.width + 3 || box.y + box.height > viewport.height + 3) throw new Error(`Vista fuera del viewport ${JSON.stringify({viewport,box})}`);
    if (box.y + box.height > navAfter.y + 2) throw new Error(`El chat invade la navegación ${JSON.stringify({viewport,box,navAfter})}`);
    console.log(`chat ${viewport.width}x${viewport.height}: OK`);
    await context.close();
  }
} finally { await browser.close(); }
