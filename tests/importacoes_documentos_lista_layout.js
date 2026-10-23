'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const html = fs.readFileSync(path.join(__dirname, '..', 'static', 'importacoes.html'), 'utf8');

const renderStart = html.indexOf('function renderListas(listas)');
const markupStart = html.indexOf('el.innerHTML =', renderStart);
const markupEnd = html.indexOf('const btnAbrir', markupStart);
assert(renderStart >= 0 && markupStart > renderStart && markupEnd > markupStart,
  'A renderização das listas deve permanecer identificável.');
const markup = html.slice(markupStart, markupEnd);
for (const marker of [
  'class="nome-lista"', 'data-abrir-lista', 'data-invoice-pill', 'lojaPill',
  'metaFornecedorListaHtml(l)', 'metaNacionalizadorListaHtml(l)', 'window.JKDataPedidoLista.html(l)',
  'lista-card-quantidade', 'resumoTransporteListaHtml(l)', 'meta-usd', 'meta-brl', 'lista-card-atualizacao',
  'data-commercial-invoice', 'data-menu-lista', 'aria-haspopup="menu"',
]) assert(markup.includes(marker), 'Controle ou informação original ausente: ' + marker);
assert(markup.includes('<button type="button" class="documento-lista-card documento-lista-card--packing" data-packing-list>Packing List</button>'),
  'Packing List deve oferecer upload ou download com semântica de botão, preservando o cartão.');

for (const id of [
  'buscarListasImportacoes', 'btnDensidadeListas', 'tabelaListasImportacoes',
  'contagemListasImportacoes', 'listaWrap', 'menuListaImportacoes',
]) {
  assert.strictEqual(html.split('id="' + id + '"').length - 1, 1, 'ID deve ser único: ' + id);
}
assert(html.includes('class="listas-colunas-cabecalho"'), 'A lista deve ter cabeçalhos para comparar os pedidos.');
assert(html.includes('Dois cliques para editar os campos'), 'A tela deve explicar a edição existente.');
const menuStart = html.indexOf('<div id="menuListaImportacoes"');
const menuEnd = html.indexOf('</div>', menuStart);
const menu = html.slice(menuStart, menuEnd);
for (const marker of ['role="menu"', 'hidden', 'data-menu-abrir', 'data-menu-editar', 'data-menu-excluir']) {
  assert(menu.includes(marker), 'Ação acessível ausente no menu compartilhado: ' + marker);
}
assert(menuStart > html.indexOf('id="tabelaListasImportacoes"'),
  'Menu compartilhado deve permanecer fora da renderização individual dos cartões.');
assert(/#menuListaImportacoes\s*\{[^}]*position:\s*fixed;/.test(html),
  'Menu deve escapar do recorte da área de listas.');
assert(/#listaWrap\s+\.lista-item--compacta\s*\{[^}]*display:\s*grid;/.test(html),
  'Listas devem alinhar as informações em colunas.');
assert(/#listaWrap\s+\.meta-pill\s+strong\s*\{[^}]*text-overflow:\s*ellipsis;/.test(html),
  'Textos longos devem preservar o recorte visual sem alterar o valor.');
assert(/@media[^}]*\{[\s\S]*#listaWrap\s+\.lista-item--compacta\s*\{[^}]*grid-template-areas:/.test(html),
  'A lista deve reorganizar os grupos em telas menores.');

const inlineScripts = [...html.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map(match => match[1]).filter(source => source.trim());
for (const source of inlineScripts) new Function(source);
console.log('OK: lista em colunas com seis campos editáveis, informações preservadas e menu de ações acessível.');
