const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const script = path.resolve(__dirname, '../../../static/js/contract_reconciliation.js');
const {fillEmpty} = require(script);
let browser;
before(async () => { browser = await chromium.launch({channel: process.env.CAPTURE_BROWSER_CHANNEL || 'msedge'}); });
after(async () => { if (browser) await browser.close(); });

function renderedForm() {
  const python = process.env.DJANGO_TEST_PYTHON || path.resolve(__dirname, '../../../venv/Scripts/python.exe');
  return execFileSync(python, ['-c', `
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aprobado_web.settings')
import django
django.setup()
from django import forms
from django.urls import set_urlconf
set_urlconf('aprobado_web.urls_contractors')
from django.template.loader import render_to_string
from contractors.forms import SolicitudPrestadorForm
form = SolicitudPrestadorForm(initial={'numero_documento':'123456789', 'nombres':'Ana', 'empresa':'1', 'cargo':'Abogada'})
form.fields['empresa'] = forms.ChoiceField(choices=[('','Seleccione'),('1','Empresa elegida'),('2','Empresa sugerida')])
print(render_to_string('contractors/solicitud_prestador.html', {'form':form, 'paso_activo':2, 'csrf_token':'x'*32}))
`], {encoding:'utf8', env:{...process.env, PYTHONIOENCODING:'utf-8'}});
}

test('solo completa vacios, incluye cero y no inventa datos ausentes', () => {
  const field = {value: ''};
  assert.equal(fillEmpty(field, null), false);
  assert.equal(fillEmpty(field, 'NO_IDENTIFICADA'), false);
  assert.equal(fillEmpty(field, 0), true);
  assert.equal(field.value, '0');
  assert.equal(fillEmpty(field, 200), false);
  assert.equal(field.value, '0');
});

for (const mobile of [false, true]) {
  test(`formulario Django real conserva campos y empresa al reanalizar (${mobile ? 'mobile' : 'desktop'})`, async t => {
    const page = await browser.newPage({viewport: {width: mobile ? 390 : 1280, height: 844}, isMobile: mobile, hasTouch: mobile});
    t.after(() => page.close());
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const html = renderedForm();
    let requestBody = '';
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/solicitar/') return route.fulfill({contentType:'text/html', body:html});
      if (url.pathname.includes('contrato/analizar')) {
        requestBody = route.request().postData();
        return route.fulfill({json:{success:true, datos:{nombres:'Maria', cargo_o_servicio:'Consultora', valor_total_contrato:'1000000.37'}, empresa_sugerida:{empresa_sugerida_id:2, nombre:'Empresa sugerida', match_tipo:'nit_exacto'}, reconciliacion:[{campo:'nombres', etiqueta:'Nombres', formulario:'Ana', documento:'Maria', estado:'DIFIERE'}]}});
      }
      if (url.pathname.startsWith('/static/')) {
        const file = path.resolve(__dirname, '../../../static', url.pathname.slice(8));
        if (fs.existsSync(file)) return route.fulfill({path:file});
      }
      return route.fulfill({status:404, body:''});
    });
    await page.goto('https://contratistas.localhost/solicitar/');
    await page.locator('#id_contrato_actual').setInputFiles({name:'contrato.pdf', mimeType:'application/pdf', buffer:Buffer.from('%PDF-1.4 test')});
    await page.locator('#id_autoriza_analisis_contractual_asistido').check();
    for (let i = 0; i < 2; i++) {
      await page.locator('#analizar_contrato_button').click();
      await page.waitForFunction(() => document.querySelector('#contract_ai_result').textContent.includes('Diferencias por revisar'));
      await page.waitForFunction(() => !document.querySelector('#analizar_contrato_button').disabled);
    }
    assert.equal(await page.locator('#id_nombres').inputValue(), 'Ana');
    assert.equal(await page.locator('#id_cargo').inputValue(), 'Abogada');
    assert.equal(await page.locator('#id_empresa').inputValue(), '1');
    assert.equal(await page.locator('[name="valor_total_contrato"]').inputValue(), '1000000.37');
    assert.ok(requestBody.includes('name="empresa"'));
    assert.ok(requestBody.includes('name="nombres"'));
    assert.deepEqual(errors, []);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  });

  test(`reanalisar conserva correccion manual y centavos (${mobile ? 'mobile' : 'desktop'})`, async t => {
    const page = await browser.newPage({viewport: {width: mobile ? 390 : 1280, height: 844}, isMobile: mobile, hasTouch: mobile});
    t.after(() => page.close());
    await page.setContent('<form><input id="nombre" name="nombres" value="Ana"><input id="monto" name="valor_total_contrato" data-money-contract></form>');
    await page.addScriptTag({path: script});
    await page.addScriptTag({path: path.resolve(__dirname, '../../../static/js/contract_money.js')});
    await page.evaluate(() => {
      const field = document.querySelector('#monto');
      ContractMoney.bind(field);
      ContractReconciliation.fillEmpty(document.querySelector('#nombre'), 'Maria');
      ContractReconciliation.fillEmpty(field, '1000000.37');
      field.dispatchEvent(new Event('blur'));
    });
    assert.equal(await page.locator('#nombre').inputValue(), 'Ana');
    assert.equal(await page.locator('[name="valor_total_contrato"]').inputValue(), '1000000.37');
    await page.locator('#monto').focus();
    await page.locator('#monto').fill('2000000.45');
    await page.locator('#monto').blur();
    await page.evaluate(() => ContractReconciliation.fillEmpty(document.querySelector('#monto'), '9999999'));
    assert.equal(await page.locator('[name="valor_total_contrato"]').inputValue(), '2000000.45');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  });
}
