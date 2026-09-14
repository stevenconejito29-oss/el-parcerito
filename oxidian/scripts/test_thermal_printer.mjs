// Dispositivos simulados: nunca imprime papel real.
import assert from 'node:assert/strict';
import {chromium} from 'playwright-core';
const browser=await chromium.launch({executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,headless:true,args:['--no-sandbox']});
try {
 const page=await browser.newPage();
 await page.route('https://printer.test/**',r=>r.request().isNavigationRequest()?r.fulfill({contentType:'text/html',body:'<body class="view-preparador operational-view"></body>'}):r.request().url().includes('/escpos')?r.fulfill({contentType:'application/vnd.escpos',body:Buffer.alloc(5000,42)}):r.fulfill({json:{ok:true,printer:null}}));
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
 await page.evaluate(()=>{ThermalPrinter.forget();Object.defineProperty(navigator,'usb',{value:undefined});Object.defineProperty(navigator,'bluetooth',{value:undefined});document.body.insertAdjacentHTML('beforeend','<form class="ticket-print-form" action="/pos/ticket/1/imprimir"><button type="submit">Ticket</button></form>');});
 await page.addScriptTag({path:'static/js/operational-roles.js'});
 await page.locator('.ticket-print-form button').click();
 await page.waitForSelector('#thermal-modal');
 assert.equal(await page.locator('#thermal-modal a').getAttribute('href'),'/pos/ticket/1?autoprint=1&reprint=0');
 assert.equal(await page.locator('[data-thermal-print="usb"], [data-thermal-print="bt"]').count(),0);
 await page.keyboard.press('Escape');
 assert.equal(await page.locator('#thermal-modal').count(),0);
 console.log('OK: USB, BLE, tickets simultáneos, transferencia incompleta, restauración exacta y alternativa del sistema.');
} finally {await browser.close();}
