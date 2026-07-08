import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { chromium } from 'playwright-core';

const ROOT = process.cwd();
const BASE_URL = process.env.VISUAL_BASE_URL || 'http://127.0.0.1:5070';
const OUTPUT_ROOT = process.env.VISUAL_OUTPUT_ROOT
  || path.join(ROOT, 'docs', 'auditoria_roles');
const RUN_ID = new Date().toISOString().replace(/[:.]/g, '-');
const OUTPUT_DIR = path.join(OUTPUT_ROOT, RUN_ID);
const ENV = loadEnv(path.join(ROOT, '.env.cosmos.local'));
const PASSWORD = process.env.VISUAL_PASSWORD || ENV.SEED_PASSWORD;
const POINTS_ENABLED = process.env.VISUAL_POINTS_ENABLED !== '0';
const BROWSER = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE
  || '/home/panzeta/.cache/ms-playwright/chromium-1169/chrome-linux/chrome';

if (!PASSWORD && ![
  'VISUAL_SUPERADMIN_PASSWORD',
  'VISUAL_ADMIN_PASSWORD',
  'VISUAL_COCINA_PASSWORD',
  'VISUAL_PREPARACION_PASSWORD',
  'VISUAL_REPARTIDOR_PASSWORD',
  'VISUAL_PROVEEDOR_PASSWORD',
].every((key) => process.env[key])) {
  throw new Error('Faltan contraseñas para la auditoría visual.');
}
if (!fs.existsSync(BROWSER)) throw new Error(`No se encontró Chromium en ${BROWSER}`);

for (const folder of ['vistas', 'modales', 'formularios', 'flujos']) {
  fs.mkdirSync(path.join(OUTPUT_DIR, folder), { recursive: true });
}

const report = {
  generatedAt: new Date().toISOString(),
  baseUrl: BASE_URL,
  outputDir: OUTPUT_DIR,
  viewport: { width: 393, height: 851 },
  captures: [],
};

const browser = await chromium.launch({
  executablePath: BROWSER,
  headless: true,
  args: ['--no-sandbox', '--disable-dev-shm-usage'],
  proxy: process.env.VISUAL_PROXY ? { server: process.env.VISUAL_PROXY } : undefined,
});

try {
  if (process.env.VISUAL_SKIP_PUBLIC !== '1') {
    const publicPage = await createPage();
    await capturePublic(publicPage);
    await publicPage.context().close();
  }

  await captureAuthenticatedRole(
    'superadmin',
    process.env.VISUAL_SUPERADMIN_EMAIL || ENV.SUPERADMIN_EMAIL || 'carmocream15@gmail.com',
    process.env.VISUAL_SUPERADMIN_PASSWORD || PASSWORD,
    captureSuperadmin,
    process.env.VISUAL_SUPERADMIN_TOTP_SECRET || '',
  );
  await captureAuthenticatedRole(
    'admin',
    process.env.VISUAL_ADMIN_EMAIL || ENV.ADMIN_EMAIL || 'admin@oxidian.com',
    process.env.VISUAL_ADMIN_PASSWORD || PASSWORD,
    (page) => captureRole(page, [
      ['admin-real-dashboard', '/admin/dashboard'],
      ['admin-real-cola', '/admin/cola'],
      ['admin-real-pedidos', '/admin/pedidos'],
    ]),
    process.env.VISUAL_ADMIN_TOTP_SECRET || '',
  );
  await captureAuthenticatedRole(
    'cocina',
    process.env.VISUAL_COCINA_EMAIL || 'cocina@oxidian.com',
    process.env.VISUAL_COCINA_PASSWORD || PASSWORD,
    captureCocina,
    process.env.VISUAL_COCINA_TOTP_SECRET || '',
  );
  await captureAuthenticatedRole(
    'preparacion',
    process.env.VISUAL_PREPARACION_EMAIL || 'preparacion@oxidian.com',
    process.env.VISUAL_PREPARACION_PASSWORD || PASSWORD,
    (page) => captureRole(page, [['preparacion-pedidos', '/preparador/pedidos']]),
  );
  await captureAuthenticatedRole(
    'repartidor',
    process.env.VISUAL_REPARTIDOR_EMAIL || 'repartidor@oxidian.com',
    process.env.VISUAL_REPARTIDOR_PASSWORD || PASSWORD,
    (page) => captureRole(page, [
      ['repartidor-ruta', '/repartidor/ruta'],
      ['repartidor-comisiones', '/repartidor/mis-comisiones'],
    ]),
  );
} finally {
  await browser.close();
}

