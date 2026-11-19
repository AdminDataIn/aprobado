(function (root) {
  'use strict';
  function fillEmpty(field, value) {
    if (!field || !['', 'NO_IDENTIFICADA'].includes(String(field.value).trim()) || value === null || value === undefined || value === '' || value === 'NO_IDENTIFICADA') return false;
    field.value = String(value);
    return true;
  }
  function missingFields(fields, getValue) {
    return Object.entries(fields).filter(([name, field]) =>
      field.editable && !field.encontrado && String(getValue(name) ?? '').trim() === ''
    ).map(([, field]) => field.etiqueta);
  }
  function refreshDerivedBalance(form, fields, resume = false) {
    const pending = form.querySelector('#id_valor_pendiente_cobrar');
    const total = form.querySelector('#id_valor_total_contrato');
    const paid = form.querySelector('#id_valor_pagado_contrato');
    if (!pending || !total || !paid) return;
    const evidence = fields.valor_pendiente_cobrar;
    if (!evidence || evidence.fuente !== 'DERIVADO_DETERMINISTICAMENTE') {
      delete pending.dataset.derivedValue;
      return;
    }
    const money = root.ContractMoney;
    const cents = text => {
      const value = money.normalize(text);
      if (value === null || value === '') return null;
      const [integer, fraction = ''] = value.split('.');
      return BigInt(integer) * 100n + BigInt(fraction.padEnd(2, '0'));
    };
    const initial = cents(pending.value), t0 = cents(total.value), p0 = cents(paid.value);
    if (initial === null || (initial !== cents(evidence.valor ?? '') &&
        !(resume && t0 !== null && p0 !== null && initial === t0 - p0))) return;
    pending.dataset.derivedValue = money.normalize(pending.value);
    const update = () => {
      if (money.normalize(pending.value) !== pending.dataset.derivedValue) return;
      const t = cents(total.value), p = cents(paid.value);
      if (t === null || p === null || p > t) return;
      const difference = t - p;
      pending.value = `${difference / 100n}.${String(difference % 100n).padStart(2, '0')}`;
      pending.dataset.derivedValue = pending.value;
      money.bind(pending).format();
    };
    if (!pending.dataset.derivedBound) {
      [total, paid].forEach(field => field.addEventListener('input', update));
      pending.dataset.derivedBound = 'true';
    }
    update();
  }
  const api = { fillEmpty, missingFields, refreshDerivedBalance };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.ContractReconciliation = api;
})(typeof window !== 'undefined' ? window : globalThis);
