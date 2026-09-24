const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const jsQR = require('jsqr');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const drawDocument = require('./document_fixture.cjs');

const root = path.resolve(__dirname, '../../..');
const id = '12345678-1234-4123-8123-123456789abc';
const token = 'solo-token-ficticio-para-pruebas-id01b';
let server, browser, origin, html, cameraHTML;
function handoffHTML(url) {
  const content = url.searchParams.has('prestadores') ? html.replaceAll('LIBRANZA', 'PRESTADORES').replace('data-contexto=""', 'data-contexto="1234"') : html;
  return '<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Captura</title><body style="margin:0;background:#f4f6f8;padding:20px;font-family:system-ui"><form style="max-width:740px;margin:20px auto"><input type="hidden" name="csrfmiddlewaretoken" value="csrf-prueba"><label>Nombre <input name="nombre" value="Nombre diligenciado"></label>' + content + '<input type="file" id="cedula_frontal" required hidden></form></body></html>';
}
before(async () => {
  const python = process.env.DJANGO_PYTHON || (process.platform === 'win32'
    ? path.join(root, 'venv/Scripts/python.exe') : 'python');
  const render = [
    "import os",
    "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aprobado_web.settings')",
    "import django; django.setup()",
    "from django.template.loader import render_to_string",
    "from django.test import RequestFactory",
    "print(render_to_string('gestion_creditos/components/captura_handoff.html', {'producto': 'LIBRANZA', 'request': RequestFactory().get('/libranza/solicitar/')}))",
  ].join('\n');
  html = execFileSync(python, ['-c', render], {cwd: root, encoding: 'utf8',
    env: {...process.env, PYTHONIOENCODING: 'utf-8'}, stdio: ['ignore', 'pipe', 'ignore']});
  cameraHTML = execFileSync(python, ['-c', render.slice(0, render.lastIndexOf('print(')) +
    "print(render_to_string('gestion_creditos/captura_continuacion.html', {'producto': 'LIBRANZA', 'sesion': {'id': '" + id +
    "'}, 'capture_base': '/captura-documental/LIBRANZA/" + id + "/', 'solicitud_id': '', 'csrf_token': 'csrf-prueba'}))"], {cwd: root, encoding: 'utf8',
    env: {...process.env, PYTHONIOENCODING: 'utf-8'}, stdio: ['ignore', 'pipe', 'ignore']});
  server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname === '/camera') {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      let content = url.searchParams.has('prestadores') ? cameraHTML.replaceAll('LIBRANZA', 'PRESTADORES')
        .replace('data-contexto=""', 'data-contexto="1234"') : cameraHTML;
      if (url.searchParams.has('grant')) content = content.replace('data-capture-mobile ', 'data-capture-mobile data-capture-grant ')
        .replace(id + '/"', id + '/movil/"');
      res.end(content);
      return;
    }
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve(root, '.' + url.pathname);
      if (!file.startsWith(path.join(root, 'static') + path.sep) || !fs.existsSync(file)) {
        res.writeHead(404); res.end(); return;
      }
      res.setHeader('Content-Type', file.endsWith('.js') ? 'application/javascript' : file.endsWith('.css') ? 'text/css' : 'image/png');
      res.end(fs.readFileSync(file)); return;
    }
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    res.end(handoffHTML(url));
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = 'http://127.0.0.1:' + server.address().port;
  browser = await chromium.launch({channel: process.env.CAPTURE_BROWSER_CHANNEL || 'chromium', headless: true,
    args: ['--use-fake-device-for-media-stream']});
});
after(async () => {
  if (browser) await browser.close();
  if (server) await new Promise(resolve => server.close(resolve));
});

async function setup(t, opts = {}) {
  const context = await browser.newContext(opts.mobile
    ? {viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true}
    : {viewport: {width: 1280, height: 900}});
  t.after(() => context.close());
  const page = await context.newPage();
  // Deliver the rendered fixture directly, excluding host antivirus HTML injection.
  await page.route(origin + '/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/') await route.fulfill({contentType: 'text/html; charset=utf-8', body: handoffHTML(url)});
    else await route.fallback();
  });
  const logs = [], errors = [], requests = [], allRequests = [];
  page.on('console', message => logs.push(message.text()));
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => allRequests.push(request.url()));
  const state = {value: 'ABIERTA', gets: 0, posts: 0, postStatus: 201, html: false,
    transient: 0, abort: false, hang: false, ...opts};
  await page.addInitScript(({blocked, restored, id}) => {
    if (restored) sessionStorage.setItem('captura:LIBRANZA:', typeof restored === 'string' ? restored : id);
    if (blocked === 'get' || blocked === 'set') {
      Storage.prototype[blocked + 'Item'] = () => { throw new DOMException('bloqueado', 'SecurityError'); };
    }
  }, {blocked: opts.blocked, restored: opts.restored, id});
  await page.route('**/captura-documental/**', async route => {
    const request = route.request();
    requests.push({url: request.url(), method: request.method(), headers: request.headers(), body: request.postData()});
    if (request.method() === 'POST') {
      state.posts++;
      if (state.abort) { await route.abort('failed'); return; }
      if (state.hang) return;
      const producto = request.url().includes('PRESTADORES') ? 'PRESTADORES' : 'LIBRANZA';
      await route.fulfill({status: state.postStatus, contentType: state.html ? 'text/html' : 'application/json',
        body: state.html ? '<h1>Login</h1>' : JSON.stringify(state.postStatus >= 400
          ? {error: 'Detalle tecnico privado ' + token}
          : {id, enlace: '/captura-documental/' + producto + '/' + id + '/movil/' +
            (producto === 'PRESTADORES' ? '?solicitud_id=1234' : '') + '#' + token,
            expira_en: new Date(Date.now() + 600000).toISOString()})});
    } else {
      state.gets++;
      if (state.invalid) { await route.fulfill({status: 400, json: {error: 'Contexto invalido'}}); return; }
      if (state.transient-- > 0) { await route.fulfill({status: 500, body: 'Error'}); return; }
      await route.fulfill({json: {id, estado: state.value, lados: []}});
    }
  });
  await page.goto(origin + (opts.prestadores ? '/?prestadores=1' : '/'));
  await page.waitForFunction(() => document.querySelector('[data-capture-handoff]').dataset.captureReady === 'true');
  const result = page.locator('[data-handoff-result]'), status = page.locator('[data-capture-status]');
  async function create() { await page.locator('[data-crear-enlace]').click(); }
  async function waitState(value) {
    await page.waitForFunction(value => document.querySelector('[data-capture-handoff]').dataset.state === value, value);
  }
  return {page, state, requests, allRequests, result, status, create, waitState, logs, errors};
}

