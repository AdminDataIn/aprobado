(function (root) {
  'use strict';

  // Money stays as decimal strings; never convert amounts to binary floats.
  function normalize(value) {
    const text = String(value ?? '').replace(/[$\s\u00a0]/g, '');
    if (!text) return '';
    if (!/^\d[\d.,]*$/.test(text)) return null;
    if (text.includes('.') && text.includes(',')) {
      const decimal = text.lastIndexOf('.') > text.lastIndexOf(',') ? '.' : ',';
      const thousands = decimal === '.' ? ',' : '.';
      const index = text.lastIndexOf(decimal);
      const integer = text.slice(0, index).split(thousands).join('');
      const fraction = text.slice(index + 1);
      if (!/^\d+$/.test(integer) || !/^\d{1,2}$/.test(fraction)) return null;
      return `${integer}.${fraction}`;
    }
    const separator = text.includes('.') ? '.' : text.includes(',') ? ',' : '';
    if (!separator) return text;
    const parts = text.split(separator);
    if (parts.some((part) => !/^\d+$/.test(part))) return null;
    if (parts.length > 2 || parts[parts.length - 1].length === 3) {
      return parts.slice(1).every((part) => part.length === 3) ? parts.join('') : null;
    }
    return /^\d{1,2}$/.test(parts[1]) ? parts.join('.') : null;
  }

  function display(canonical) {
    const [integer, fraction] = canonical.split('.');
    const grouped = integer.replace(/\B(?=(\d{3})+(?!\d))/g, '.');
    return `$ ${grouped}${fraction ? ',' + fraction : ''}`;
  }

  const bindings = new WeakMap();
  function bind(input) {
    if (bindings.has(input)) return bindings.get(input);
    const hidden = input.ownerDocument.createElement('input');
    hidden.type = 'hidden';
    hidden.name = input.name;
    hidden.dataset.moneyCanonical = 'true';
    input.removeAttribute('name');
    input.insertAdjacentElement('afterend', hidden);
    const sync = () => {
      const canonical = normalize(input.value);
      // Invalid input must reach Django unchanged, not become an empty/zero amount.
      hidden.value = canonical === null ? input.value : canonical;
      return canonical;
    };
    const format = () => {
      const canonical = sync();
      if (canonical !== null && canonical !== '') input.value = display(canonical);
    };
    input.addEventListener('input', sync);
    input.addEventListener('change', sync);
    input.addEventListener('blur', format);
    input.addEventListener('focus', () => {
      const canonical = sync();
      if (canonical === null || canonical === '') return;
      const old = input.value;
      const decimalIndex = canonical.includes('.') ? Math.max(old.lastIndexOf('.'), old.lastIndexOf(',')) : -1;
      const position = (end) => Array.from(old.slice(0, end)).filter((char, index) =>
        /\d/.test(char) || index === decimalIndex).length;
      const start = position(input.selectionStart);
      const end = position(input.selectionEnd);
      input.value = canonical;
      input.setSelectionRange(start, end);
    });
    const binding = {sync, format};
    bindings.set(input, binding);
    format();
    return binding;
  }

  const api = {normalize, display, bind};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ContractMoney = api;
}(typeof window !== 'undefined' ? window : globalThis));
