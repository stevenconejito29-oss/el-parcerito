import fs from 'node:fs';
import path from 'node:path';
import {chromium,webkit} from 'playwright-core';
const root=process.cwd(), out=process.env.ROLE_REVIEW_OUTPUT || '/tmp/parcerito-role-review';
const views=JSON.parse(fs.readFileSync(path.join(out,'manifest.json')));
const engine = process.env.REVIEW_BROWSER === 'webkit' ? webkit : chromium;
const browser=await engine.launch({...(engine === chromium ? {executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,args:['--no-sandbox']} : {}),headless:true});
const results=[];
try {
  for(const view of views) for(const [mode,width,height,theme] of [['small',320,740,'light'],['mobile',375,812,'light'],['landscape',852,393,'light'],['desktop',1280,900,'light'],['dark',375,812,'dark']]) {
    const context=await browser.newContext({viewport:{width,height},colorScheme:theme,serviceWorkers:'block'});
    const page=await context.newPage(), errors=[];
    page.on('pageerror',error=>errors.push(error.message));
    await page.route('**/*',route=>{
      const url=new URL(route.request().url());
      if(route.request().isNavigationRequest()) return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(out,view.name+'.html'))});
      if(url.pathname.startsWith('/static/')) {
        const filename=url.pathname.startsWith('/static/vendor/leaflet/') ? path.join(root,'node_modules/leaflet/dist',url.pathname.slice('/static/vendor/leaflet/'.length)) : path.join(root,url.pathname);
        if(fs.existsSync(filename)&&fs.statSync(filename).isFile())return route.fulfill({path:filename});
      }
      return route.fulfill({json:{ok:true,signature:'qa'}});
    });
    await page.goto('http://review.test'+view.route);
    await page.evaluate(theme=>{document.documentElement.dataset.theme=theme;document.documentElement.dataset.deliveryTheme=theme;},theme);
    await page.waitForTimeout(150);
    const lowContrast = ['cocina','preparacion'].includes(view.name) && theme === 'light' ? await page.evaluate(() => {
      const luminance = color => color.match(/[\d.]+/g).slice(0,3).map(Number).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((sum,v,i)=>sum+v*[.2126,.7152,.0722][i],0);
      return [...document.querySelectorAll('.work-hero .work-btn-ghost, .prep-device-tools > summary')].some(el=>{
        const style=getComputedStyle(el), foreground=luminance(style.color), background=luminance(style.backgroundColor);
        return (Math.max(foreground,background)+.05)/(Math.min(foreground,background)+.05)<4.5;
      });
    }) : false;
    const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
    const offenders=overflow?await page.evaluate(()=>[...document.querySelectorAll('main *')].filter(el=>{const r=el.getBoundingClientRect();return r.width&&r.right>innerWidth+1;}).slice(0,6).map(el=>({tag:el.tagName,cls:el.className,text:el.textContent.trim().slice(0,70)}))):[];
    if(['mobile','desktop'].includes(mode)) await page.screenshot({animations:'disabled',timeout:10000,path:path.join(out,`${view.name}-${mode}.png`),fullPage:true});
    // Los detalles de incidencia deben abrirse sin habilitar una cancelación accidental.
    const incident=page.locator('summary').filter({hasText:'Resolver una incidencia'}).first();
    let incidentOverflow=false;
    if(await incident.count() && await incident.isVisible()) {
      await incident.click();
      incidentOverflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1);
    }
    results.push({name:view.name,mode,overflow,incidentOverflow,lowContrast,errors,offenders});
    await context.close();
  }
} finally {await browser.close();}
fs.writeFileSync(path.join(out,'report.json'),JSON.stringify(results,null,2));
const failures=results.filter(row=>row.overflow||row.incidentOverflow||row.lowContrast||row.errors.length);
console.log(JSON.stringify({scenarios:results.length,failures},null,2));
process.exitCode=failures.length?1:0;
