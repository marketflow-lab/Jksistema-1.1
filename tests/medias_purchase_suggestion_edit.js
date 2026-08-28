'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const canonical = fs.readFileSync(path.join(root, 'medias_compras.html'), 'utf8');
const served = fs.readFileSync(path.join(root, 'static', 'medias_compras.html'), 'utf8');
const moduleDirectory = path.join(root, 'static', 'medias_compras');
const moduleSources = fs.readdirSync(moduleDirectory)
  .filter(name => name.endsWith('.js'))
  .sort()
  .map(name => fs.readFileSync(path.join(moduleDirectory, name), 'utf8'));
const runtimeSource = moduleSources.join('\n');

assert.strictEqual(served, canonical, 'os espelhos de Medias e Pedidos divergiram');
assert(runtimeSource.includes('class="compra-sugerida-editavel'), 'a sugestao deve ser renderizada como botao editavel');
assert(runtimeSource.includes("botaoCompraSugerida.addEventListener('click'"), 'o clique deve iniciar a edicao');
assert(runtimeSource.includes("input.type = 'number'"), 'a edicao deve usar um campo numerico');
assert(runtimeSource.includes("input.addEventListener('blur', () => finalizar(true))"), 'sair do campo deve confirmar a edicao');
assert(runtimeSource.includes("event.key === 'Enter'"), 'Enter deve confirmar a edicao');
assert(runtimeSource.includes("event.key === 'Escape'"), 'Escape deve cancelar a edicao');
assert(runtimeSource.includes('payload.quantidades_sugeridas = obterQuantidadesSugeridasEditadas()'));
assert(runtimeSource.includes("params.set('quantidades_sugeridas', JSON.stringify(payload.quantidades_sugeridas))"));
assert(runtimeSource.includes("params.set('quantidades_sugeridas', JSON.stringify(quantidadesSugeridas))"));
assert(runtimeSource.includes('comprasSugeridasEditadas.clear()'), 'uma nova consulta deve limpar ajustes da consulta anterior');

const normalizarSource = runtimeSource.match(/function normalizarQuantidadeCompraSugerida\(valor\) \{[\s\S]*?\n    \}/);
assert(normalizarSource, 'normalizador da quantidade editada nao encontrado');
const normalizar = new Function(normalizarSource[0] + '; return normalizarQuantidadeCompraSugerida;')();
assert.strictEqual(normalizar('12'), 12);
assert.strictEqual(normalizar('0'), 0);
assert.strictEqual(normalizar('-1'), null);
assert.strictEqual(normalizar('1.5'), null);
assert.strictEqual(normalizar(''), null);

const serializarSource = runtimeSource.match(/function obterQuantidadesSugeridasEditadas\(\) \{[\s\S]*?\n        \}/);
assert(serializarSource, 'serializador dos ajustes nao encontrado');
const serializar = new Function(
  'comprasSugeridasEditadas',
  serializarSource[0] + '; return obterQuantidadesSugeridasEditadas;',
)(new Map([['SKU-2', 0], ['SKU-1', 7]]));
assert.deepStrictEqual(serializar(), { 'SKU-1': 7, 'SKU-2': 0 });

const scripts = [...canonical.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map(match => match[1])
  .filter(source => source.trim());
scripts.forEach((source, index) => {
  assert.doesNotThrow(
    () => new Function(source),
    `script inline ${index + 1} de Medias e Pedidos possui erro de sintaxe`,
  );
});
moduleSources.forEach((source, index) => {
  assert.doesNotThrow(() => new Function(source), `modulo ${index + 1} de Medias possui erro de sintaxe`);
});

console.log('medias purchase suggestion edit: OK');
