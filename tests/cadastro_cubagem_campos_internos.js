"use strict";
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const root = path.resolve(__dirname, '..');
const context = { window: { JKCadastro: { __runtimeInitialized: true, components: new Set(), runtime: { elements: {}, state: {}, constants: {} } } } };
vm.createContext(context);
for (const name of ['static/cadastro/form/00-core.js', 'static/cadastro/01-core.js']) {
    vm.runInContext(fs.readFileSync(path.join(root, name), 'utf8'), context, { filename: name });
}
const product = { sku: '001', cubagem_sku: '1', store_id: 'store-A', 'm3 individual': '0.4' };
const before = JSON.stringify(product);
const columns = context.window.JKCadastro.core.construirColunas([product]);
assert(!columns.includes('cubagem_sku'), 'Tabela não expõe a identidade interna da cubagem');
assert(columns.includes('m3 individual'), 'Cubagem comercial continua visível');
assert(!context.window.JKCadastroForm.garantirCamposProduto(Object.keys(product)).includes('cubagem_sku'), 'Formulários não criam controle para identidade interna');
assert.strictEqual(JSON.stringify(product), before, 'Dados da API preservam cubagem_sku para o lookup exato');
console.log('Cadastro: cubagem_sku oculto em tabela e formulários; resposta de API preservada: OK');
