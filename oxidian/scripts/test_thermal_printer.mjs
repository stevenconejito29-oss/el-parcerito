// Dispositivos simulados: nunca imprime papel real.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {chromium} from 'playwright-core';
const browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,headless:true,args:['--no-sandbox']});
try {
 const page=await browser.newPage();
 let invalidTicket = false, networkRequests = 0;
 await page.route('https://printer.test/**', route => {
  if (route.request().isNavigationRequest()) return route.fulfill({contentType:'text/html',body:'<body class="view-preparador operational-view"></body>'});
  if (route.request().url().includes('/escpos')) return route.fulfill({contentType:invalidTicket ? 'text/html' : 'application/vnd.escpos',body:Buffer.alloc(5000,42)});
  if (route.request().method() === 'POST' && route.request().url().includes('/imprimir')) networkRequests++;
  return route.fulfill({json:{ok:true,printer:null,network_available:true}});
 });
 await page.goto('https://printer.test');
 await page.evaluate(()=>{
  window.writes=[];
  const alternate={alternateSetting:0,interfaceClass:7,endpoints:[{direction:'out',type:'bulk',endpointNumber:2}]};
  window.mockUSB={vendorId:1,productId:2,serialNumber:'qa',productName:'QA USB',opened:false,configuration:{interfaces:[{interfaceNumber:0,alternate,alternates:[alternate]}]},async open(){this.opened=true;},async close(){this.opened=false;},async claimInterface(){},async transferOut(endpoint,bytes){window.writes.push({endpoint,length:bytes.length});await new Promise(r=>setTimeout(r,20));return {status:'ok',bytesWritten:window.shortWrite?0:bytes.length};}};
  Object.defineProperty(navigator,'usb',{configurable:true,value:{requestDevice:async()=>window.mockUSB,getDevices:async()=>window.authorizedUSB || [window.mockUSB]}});
 });
 await page.addScriptTag({path:'static/js/thermal-printer.js'});
 await page.evaluate(()=>document.dispatchEvent(new Event('DOMContentLoaded')));
 await page.evaluate(()=>ThermalPrinter.ready);
 await page.evaluate(()=>ThermalPrinter.pairUSB());
 assert.equal(await page.evaluate(()=>ThermalPrinter.isPaired()),true);
 assert.deepEqual(await page.evaluate(()=>Promise.allSettled([ThermalPrinter.printTicket(1),ThermalPrinter.printTicket(1)]).then(rows=>rows.map(r=>r.status))),['fulfilled','rejected']);
 assert.deepEqual(await page.evaluate(()=>window.writes),[{endpoint:2,length:4096},{endpoint:2,length:904}]);
 invalidTicket = true;
 assert.match(await page.evaluate(async()=>{try{await ThermalPrinter.printTicket(1);}catch(e){return e.message;}}),/ticket válido/);
 assert.equal(await page.evaluate(()=>window.writes.length),2,'Una página de login no se envía a la impresora');
 invalidTicket = false;
 assert.match(await page.evaluate(async()=>{window.shortWrite=true;try{await ThermalPrinter.printTicket(1);}catch(e){return e.message;}}),/incompleto/);
 await page.evaluate(()=>{window.shortWrite=false;ThermalPrinter.forget();window.authorizedUSB=[{...window.mockUSB,serialNumber:'other'}];localStorage.setItem('oxidian.thermal.paired',JSON.stringify({transport:'usb',device_id:'1:2:qa'}));});
 await page.evaluate(()=>ThermalPrinter.restoreUSB());
 assert.equal(await page.evaluate(()=>ThermalPrinter.isPaired()),false,'No restaura otra impresora');
 await page.evaluate(()=>{
  window.btWrites=[];
  const characteristic={properties:{writeWithoutResponse:true},async writeValueWithoutResponse(bytes){window.btWrites.push(bytes.length);}};
  const server={getPrimaryService:async()=>({getCharacteristics:async()=>[characteristic]})};
  const dev={id:'ble-qa',name:'QA BLE',gatt:{connected:false,async connect(){this.connected=true;return server;},disconnect(){this.connected=false;}},addEventListener(){}};
  Object.defineProperty(navigator,'bluetooth',{configurable:true,value:{requestDevice:async()=>dev,getDevices:async()=>[dev]}});
 });
 await page.evaluate(()=>ThermalPrinter.pairBT());
 await page.evaluate(()=>ThermalPrinter.printTicket(1));
 assert.equal(await page.evaluate(()=>window.btWrites.reduce((a,b)=>a+b,0)),5000);
 assert.equal(await page.evaluate(()=>Math.max(...window.btWrites)),20);
 await page.evaluate(async()=>{
  ThermalPrinter.forget(); window.serialWrites=[]; window.writerReleased=false;
  const port={writable:null,async open(){this.writable={getWriter:()=>({write:async bytes=>window.serialWrites.push(bytes.length),releaseLock:()=>{window.writerReleased=true;}})};},async close(){this.writable=null;}};
  Object.defineProperty(navigator,'serial',{configurable:true,value:{requestPort:async()=>port}});
  await ThermalPrinter.pairSerial(); await ThermalPrinter.printTicket(1);
 });
 assert.equal(await page.evaluate(()=>window.serialWrites.reduce((a,b)=>a+b,0)),5000);
 assert.equal(await page.evaluate(()=>window.writerReleased),true);
 await page.evaluate(()=>{ThermalPrinter.forget();Object.defineProperty(navigator,'serial',{value:undefined});});
 await page.evaluate(()=>{ThermalPrinter.forget();Object.defineProperty(navigator,'usb',{value:undefined});Object.defineProperty(navigator,'bluetooth',{value:undefined});document.body.insertAdjacentHTML('beforeend','<form class="ticket-print-form" action="/pos/ticket/1/imprimir"><button type="submit">Ticket</button></form>');});
 await page.addScriptTag({path:'static/js/operational-roles.js'});
 await page.locator('.ticket-print-form button').click();
 await page.waitForSelector('#thermal-modal');
 assert.equal(await page.locator('#thermal-modal a').getAttribute('href'),'/pos/ticket/1?autoprint=1&reprint=0');
 assert.equal(await page.locator('[data-thermal-print="usb"], [data-thermal-print="bt"]').count(),0);
 await page.locator('[data-thermal-print="network"]').click();
 await page.waitForFunction(()=>document.getElementById('thermal-status').textContent.includes('Ticket enviado'));
 assert.equal(networkRequests,1,'La alternativa de red usa el endpoint autorizado');
 await page.keyboard.press('Escape');
 assert.equal(await page.locator('#thermal-modal').count(),0);
 // La confirmación guarda el estado antes de imprimir y conserva la conexión.
 const readyPage = await browser.newPage();
 const sequence = [];
 let rejected = true;
 await readyPage.exposeFunction('recordPrint', () => sequence.push('print'));
 await readyPage.route('https://ready.test/**', async route => {
  const req = route.request();
  if (req.method() === 'POST') {
   sequence.push('confirmed');
   return route.fulfill({status: rejected ? 409 : 200, json: {ok:!rejected, print_order_id:1, next_url:'/done'}});
  }
  if (req.url().endsWith('/done')) sequence.push('navigate');
  return route.fulfill({contentType:'text/html', body:'<form action="/preparador/pedidos/1/listo" method="post"><button type="submit">Listo</button></form>'});
 });
 await readyPage.goto('https://ready.test/');
 await readyPage.evaluate(() => {window.ThermalPrinter={isPaired:()=>true,ready:Promise.resolve(),printTicket:async()=>window.recordPrint()};});
 await readyPage.addScriptTag({path:'static/js/operational-roles.js'});
 await readyPage.locator('button').click();
 await readyPage.waitForSelector('[data-print-status]');
 assert.deepEqual(sequence, ['confirmed'], 'Una transición rechazada no imprime');
 sequence.length = 0;
 rejected = false;
 await readyPage.locator('button').click();
 await readyPage.waitForURL('https://ready.test/done');
 assert.deepEqual(sequence, ['confirmed','print','navigate'], 'Imprime antes de perder la conexión al navegar');
 await readyPage.close();
 await page.route('https://ticket.test/**', route=>route.fulfill({contentType:'text/html',body:fs.readFileSync('/tmp/parcerito-role-review/ticket.html')}));
 await page.addInitScript(()=>{window.printCalls=0;window.print=()=>{window.printCalls++;};});
 await page.goto('https://ticket.test/pos/ticket/1');
 await page.locator('[data-print]').click();
 assert.equal(await page.evaluate(()=>window.printCalls),1,'El ticket independiente abre impresión del sistema');
 console.log('OK: USB, BLE, tickets simultáneos, transferencia incompleta, restauración exacta y alternativa del sistema.');
} finally {await browser.close();}
