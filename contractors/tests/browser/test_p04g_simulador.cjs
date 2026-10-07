const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
let browser;
before(async () => { browser = await chromium.launch({channel: 'msedge'}); });
after(async () => { await browser?.close(); });

function fixture() {
  return execFileSync(path.resolve('venv/Scripts/python.exe'), ['-c', `
import os, json
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aprobado_web.settings')
import django
django.setup()
from django.template.loader import render_to_string
from django.urls import set_urlconf
from django.utils import timezone
from types import SimpleNamespace
from decimal import Decimal
from contractors.forms import SimulacionPrestadorForm
from contractors.models import ConfiguracionSimuladorPrestador, ContractorApplication
from gestion_creditos.models import Empresa
from contractors.services.capacidad_contractual import simular_credito_prestador_informativo
from contractors.services.politica_financiera_prestador import PARAMETROS
set_urlconf('aprobado_web.urls_contractors')
config = ConfiguracionSimuladorPrestador(version='prestadores-prod-v1', monto_minimo=Decimal('1000000'), monto_maximo=Decimal('10000000'), plazo_minimo_meses=1, plazo_maximo_meses=8)
for campo, valor in PARAMETROS.items():
    setattr(config, campo, valor)
form = SimulacionPrestadorForm(configuracion=config, initial={'monto':'1000000','plazo_meses':4})
html = render_to_string('contractors/simulador_prestador.html', {'form':form,'solicitud':SimpleNamespace(id=1234),'configuracion_simulador':config,'configuracion_publica_simulador':{'disponible':True},'documentos_cargados':True,'analisis_habilita_simulacion':True,'horizonte_disponible':True,'puede_registrar':True,'csrf_token':'x'*32})
results = {str(m): {k:str(v) for k,v in simular_credito_prestador_informativo(monto=Decimal(m), plazo_meses=4, configuracion=config).como_dict().items()} for m in (1000000,2000000,3000000,10000000)}
application = ContractorApplication(id=1234, empresa=Empresa(nombre='Convenio de prueba'), monto_solicitado=Decimal('3000000'), plazo_meses=4, created_at=timezone.now(), updated_at=timezone.now(), simulada_en=timezone.now())
previous = ContractorApplication(id=999, empresa=application.empresa, created_at=timezone.now(), updated_at=timezone.now())
progress = {'cargados':4, 'total':4, 'porcentaje':100}
state = {'etiqueta':'Evaluacion en curso','titulo':'Estamos validando tu informacion.','detalle':'Te avisaremos del siguiente paso.','accion':'CONDICIONES','accion_etiqueta':'Ver detalle','tono':'neutral'}
current = (application,progress,state)
history = [(previous,progress,state)]
dashboard = render_to_string('contractors/mi_credito_prestador.html', {'solicitud_principal':current,'solicitudes_anteriores':history,'timeline_publico_principal':{'etapa_actual':4,'total_etapas':4,'eventos':[]}})
print(json.dumps({'html':html,'results':results,'dashboard':dashboard}))
`], {encoding:'utf8', env:{...process.env, PYTHONIOENCODING:'utf-8'}});
}

