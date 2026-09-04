'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const storage = new Map([
  ['user_data', JSON.stringify({ client_id: '000016' })],
]);
const elements = new Map();
const context = {
  console,
  document: {
    createElement() { return {}; },
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, {});
      return elements.get(id);
    },
  },
  localStorage: {
    getItem(key) {
      return storage.has(key) ? storage.get(key) : null;
    },
    setItem(key, value) {
      storage.set(key, String(value));
    },
    removeItem(key) { storage.delete(key); },
  },
  location: { pathname: '/cadastro.html', search: '' },
  URLSearchParams,
};
context.window = context;
vm.createContext(context);

for (const relative of ['static/cadastro/form/00-core.js', 'static/cadastro/00-runtime.js', 'static/cadastro/01-core.js']) {
  const source = fs.readFileSync(path.join(root, relative), 'utf8');
  vm.runInContext(source, context, { filename: relative });
}

const scoped = context.JKCadastro.core.renderConteudoCelula('foto', {
  foto: 'cadastro_fotos/lojas/store-16/005.png',
  store_id: 'store-16',
});
assert(scoped.includes('/api/cadastro/foto/000016/lojas/store-16/005.png'));
assert(!scoped.includes('/api/cadastro/foto-arquivo/'));

const legacy = context.JKCadastro.core.renderConteudoCelula('foto', {
  foto: 'cadastro_fotos/005.png',
  store_id: 'store-16',
});
assert(legacy.includes('/api/cadastro/foto/000016/005.png'));

const cruzada = context.JKCadastro.core.renderConteudoCelula('foto', {
  foto: 'cadastro_fotos/lojas/store-17/005.png',
  store_id: 'store-16',
});
assert(cruzada.includes('Sem foto'));

storage.delete('user_data');
const fallback = context.JKCadastro.core.renderConteudoCelula('foto', {
  foto: 'cadastro_fotos/005.png',
  store_id: 'store-16',
});
assert(fallback.includes('Sem foto'), 'sem cliente não deve montar uma rota de foto cruzada');

console.log('cadastro photo tenant scope: OK');
