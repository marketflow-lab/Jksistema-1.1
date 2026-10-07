'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(process.env.FAVORITOS_TEST_SOURCE_ROOT || path.join(__dirname, '..'));
const context = {
  FavoritosV2: { browser: { pageScripts: {} } },
  mlSearchTermInput: { value: '' },
  mlSearchTerm2Input: { value: '' },
  mlSearchTerm3Input: { value: '' }
};
context.window = context;
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(root, 'static/favoritos/v2/browser/browser-ui.js'), 'utf8'), context);

const plain = value => JSON.parse(JSON.stringify(value));
function terms(input, expected, message) {
  assert.deepStrictEqual(plain(context.normalizarTermosPesquisaFavoritos(input)), expected, message);
}
function fields(values, expected) {
  context.mlSearchTermInput.value = values[0] || '';
  context.mlSearchTerm2Input.value = values[1] || '';
  context.mlSearchTerm3Input.value = values[2] || '';
  assert.deepStrictEqual(plain(context.obterTermosPesquisaAvulsaMl()), expected, 'normalizar os campos visiveis: ' + JSON.stringify(values));
}

fields(['', '', ''], []);
fields(['  ', '\t', '\n'], []);
fields(['Machado bombeiro', '', ''], [{ campo: 1, termo: 'Machado bombeiro' }]);
fields(['', '', ' Machado bombeiro '], [{ campo: 3, termo: 'Machado bombeiro' }]);
fields(['Machado bombeiro', '', 'Machado resgate'], [
  { campo: 1, termo: 'Machado bombeiro' }, { campo: 3, termo: 'Machado resgate' }
]);
fields(['Machado bombeiro', 'Machado resgate', 'Ferramenta resgate'], [
  { campo: 1, termo: 'Machado bombeiro' }, { campo: 2, termo: 'Machado resgate' }, { campo: 3, termo: 'Ferramenta resgate' }
]);
fields([' Machado bombeiro ', 'machado BOMBEIRO', 'Machado resgate'], [
  { campo: 1, termo: 'Machado bombeiro' }, { campo: 3, termo: 'Machado resgate' }
]);

terms([], [], 'lista vazia permanece vazia');
for (const invalid of [null, undefined, '', 'texto fora de uma lista', {}, 5, true]) {
  terms(invalid, [], 'entrada fora do contrato de lista deve ser descartada');
}
terms([null, undefined, false, true, 0, 42, [], ['indevido'], {}, { campo: 2 },
  { termo: '' }, { term: ' ' }, { valor: null }, { value: undefined }, { termo: 123 }, { termo: false },
  { toString() { throw new Error('nao converter objeto vazio em termo'); } }], [], 'nenhum tipo invalido pode fabricar um termo');
terms(['', '  ', ' Machado bombeiro '], [{ campo: 3, termo: 'Machado bombeiro' }], 'strings preservam o indice original depois de filtrar vazios');
terms(['[object Object]'], [{ campo: 1, termo: '[object Object]' }], 'texto literal digitado pelo usuario permanece valido');
terms([{ campo: 2, termo: '[object Object]' }], [{ campo: 2, termo: '[object Object]' }], 'texto literal explicito em objeto permanece valido');
terms([{ termo: '', campo: 1 }, { termo: 'Machado', campo: 3 }], [{ campo: 3, termo: 'Machado' }], 'objeto vazio nao pode virar [object Object]');

for (const [termAlias, fieldAlias] of [['termo', 'campo'], ['term', 'field'], ['valor', 'numero'], ['value', 'index']]) {
  terms([{ [termAlias]: ' Machado bombeiro ', [fieldAlias]: '3' }], [{ campo: 3, termo: 'Machado bombeiro' }], 'preservar aliases ' + termAlias + '/' + fieldAlias);
}
terms([{ campo: 3, termo: 'Machado bombeiro' }, { campo: 1, termo: ' machado BOMBEIRO ' }, 'Machado resgate'], [
  { campo: 3, termo: 'Machado bombeiro' }, { campo: 3, termo: 'Machado resgate' }
], 'duplicatas preservam primeira grafia e primeiro campo');
terms(['', {}, 'Primeiro', 'primeiro', 'Segundo', 'Terceiro', 'Quarto'], [
  { campo: 3, termo: 'Primeiro' }, { campo: 5, termo: 'Segundo' }, { campo: 6, termo: 'Terceiro' }
], 'limite de tres se aplica depois de filtrar e deduplicar');
terms([{ termo: 'Campo invalido', campo: 'nao numerico' }, { termo: 'Sem campo' }], [
  { campo: 1, termo: 'Campo invalido' }, { campo: 2, termo: 'Sem campo' }
], 'campo ausente ou invalido usa indice original');
const frozen = Object.freeze([Object.freeze({ campo: 2, termo: ' Machado bombeiro ' }), 'Machado resgate']);
terms(frozen, [{ campo: 2, termo: 'Machado bombeiro' }, { campo: 2, termo: 'Machado resgate' }], 'normalizacao nao altera dados recebidos');
assert.strictEqual(frozen[0].termo, ' Machado bombeiro ');

context.preencherCamposPesquisaAvulsaMl([{ campo: 1, termo: '' }, { campo: 3, termo: 'Machado resgate' }]);
assert.deepStrictEqual([context.mlSearchTermInput.value, context.mlSearchTerm2Input.value, context.mlSearchTerm3Input.value], ['', '', 'Machado resgate']);
assert.strictEqual(context.formatarTermosPesquisaFavoritos([{ campo: 1, termo: '' }, { campo: 3, termo: 'Machado resgate' }]), '3: Machado resgate');
console.log('OK: termos de Favoritos preservam campos validos e descartam vazios e tipos invalidos.');
