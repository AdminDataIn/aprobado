// Real HTTPS browser/Django integration; authentication seed belongs ONLY to the disposable test DB.
const fs = require('node:fs');
const http = require('node:http');
const https = require('node:https');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');
const jsQR = require('jsqr');

(async () => {
  const config = JSON.parse(fs.readFileSync(0, 'utf8'));
  const target = new URL(config.target);
  let browser, stage = 'HTTPS proxy';
  const proxy = https.createServer({key: fs.readFileSync(config.key), cert: fs.readFileSync(config.cert)}, (req, res) => {
    const upstream = http.request({hostname: target.hostname, port: target.port, path: req.url, method: req.method,
      headers: {...req.headers, 'x-forwarded-proto': 'https'}}, reply => {
      res.writeHead(reply.statusCode, reply.headers); reply.pipe(res);
    });
    upstream.on('error', () => { res.writeHead(502); res.end(); });
    req.pipe(upstream);
  });
  try {
    await new Promise(resolve => proxy.listen(0, '127.0.0.1', resolve));
    const origin = 'https://127.0.0.1:' + proxy.address().port;
    browser = await chromium.launch({channel: 'chromium', headless: true, args: ['--use-fake-device-for-media-stream']});
    const pc = await browser.newContext({ignoreHTTPSErrors: true});
    const mobile = await browser.newContext({ignoreHTTPSErrors: true, permissions: ['camera'],
      viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
    await pc.addCookies([{name: config.sessionName, value: config.pcSession, url: origin, httpOnly: true, secure: true}]);
    assert.equal((await mobile.cookies()).length, 0);
    const desktop = await pc.newPage(), phone = await mobile.newPage();
    desktop.setDefaultTimeout(15000); phone.setDefaultTimeout(15000);
    stage = 'PC crea QR';
    await desktop.goto(origin + '/libranza/solicitar/');
    // This test covers the handoff, not completion of the unrelated loan wizard.
    await desktop.locator('[data-capture-handoff]').waitFor({state: 'attached'});
    await desktop.locator('[data-capture-handoff]').evaluate(panel => {
      document.querySelectorAll('.form-step').forEach(step => step.classList.remove('active'));
      panel.closest('.form-step').classList.add('active');
    });
    await desktop.locator('[data-crear-enlace]').click();
    await desktop.locator('[data-enlace-captura]').waitFor({state: 'visible'});
    const link = await desktop.locator('[data-enlace-captura]').getAttribute('href');
    const secret = new URL(link).hash.slice(1);
    const pixels = await desktop.locator('[data-capture-qr]').evaluate(canvas => ({width: canvas.width, height: canvas.height,
      data: Array.from(canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data)}));
    assert.ok(jsQR(new Uint8ClampedArray(pixels.data), pixels.width, pixels.height).data === link);
    const requests = [], messages = [];
    phone.on('request', request => requests.push(request.url()));
    phone.on('console', message => messages.push(message.text()));
    phone.on('pageerror', () => messages.push('PAGEERROR'));
    stage = 'movil abre shell anonimo';
    await phone.goto(link);
    assert.ok(new URL(phone.url()).pathname.endsWith('/movil/'));
    assert.ok(await phone.evaluate(secret => location.hash === '#' + secret, secret));
    assert.ok(!(await mobile.cookies()).some(cookie => cookie.name === config.sessionName));
    const step = value => phone.waitForFunction(value => document.querySelector('[data-capture-mobile]').dataset.state === value, value);
    async function openCamera() {
      await phone.locator('[data-canjear]').click(); await step('live');
      await phone.waitForFunction(() => !document.querySelector('[data-tomar-foto]').disabled);
    }
    async function take() { await phone.locator('[data-tomar-foto]').click(); await step('preview'); }
    stage = 'canje anonimo CSRF HTTPS';
    await openCamera();
    assert.equal(await phone.evaluate(() => location.hash), '');
    const grant = (await mobile.cookies()).find(cookie => cookie.name === '__Secure-capture_grant_token');
    assert.ok(grant && grant.secure && grant.httpOnly && grant.sameSite === 'Strict');
    assert.equal(grant.path, new URL(link).pathname);
    assert.ok(!(await mobile.cookies()).some(cookie => cookie.name === config.sessionName));
    assert.ok(!(await phone.evaluate(() => document.cookie)).includes(grant.name));
    assert.equal(await phone.evaluate(() => JSON.stringify({...localStorage, ...sessionStorage})), '{}');
    stage = 'CSRF incorrecto sigue rechazado';
    await take();
    // Tamper only the CSRF header: the rejection still comes from real Django middleware.
    await phone.route('**/movil/frontal/', route => route.continue({headers: {...route.request().headers(), 'x-csrftoken': 'incorrecto'}}));
    await phone.locator('[data-usar-foto]').click();
    await phone.waitForFunction(() => document.querySelector('[data-capture-status]').textContent.includes('No pudimos autorizar'));
    assert.ok(!(await phone.locator('[data-capture-status]').textContent()).includes('inicia sesi'));
    await phone.unroute('**/movil/frontal/');
    stage = 'frontal real';
    await phone.locator('[data-usar-foto]').click(); await step('ready');
    stage = 'reload grant sin token';
    await phone.reload(); await step('ready');
    assert.equal(await phone.evaluate(() => location.hash), '');
    await phone.waitForFunction(() => document.querySelector('[data-camera-step]').textContent.includes('Posterior'));
    await openCamera();
    assert.match(await phone.locator('[data-camera-step]').textContent(), /Posterior/);
    stage = 'posterior real y finalizar';
    await take(); await phone.locator('[data-usar-foto]').click(); await step('finish');
    await phone.locator('[data-finalizar]').click(); await step('done');
    assert.ok(!(await mobile.cookies()).some(cookie => cookie.name === '__Secure-capture_grant_token'));
    assert.ok(!(await mobile.cookies()).some(cookie => cookie.name === config.sessionName));
    stage = 'PC detecta FINALIZADA';
    await desktop.waitForFunction(() => document.querySelector('[data-capture-handoff]').dataset.state === 'success');
    const denied = await mobile.request.get(origin + '/libranza/solicitar/', {maxRedirects: 0});
    assert.equal(denied.status(), 302);
    assert.ok(requests.every(url => !url.includes(secret)));
    assert.ok(!messages.join().includes(secret) && !messages.join().includes(grant.value) && !messages.includes('PAGEERROR'));
    stage = 'grant finalizado no puede reutilizarse';
    await phone.reload(); await step('error');
    assert.match(await phone.locator('[data-capture-status]').textContent(), /expir|disponible/);
    console.log('cross-device HTTPS OK; endpoints reales; dos contextos aislados; CSRF y cookies verificados');
  } catch (_) {
    console.error('Fallo de integracion en etapa: ' + stage);
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
    await new Promise(resolve => proxy.close(resolve));
  }
})();