fs.writeFileSync(
  path.join(OUTPUT_DIR, 'audit.json'),
  JSON.stringify(report, null, 2),
);
fs.writeFileSync(path.join(OUTPUT_DIR, 'README.md'), buildReadme(report));
updateLatestPointer(OUTPUT_ROOT, OUTPUT_DIR);

const failures = report.captures.filter((item) => !item.ok);
const overflows = report.captures.filter((item) => item.horizontalOverflow);
console.log(`Capturas: ${report.captures.length}`);
console.log(`Fallos: ${failures.length}`);
console.log(`Desbordamientos horizontales: ${overflows.length}`);
console.log(`Salida: ${OUTPUT_DIR}`);
if (failures.length) process.exitCode = 1;

async function createPage() {
  const context = await browser.newContext({
    viewport: report.viewport,
    deviceScaleFactor: 1,
    isMobile: true,
    hasTouch: true,
    locale: 'es-ES',
    colorScheme: 'light',
    serviceWorkers: 'allow',
  });
  return context.newPage();
}

async function captureAuthenticatedRole(role, email, password, capture, totpSecret = '') {
  const page = await createPage();
  try {
    await login(page, email, password, totpSecret);
    await capture(page);
  } catch (error) {
    report.captures.push({
      name: `${role}-login`,
      route: '/auth/login',
      folder: 'formularios',
      ok: false,
      error: error.message,
      finalUrl: page.url(),
    });
    console.error(`✗ ${role}-login: ${error.message}`);
  } finally {
    await page.context().close();
  }
}

async function login(page, email, password, totpSecret = '') {
  await page.goto(`${BASE_URL}/auth/login`, { waitUntil: 'domcontentloaded' });
  await page.locator('input[name="email"]').fill(email);
  await page.locator('input[name="password"]').fill(password);
  await Promise.all([
    page.waitForLoadState('domcontentloaded'),
    page.locator('button[type="submit"]').click(),
  ]);
  await settle(page);
  if (page.url().includes('/auth/login/mfa') && totpSecret) {
    await page.locator('input[name="code"]').fill(totp(totpSecret));
    await Promise.all([
      page.waitForLoadState('domcontentloaded'),
      page.locator('button[type="submit"]').click(),
    ]);
    await settle(page);
  }
  if (page.url().includes('/auth/login')) {
    throw new Error(`No se pudo iniciar sesión con ${email}`);
  }
}

function totp(secret) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = '';
  for (const char of secret.replace(/=+$/g, '').toUpperCase()) {
    const index = alphabet.indexOf(char);
    if (index >= 0) bits += index.toString(2).padStart(5, '0');
  }
  const key = Buffer.from((bits.match(/.{8}/g) || []).map((byte) => parseInt(byte, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 1000 / 30)));
  const digest = crypto.createHmac('sha1', key).update(counter).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const code = ((digest.readUInt32BE(offset) & 0x7fffffff) % 1_000_000);
  return String(code).padStart(6, '0');
}

async function capturePublic(page) {
  await snap(page, 'menu-principal', '/', 'vistas');

  await snap(page, 'menu-informacion', '/', 'modales', async () => {
    await page.evaluate(() => {
      const sheet = document.getElementById('ox-info-sheet');
      if (!sheet) throw new Error('No existe el panel de información');
      sheet.hidden = false;
      sheet.classList.add('is-open');
      document.body.classList.add('ox-modal-open');
    });
  }, false);

  await snap(page, 'menu-modal-producto', '/', 'modales', async () => {
    await page.evaluate(() => {
      const entries = Object.entries(EP_DATA || {});
      const first = entries.find(([, value]) => value && value.disponible !== false) || entries[0];
      if (!first) throw new Error('No hay productos para abrir');
      openModal(first[0]);
    });
  }, false);

  await page.goto(`${BASE_URL}/`, { waitUntil: 'domcontentloaded' }).catch((error) => {
    if (!String(error?.message || error).includes('interrupted by another navigation')) {
      throw error;
    }
  });
  await settle(page);
  const productId = await page.evaluate(() => {
    const entries = Object.entries(EP_DATA || {});
    const first = entries.find(([, value]) => (
      value && value.disponible !== false && value.disponible_directo === true
    )) || entries.find(([, value]) => value && value.disponible !== false) || entries[0];
    if (!first) return null;
    openModal(first[0]);
    return first[1]?.disponible_directo === true ? first[0] : null;
  });
  if (productId) {
    const addButton = page.locator('#ep-modal-form button[type="submit"]').first();
    if (await addButton.count()) {
      await Promise.all([
        page.waitForLoadState('domcontentloaded').catch(() => {}),
        addButton.click(),
      ]);
      await settle(page);
    }
  }
  await snap(page, 'carrito-con-producto', '/carrito', 'flujos');
  await snap(page, 'checkout-datos-pedido', '/checkout', 'flujos');
  if (POINTS_ENABLED) await snap(page, 'checkout-puntos-whatsapp', '/checkout', 'flujos', async () => {
    if (!page.url().includes('/checkout')) return;
    const phone = page.locator('#tel_input');
    await phone.fill('699 111 222');
    await phone.dispatchEvent('input');
    await phone.dispatchEvent('blur');
    await page.evaluate(() => document.getElementById('puntos-verificando')?.classList.remove('hidden'));
    await page.locator('#cod_puntos_input').fill('123456');
    await page.evaluate(() => verificarCodigoPuntos());
    await page.waitForSelector('#checkout-rewards-panel:not(.hidden)', { timeout: 5000 });
    const reward = page.locator('input[name="reward_product_choice"][value]:not([value=""])').first();
    if (await reward.count()) await reward.check();
    await page.locator('#puntos-section').scrollIntoViewIfNeeded();
  });
  await snap(page, 'club-puntos', '/club', 'vistas');
  await snap(page, 'whatsapp-cliente', '/whatsapp', 'vistas');
  await snap(page, 'inicio-sesion', '/auth/login', 'formularios');
}

