(function (root) {
  'use strict';
  function fillEmpty(field, value) {
    if (!field || String(field.value).trim() !== '' || value === null || value === undefined || value === '' || value === 'NO_IDENTIFICADA') return false;
    field.value = String(value);
    return true;
  }
  function missingFields(fields, getValue) {
    return Object.entries(fields).filter(([name, field]) =>
      field.editable && !field.encontrado && String(getValue(name) ?? '').trim() === ''
    ).map(([, field]) => field.etiqueta);
  }
  const api = { fillEmpty, missingFields };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.ContractReconciliation = api;
})(typeof window !== 'undefined' ? window : globalThis);
