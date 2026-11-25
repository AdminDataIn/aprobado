const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

(async () => {
  const { url, sesion, cookie, ancho } = JSON.parse(fs.readFileSync(0, 'utf8'));
  const origin = new URL(url).origin;
  const browser = await chromium.launch({
    executablePath: process.env.SME_CHROMIUM_PATH || undefined,
  });
  try {
    const context = await browser.newContext({ viewport: { width: ancho, height: 900 } });
    await context.route('**/*', (route) => {
      const target = new URL(route.request().url());
      if (target.origin === origin) return route.continue();
      if (target.origin === 'https://cdn.jsdelivr.net' &&
          target.pathname.endsWith('/bootstrap.min.css') && process.env.SME_BOOTSTRAP_CSS) {
        return route.fulfill({ path: process.env.SME_BOOTSTRAP_CSS, contentType: 'text/css' });
      }
      return route.abort();
    });
    await context.addCookies([{ name: sesion, value: cookie, url: origin }]);
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    await page.goto(url);
    assert.equal(await page.locator('input[type=date]').inputValue(), '2026-10-31');
    await page.getByLabel('Producto').selectOption('LIBRANZA');
    await page.getByRole('button', { name: 'Previsualizar corte' }).click();
    await page.getByRole('heading', { name: 'Corte 31/10/2026' }).waitFor();
    assert.match(await page.getByRole('status').innerText(), /Remisi.n oficial bloqueada/);
    for (const label of [/Pendientes de conciliaci.n/, /No habilitadas/, /Al d.a entre conciliadas/]) {
      assert.equal(await page.getByText(label).count(), 1);
    }
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('link', { name: 'Conciliación interna' }).click(),
    ]);
    const snapshot = JSON.parse(fs.readFileSync(await download.path(), 'utf8'));
    assert.equal(snapshot.total.al_dia, 1);
    assert.equal(snapshot.total.reportables, 0);
    assert.equal(snapshot.filas.length, 1);
    await page.getByText('Simulaciones de primera entrega', { exact: true }).click();
    for (const name of ['Excepciones Excel', 'Carga Facilidat sintética', 'Cartera Excel',
      'Preparación Facilidat', 'Escenario positivo', 'Escenario mixto']) {
      const [excel] = await Promise.all([
        page.waitForEvent('download'),
        page.getByRole('link', { name, exact: true }).click(),
      ]);
      assert.match(excel.suggestedFilename(), /_NO_REMITIR\.xlsx$/);
      assert.equal(fs.readFileSync(await excel.path()).subarray(0, 2).toString(), 'PK');
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), ancho);
    if (process.env.SME_EVIDENCIA_NAVEGADOR) {
      await page.screenshot({ path: path.join(process.env.SME_EVIDENCIA_NAVEGADOR, `sme-${ancho}.png`),
        fullPage: true });
    }
    await context.clearCookies();
    assert.notEqual((await page.goto(url)).url(), url);
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