async function captureSuperadmin(page) {
  const views = [
    ['admin-dashboard', '/admin/dashboard'],
    ['admin-cola-operativa', '/admin/cola'],
    ['admin-pedidos', '/admin/pedidos'],
    ['admin-pagos-pendientes', '/admin/pagos-pendientes'],
    ['admin-caja', '/admin/caja'],
    ['admin-pagos-staff', '/admin/pagos-staff'],
    ['admin-stock', '/admin/stock'],
    ['admin-productos', '/admin/productos'],
    ['admin-combo-nuevo', '/admin/combos/nuevo'],
    ['admin-categorias', '/admin/categorias'],
    ['admin-cupones', '/admin/cupones'],
    ['admin-resenas', '/admin/resenas'],
    ['admin-usuarios', '/admin/usuarios'],
    ['admin-whatsapp', '/admin/whatsapp-qr'],
    ['admin-notificaciones', '/admin/notificaciones'],
    ['admin-afiliados', '/admin/afiliados'],
    ['admin-menu-config', '/admin/menu-config'],
    ['admin-analytics', '/admin/analytics'],
    ['marketing-dashboard', '/marketing/dashboard'],
    ...(POINTS_ENABLED ? [['marketing-puntos', '/marketing/puntos']] : []),
    ['marketing-campanas', '/marketing/campanas'],
    ['pos-catalogo', '/pos/'],
    ['pos-historial', '/pos/historial'],
    ['superadmin-dashboard', '/superadmin/dashboard'],
    ['superadmin-chatbot', '/superadmin/chatbot'],
    ['superadmin-config', '/superadmin/config'],
    ['superadmin-administradores', '/superadmin/admins'],
    ['superadmin-zonas', '/superadmin/zonas'],
    ['superadmin-finanzas', '/superadmin/pl'],
    ['superadmin-auditoria', '/superadmin/audit'],
    ['staff-panel', '/staff/'],
    ['staff-inventario', '/staff/inventario'],
  ];
  await captureRole(page, views);

  await snap(page, 'producto-nuevo', '/admin/productos', 'formularios', async () => {
    await reveal(page, '#modal-nuevo');
  }, false);
  await snap(page, 'producto-editar', '/admin/productos', 'formularios', async () => {
    const button = page.locator('button[onclick^="abrirEditar("]:visible').first();
    if (await button.count()) await button.click();
    else await reveal(page, '#modal-editar');
  }, false);
  await snap(page, 'categoria-nueva', '/admin/categorias', 'formularios', async () => {
    await reveal(page, '#form-nueva');
  });
  await snap(page, 'cupon-nuevo', '/admin/cupones', 'formularios', async () => {
    await reveal(page, '#form-nuevo');
  });
  await snap(page, 'usuario-nuevo', '/admin/usuarios', 'formularios', async () => {
    await reveal(page, '#modal-nuevo');
  }, false);
  await snap(page, 'usuario-editar', '/admin/usuarios', 'formularios', async () => {
    const button = page.locator('button[onclick^="abrirEditar("]:visible').first();
    if (await button.count()) await button.click();
    else await reveal(page, '#modal-editar');
  }, false);
  await snap(page, 'afiliado-nuevo', '/admin/afiliados', 'formularios', async () => {
    await reveal(page, '#form-nuevo');
  });
  await snap(page, 'menu-seccion-nueva', '/admin/menu-config', 'formularios', async () => {
    await reveal(page, '#form-nuevo');
  });
  await snap(page, 'campana-nueva', '/marketing/campanas', 'formularios', async () => {
    await reveal(page, '#modal-nueva');
  }, false);
  await snap(page, 'administrador-nuevo', '/superadmin/admins', 'formularios', async () => {
    await reveal(page, '#modal-nuevo');
  }, false);
  await snap(page, 'administrador-editar', '/superadmin/admins', 'formularios', async () => {
    const button = page.locator('button[onclick^="abrirEditar("]:visible').first();
    if (await button.count()) await button.click();
    else await reveal(page, '#modal-editar');
  }, false);
  await snap(page, 'zona-nueva', '/superadmin/zonas', 'formularios', async () => {
    const details = page.locator('#form-crear');
    if (await details.count()) await details.evaluate((element) => { element.open = true; });
  });

  for (const section of ['tienda', 'operacion', 'entregas', 'puntos', 'integraciones', 'avanzado']) {
    await snap(page, `config-${section}`, '/superadmin/config', 'formularios', async () => {
      await page.evaluate((id) => {
        const target = document.getElementById(id);
        if (target?.tagName === 'DETAILS') target.open = true;
        document.querySelectorAll('.cfg-section').forEach((item) => {
          item.classList.toggle('cfg-active', item.id === id);
        });
        target?.scrollIntoView({ block: 'start' });
      }, section);
    }, false);
  }

  await snap(page, 'pos-modal-combo', '/pos/', 'modales', async () => {
    const button = page.locator('.pos-prod-btn:not([disabled])').filter({ hasText: /combo/i }).first();
    if (await button.count()) await button.click();
    if (await page.locator('#modal-combo:visible').count() === 0) {
      await page.locator('#modal-combo').evaluate((element) => { element.style.display = 'flex'; });
    }
  }, false);
  await snap(page, 'pos-modal-efectivo', '/pos/', 'modales', async () => {
    await page.evaluate(() => {
      document.getElementById('ef-total').textContent = '€24.90';
      document.getElementById('ef-entregado').value = '30';
      document.getElementById('ef-cambio').textContent = '€5.10';
      document.getElementById('modal-efectivo').style.display = 'flex';
    });
  }, false);
  await snap(page, 'pos-venta-confirmada', '/pos/', 'modales', async () => {
    await page.evaluate(() => {
      document.getElementById('modal-numero').textContent = 'PEDIDO DE EJEMPLO';
      document.getElementById('modal-total').textContent = '€24.90';
      document.getElementById('modal-puntos').textContent = '+24 puntos al cliente';
      document.getElementById('modal-ok').style.display = 'flex';
    });
  }, false);
}

