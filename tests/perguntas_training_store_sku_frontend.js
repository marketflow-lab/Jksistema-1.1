'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const read = (...parts) => fs.readFileSync(path.join(root, ...parts), 'utf8');

const canonicalHtml = read('perguntas_pos_venda.html');
const servedHtml = read('static', 'perguntas_pos_venda.html');
const runtime = read('static', 'perguntas_pos_venda', 'runtime.js');
const training = read('static', 'perguntas_pos_venda', 'treinamento-ia.js');
const init = read('static', 'perguntas_pos_venda', 'init.js');
const styles = read('static', 'perguntas_pos_venda', 'styles.css');

assert.strictEqual(servedHtml, canonicalHtml, 'espelhos HTML de Perguntas devem permanecer idênticos');
for (const id of [
  'ai-training-scope',
  'ai-training-general-summary',
  'btn-ai-training-adicionar-geral',
  'btn-ai-training-editar-gerais',
  'ai-training-sku-search',
  'ai-training-sku-count',
  'ai-training-sku-list',
  'ai-training-sku-popover',
  'ai-training-sku-guidance-view',
  'btn-ai-training-salvar-sku',
]) {
  assert(canonicalHtml.includes(`id="${id}"`), `controle da base por loja ausente: ${id}`);
}

assert.doesNotMatch(canonicalHtml, /Padrao para todas as contas|O escopo global só pode ser escolhido/);
assert.match(canonicalHtml, /Cada loja possui sua própria base/);
assert.match(runtime, /optSelecione\.disabled = true/);
assert.match(runtime, /storeIdSelecionado \|\| primeiroStoreId/);
assert.match(training, /renderizarListaSkusTreinamento/);
assert.match(training, /data-training-sku=/);
assert.match(training, /abrirBalaoSkuTreinamento/);
assert.match(training, /params\.set\('store_id', lojaEscopo\)/);
assert((training.match(/if \(lojaEscopo !== lojaEscopoTreinamento\(\)\) return;/g) || []).length >= 2,
  'respostas atrasadas de outra loja não podem sobrescrever o escopo atual');
assert.match(training, /Rascunho de[\s\S]*salvo no Obsidian[\s\S]*Revise e publique/);
assert.match(init, /aiTrainingSkuSearch\.addEventListener\('input', renderizarListaSkusTreinamento\)/);
assert.match(init, /btnAiTrainingSalvarSku\?\.addEventListener\('click', salvarOrientacaoSkuTreinamento\)/);
assert.match(styles, /\.training-sku-list[\s\S]*grid-template-columns: repeat\(2/);
assert.match(styles, /\.training-sku-popover[\s\S]*position: fixed/);

console.log('Treinar IA por loja e balão de orientação por SKU: OK');
