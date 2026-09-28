/* QA sintético: simula display-mode, no sustituye instalación física Android/iOS. */
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {chromium} from 'playwright-core';
const browser=await chromium.launch({headless:true,args:['--no-sandbox']});
const base='http://127.0.0.1:5079';
const errors=[];
try {
 const staff=await browser.newContext();const admin=await staff.newPage();
 await admin.goto(base+'/auth/login');
 await admin.locator('[name=email]').fill('super_admin@test.invalid');
 await admin.locator('[name=password]').fill('qa-only-password');
 await admin.locator('button[type=submit]').click();await admin.waitForURL('**/superadmin/dashboard');
 await admin.goto(base+'/superadmin/config?section=acceso');
 await admin.locator('[name=ACCESO_CLIENTES_REGISTRADOS]').selectOption('1');
 await admin.locator('[name=ACCESO_REQUIERE_PWA]').selectOption('1');
 await admin.getByRole('button',{name:'Guardar acceso',exact:true}).click();
 await admin.waitForURL('**/superadmin/config?section=acceso');
 assert.equal(await admin.locator('[name=ACCESO_REQUIERE_PWA]').inputValue(),'1');
 const context=await browser.newContext({viewport:{width:320,height:740}});
 const page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 page.on('console',m=>{if(m.type()==='error'&&m.text().includes('Content Security Policy'))errors.push(m.text());});
 await page.goto(base+'/');await page.waitForURL('**/acceso');
 assert.equal(await page.locator('#telefono').isVisible(),false);
 assert.equal(await page.locator('#private-install-button').isVisible(),true);
 assert.equal((await context.request.get(base+'/manifest.webmanifest')).status(),403);
 const manifest=await context.request.get(base+'/acceso/manifest.webmanifest');assert.equal(manifest.status(),200);
 const data=await manifest.json();assert.equal(data.start_url,'/acceso?source=pwa');assert.equal(data.shortcuts,undefined);
 for (const icon of data.icons) assert.equal((await context.request.get(base+icon.src)).status(),200);
 assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
 await page.screenshot({path:'/tmp/parcerito-install-320.png',fullPage:true});
 // El mismo navegador, ahora abierto en una ventana de app.
 await context.addInitScript(()=>Object.defineProperty(navigator,'standalone',{get:()=>true}));
 await page.goto(base+'/acceso?source=pwa');await page.locator('#telefono').waitFor({state:'visible'});
 assert.equal(await page.locator('#private-install-button').isVisible(),false);
 assert.equal((await context.request.get(base+'/api/producto/1/opciones')).status(),403);
 await page.locator('#telefono').fill('+34600000000');
 await page.getByRole('button',{name:'Recibir código',exact:true}).click();
 await page.locator('#codigo').waitFor({state:'visible'});
 await page.locator('#codigo').fill(fs.readFileSync('/tmp/parcerito-flow-qa-otp','utf8'));
 await page.getByRole('button',{name:'Entrar',exact:true}).click();await page.waitForURL(base+'/');
 assert.equal(await page.locator('#private-app-veil').count(),0);
 assert.equal((await context.request.get(base+'/manifest.webmanifest')).status(),200);
 // Cookie compartida: una pestaña normal vuelve a la pantalla de instalación.
 const web=await browser.newContext();await web.addCookies(await context.cookies());
 const tab=await web.newPage();await tab.goto(base+'/');await tab.waitForURL('**/acceso?instalar=1');
 assert.equal(await tab.locator('#telefono').isVisible(),false);
 assert.equal(await tab.locator('#private-install-button').isVisible(),true);
 const other=await browser.newContext();assert.equal((await other.request.get(base+'/api/producto/1/opciones')).status(),403);
 await admin.locator('[name=ACCESO_CLIENTES_REGISTRADOS]').selectOption('0');
 await admin.getByRole('button',{name:'Guardar acceso',exact:true}).click();await admin.waitForURL('**/superadmin/config?section=acceso');
 const publicPage=await other.newPage();await publicPage.goto(base+'/');assert.equal(publicPage.url(),base+'/');
 assert.equal(await publicPage.locator('#private-app-veil').count(),0);
 assert.deepEqual(errors,[]);
 console.log('Instalación → apertura → OTP → menú; navegador normal, permisos y vuelta a público: OK. Display-mode simulado.');
} finally {await browser.close();}