async function captureCocina(page) {
  await snap(page, 'cocina-pedidos', '/preparador/pedidos', 'vistas');
  await page.setViewportSize({ width: 851, height: 393 });
  await snap(page, 'cocina-pedidos-horizontal', '/preparador/pedidos', 'vistas');
}

async function captureRole(page, views) {
  for (const [name, route] of views) {
    await snap(page, name, route, 'vistas');
  }
}

async function snap(page, name, route, folder, prepare = null, fullPage = true) {
  const errors = [];
  const failedRequests = [];
  const onConsole = (message) => {
    if (message.type() === 'error') errors.push(message.text());
  };
  const onPageError = (error) => errors.push(error.message);
  const onRequestFailed = (request) => {
    const failure = request.failure();
    failedRequests.push(`${request.method()} ${request.url()} ${failure?.errorText || ''}`.trim());
  };
  page.on('console', onConsole);
  page.on('pageerror', onPageError);
  page.on('requestfailed', onRequestFailed);

  const entry = {
    name,
    route,
    folder,
    ok: false,
    status: null,
    finalUrl: null,
    viewport: page.viewportSize(),
    horizontalOverflow: false,
    unexpectedAuthRedirect: false,
    consoleErrors: errors,
    failedRequests,
  };

  try {
    const response = await page.goto(`${BASE_URL}${route}`, {
      waitUntil: 'domcontentloaded',
      timeout: 30000,
    });
    entry.status = response?.status() ?? null;
    await settle(page);
    if (prepare) {
      await prepare();
      await settle(page);
    }
    entry.finalUrl = page.url();
    entry.unexpectedAuthRedirect = isAuthRoute(entry.finalUrl) && !isAuthRoute(route);
    if (entry.unexpectedAuthRedirect) {
      throw new Error(`Redirección inesperada de ${route} a ${new URL(entry.finalUrl).pathname}`);
    }
    const dimensions = await page.evaluate(() => ({
      clientWidth: document.documentElement.clientWidth,
      scrollWidth: document.documentElement.scrollWidth,
    }));
    entry.horizontalOverflow = dimensions.scrollWidth > dimensions.clientWidth + 3;

    if (fullPage) {
      await page.addStyleTag({
        content: `
          .ox-bottom-nav,.ep-float-cart{display:none!important}
          .ox-header,.ox-topbar,.cfg-nav{position:relative!important;top:auto!important}
        `,
      });
    }
    const file = `${slug(name)}.png`;
    const target = path.join(OUTPUT_DIR, folder, file);
    await page.screenshot({ path: target, fullPage, animations: 'disabled' });
    entry.file = path.relative(OUTPUT_DIR, target);
    entry.ok = Boolean(entry.status && entry.status < 400);
  } catch (error) {
    entry.error = error.message;
  } finally {
    page.off('console', onConsole);
    page.off('pageerror', onPageError);
    page.off('requestfailed', onRequestFailed);
    report.captures.push(entry);
    console.log(`${entry.ok ? '✓' : '✗'} ${name}${entry.horizontalOverflow ? ' [overflow]' : ''}`);
  }
}