for (const value of ['UTILIZADA', 'EXPIRADA', 'REVOCADA']) {
  test('restaurar ' + value + ' limpia UUID y crea nueva sesion, no regenera la anterior', async t => {
    const ui = await setup(t, {restored: true, value});
    await ui.waitState(value === 'UTILIZADA' ? 'success' : 'expired');
    assert.equal(await ui.page.locator('[data-sesion-documental]').inputValue(), '');
    assert.equal(await ui.page.evaluate(() => sessionStorage.getItem('captura:LIBRANZA:')), null);
    ui.state.value = 'ABIERTA';
    await ui.create(); await ui.waitState('waiting');
    assert.equal(ui.requests.find(r => r.method === 'POST').url, origin + '/captura-documental/LIBRANZA/crear/');
  });
}

test('UUID invalido o contexto ajeno no se conserva en el formulario', async t => {
  const ui = await setup(t, {restored: true, invalid: true});
  await ui.waitState('error');
  assert.equal(await ui.page.locator('[data-sesion-documental]').inputValue(), '');
  assert.equal(await ui.page.evaluate(() => sessionStorage.getItem('captura:LIBRANZA:')), null);
});

test('UUID malformado se descarta sin consultar una ruta arbitraria', async t => {
  const ui = await setup(t, {restored: '../../ajeno'});
  assert.equal(await ui.page.locator('[data-sesion-documental]').inputValue(), '');
  assert.equal(await ui.page.evaluate(() => sessionStorage.getItem('captura:LIBRANZA:')), null);
  assert.equal(ui.state.gets, 0);
});

test('temporizador del enlace consulta servidor y no expira evidencia finalizada', async t => {
  const ui = await setup(t);
  await ui.page.clock.install();
  await ui.create(); await ui.waitState('waiting');
  ui.state.value = 'FINALIZADA';
  await ui.page.clock.runFor(600001); await ui.waitState('success');
  assert.equal(await ui.page.locator('[data-sesion-documental]').inputValue(), id);
  assert.equal(await ui.page.evaluate(() => sessionStorage.getItem('captura:LIBRANZA:')), id);
});

