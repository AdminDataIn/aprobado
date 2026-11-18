const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium, webkit} = require('playwright');

// Synthetic Django responses provided by the isolated integration test.
const fixture = JSON.parse(fs.readFileSync(0, 'utf8'));
const engine = process.env.PRESTADORES_CALENDARIO_BROWSER;
let browser;
before(async () => {
  browser = engine === 'webkit' ? await webkit.launch() : await chromium.launch({channel: 'msedge'});
});
after(async () => browser?.close());

for (const width of [390, 1280]) {
  for (const pendiente of [true, false]) {
    test(`${engine} ${width}: ${pendiente ? 'estimacion sin envio' : 'calendario confirmado'}`, async t => {
      const page = await browser.newPage({viewport: {width, height: 844}});
      t.after(() => page.close());
      let posts = 0;
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.pathname === '/simular/calcular/') {
          return route.fulfill({json: pendiente ? fixture.estimacion : fixture.calculo});
        }
        if (url.pathname === '/simular/') {
          if (route.request().method() === 'POST') {
            posts += 1;
            return route.fulfill({body: 'Confirmada'});
          }
          return route.fulfill({contentType: 'text/html', body: pendiente ? fixture.pendiente : fixture.confirmado});
        }
        return route.abort();
      });
      await page.goto('https://contratistas.localhost/simular/');
      const cta = page.locator('[data-register-application]');
      const cuota = page.locator('[data-result="cuota_mensual"]');
      await page.waitForFunction(() => document.querySelector('[data-result="cuota_mensual"]').value !== '--');
      assert.notEqual(await cuota.inputValue(), '--');
      assert.equal(await cta.isDisabled(), pendiente);
      assert.equal(await page.locator('.simulador-configuracion-ausente').isVisible(), pendiente);
      if (pendiente) {
        assert.match(await page.locator('#simulator-status').textContent(), /sin calendario confirmado/);
        await page.locator('#simulator-form').evaluate(form => form.requestSubmit());
        assert.equal(posts, 0);
      } else {
        await cta.click();
        assert.equal(posts, 1);
      }
      assert.deepEqual(errors, []);
    });
  }
}