function isAuthRoute(value) {
  const pathname = value.startsWith('http') ? new URL(value).pathname : value;
  return pathname === '/auth/login'
    || pathname.startsWith('/auth/login/')
    || pathname.includes('/mfa');
}

async function reveal(page, selector) {
  const element = page.locator(selector).first();
  if (!await element.count()) throw new Error(`No existe ${selector}`);
  await element.evaluate((node) => {
    node.classList.remove('hidden');
    if (node.tagName === 'DETAILS') node.open = true;
    node.scrollIntoView({ block: 'start' });
  });
}

async function settle(page) {
  await page.waitForLoadState('domcontentloaded').catch(() => {});
  await page.waitForTimeout(450);
}

function loadEnv(file) {
  if (!fs.existsSync(file)) return {};
  return Object.fromEntries(
    fs.readFileSync(file, 'utf8')
      .split(/\r?\n/)
      .filter((line) => line && !line.trimStart().startsWith('#') && line.includes('='))
      .map((line) => {
        const index = line.indexOf('=');
        return [line.slice(0, index).trim(), line.slice(index + 1).trim().replace(/^['"]|['"]$/g, '')];
      }),
  );
}

function slug(value) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

function buildReadme(data) {
  const grouped = Object.groupBy
    ? Object.groupBy(data.captures, (item) => item.folder)
    : data.captures.reduce((acc, item) => {
      (acc[item.folder] ||= []).push(item);
      return acc;
    }, {});
  const lines = [
    '# Pantallazos actuales de Oxidian',
    '',
    `Generados: ${data.generatedAt}`,
    `Origen: ${data.baseUrl}`,
    `Viewport móvil: ${data.viewport.width} x ${data.viewport.height}`,
    '',
    'Cada captura fue generada navegando la aplicación real. `audit.json` contiene estado HTTP, URL final, viewport, errores de navegador, control de desbordamiento horizontal y redirecciones inesperadas a login/MFA.',
    '',
  ];
  for (const [folder, items] of Object.entries(grouped)) {
    lines.push(`## ${folder[0].toUpperCase()}${folder.slice(1)}`, '');
    for (const item of items) {
      const state = item.ok ? 'OK' : 'ERROR';
      const overflow = item.horizontalOverflow ? ' · desbordamiento horizontal' : '';
      lines.push(`- [${item.name}](${item.file || '#'}) · ${state} · HTTP ${item.status ?? 'N/A'}${overflow}`);
    }
    lines.push('');
  }
  return `${lines.join('\n')}\n`;
}

function updateLatestPointer(root, current) {
  fs.mkdirSync(root, { recursive: true });
  const latest = path.join(root, 'latest');
  try {
    fs.rmSync(latest, { recursive: true, force: true });
    fs.symlinkSync(path.basename(current), latest, 'dir');
  } catch (error) {
    console.warn(`No se pudo actualizar ${latest}: ${error.message}`);
  }
}