for (const width of [390, 1280]) {
  test(`simulador real: debounce estable, detalles y CTA unico (${width})`, async t => {
    const page = await browser.newPage({viewport:{width,height:844}, isMobile:width<640, hasTouch:width<640});
    t.after(() => page.close());
    const {html, results} = JSON.parse(fixture());
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let posted;
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/simular/calcular/') {
        const selection = route.request().postDataJSON();
        await new Promise(resolve => setTimeout(resolve, 100));
        return route.fulfill({json:{ok:true,resultado:results[selection.monto]}});
      }
      if (url.pathname === '/simular/' && route.request().method() === 'POST') {
        posted = new URLSearchParams(route.request().postData());
        return route.fulfill({contentType:'text/html', body:'Solicitud recibida'});
      }
      if (url.pathname === '/simular/') return route.fulfill({contentType:'text/html',body:html});
      if (url.pathname.startsWith('/static/')) {
        const file = path.resolve('static', url.pathname.slice(8));
        if (fs.existsSync(file)) return route.fulfill({path:file});
      }
      return route.fulfill({status:404,body:''});
    });
    await page.goto('https://contratistas.localhost/simular/');
    assert.ok(!html.includes('prestadores-prod-v1'));
    const quota = page.locator('[data-result="cuota_mensual"]');
    await page.waitForFunction(() => document.querySelector('[data-result="cuota_mensual"]').value !== '--');
    const previous = await quota.inputValue();
    const before = await quota.boundingBox();
    await page.evaluate(() => {
      const slider = document.querySelector('#simulador-range-1');
      for (const value of ['2000000','3000000']) {
        slider.value = value;
        slider.dispatchEvent(new Event('input', {bubbles:true}));
      }
    });
    assert.equal(await quota.inputValue(), previous);
    assert.equal(await page.locator('[data-selection-summary]').textContent(), '$\u00a03.000.000,00 · 4 meses');
    assert.equal((await quota.boundingBox()).height, before.height);
    await page.waitForFunction(previous => document.querySelector('[data-result="cuota_mensual"]').value !== previous, previous);
    assert.equal(await page.locator('#simulador-monto-hidden').inputValue(), '3000000');
    for (const value of ['10000000','1000000','3000000']) {
      await page.locator('#simulador-range-1').evaluate((input, selected) => {
        input.value = selected;
        input.dispatchEvent(new Event('input', {bubbles:true}));
      }, value);
      await page.waitForFunction(() => document.querySelector('#simulator-status').textContent === '');
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    }
    assert.equal(await page.locator('.simulador-cost-details').getAttribute('open'), null);
    await page.locator('.simulador-cost-details summary').click();
    assert.notEqual(await page.locator('.simulador-cost-details').getAttribute('open'), null);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.equal(await quota.evaluate(input => {
      const canvas = document.createElement('canvas');
      const context = canvas.getContext('2d');
      const style = getComputedStyle(input);
      context.font = `${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
      return context.measureText(input.value).width <= input.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
    }), true);
    if (width < 640) {
      const box = await page.locator('.simulador-sticky-cta').boundingBox();
      assert.ok(box.y >= 0 && box.y + box.height <= 845);
      const text = await page.locator('[data-selection-summary]').boundingBox();
      const button = await page.locator('.simulador-sticky-cta button').boundingBox();
      assert.ok(text.x + text.width <= button.x - 8);
    }
    await page.screenshot({path:path.join(os.tmpdir(), `p04g-simulador-${width}.png`), fullPage:true});
    const buttons = page.locator('[data-register-application]');
    assert.equal(await buttons.count(), 3);
    for (const button of await buttons.all()) assert.equal(await button.getAttribute('form'), 'simulator-form');
    await (width < 640 ? page.locator('.simulador-sticky-cta button') : buttons.first()).click();
    await page.waitForLoadState();
    assert.equal(posted.get('monto'), '3000000');
    assert.equal(posted.get('plazo_meses'), '4');
    assert.deepEqual(errors, []);
  });

  test(`mi credito: resumen compacto e historial colapsado (${width})`, async t => {
    const page = await browser.newPage({viewport:{width,height:844}, isMobile:width<640, hasTouch:width<640});
    t.after(() => page.close());
    const {dashboard} = JSON.parse(fixture());
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith('/static/')) {
        const file = path.resolve('static', url.pathname.slice(8));
        if (fs.existsSync(file)) return route.fulfill({path:file});
      }
      return route.fulfill({contentType:'text/html', body:dashboard});
    });
    await page.goto('https://contratistas.localhost/mi-credito/');
    assert.equal(await page.locator('.cp-history').getAttribute('open'), null);
    assert.equal(await page.locator('.cp-dashboard-details').getAttribute('open'), null);
    assert.match(await page.locator('.cp-dashboard-hero').textContent(), /3\.000\.000/);
    assert.ok((await page.locator('.cp-dashboard-hero').boundingBox()).y < 350);
    await page.screenshot({path:path.join(os.tmpdir(), `p04g-mi-credito-${width}.png`), fullPage:true});
    await page.locator('.cp-dashboard-details > summary').click();
    assert.match(await page.locator('.cp-dashboard-grid').textContent(), /Validación con contratante/);
    await page.locator('.cp-history > summary').click();
    assert.equal(await page.locator('.cp-history-row').count(), 1);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
  });
}