for (const prestadores of [false, true]) {
  test('captura propia conserva formulario y PDF, valida servidor y vuelve a Documentos ' + prestadores, async t => {
    const ui = await setup(t, {mobile: true, prestadores});
    const producto = prestadores ? 'PRESTADORES' : 'LIBRANZA';
    const base = '/captura-documental/' + producto + '/' + id + '/';
    const retorno = prestadores ? '/solicitar/?solicitud_id=1234#step-2' : '/libranza/solicitar/#step-3';
    await ui.page.route('**/captura-documental/**', async route => {
      const req = route.request(), pathname = new URL(req.url()).pathname;
      if (pathname.endsWith('/crear/')) {
        await route.fulfill({json: {id, enlace: base + 'movil/#' + token, enlace_propio: base + '#' + token,
          expira_en: new Date(Date.now() + 600000).toISOString()}});
      } else if (pathname === base) {
        const content = cameraHTML.replaceAll('LIBRANZA', producto)
          .replace('data-capture-mobile', 'data-capture-mobile data-retorno-seguro="' + retorno + '"');
        await route.fulfill({contentType: 'text/html', body: content});
      } else await route.fulfill({json: {id, estado: ui.state.value, lados: [], retorno}});
    });
    await ui.page.evaluate(() => {
      const pdf = document.createElement('input'); pdf.type = 'file'; pdf.id = 'test-contract';
      document.querySelector('form').append(pdf);
      document.addEventListener('captura:finalizada', () => { document.body.dataset.step = 'documentos'; });
    });
    await ui.page.locator('#test-contract').setInputFiles({name: 'contrato.pdf', mimeType: 'application/pdf', buffer: Buffer.from('%PDF-1.4 fixture')});
    await ui.page.locator('[name=nombre]').fill('Formulario conservado');
    await ui.create();
    await ui.page.waitForFunction(() => document.querySelector('[data-capture-dialog]').open);
    const frame = await ui.page.locator('[data-capture-frame]').elementHandle().then(el => el.contentFrame());
    await frame.waitForSelector('[data-canjear]');
    if (process.env.HANDOFF_SCREENSHOTS) await ui.page.screenshot({path: path.join(process.env.HANDOFF_SCREENSHOTS, 'capture-own-' + producto + '.png')});
    // Neither a message from the parent nor a child message alone proves completion.
    await ui.page.evaluate(id => window.postMessage({type: 'captura-finalizada', id}, location.origin), id);
    await frame.evaluate(id => parent.postMessage({type: 'captura-finalizada', id}, location.origin), id);
    await ui.page.waitForTimeout(50);
    assert.equal(await ui.page.locator('[data-capture-dialog]').evaluate(el => el.open), true);
    ui.state.value = 'FINALIZADA';
    // Execute the real child terminal handler, which posts its scoped session identifier.
    await frame.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await ui.page.waitForFunction(() => !document.querySelector('[data-capture-dialog]').open);
    assert.equal(await ui.page.locator('[name=nombre]').inputValue(), 'Formulario conservado');
    assert.equal(await ui.page.locator('#test-contract').evaluate(el => el.files[0].name), 'contrato.pdf');
    assert.equal(await ui.page.locator('body').getAttribute('data-step'), 'documentos');
    assert.equal(await ui.page.locator('[data-sesion-documental]').inputValue(), id);
    assert.match(await ui.status.textContent(), /capturada correctamente/);
    assert.deepEqual(ui.errors, []);
    assert.equal(await ui.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  });
}

for (const blocked of ['get', 'set']) {
  test('storage ' + blocked + ' bloqueado no impide listener, POST ni handoff', async t => {
    const ui = await setup(t, {blocked});
    await ui.create(); await ui.waitState('waiting');
    assert.equal(await ui.result.isVisible(), true);
    assert.equal(ui.state.posts, 1);
    assert.equal(ui.requests.find(r => r.method === 'POST').headers['x-csrftoken'], 'csrf-prueba');
    assert.equal(await ui.page.locator('[name=nombre]').inputValue(), 'Nombre diligenciado');
    assert.deepEqual(ui.errors, []);
  });
}
test('QR decodifica el mismo enlace, sin token en storage, logs ni requests externas', async t => {
  const ui = await setup(t);
  await ui.create(); await ui.waitState('waiting');
  const href = await ui.page.locator('[data-enlace-captura]').getAttribute('href');
  const image = await ui.page.locator('canvas').evaluate(canvas => ({width: canvas.width, height: canvas.height,
    pixels: Array.from(canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data)}));
  assert.equal(jsQR(new Uint8ClampedArray(image.pixels), image.width, image.height).data, href);
  const stored = await ui.page.evaluate(() => JSON.stringify({...sessionStorage, ...localStorage}));
  assert.equal(stored.includes(token), false);
  assert.equal(ui.logs.join().includes(token), false);
  assert.equal(ui.requests.some(r => r.url.includes(token)), false);
  assert.deepEqual(ui.allRequests.filter(url => !url.startsWith(origin + '/') && url !== 'about:blank'), []);
  assert.deepEqual(ui.errors, []);
  if (process.env.HANDOFF_SCREENSHOTS) await ui.page.screenshot({path: path.join(process.env.HANDOFF_SCREENSHOTS, 'handoff-desktop.png'), fullPage: true});
});
for (const postStatus of [400, 403, 429, 500]) {
  test('error ' + postStatus + ' muestra mensaje seguro y permite reintento', async t => {
    const ui = await setup(t, {postStatus});
    await ui.create(); await ui.waitState('error');
    assert.equal((await ui.status.textContent()).includes(token), false);
    assert.equal(await ui.result.isVisible(), false);
    assert.equal(await ui.page.locator('[data-crear-enlace]').isEnabled(), true);
    ui.state.postStatus = 201;
    await ui.create(); await ui.waitState('waiting');
    assert.equal(await ui.result.isVisible(), true);
  });
}
test('HTML inesperado/login no expone error tecnico', async t => {
  const ui = await setup(t, {html: true});
  await ui.create(); await ui.waitState('error');
  assert.match(await ui.status.textContent(), /respuesta esperada/);
  assert.equal(await ui.result.isVisible(), false);
});
test('fallo de red permite reintento sin POST automatico', async t => {
  const ui = await setup(t, {abort: true});
  await ui.create(); await ui.waitState('error');
  assert.equal(ui.state.posts, 1);
  ui.state.abort = false;
  await ui.create(); await ui.waitState('waiting');
});
test('timeout permite reintentar sin dejar el boton deshabilitado', async t => {
  const ui = await setup(t, {hang: true});
  await ui.page.clock.install();
  await ui.create();
  await ui.page.clock.runFor(15001); await ui.waitState('error');
  assert.match(await ui.status.textContent(), /tard\u00f3 demasiado/);
  assert.equal(await ui.page.locator('[data-crear-enlace]').isEnabled(), true);
});
for (const value of ['EXPIRADA', 'REVOCADA', 'FINALIZADA', 'UTILIZADA']) {
  test('estado ' + value + ' detiene polling y limpia enlace', async t => {
    const ui = await setup(t, {value});
    await ui.page.clock.install();
    await ui.create(); await ui.waitState(['EXPIRADA', 'REVOCADA'].includes(value) ? 'expired' : 'success');
    assert.equal(await ui.result.isVisible(), false);
    assert.equal(await ui.page.locator('[data-enlace-captura]').getAttribute('href'), null);
    const gets = ui.state.gets;
    await ui.page.clock.runFor(20000);
    assert.equal(ui.state.gets, gets);
    if (value === 'FINALIZADA') {
      assert.equal(await ui.page.locator('#cedula_frontal').getAttribute('required'), null);
      assert.equal(await ui.page.locator('[data-crear-enlace]').isVisible(), false);
    }
    if (value === 'EXPIRADA') {
      ui.state.value = 'ABIERTA';
      await ui.create(); await ui.waitState('waiting');
      assert.equal(ui.requests.filter(r => r.method === 'POST').every(r => r.url.endsWith('/crear/')), true);
    }
  });
}
test('recarga UUID no reconstruye token; regeneracion requiere click explicito', async t => {
  const ui = await setup(t, {restored: true});
  await ui.waitState('waiting');
  assert.equal(ui.state.posts, 0);
  assert.equal(await ui.result.isVisible(), false);
  assert.equal(await ui.page.locator('[data-enlace-captura]').getAttribute('href'), null);
  await ui.create(); await ui.waitState('waiting');
  assert.equal(ui.requests.find(r => r.method === 'POST').url, origin + '/captura-documental/LIBRANZA/' + id + '/regenerar/');
  assert.equal(await ui.result.isVisible(), true);
});
test('polling recupera error transitorio y permite consulta manual', async t => {
  const ui = await setup(t, {transient: 1});
  await ui.page.clock.install();
  await ui.create(); await ui.waitState('error');
  await ui.page.clock.runFor(5000); await ui.waitState('waiting');
  assert.equal(ui.state.gets, 2);
  ui.state.transient = 3;
  await ui.page.clock.runFor(5000); await ui.waitState('error');
  ui.state.transient = 0;
  await ui.page.locator('[data-reintentar-estado]').click(); await ui.waitState('waiting');
  assert.equal(ui.state.posts, 1);
});
test('movil y Prestadores abren directamente sin obligar a escanear QR', async t => {
  const ui = await setup(t, {mobile: true, prestadores: true});
  await ui.create(); await ui.waitState('waiting');
  assert.equal(await ui.page.locator('[data-qr-block]').isVisible(), false);
  assert.equal(await ui.page.locator('[data-enlace-captura]').isVisible(), true);
  assert.match(ui.requests[0].body, /1234/);
  assert.equal(await ui.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  if (process.env.HANDOFF_SCREENSHOTS) await ui.page.screenshot({path: path.join(process.env.HANDOFF_SCREENSHOTS, 'handoff-mobile.png'), fullPage: true});
});
test('QR no disponible conserva enlace y copiar; doble clic no duplica POST', async t => {
  const ui = await setup(t);
  await ui.page.evaluate(() => { window.qrcode = undefined; });
  await ui.page.locator('[data-crear-enlace]').evaluate(button => { button.click(); button.click(); });
  await ui.waitState('waiting');
  assert.equal(ui.state.posts, 1);
  assert.equal(await ui.result.isVisible(), true);
  assert.equal(await ui.page.locator('canvas').isVisible(), false);
  assert.equal(await ui.page.locator('[data-enlace-captura]').isVisible(), false);
  assert.equal(await ui.page.locator('[data-copiar-enlace]').isVisible(), true);
});
test('localStorage inaccesible o borrador antiguo no altera UUID actual de Libranza', async t => {
  const ui = await setup(t);
  const template = fs.readFileSync(path.join(root, 'templates/gestion_creditos/solicitud_libranza.html'), 'utf8');
  const functions = template.slice(template.indexOf('    function saveFormData()'), template.indexOf('    // Cargar datos guardados'));
  await ui.page.locator('form').evaluate(form => { form.id = 'formularioLibranza'; });
  await ui.page.addScriptTag({content: functions});
  await ui.create(); await ui.waitState('waiting');
  await ui.page.evaluate(() => {
    localStorage.setItem('formularioLibranzaData', JSON.stringify({sesion_documental_id: 'uuid-antiguo'}));
    loadFormData(); saveFormData();
  });
  assert.equal(await ui.page.locator('[data-sesion-documental]').inputValue(), id);
  assert.equal(await ui.page.evaluate(() => localStorage.getItem('formularioLibranzaData').includes('sesion_documental_id')), false);
  await ui.page.evaluate(() => {
    for (const method of ['getItem', 'setItem', 'removeItem']) Storage.prototype[method] = () => { throw new DOMException('bloqueado', 'SecurityError'); };
    loadFormData(); saveFormData();
  });
  assert.deepEqual(ui.errors, []);
  assert.equal(await ui.page.locator('[name=nombre]').inputValue(), 'Nombre diligenciado');
});
for (const width of [320, 768]) {
  test('sin overflow en viewport ' + width, async t => {
    const ui = await setup(t);
    await ui.page.setViewportSize({width, height: 1000});
    await ui.create(); await ui.waitState('waiting');
    assert.equal(await ui.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    for (const selector of ['[data-enlace-captura]', '[data-copiar-enlace]', '[data-capture-status]']) {
      if (!(await ui.page.locator(selector).isVisible())) continue;
      assert.equal(await ui.page.locator(selector).evaluate(element => element.scrollWidth <= element.clientWidth), true);
    }
    if (process.env.HANDOFF_SCREENSHOTS) await ui.page.screenshot({path: path.join(process.env.HANDOFF_SCREENSHOTS, 'handoff-' + width + '.png'), fullPage: true});
  });
}

async function cameraSetup(t, opts = {}) {
  const context = await browser.newContext({viewport: {width: opts.width || 390, height: opts.height || 844},
    permissions: ['camera'], ...(opts.touch ? {isMobile: true, hasTouch: true} : {})});
  t.after(() => context.close());
  const page = await context.newPage(), requests = [], errors = [], logs = [];
  await page.addInitScript({content: `window.drawDocumentFixture = ${drawDocument.toString()};`});
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => logs.push(message.text()));
  await page.addInitScript(({failure, delayed, nativeMode, photoWidth = 3200, photoHeight = 2000, caps, constraintFailure, largeBytes, portrait, qualityScene}) => {
    window.cameraStats = {calls: [], tracks: [], stops: 0, constraints: [], encodes: [], nativeCalls: 0};
    document.addEventListener('DOMContentLoaded', () => {
      // Existing camera tests isolate acquisition; quality tests below run the real checker.
      if (!qualityScene) window.DocumentCaptureQuality = {analyze: async () => ({decision: 'pass'})};
      else {
        const analyze = DocumentCaptureQuality.analyze;
        window.DocumentCaptureQuality = {analyze: async (...args) => {
          const result = await analyze(...args); cameraStats.quality = result; return result;
        }};
      }
    });
    const encode = HTMLCanvasElement.prototype.toBlob;
    HTMLCanvasElement.prototype.toBlob = function(callback, ...args) {
      cameraStats.encodes.push([this.width, this.height, ...args]);
      return encode.call(this, callback, ...args);
    };
    window.ImageCapture = nativeMode ? class {
      async takePhoto() {
        cameraStats.nativeCalls++;
        if (nativeMode === 'fail') throw new Error('PRIVATE-TECHNICAL-ERROR');
        if (nativeMode === 'delayed') await new Promise(resolve => { window.allowPhoto = resolve; });
        const canvas = document.createElement('canvas'); canvas.width = photoWidth; canvas.height = photoHeight;
        const ctx = canvas.getContext('2d'); ctx.fillStyle = '#08a4a4'; ctx.fillRect(0, 0, canvas.width, canvas.height);
        let blob = await new Promise(resolve => encode.call(canvas, resolve, 'image/jpeg', .96));
        canvas.width = canvas.height = 0;
        if (largeBytes) blob = new Blob([blob, new Uint8Array(8 * 1024 * 1024)], {type: 'image/jpeg'});
        cameraStats.nativeSize = blob.size;
        return blob;
      }
    } : undefined;
    if (failure === 'unsupported') {
      Object.defineProperty(navigator, 'mediaDevices', {value: undefined}); return;
    }
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async constraints => {
      cameraStats.calls.push(constraints);
      if (failure) throw new DOMException('PRIVATE-TECHNICAL-ERROR', failure);
      if (delayed) await new Promise(resolve => { window.allowCamera = resolve; });
      let stream;
      try {
        if (portrait || qualityScene) {
          const canvas = document.createElement('canvas'); canvas.width = 1080; canvas.height = 1920;
          const ctx = canvas.getContext('2d');
          window.syntheticTimer = setInterval(() => {
            if (qualityScene) { window.drawDocumentFixture(canvas, qualityScene); return; }
            ctx.fillStyle = '#667578'; ctx.fillRect(0, 0, canvas.width, canvas.height);
            ctx.fillStyle = '#fff'; ctx.fillRect(100, 650, 880, 555);
            ctx.fillStyle = '#147e85'; ctx.fillRect(160, 720, 200, 250);
            ctx.fillStyle = '#243b41'; ctx.font = '38px sans-serif'; ctx.fillText('DOCUMENTO DE PRUEBA', 390, 770);
            ctx.fillText('CAMARA SINTETICA', 390, 835);
          }, 50);
          stream = canvas.captureStream(20);
        } else stream = await original(constraints);
      }
      catch (error) { cameraStats.failure = error.name; throw error; }
      for (const track of stream.getTracks()) {
        cameraStats.tracks.push(track);
        const stop = track.stop.bind(track);
        track.stop = () => { cameraStats.stops++; stop(); };
        if (caps) {
          const settings = track.getSettings.bind(track);
          let zoom = caps.zoom ? caps.zoom.min : undefined;
          track.getSettings = () => ({...settings(), zoom});
          track.getCapabilities = () => caps;
          track.applyConstraints = async value => {
            cameraStats.constraints.push(value);
            if (constraintFailure) throw new Error('PRIVATE-TECHNICAL-ERROR');
            if (value.advanced[0].zoom) zoom = value.advanced[0].zoom;
          };
        }
      }
      return stream;
    };
  }, opts);
  const state = {estado: 'ABIERTA', lados: [], uploadFail: false};
  await page.route('**/captura-documental/**', async route => {
    const request = route.request(), name = new URL(request.url()).pathname.split('/').filter(Boolean).at(-1);
    requests.push({name, method: request.method(), url: request.url(), body: request.postDataBuffer(), headers: request.headers()});
    if (state.grantInvalid) { await route.fulfill({status: 403, json: {code: 'CAPTURE_GRANT_INVALID'}}); return; }
    if (request.method() === 'POST') {
      if (name === 'canjear') state.estado = 'CANJEADA';
      if (['FRONTAL', 'TRASERA'].includes(name.toUpperCase())) {
        if (state.uploadFail) { await route.fulfill({status: 500, body: 'PRIVATE-TECHNICAL-ERROR'}); return; }
        state.lados = [...new Set([...state.lados, name.toUpperCase()])];
      }
      if (name === 'finalizar') state.estado = 'FINALIZADA';
    }
    await route.fulfill({json: {id, estado: state.estado, lados: state.lados}});
  });
  await page.goto(origin + '/camera?' + new URLSearchParams({...(opts.prestadores ? {prestadores: '1'} : {}), ...(opts.grant ? {grant: '1'} : {})}) + '#' + token);
  const waitStep = async value => {
    try { await page.waitForFunction(value => document.querySelector('[data-capture-mobile]').dataset.state === value, value, {timeout: 5000}); }
    catch (error) {
      assert.fail(JSON.stringify(await page.evaluate(() => ({state: document.querySelector('[data-capture-mobile]').dataset.state,
        message: document.querySelector('[data-capture-status]').textContent, camera: cameraStats}))) + error.message);
    }
  };
  await waitStep('ready');
  async function openCamera() {
    await page.locator('[data-canjear]').click(); await waitStep('live');
    await page.waitForFunction(() => !document.querySelector('[data-tomar-foto]').disabled);
  }
  async function photo() { await page.locator('[data-tomar-foto]').click(); await waitStep('preview'); }
  async function accept() { await page.locator('[data-usar-foto]').click(); }
  async function stopped() {
    await page.waitForFunction(() => cameraStats.tracks.every(track => track.readyState === 'ended'));
    assert.equal(await page.locator('video').evaluate(video => video.srcObject === null), true);
  }
  return {page, requests, state, errors, logs, waitStep, openCamera, photo, accept, stopped};
}

test('desktop dirige al celular: QR y copiar, sin CTA Abrir captura', async t => {
  const ui = await setup(t); await ui.create(); await ui.waitState('waiting');
  assert.equal(await ui.page.locator('[data-enlace-captura]').isVisible(), false);
  assert.equal(await ui.page.locator('[data-qr-block]').isVisible(), true);
  assert.equal(await ui.page.locator('[data-copiar-enlace]').isVisible(), true);
  assert.match(await ui.status.textContent(), /Esperando captura desde tu celular/);
});

test('handoff delegado finaliza en pantalla publica sin retorno privado', async t => {
  const ui = await cameraSetup(t, {grant: true});
  await ui.openCamera(); await ui.photo(); await ui.accept(); await ui.waitStep('ready');
  await ui.openCamera(); await ui.photo(); await ui.accept(); await ui.waitStep('finish');
  await ui.page.locator('[data-finalizar]').click(); await ui.waitStep('done');
  assert.match(await ui.page.locator('[data-capture-status]').textContent(), /Captura completada.*continuar en tu computador/);
  assert.equal(new URL(ui.page.url()).pathname, '/camera');
  assert.equal(await ui.page.locator('[data-capture-mobile]').getAttribute('data-retorno-seguro'), null);
  assert.equal(await ui.page.evaluate(() => JSON.stringify({...sessionStorage, ...localStorage})), '{}');
  await ui.stopped();
});

async function frameGeometry(page) {
  await page.waitForFunction(() => !document.querySelector('[data-camera-guide]').hidden);
  return page.evaluate(() => {
    const video = document.querySelector('video'), box = video.getBoundingClientRect();
    const frame = document.querySelector('[data-camera-guide]').getBoundingClientRect();
    const scale = Math.min(box.width / video.videoWidth, box.height / video.videoHeight);
    return {frame: {x: frame.x, y: frame.y, width: frame.width, height: frame.height},
      visible: {x: box.x + (box.width - video.videoWidth * scale) / 2, y: box.y + (box.height - video.videoHeight * scale) / 2,
        width: video.videoWidth * scale, height: video.videoHeight * scale}};
  });
}
function assertFrame({frame: f, visible: v}) {
  assert.ok(Math.abs(f.width / f.height - 85.60 / 53.98) < .005);
  assert.ok(f.x >= v.x - 1 && f.y >= v.y - 1 && f.x + f.width <= v.x + v.width + 1 && f.y + f.height <= v.y + v.height + 1);
  assert.ok(Math.abs(f.width - Math.min(v.width * .82, v.height * .72 * (85.60 / 53.98))) < 1);
}
test('visor dedicado y marco dentro del video real; rotacion y stream vertical', async t => {
  const ui = await cameraSetup(t); await ui.openCamera();
  assert.equal(await ui.page.locator('.camera-immersive').count(), 1);
  assert.equal(await ui.page.locator('.camera-intro').isVisible(), false);
  assertFrame(await frameGeometry(ui.page));
  await ui.page.evaluate(() => {
    const video = document.querySelector('video');
    Object.defineProperty(video, 'videoWidth', {value: 1080}); Object.defineProperty(video, 'videoHeight', {value: 1920});
    video.dispatchEvent(new Event('resize'));
  });
  assertFrame(await frameGeometry(ui.page));
  await ui.page.setViewportSize({width: 844, height: 390});
  await ui.page.evaluate(() => window.dispatchEvent(new Event('orientationchange')));
  await ui.page.waitForTimeout(80);
  assert.equal(await ui.page.locator('[data-rotate]').isVisible(), true);
  await ui.stopped();
  await ui.page.setViewportSize({width: 390, height: 844}); await ui.waitStep('live');
  assertFrame(await frameGeometry(ui.page));
});
for (const nativeMode of [undefined, 'fail', 'success']) {
  test('captura intrinseca ImageCapture/fallback: ' + (nativeMode || 'ausente'), async t => {
    const ui = await cameraSetup(t, {nativeMode}); await ui.openCamera();
    const dimensions = await ui.page.locator('video').evaluate(v => [v.videoWidth, v.videoHeight]);
    await ui.photo(); await ui.stopped();
    const actual = await ui.page.locator('[data-camera-preview]').evaluate(img => [img.naturalWidth, img.naturalHeight]);
    assert.deepEqual(actual, nativeMode === 'success' ? [3200, 2000] : dimensions);
    assert.equal(await ui.page.evaluate(() => cameraStats.nativeCalls), nativeMode ? 1 : 0);
    assert.equal(await ui.page.evaluate(() => cameraStats.encodes.length), nativeMode === 'success' ? 0 : 1);
  });
}
for (const options of [{photoWidth: 5100, photoHeight: 4000}, {largeBytes: true}]) {
  test('normalizacion solo por limites backend ' + JSON.stringify(options), async t => {
    const ui = await cameraSetup(t, {nativeMode: 'success', ...options}); await ui.openCamera(); await ui.photo();
    const photo = await ui.page.locator('[data-camera-preview]').evaluate(async img => ({w: img.naturalWidth, h: img.naturalHeight, size: (await (await fetch(img.src)).blob()).size}));
    assert.ok(photo.w * photo.h <= 20000000 && photo.size <= 8 * 1024 * 1024 && photo.w > 2048);
    assert.ok(await ui.page.evaluate(() => cameraStats.encodes.length > 0));
    await ui.stopped();
  });
}
test('zoom real y foco continuo solo con capacidades; sin escalado CSS', async t => {
  const ui = await cameraSetup(t, {caps: {zoom: {min: 1, max: 3, step: .5}, focusMode: ['continuous']}});
  await ui.openCamera();
  assert.equal(await ui.page.locator('[data-zoom-control]').isVisible(), true);
  await ui.page.locator('[data-camera-zoom]').evaluate(el => { el.value = 2.5; el.dispatchEvent(new Event('change')); });
  await ui.page.waitForFunction(() => cameraStats.constraints.some(c => c.advanced[0].zoom === 2.5));
  const calls = await ui.page.evaluate(() => cameraStats.constraints);
  assert.ok(calls.some(c => c.advanced[0].focusMode === 'continuous'));
  assert.ok(calls.every(c => !c.advanced[0].zoom || (c.advanced[0].zoom >= 1 && c.advanced[0].zoom <= 2.5)));
  assert.equal(await ui.page.locator('video').evaluate(v => getComputedStyle(v).transform), 'none');
});
test('sin zoom/foco no se muestran controles ni se aplican constraints', async t => {
  const ui = await cameraSetup(t, {caps: {focusMode: ['manual']}}); await ui.openCamera();
  assert.equal(await ui.page.locator('[data-zoom-control]').isVisible(), false);
  assert.deepEqual(await ui.page.evaluate(() => cameraStats.constraints), []);
  await ui.photo();
});
test('fallo de zoom/foco es opcional y permite capturar', async t => {
  const ui = await cameraSetup(t, {caps: {zoom: {min: 1, max: 2, step: .1}, focusMode: ['continuous']}, constraintFailure: true});
  await ui.openCamera();
  await ui.page.locator('[data-camera-zoom]').evaluate(el => { el.value = 1.5; el.dispatchEvent(new Event('change')); });
  await ui.page.waitForFunction(() => document.querySelector('[data-zoom-control]').hidden);
  await ui.photo(); assert.deepEqual(ui.errors, []);
});
test('ImageCapture tardio tras salir no restaura preview ni deja tracks', async t => {
  const ui = await cameraSetup(t, {nativeMode: 'delayed'}); await ui.openCamera();
  await ui.page.locator('[data-tomar-foto]').click();
  await ui.page.waitForFunction(() => !!window.allowPhoto);
  await ui.page.locator('[data-salir]').click();
  await ui.page.evaluate(() => window.allowPhoto());
  await ui.page.waitForFunction(() => !document.querySelector('[data-canjear]').disabled);
  await ui.waitStep('ready'); await ui.stopped();
  assert.equal(await ui.page.locator('[data-camera-preview]').isVisible(), false);
});
test('fallo canvas recuperable sin galeria', async t => {
  const ui = await cameraSetup(t); await ui.openCamera();
  await ui.page.evaluate(() => { window.originalEncode = HTMLCanvasElement.prototype.toBlob; HTMLCanvasElement.prototype.toBlob = callback => callback(null); });
  await ui.page.locator('[data-tomar-foto]').click(); await ui.waitStep('ready'); await ui.stopped();
  assert.equal(await ui.page.locator('input[type=file]').count(), 0);
  await ui.page.evaluate(() => { HTMLCanvasElement.prototype.toBlob = originalEncode; });
  await ui.openCamera(); await ui.photo();
});

test('CaptureGrant 403 durante visor termina stream y no pide login', async t => {
  const ui = await cameraSetup(t, {grant: true}); await ui.openCamera();
  ui.state.grantInvalid = true;
  await ui.page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await ui.waitStep('error'); await ui.stopped();
  assert.match(await ui.page.locator('[data-capture-status]').textContent(), /expir|disponible/);
  assert.equal(await ui.page.locator('[data-canjear]').isVisible(), false);
});

for (const viewport of [{width: 390, height: 844}, {width: 360, height: 640}]) {
  test('stream vertical mobile safe-area y controles ' + JSON.stringify(viewport), async t => {
    const ui = await cameraSetup(t, {...viewport, portrait: true, touch: true}); await ui.openCamera();
    assertFrame(await frameGeometry(ui.page));
    const colors = await ui.page.locator('video').evaluate(video => {
      const c = document.createElement('canvas'); c.width = 16; c.height = 16;
      const ctx = c.getContext('2d'); ctx.drawImage(video, 0, 0, 16, 16);
      return new Set(ctx.getImageData(0, 0, 16, 16).data).size;
    });
    assert.ok(colors > 5);
    for (const step of ['live', 'preview']) {
      if (step === 'preview') await ui.photo();
      assert.ok(await ui.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight + 1));
      for (const selector of step === 'live' ? ['[data-salir]', '[data-tomar-foto]'] : ['[data-repetir]', '[data-usar-foto]']) {
        assert.ok(await ui.page.locator(selector).evaluate(el => { const r = el.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight && r.left >= 0 && r.right <= innerWidth; }));
      }
      if (process.env.HANDOFF_SCREENSHOTS) await ui.page.screenshot({path: path.join(process.env.HANDOFF_SCREENSHOTS, `portrait-${viewport.width}-${step}.png`)});
    }
  });
}
test('cache busting consistente entre handoff y camara', () => {
  for (const content of [html, cameraHTML]) {
    assert.match(content, /captura_documental\.js\?v=p03p02-1/);
    assert.match(content, /captura_documental\.css\?v=p03p02-1/);
  }
  assert.match(html, /qrcode-generator-1\.4\.4\.js/);
});

for (const [qualityScene, reason] of [['sharp', null], ['white-card', null], ['dim', null], ['blur', 'blur'], ['dark', 'dark'], ['bright', 'bright'],
  ['wall', 'missing'], ['keyboard', 'missing'], ['blank', 'missing'], ['small', 'small'], ['outside', 'missing'], ['offcenter', 'framing']]) {
  test('quality gate local: ' + qualityScene, async t => {
    const ui = await cameraSetup(t, {qualityScene}); await ui.openCamera(); await ui.photo();
    const result = await ui.page.evaluate(() => cameraStats.quality);
    t.diagnostic(JSON.stringify({scene: qualityScene, decision: result.decision, reasons: result.reasons, ms: Math.round(result.elapsedMs)}));
    assert.equal(result.decision, reason ? 'reject' : 'pass');
    if (reason) {
      assert.ok(result.reasons.includes(reason));
      assert.equal(await ui.page.locator('[data-usar-foto]').isEnabled(), false);
      await ui.page.locator('[data-usar-foto]').evaluate(el => el.dispatchEvent(new Event('click')));
      assert.equal(ui.requests.some(r => r.name === 'FRONTAL'), false);
    } else { await ui.accept(); await ui.waitStep('ready'); }
    assert.equal(ui.logs.join().includes(token), false);
    assert.deepEqual(ui.errors, []);
    if (process.env.HANDOFF_SCREENSHOTS) await ui.page.screenshot({path: path.join(process.env.HANDOFF_SCREENSHOTS, `quality-${qualityScene}.png`)});
  });
}
test('quality gate falla tecnicamente: revision manual explicita y sin bloqueo total', async t => {
  const ui = await cameraSetup(t, {qualityScene: 'sharp'}); await ui.openCamera();
  await ui.page.evaluate(() => { window.jsfeat = undefined; });
  await ui.photo();
  assert.equal(await ui.page.locator('[data-usar-foto]').isEnabled(), false);
  assert.equal(await ui.page.locator('[data-manual-review]').isVisible(), true);
  await ui.page.locator('[data-confirm-quality]').check();
  await ui.accept(); await ui.waitStep('ready');
  assert.deepEqual(ui.errors, []);
});
for (const prestadores of [false, true]) {
  test('guias por cara y confirmacion centrada con quality real ' + prestadores, async t => {
    const ui = await cameraSetup(t, {qualityScene: 'sharp', prestadores});
    assert.equal(await ui.page.locator('[data-example-side]').getAttribute('data-example-side'), 'FRONTAL');
    assert.equal(await ui.page.locator('[data-camera-instructions]').isVisible(), true);
    if (process.env.HANDOFF_SCREENSHOTS) await ui.page.screenshot({path: path.join(process.env.HANDOFF_SCREENSHOTS, 'guide-front.png')});
    await ui.openCamera(); await ui.photo(); await ui.accept(); await ui.waitStep('ready');
    assert.equal(await ui.page.locator('[data-example-side]').getAttribute('data-example-side'), 'TRASERA');
    if (process.env.HANDOFF_SCREENSHOTS) await ui.page.screenshot({path: path.join(process.env.HANDOFF_SCREENSHOTS, 'guide-back.png')});
    await ui.openCamera(); await ui.photo(); await ui.accept(); await ui.waitStep('finish');
    const center = await ui.page.locator('[data-mobile-controls]').evaluate(el => { const r = el.getBoundingClientRect(); return {y: r.y + r.height / 2, h: innerHeight}; });
    assert.ok(center.y > center.h * .35 && center.y < center.h * .8);
    await ui.page.locator('[data-finalizar]').click(); await ui.waitStep('done');
    assert.equal(await ui.page.locator('input[type=file]').count(), 0);
    if (process.env.HANDOFF_SCREENSHOTS) await ui.page.screenshot({path: path.join(process.env.HANDOFF_SCREENSHOTS, 'quality-done.png')});
  });
}
test('zoom acotado aunque hardware reporte 100x y debounce de input', async t => {
  const ui = await cameraSetup(t, {caps: {zoom: {min: 1, max: 100, step: .1}}}); await ui.openCamera();
  assert.equal(await ui.page.locator('[data-camera-zoom]').getAttribute('max'), '2.5');
  await ui.page.locator('[data-camera-zoom]').evaluate(el => {
    for (const value of [1.2, 1.5, 2, 2.5]) { el.value = value; el.dispatchEvent(new Event('input')); }
  });
  await ui.page.waitForFunction(() => cameraStats.constraints.length > 0);
  assert.equal(await ui.page.evaluate(() => cameraStats.constraints.length), 1);
  assert.equal(await ui.page.locator('[data-zoom-value]').textContent(), '2.5x');
});

for (const prestadores of [false, true]) {
  test('camara secuencial real (dispositivo sintetico) ' + (prestadores ? 'PRESTADORES' : 'LIBRANZA'), async t => {
    const ui = await cameraSetup(t, {prestadores, touch: prestadores});
    assert.equal(await ui.page.locator('input[type=file]').count(), 0);
    assert.equal(await ui.page.evaluate(() => location.hash), '');
    await ui.openCamera();
    assert.equal(await ui.page.evaluate(() => cameraStats.calls[0].video.facingMode.ideal), 'environment');
    assert.equal(await ui.page.evaluate(() => cameraStats.calls[0].audio), false);
    if (process.env.HANDOFF_SCREENSHOTS) await ui.page.screenshot({path: path.join(process.env.HANDOFF_SCREENSHOTS, 'camera-live-' + prestadores + '.png'), fullPage: true});
    await ui.photo(); await ui.stopped();
    assert.equal(await ui.page.locator('[data-camera-preview]').evaluate(img => img.naturalWidth > 0), true);
    const firstURL = await ui.page.locator('[data-camera-preview]').getAttribute('src');
    await ui.page.locator('[data-repetir]').click(); await ui.waitStep('live');
    await ui.page.waitForFunction(() => !document.querySelector('[data-tomar-foto]').disabled);
    assert.equal(await ui.page.evaluate(async url => { try { await fetch(url); return true; } catch (_) { return false; } }, firstURL), false);
    await ui.photo(); await ui.accept(); await ui.waitStep('ready');
    assert.match(await ui.page.locator('[data-camera-step]').textContent(), /Posterior/);
    await ui.openCamera(); await ui.photo(); await ui.accept(); await ui.waitStep('finish');
    await ui.page.locator('[data-finalizar]').click(); await ui.waitStep('done'); await ui.stopped();
    const posts = ui.requests.filter(r => r.method === 'POST');
    assert.deepEqual(posts.map(r => r.name), ['canjear', 'FRONTAL', 'TRASERA', 'finalizar']);
    for (const post of posts.filter(r => ['FRONTAL', 'TRASERA'].includes(r.name))) {
      assert.match(post.body.toString(), /name="archivo"; filename="captura.jpg"/);
      assert.match(post.body.toString(), /Content-Type: image\/jpeg/);
      assert.ok(post.body.includes(Buffer.from([0xff, 0xd8, 0xff])));
      assert.equal(post.headers['x-csrftoken'], 'csrf-prueba');
      assert.ok(post.url.includes(prestadores ? '/PRESTADORES/' : '/LIBRANZA/'));
      if (prestadores) assert.match(post.body.toString(), /1234/);
    }
    assert.equal(await ui.page.evaluate(() => JSON.stringify({...sessionStorage, ...localStorage})), '{}');
    assert.equal(ui.logs.join().includes(token), false);
    assert.equal(ui.requests.some(r => r.url.includes(token)), false);
    assert.deepEqual(ui.errors, []);
  });
}
for (const [failure, message] of [
  ['NotAllowedError', /Permite el acceso/], ['NotFoundError', /No encontramos/],
  ['NotReadableError', /ocupada/], ['AbortError', /No pudimos iniciar/], ['unsupported', /compatible/],
]) {
  test('camara: ' + failure + ' no ofrece galeria ni filtra error tecnico', async t => {
    const ui = await cameraSetup(t, {failure});
    await ui.page.locator('[data-canjear]').click();
    await ui.page.waitForFunction(() => !document.querySelector('[data-canjear]').disabled && document.querySelector('[data-capture-status]').textContent.length > 0);
    assert.match(await ui.page.locator('[data-capture-status]').textContent(), message);
    assert.equal(await ui.page.locator('input[type=file]').count(), 0);
    assert.equal((await ui.page.locator('body').textContent()).includes('PRIVATE-TECHNICAL'), false);
    await ui.stopped(); assert.deepEqual(ui.errors, []);
  });
}
test('upload fallido conserva preview para reintento sin otro canje', async t => {
  const ui = await cameraSetup(t);
  await ui.openCamera(); await ui.photo();
  const url = await ui.page.locator('[data-camera-preview]').getAttribute('src');
  ui.state.uploadFail = true;
  await ui.accept();
  await ui.page.waitForFunction(() => !document.querySelector('[data-usar-foto]').disabled);
  await ui.waitStep('preview'); await ui.stopped();
  assert.equal(await ui.page.locator('[data-camera-preview]').getAttribute('src'), url);
  ui.state.uploadFail = false; await ui.accept(); await ui.waitStep('ready');
  assert.equal(ui.requests.filter(r => r.name === 'canjear').length, 1);
  assert.equal(ui.requests.filter(r => r.name === 'FRONTAL').length, 2);
});
for (const estado of ['EXPIRADA', 'REVOCADA', 'FINALIZADA']) {
  test(estado + ' durante visor detiene stream y elimina acciones', async t => {
    const ui = await cameraSetup(t);
    await ui.openCamera(); await ui.page.clock.install();
    ui.state.estado = estado;
    // Visibility resume rechecks server state without changing authorization.
    await ui.page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await ui.waitStep(estado === 'FINALIZADA' ? 'done' : 'error'); await ui.stopped();
    assert.equal(await ui.page.locator('[data-canjear]').isVisible(), false);
    assert.equal(await ui.page.locator('[data-tomar-foto]').isVisible(), false);
  });
}
for (const event of ['pagehide', 'visibilitychange']) {
  test('camara cerrada al ' + event, async t => {
    const ui = await cameraSetup(t); await ui.openCamera();
    await ui.page.evaluate(event => {
      if (event === 'visibilitychange') {
        Object.defineProperty(document, 'hidden', {configurable: true, value: true});
        document.dispatchEvent(new Event(event));
      } else window.dispatchEvent(new Event(event));
    }, event);
    await ui.stopped(); await ui.waitStep('ready');
  });
}
test('permiso concedido tarde tras abandonar no deja camara activa', async t => {
  const ui = await cameraSetup(t, {delayed: true});
  await ui.page.locator('[data-canjear]').click(); await ui.waitStep('opening');
  await ui.page.evaluate(() => { window.dispatchEvent(new Event('pagehide')); window.allowCamera(); });
  await ui.page.waitForFunction(() => cameraStats.tracks.length > 0);
  await ui.stopped(); assert.deepEqual(ui.errors, []);
});
test('spinner visible solo durante espera/loading, sin logo redundante', async t => {
  const ui = await setup(t); await ui.create(); await ui.waitState('waiting');
  const animation = () => ui.page.locator('[data-status-icon]').evaluate(el => getComputedStyle(el).animationName);
  assert.equal(await animation(), 'capture-spin');
  assert.equal(await ui.page.locator('.capture-heading img').count(), 0);
  ui.state.value = 'FINALIZADA';
  await ui.page.locator('[data-reintentar-estado]').evaluate(el => el.click());
  await ui.waitState('success'); assert.equal(await animation(), 'none');
});
for (const width of [320, 768]) {
  test('camara/preview responsive ' + width, async t => {
    const ui = await cameraSetup(t, {width}); await ui.openCamera();
    for (const step of ['live', 'preview']) {
      if (step === 'preview') await ui.photo();
      assert.equal(await ui.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      const button = ui.page.locator(step === 'live' ? '[data-tomar-foto]' : '[data-usar-foto]');
      assert.equal(await button.evaluate(el => el.getBoundingClientRect().height >= 44 && el.scrollWidth <= el.clientWidth), true);
      if (process.env.HANDOFF_SCREENSHOTS) await ui.page.screenshot({path: path.join(process.env.HANDOFF_SCREENSHOTS, 'camera-' + width + '-' + step + '.png'), fullPage: true});
    }
    await ui.stopped();
  });
}
