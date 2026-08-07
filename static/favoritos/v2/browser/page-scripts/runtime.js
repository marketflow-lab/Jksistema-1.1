(function (global) {
  'use strict';

  const browser = global.FavoritosV2 = global.FavoritosV2 || {};
  browser.browser = browser.browser || {};
  const templates = new Map();

  function registerPart(name, index, value) {
    const key = String(name || '');
    const partIndex = Number(index);
    if (!key || !Number.isInteger(partIndex) || partIndex < 0) throw new Error('Parte de script invalida.');
    if (!templates.has(key)) templates.set(key, []);
    const parts = templates.get(key);
    if (parts[partIndex] !== undefined) throw new Error('Parte de script duplicada: ' + key + ':' + partIndex);
    parts[partIndex] = String(value || '');
  }

  function source(name) {
    const parts = templates.get(String(name || '')) || [];
    if (!parts.length || parts.some(part => part === undefined)) throw new Error('Script de pagina incompleto: ' + name);
    return parts.join('\n');
  }

  function render(name, values = {}) {
    let output = source(name);
    const tokenStart = '__JK_' + String(name || '').toUpperCase().replace(/[^A-Z0-9]+/g, '_');
    Object.entries(values || {}).forEach(([key, value]) => {
      const tokenSuffix = '_' + String(key || '').toUpperCase() + '__';
      output = output.split(tokenStart + tokenSuffix).join(String(value));
    });
    if (output.includes(tokenStart + '_')) throw new Error('Parametro de script nao materializado: ' + name);
    return output;
  }

  browser.browser.pageScripts = Object.freeze({ registerPart, source, render });
})(window);
