// Render the real Django template without a database, then inspect only this section.
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const {spawnSync} = require('node:child_process');
const {chromium, webkit} = require('playwright');
let browser, markup, styles;

before(async () => {
  const render = spawnSync(path.resolve('venv/Scripts/python.exe'), ['-c', `
import os
from unittest.mock import patch
with patch('dotenv.load_dotenv', return_value=False):
    os.environ.update(DJANGO_SETTINGS_MODULE='aprobado_web.settings', USE_SQLITE='True',
                      DATACREDITO_ENABLED='False', DATACREDITO_REAL_ENABLED='False')
    from django.conf import settings
    settings.DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}
    import django
    django.setup()
    from django.template.loader import render_to_string
    from usuarios.views import _build_landing_backers
    print(render_to_string('libranza/libranza_landing.html', {'landing_backers': _build_landing_backers()}))
`], {encoding: 'utf8', env: {...process.env, PYTHONIOENCODING: 'utf-8'}, maxBuffer: 5e6});
  assert.equal(render.status, 0, render.stderr);
  markup = render.stdout.match(/<section class="libranza-backers-section"[\s\S]*?<\/section>/)[0];
  styles = fs.readFileSync('static/css/styles.css', 'utf8') + '\n' +
    Array.from(render.stdout.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g), m => m[1]).join('\n');
  browser = process.env.ECOSYSTEM_WEBKIT === '1' ? await webkit.launch() :
    await chromium.launch({channel: 'msedge'});
});

after(async () => browser?.close());

for (const width of [320, 390, 768, 1440]) {
  test(`ecosistema: logos, texto y limites del viewport ${width}`, async t => {
    const page = await browser.newPage({viewport: {width, height: 900}});
    t.after(() => page.close());
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.hostname !== 'ecosistema.invalid' || !url.pathname.startsWith('/static/')) return route.abort();
      const file = path.resolve('static', decodeURIComponent(url.pathname.slice('/static/'.length)));
      if (!file.startsWith(path.resolve('static') + path.sep) || !fs.existsSync(file)) return route.abort();
      await route.fulfill({path: file});
    });
    await page.setContent(`<html lang="es"><head><meta charset="UTF-8"><base href="https://ecosistema.invalid/">
      <meta name="viewport" content="width=device-width, initial-scale=1"><style>${styles}</style></head>
      <body>${markup}</body></html>`);
    const fenalco = page.getByAltText('Fenalco Meta', {exact: true});
    await fenalco.scrollIntoViewIfNeeded();
    await page.waitForFunction(() => Array.from(document.images).every(img => img.complete && img.naturalWidth > 0));
    assert.equal(await page.locator('.libranza-backer-card').count(), 6);
    assert.equal(await page.locator('h2').innerText(), 'Conoce nuestro ecosistema');
    const dimensiones = await page.evaluate(() => ({
      overflow: document.documentElement.scrollWidth > window.innerWidth,
      tarjetas: Array.from(document.querySelectorAll('.libranza-backer-card')).map(el => {
        const box = el.getBoundingClientRect();
        return {izquierda: box.left, derecha: box.right, overflow: el.scrollWidth > el.clientWidth};
      }),
    }));
    assert.equal(dimensiones.overflow, false);
    for (const tarjeta of dimensiones.tarjetas) {
      assert.equal(tarjeta.overflow, false);
      assert.ok(tarjeta.izquierda >= 0 && tarjeta.derecha <= width);
    }
    await page.screenshot({path: path.join(os.tmpdir(), `aprobado-ecosistema-${process.env.ECOSYSTEM_WEBKIT === '1' ? 'webkit' : 'chromium'}-${width}.png`), fullPage: true});
  });
}
