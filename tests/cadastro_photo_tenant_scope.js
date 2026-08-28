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
  },
};
context.window = context;
vm.createContext(context);

for (const relative of ['static/cadastro/00-runtime.js', 'static/cadastro/01-core.js']) {
  const source = fs.readFileSync(path.join(root, relative), 'utf8');
  vm.runInContext(source, context, { filename: relative });
}

const scoped = context.JKCadastro.core.renderConteudoCelula('foto', {
  foto: 'cadastro_fotos/005.png',
});
assert(scoped.includes('/api/cadastro/foto/000016/005.png'));
assert(!scoped.includes('/api/cadastro/foto-arquivo/'));

storage.delete('user_data');
const fallback = context.JKCadastro.core.renderConteudoCelula('foto', {
  foto: 'cadastro_fotos/005.png',
});
assert(fallback.includes('/api/cadastro/foto/default/005.png'));

console.log('cadastro photo tenant scope: OK');
