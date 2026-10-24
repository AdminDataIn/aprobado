const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium, webkit} = require('playwright');
let browser;
before(async () => {
  browser = process.env.RESTRICTION_WEBKIT === '1' ? await webkit.launch() :
    await chromium.launch({channel: 'msedge'});
});
after(async () => browser?.close());

function handler(file, start, end) {
  const source = fs.readFileSync(file, 'utf8');
  const desde = source.indexOf(start);
  const hasta = source.indexOf(end, desde);
  assert.ok(desde >= 0 && hasta > desde);
  return source.slice(desde, hasta);
}

for (const width of [390, 1280]) {
  test(`Prestadores ${width}: seleccion restringida no conserva una empresa anterior`, async t => {
    const page = await browser.newPage({viewport: {width, height: 844}});
    t.after(() => page.close());
    await page.setContent('<select id="empresa"><option value="1">Permitida</option>' +
      '<option value="2" disabled data-restriccion="Consulta con Aprobado.">Restringida</option></select>' +
      '<input id="busqueda"><div id="resultados"></div><span id="indicador"></span>' +
      '<p id="helper" role="status"></p><div id="seleccionada"></div>');
    await page.addScriptTag({content: `
      const selectEmpresa = document.querySelector('#empresa'), inputEmpresa = document.querySelector('#busqueda'),
        resultadosEmpresa = document.querySelector('#resultados'), indicadorEmpresa = document.querySelector('#indicador'),
        helperEmpresa = document.querySelector('#helper'), empresaSeleccionada = document.querySelector('#seleccionada');
      ${handler('templates/contractors/solicitud_prestador.html', 'function confirmarEmpresa(', 'function renderResultadosEmpresa(')}
    `});
    await page.evaluate(() => confirmarEmpresa('1', 'Permitida'));
    assert.equal(await page.locator('#empresa').inputValue(), '1');
    await page.evaluate(() => confirmarEmpresa('2', 'Restringida'));
    assert.equal(await page.locator('#empresa').inputValue(), '');
    assert.equal(await page.locator('#helper').textContent(), 'Consulta con Aprobado.');
    assert.equal(await page.locator('#seleccionada').isVisible(), false);
    await page.evaluate(() => confirmarEmpresa('1', 'Permitida'));
    assert.equal(await page.locator('#empresa').inputValue(), '1');
  });

  test(`Libranza ${width}: resultado API restringido no se asigna al campo POST`, async t => {
    const page = await browser.newPage({viewport: {width, height: 844}});
    t.after(() => page.close());
    await page.setContent('<input type="hidden" id="empresa" value="1"><input id="busqueda"><p id="estado"></p>');
    await page.addScriptTag({content: `
      const empresaHiddenInput = document.querySelector('#empresa'), empresaBusquedaInput = document.querySelector('#busqueda');
      function setCompanyState(clase, texto) { document.querySelector('#estado').textContent = texto; }
      function limpiarError() {} function limpiarResultadosEmpresa() {}
      ${handler('templates/gestion_creditos/solicitud_libranza.html', 'function seleccionarEmpresa(', 'function renderizarResultadosEmpresa(')}
    `});
    await page.evaluate(() => seleccionarEmpresa({id: '2', nombre: 'Restringida', mensaje_restriccion: 'Consulta con Aprobado.'}));
    assert.equal(await page.locator('#empresa').inputValue(), '');
    assert.equal(await page.locator('#estado').textContent(), 'Consulta con Aprobado.');
    await page.evaluate(() => seleccionarEmpresa({id: '1', nombre: 'Permitida', mensaje_restriccion: ''}));
    assert.equal(await page.locator('#empresa').inputValue(), '1');
  });
}
