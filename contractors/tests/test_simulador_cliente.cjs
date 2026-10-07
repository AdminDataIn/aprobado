const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const template = fs.readFileSync(path.join(__dirname, '../../templates/contractors/simulador_prestador.html'), 'utf8');
const script = template.match(/<script>([\s\S]*?)<\/script>/)[1];

function harness() {
  const element = (extra = {}) => ({style: {}, value: '--', addEventListener(name, fn) {this[name] = fn;}, ...extra});
  const amount = element({min: '500000', max: '3000000', value: '1000000'});
  const term = element({min: '3', max: '3', value: '3'});
  const form = element({dataset: {calculateUrl: '/simular/calcular/', applicationId: '1'},
    querySelector: () => ({value: 'test-csrf'})});
  const results = ['desembolso_neto', 'cuota_mensual', 'plazo_meses'].map(key => element({dataset: {result: key}}));
  const elements = {'simulator-form': form, 'simulador-range-1': amount, 'simulador-range-2': term};
  const requests = [];
  let scheduled;
  const document = {
    addEventListener: (_name, fn) => fn(),
    getElementById: id => elements[id] ||= element(),
    querySelector: () => element(),
    querySelectorAll: () => results,
  };
  vm.runInNewContext(script, {document, Intl, AbortController,
    setTimeout: fn => {scheduled = fn;}, clearTimeout: () => {},
    fetch: (url, options) => new Promise(resolve => {requests.push({url, options, resolve});}),
  });
  return {amount, results, requests, elements, runScheduled: () => scheduled()};
}

const flush = () => new Promise(resolve => setImmediate(resolve));
const answer = (request, resultado) => request.resolve({ok: true, json: async () => ({ok: true, resultado})});

test('solo muestra los importes calculados por backend y envia monto/plazo/CSRF', async () => {
  const h = harness();
  assert.ok(h.results.every(node => node.value === '--'));
  assert.deepEqual(JSON.parse(h.requests[0].options.body), {solicitud_id: '1', monto: '1000000', plazo_meses: '3'});
  assert.equal(h.requests[0].options.headers['X-CSRFToken'], 'test-csrf');
  answer(h.requests[0], {desembolso_neto: '1000000.00', cuota_mensual: '388547.01', plazo_meses: 3});
  await flush();
  assert.match(h.results[1].value, /388\.547,01/);
  assert.equal(h.results[2].value, 3);
  assert.doesNotMatch(script, /Math\.pow|roundMoney|previewCalculation/);
});

test('un cambio conserva resultados y descarta respuestas anteriores', async () => {
  const h = harness();
  answer(h.requests[0], {desembolso_neto: '1000000.00', cuota_mensual: '388547.01', plazo_meses: 3});
  await flush();
  const previous = h.results[1].value;
  h.amount.value = '2000000';
  h.amount.input();
  assert.equal(h.requests[0].options.signal.aborted, true);
  answer(h.requests[0], {cuota_mensual: '999.00'});
  await flush();
  assert.equal(h.results[1].value, previous);
  assert.equal(h.elements['simulator-status'].textContent, 'Actualizando…');
  const pending = h.runScheduled();
  answer(h.requests[1], {desembolso_neto: '2000000.00', cuota_mensual: '777094.02', plazo_meses: 3});
  await pending;
  assert.match(h.results[1].value, /777\.094,02/);
});

test('error backend no inventa cuota local', async () => {
  const h = harness();
  h.requests[0].resolve({ok: false, json: async () => ({ok: false, error: 'Configuracion no disponible'})});
  await flush();
  assert.ok(h.results.every(node => node.value === '--'));
  assert.equal(h.elements['simulator-status'].textContent, 'Configuracion no disponible');
});

test('respuesta tardia nunca pisa el ultimo monto ni borra una cuota vigente', async () => {
  const h = harness();
  answer(h.requests[0], {cuota_mensual:'100.00'});
  await flush();
  h.amount.value = '2000000';
  h.amount.input();
  const old = h.runScheduled();
  h.amount.value = '3000000';
  h.amount.input();
  const latest = h.runScheduled();
  assert.equal(h.requests[1].options.signal.aborted, true);
  answer(h.requests[2], {cuota_mensual:'300.00'});
  await latest;
  const actual = h.results[1].value;
  answer(h.requests[1], {cuota_mensual:'200.00'});
  await old;
  assert.equal(h.results[1].value, actual);
  assert.match(actual, /300,00/);
  assert.equal(h.elements['simulador-monto-hidden'].value, '3000000');
});
