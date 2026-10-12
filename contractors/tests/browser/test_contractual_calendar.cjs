const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const readline = require('node:readline');
const os = require('node:os');
const {spawn} = require('node:child_process');
const {webkit} = require('playwright');

test('WebKit movil: calendario ausente -> contrato/adenda -> simulacion Django', {timeout: 240000}, async t => {
  const root = path.resolve(__dirname, '../../..');
  const python = process.env.DJANGO_TEST_PYTHON || path.join(root, 'venv/Scripts/python.exe');
  const bridge = spawn(python, ['-u', path.join(__dirname, 'calendario_django_bridge.py')], {
    cwd: root, env: {...process.env, PYTHONIOENCODING: 'utf-8'}, stdio: ['pipe', 'pipe', 'inherit'],
  });
  const pending = [];
  const responses = [];
  let exited = false;
  const finished = new Promise(resolve => bridge.once('exit', code => { exited = true; resolve(code); }));
  const lines = readline.createInterface({input: bridge.stdout});
  lines.on('line', line => {
    const value = JSON.parse(line);
    if (pending.length) pending.shift().resolve(value);
    else responses.push(value);
  });
  bridge.on('exit', () => {
    while (pending.length) pending.shift().reject(new Error('Django fixture exited before response'));
  });
  const read = () => responses.length ? Promise.resolve(responses.shift()) :
    new Promise((resolve, reject) => pending.push({resolve, reject}));
  let queue = Promise.resolve();
  const request = command => {
    const result = queue.then(() => { bridge.stdin.write(JSON.stringify(command) + '\n'); return read(); });
    queue = result.then(() => {}, () => {});
    return result;
  };
  let browser;
  t.after(async () => {
    if (browser) await browser.close();
    if (!exited) bridge.stdin.end(JSON.stringify({stop: true}) + '\n');
    const timer = setTimeout(() => { if (!exited) bridge.kill(); }, 15000);
    const code = await finished;
    clearTimeout(timer);
    assert.equal(code, 0, 'Disposable Django fixture must clean up successfully');
  });
  const fixture = await read();
  assert.equal(fixture.ready, true);
  browser = await webkit.launch();
  const page = await browser.newPage({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
  // WebKit's intercepted postDataBuffer omits file bytes. Preserve the actual
  // in-browser FormData and let Django's client encode those same bytes.
  await page.addInitScript(() => {
    window.__calendarMultipart = {};
    window.__calendarFiles = {};
    window.__encodeCalendarForm = async data => {
      const fields = [];
      for (const [name, value] of data.entries()) {
        if (typeof value === 'string') fields.push({name, value});
        else {
          if (!value.name) continue;
          const key = value.name + ':' + value.size + ':' + value.type;
          if (!window.__calendarFiles[key]) {
            const bytes = new Uint8Array(await value.arrayBuffer());
            let binary = '';
            for (const byte of bytes) binary += String.fromCharCode(byte);
            window.__calendarFiles[key] = {name: value.name, type: value.type, data: btoa(binary)};
          }
          fields.push({name, value: window.__calendarFiles[key]});
        }
      }
      return fields;
    };
    const fetch = window.fetch.bind(window);
    window.fetch = async (url, options) => {
      if (options?.body instanceof FormData) {
        window.__calendarMultipart[new URL(url, location.href).pathname] =
          await window.__encodeCalendarForm(options.body);
      }
      return fetch(url, options);
    };
  });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let analysis;
  let submission;
  await page.route('**/*', async route => {
    const req = route.request();
    const url = new URL(req.url());
    if (url.hostname !== 'contratistas.localhost') return route.abort();
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve(root, '.' + url.pathname);
      if (file.startsWith(path.join(root, 'static') + path.sep) && fs.existsSync(file)) {
        return route.fulfill({path: file});
      }
      return route.fulfill({status: 404, body: ''});
    }
    const command = {method: req.method(), path: url.pathname + url.search, headers: req.headers(),
      body: (req.postDataBuffer() || Buffer.alloc(0)).toString('base64')};
    if (req.method() === 'POST' && (req.headers()['content-type'] || '').startsWith('multipart/form-data')) {
      command.multipart = await page.evaluate(async pathname =>
        window.__calendarMultipart[pathname] ||
        await window.__encodeCalendarForm(new FormData(document.getElementById('formularioPrestador'))), url.pathname);
    }
    const response = await request(command);
    assert.equal(response.error, undefined);
    const body = Buffer.from(response.body, 'base64');
    if (url.pathname.endsWith('/contrato/analizar/')) analysis = JSON.parse(body.toString());
    if (url.pathname === '/solicitar/' && req.method() === 'POST') submission = response;
    // WebKit interception cannot fulfill a 3xx response. Navigate to the exact
    // Location returned by Django; keep the real status for assertions below.
    if (response.status >= 300 && response.status < 400) {
      const target = new URL(response.headers.Location, req.url());
      assert.equal(target.hostname, 'contratistas.localhost');
      return route.fulfill({status: 200, contentType: 'text/html',
        body: '<!doctype html><script>location.replace(' + JSON.stringify(target.href) + ')</script>'});
    }
    return route.fulfill({status: response.status, headers: response.headers, body});
  });
  await page.goto('https://contratistas.localhost/simular/?solicitud_id=' + fixture.solicitud_id);
  assert.match(await page.locator('main').textContent(), /Falta una regla precisa/);
  assert.equal(await page.locator('#simulator-form').count(), 0);
  await page.getByRole('link', {name: 'Revisar contrato'}).click();
  await page.locator('#step-2.active').waitFor();
  assert.equal(await page.locator('#id_fecha_inicio_contrato').inputValue(), '2026-07-01');
  assert.equal(await page.locator('#id_fecha_fin_contrato').inputValue(), '2027-06-30');
  // A reanalysis must not restore an erroneous extracted/manual previous value.
  await page.evaluate(() => { document.querySelector('#id_fecha_fin_contrato').value = '2027-06-29'; });
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'aprobado-calendario-'));
  const pdf = path.join(directory, 'contrato-adenda-sintetico.pdf');
  fs.writeFileSync(pdf, Buffer.from(fixture.adenda, 'base64'));
  t.after(() => { fs.unlinkSync(pdf); fs.rmdirSync(directory); });
  await page.locator('#id_contrato_actual').setInputFiles(pdf);
  await page.evaluate(async () => {
    const data = new FormData();
    data.set('contrato_actual', document.querySelector('#id_contrato_actual').files[0]);
    await window.__encodeCalendarForm(data);
  });
  await page.locator('#id_autoriza_analisis_contractual_asistido').check();
  await page.locator('#analizar_contrato_button').click();
  await page.waitForFunction(() => !document.querySelector('#analizar_contrato_button').disabled);
  assert.equal(analysis.success, true, JSON.stringify({estado: analysis.estado,
    error: analysis.error, bloqueos: analysis.bloqueos}));
  assert.equal(analysis.campos_extraidos.valor_mensual_contractual.valor, '7000000');
  assert.equal(analysis.campos_extraidos.valor_pagado_contrato.encontrado, false);
  assert.equal(await page.locator('#id_fecha_fin_contrato').inputValue(), '2027-06-29');
  await page.locator('#step-2 [data-next-step="3"]').click();
  await page.locator('#step-3 [data-next-step="4"]').click();
  await page.locator('#id_fecha_fin_contrato').fill('2027-06-30');
  await page.locator('#id_autoriza_consulta_centrales').check();
  await page.locator('#id_acepta_terminos').check();
  await page.locator('#id_acepta_politica_privacidad').check();
  await page.getByRole('button', {name: 'Continuar a simulación'}).click();
  await page.waitForURL('**/simular/?solicitud_id=*');
  assert.equal(submission.status, 302);
  await page.locator('#simulator-form').waitFor();
  await page.waitForFunction(() => !document.querySelector('[data-register-application]').disabled);
  assert.match(await page.locator('[data-result="cuota_mensual"]').inputValue(), /\d/);
  assert.equal(await page.locator('[data-register-application]').count(), 1);
  assert.deepEqual(errors, []);
  assert.deepEqual(await request({integrity: true}), {applications: 1, documents: 4,
    credits: 0, audits: 0, snapshots: 0, simulation_registered: false});
  const screenshot = process.env.CALENDAR_SCREENSHOT;
  if (screenshot) await page.screenshot({path: screenshot, fullPage: true});
});
