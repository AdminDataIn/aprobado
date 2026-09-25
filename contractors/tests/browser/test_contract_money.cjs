// NODE_PATH may point to an existing Playwright installation. No HTTP services required.
const {test, before, after} = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const {chromium} = require('playwright');
const script = path.resolve(__dirname, '../../../static/js/contract_money.js');
const money = require(script);
const fields = ['valor_total_contrato', 'valor_pagado_contrato', 'valor_pendiente_cobrar', 'valor_mensual_contractual'];
let browser;

before(async () => {
  browser = await chromium.launch({channel: process.env.CAPTURE_BROWSER_CHANNEL || 'chromium', headless: true});
});
after(async () => { if (browser) await browser.close(); });

async function form(t, mobile = false) {
  const page = await browser.newPage({viewport: {width: mobile ? 390 : 1280, height: 844},
    isMobile: mobile, hasTouch: mobile});
  t.after(() => page.close());
  await page.setContent('<form>' + fields.map(name =>
    `<input type="text" inputmode="decimal" id="id_${name}" name="${name}" data-money-contract>`).join('') +
    '<button type="button">Continuar</button></form>');
  await page.addScriptTag({path: script});
  await page.evaluate(() => document.querySelectorAll('[data-money-contract]').forEach(input => ContractMoney.bind(input)));
  return page;
}

test('normalizacion exacta sin floats, con centavos y entradas invalidas', () => {
  for (const value of ['120000000', '120.000.000', '$ 120.000.000']) {
    assert.equal(money.normalize(value), '120000000');
    assert.equal(money.display(money.normalize(value)), '$ 120.000.000');
  }
  for (const value of ['$ 120.000.000,37', '120000000.37', '120,000,000.37']) {
    assert.equal(money.normalize(value), '120000000.37');
  }
  assert.equal(money.normalize('$ 999.999.999.999,99'), '999999999999.99');
  assert.equal(money.display('999999999999.99'), '$ 999.999.999.999,99');
  for (const value of ['1.20000000', 'abc', '-100', '1e8', '12..000', '1,23,45']) {
    assert.equal(money.normalize(value), null);
  }
});

for (const mobile of [false, true]) {
  test(`escritura sin saltos, display separado y POST canonico (${mobile ? 'movil' : 'desktop'})`, async t => {
    const page = await form(t, mobile);
    for (const name of fields) {
      const input = page.locator('#id_' + name);
      await input.focus();
      for (const digit of '120000000') {
        const before = await input.inputValue();
        await input.pressSequentially(digit);
        assert.equal(await input.inputValue(), before + digit);
        assert.equal(await input.evaluate(el => el.selectionStart), before.length + 1);
      }
      await input.blur();
      assert.equal(await input.inputValue(), '$ 120.000.000');
    }
    const entries = await page.evaluate(() => Array.from(new FormData(document.querySelector('form')).entries()));
    assert.deepEqual(entries, fields.map(name => [name, '120000000']));
  });
}

test('pegar formatos, editar en medio, reanudar escritura y conservar centavos', async t => {
  const page = await form(t);
  const input = page.locator('#id_' + fields[0]);
  for (const value of ['120000000', '120.000.000', '$ 120.000.000', '$ 120.000.000,37']) {
    await input.focus();
    await input.press('ControlOrMeta+A');
    await page.keyboard.insertText(value);
    await input.blur();
    assert.equal(await input.inputValue(), money.display(money.normalize(value)));
    assert.equal(await page.locator('[name="' + fields[0] + '"]').inputValue(), money.normalize(value));
  }
  await input.focus();
  await input.fill('1200');
  await input.blur();
  assert.equal(await input.inputValue(), '$ 1.200');
  await input.focus();
  await input.press('End');
  await input.pressSequentially('0');
  assert.equal(await input.inputValue(), '12000');
  await input.evaluate(el => el.setSelectionRange(2, 3));
  await input.pressSequentially('5');
  assert.equal(await input.inputValue(), '12500');
  assert.equal(await input.evaluate(el => el.selectionStart), 3);
  await input.blur();
  assert.equal(await input.inputValue(), '$ 12.500');
});

test('errores llegan al backend, vacios no son cero y autofill sincroniza sin duplicar campos', async t => {
  const page = await form(t);
  const input = page.locator('#id_' + fields[0]);
  await input.fill('1.20000000');
  await input.blur();
  assert.equal(await input.inputValue(), '1.20000000');
  assert.equal(await page.locator('[name="' + fields[0] + '"]').inputValue(), '1.20000000');
  await input.fill('');
  assert.equal(await page.locator('[name="' + fields[0] + '"]').inputValue(), '');
  await input.evaluate(el => { el.value = '120000000.37'; ContractMoney.bind(el).format(); });
  assert.equal(await input.inputValue(), '$ 120.000.000,37');
  assert.equal(await page.locator('[name="' + fields[0] + '"]').inputValue(), '120000000.37');
  assert.equal(await page.locator('[data-money-canonical]').count(), 4);
});
