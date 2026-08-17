import fs from 'node:fs';
import { chromium } from 'playwright-core';
const root = new URL('../../docs/auditoria_cruce/', import.meta.url).pathname;
fs.mkdirSync(root, {recursive:true});
const browser = await chromium.launch({headless:true, executablePath:'/home/panzeta/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome', args:['--no-sandbox']});
const base = process.env.AUDIT_URL || 'https://elparcerito.com';
const session = process.env.RIDER_SESSION || '';
const views = [
  ['iphone16-cruce','/favor',393,852,'light',false],
  ['iphone16-chat','/ayuda',393,852,'light',false],
  ['android-cruce','/favor',412,915,'dark',false],
  ['rider-cruce','/repartidor/favores',393,852,'light',true],
  ['desktop-cruce','/favor',1440,1000,'light',false],
];
const report=[];
for (const [name,path,width,height,colorScheme,rider] of views) {
  const context=await browser.newContext({viewport:{width,height},deviceScaleFactor:2,isMobile:width<500,hasTouch:width<500,colorScheme,locale:'es-CO',userAgent:'OxidianVisualAudit/1.0',extraHTTPHeaders:{'X-Forwarded-For':'127.0.0.1'}});
  await context.addInitScript(() => { const mm=window.matchMedia.bind(window); window.matchMedia=q=>q==='(display-mode: standalone)'?{matches:true,media:q,addListener(){},removeListener(){},addEventListener(){},removeEventListener(){}}:mm(q); Object.defineProperty(navigator,'standalone',{value:true}); });
  if(rider&&session) await context.addCookies([{name:'session',value:session,domain:'elparcerito.com',path:'/',secure:true,httpOnly:true,sameSite:'Lax'}]);
  const page=await context.newPage(); const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  const response=await page.goto(base+path,{waitUntil:'networkidle'});
  await page.screenshot({path:root+name+'.png',fullPage:true});
  const metrics=await page.evaluate(()=>({scrollWidth:document.documentElement.scrollWidth,clientWidth:document.documentElement.clientWidth,small:[...document.querySelectorAll('button,a,input')].filter(e=>{const r=e.getBoundingClientRect();return r.width>0&&r.height>0&&(r.width<40||r.height<40)}).length,body:getComputedStyle(document.body).backgroundColor,title:document.title}));
  report.push({name,status:response?.status(),url:page.url(),errors,metrics}); await context.close();
}
await browser.close(); fs.writeFileSync(root+'report.json',JSON.stringify(report,null,2)); console.log(JSON.stringify(report,null,2));
