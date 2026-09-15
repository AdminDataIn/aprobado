const { test, before, after } = require('node:test');
const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const jsQR = require('jsqr');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const root = path.resolve(__dirname, '../../..');
const id = '12345678-1234-4123-8123-123456789abc';
const token = 'solo-token-ficticio-para-pruebas-id01b';
let server, browser, origin, html, cameraHTML;
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
    "'}, 'solicitud_id': '', 'csrf_token': 'csrf-prueba'}))"], {cwd: root, encoding: 'utf8',
    env: {...process.env, PYTHONIOENCODING: 'utf-8'}, stdio: ['ignore', 'pipe', 'ignore']});
  server = http.createServer((req, res) => {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname === '/camera') {
      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      res.end(url.searchParams.has('prestadores') ? cameraHTML.replaceAll('LIBRANZA', 'PRESTADORES')
        .replace('data-contexto=""', 'data-contexto="1234"') : cameraHTML);
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
    const content = url.searchParams.has('prestadores') ? html.replaceAll('LIBRANZA', 'PRESTADORES').replace('data-contexto=""', 'data-contexto="1234"') : html;
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    res.end('<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Captura</title><body style="margin:0;background:#f4f6f8;padding:20px;font-family:system-ui"><form style="max-width:740px;margin:20px auto"><input type="hidden" name="csrfmiddlewaretoken" value="csrf-prueba"><label>Nombre <input name="nombre" value="Nombre diligenciado"></label>' + content + '<input type="file" id="cedula_frontal" required hidden></form></body></html>');
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  origin = 'http://127.0.0.1:' + server.address().port;
  browser = await chromium.launch({channel: 'chromium', headless: true, args: ['--use-fake-device-for-media-stream']});
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
  const logs = [], errors = [], requests = [], allRequests = [];
  page.on('console', message => logs.push(message.text()));
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => allRequests.push(request.url()));
  const state = {value: 'ABIERTA', gets: 0, posts: 0, postStatus: 201, html: false,
    transient: 0, abort: false, hang: false, ...opts};
  await page.addInitScript(({blocked, restored, id}) => {
    if (restored) sessionStorage.setItem('captura:LIBRANZA:', id);
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
          : {id, enlace: '/captura-documental/' + producto + '/' + id + '/' +
            (producto === 'PRESTADORES' ? '?solicitud_id=1234' : '') + '#' + token,
            expira_en: new Date(Date.now() + 600000).toISOString()})});
    } else {
      state.gets++;
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
  assert.equal(ui.allRequests.every(url => url.startsWith(origin + '/')), true);
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
  assert.equal(await ui.page.locator('[data-enlace-captura]').isVisible(), true);
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
      assert.equal(await ui.page.locator(selector).evaluate(element => element.scrollWidth <= element.clientWidth), true);
    }
    if (process.env.HANDOFF_SCREENSHOTS) await ui.page.screenshot({path: path.join(process.env.HANDOFF_SCREENSHOTS, 'handoff-' + width + '.png'), fullPage: true});
  });
}

async function cameraSetup(t, opts = {}) {
  const context = await browser.newContext({viewport: {width: opts.width || 390, height: 844},
    permissions: ['camera'], ...(opts.touch ? {isMobile: true, hasTouch: true} : {})});
  t.after(() => context.close());
  const page = await context.newPage(), requests = [], errors = [], logs = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => logs.push(message.text()));
  await page.addInitScript(({failure, delayed}) => {
    window.cameraStats = {calls: [], tracks: [], stops: 0};
    if (failure === 'unsupported') {
      Object.defineProperty(navigator, 'mediaDevices', {value: undefined}); return;
    }
    const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = async constraints => {
      cameraStats.calls.push(constraints);
      if (failure) throw new DOMException('PRIVATE-TECHNICAL-ERROR', failure);
      if (delayed) await new Promise(resolve => { window.allowCamera = resolve; });
      let stream;
      try { stream = await original(constraints); }
      catch (error) { cameraStats.failure = error.name; throw error; }
      for (const track of stream.getTracks()) {
        cameraStats.tracks.push(track);
        const stop = track.stop.bind(track);
        track.stop = () => { cameraStats.stops++; stop(); };
      }
      return stream;
    };
  }, opts);
  const state = {estado: 'ABIERTA', lados: [], uploadFail: false};
  await page.route('**/captura-documental/**', async route => {
    const request = route.request(), name = new URL(request.url()).pathname.split('/').filter(Boolean).at(-1);
    requests.push({name, method: request.method(), url: request.url(), body: request.postDataBuffer(), headers: request.headers()});
    if (request.method() === 'POST') {
      if (name === 'canjear') state.estado = 'CANJEADA';
      if (['FRONTAL', 'TRASERA'].includes(name)) {
        if (state.uploadFail) { await route.fulfill({status: 500, body: 'PRIVATE-TECHNICAL-ERROR'}); return; }
        state.lados = [...new Set([...state.lados, name])];
      }
      if (name === 'finalizar') state.estado = 'FINALIZADA';
    }
    await route.fulfill({json: {id, estado: state.estado, lados: state.lados}});
  });
  await page.goto(origin + '/camera' + (opts.prestadores ? '?prestadores=1' : '') + '#' + token);
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
for (const width of [320, 768, 1280]) {
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
