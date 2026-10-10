const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {chromium} = require('playwright');
const script = fs.readFileSync(path.resolve('static/js/seguimiento_evaluacion_prestador.js'), 'utf8');
const css = fs.readFileSync(path.resolve('static/css/contractors-portal.css'), 'utf8');
let browser;
before(async () => { browser = await chromium.launch({channel: process.env.CAPTURE_BROWSER_CHANNEL || 'msedge'}); });
after(async () => { await browser?.close(); });

async function fixture(t, {poll = true, width = 390, response} = {}) {
  const context = await browser.newContext({viewport: {width, height: 844}});
  t.after(() => context.close());
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  let calls = 0;
  await page.clock.install();
  await page.route('**/*', async route => {
    if (route.request().url().endsWith('/estado/')) {
      calls++;
      if (response) await response(route, calls);
      else await route.fulfill({json: {mensaje: 'Estamos validando tu información', procesando: true, continuar_seguimiento: true}});
    } else {
      await route.fulfill({contentType: 'text/html', body: `<!doctype html><meta charset="utf-8"><style>${css}</style>
        <body class="contractors-portal"><main class="cp-page"><div class="cp-shell"><section class="cp-dashboard-hero">
        <div data-evaluation-tracking data-state-url="/estado/" data-poll="${poll}" role="status" aria-live="polite">
        <span class="cp-evaluation-spinner" data-evaluation-spinner hidden></span>
        <p data-evaluation-message>Tu solicitud fue enviada para evaluación</p></div></section></div></main></body>`});
    }
  });
  await page.goto('https://synthetic.invalid/mi-credito/');
  await page.addScriptTag({content: script});
  return {page, calls: () => calls, errors};
}

test('pendiente sin procesamiento no inicia polling ni loader', async t => {
  const f = await fixture(t, {poll: false});
  await f.page.clock.runFor(125000);
  assert.equal(f.calls(), 0);
  assert.equal(await f.page.locator('[data-evaluation-spinner]').isVisible(), false);
});

test('polling usa respuesta publica, termina y no solapa solicitudes', async t => {
  const f = await fixture(t, {response: async (route, call) => route.fulfill({json: {
    mensaje: call === 1 ? 'Estamos validando tu información' : 'Tu solicitud superó la evaluación inicial',
    procesando: call === 1, continuar_seguimiento: call === 1
  }})});
  await f.page.clock.runFor(3001);
  await f.page.waitForFunction(() => document.querySelector('[data-evaluation-message]').textContent.includes('validando'));
  await f.page.clock.runFor(5000);
  await f.page.waitForFunction(() => document.querySelector('[data-evaluation-message]').textContent.includes('superó'));
  await f.page.clock.runFor(120000);
  assert.equal(f.calls(), 2);
  assert.equal(await f.page.locator('[data-evaluation-spinner]').isVisible(), false);
  assert.deepEqual(f.errors, []);
});

test('request lento nunca genera polling solapado', async t => {
  let release;
  const waiting = new Promise(resolve => { release = resolve; });
  const f = await fixture(t, {response: async route => {
    await waiting;
    await route.fulfill({json: {mensaje: 'En revisión', procesando: false, continuar_seguimiento: false}}).catch(() => {});
  }});
  await f.page.clock.runFor(3001);
  await f.page.evaluate(() => Promise.resolve());
  await f.page.clock.runFor(8000);
  assert.equal(f.calls(), 1);
  release();
});

test('cierre de pagina detiene seguimiento sin POST ni cancelacion del job', async t => {
  const f = await fixture(t);
  await f.page.evaluate(() => window.dispatchEvent(new Event('pagehide')));
  await f.page.clock.runFor(125000);
  assert.equal(f.calls(), 0);
  assert.equal(await f.page.locator('[data-evaluation-spinner]').isVisible(), false);
});

test('ocultar pantalla pausa las lecturas y volver reanuda dentro del limite', async t => {
  const f = await fixture(t);
  await f.page.evaluate(() => {
    Object.defineProperty(document, 'hidden', {configurable: true, value: true});
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await f.page.clock.runFor(10000);
  assert.equal(f.calls(), 0);
  await f.page.evaluate(() => {
    Object.defineProperty(document, 'hidden', {configurable: true, value: false});
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await f.page.clock.runFor(3001);
  await f.page.waitForFunction(() => document.querySelector('[data-evaluation-message]').textContent.includes('validando'));
  assert.equal(f.calls(), 1);
});

test('401 403 404 detienen el seguimiento', async t => {
  for (const status of [401, 403, 404]) {
    const f = await fixture(t, {response: route => route.fulfill({status, body: ''})});
    await f.page.clock.runFor(3001);
    await f.page.evaluate(() => Promise.resolve());
    await f.page.clock.runFor(125000);
    assert.equal(f.calls(), 1);
  }
});

test('seguimiento limitado a dos minutos y mensaje seguro ante red fallida', async t => {
  const f = await fixture(t, {response: route => route.fulfill({status: 503, body: ''})});
  for (let i = 0; i < 30; i++) {
    await f.page.clock.runFor(5000);
    await f.page.evaluate(() => Promise.resolve());
  }
  const total = f.calls();
  await f.page.clock.runFor(120000);
  assert.equal(f.calls(), total);
  assert.ok(total <= 12);
  assert.equal(await f.page.locator('[data-evaluation-spinner]').isVisible(), false);
});

test('estado cabe en mobile y desktop sin desbordamiento', async t => {
  for (const width of [390, 1280]) {
    const f = await fixture(t, {width, poll: false});
    assert.equal(await f.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await f.page.screenshot({path: path.join(require('node:os').tmpdir(), `prestadores-seguimiento-${width}.png`)});
  }
});
