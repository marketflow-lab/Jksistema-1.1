'use strict';

const fs = require('fs');
const path = require('path');

const htmlPath = path.join(__dirname, '..', 'static', 'importacoes.html');
const html = fs.readFileSync(htmlPath, 'utf8');

const renderStart = html.indexOf('function renderListas(listas)');
const markupStart = html.indexOf('el.innerHTML =', renderStart);
const markupEnd = html.indexOf('const btnCancelar', markupStart);
if (renderStart < 0 || markupStart < 0 || markupEnd < 0) {
    throw new Error('Markup dos cards de listas nao foi encontrado');
}

const markup = html.slice(markupStart, markupEnd);
const topStart = markup.indexOf('<div class="lista-card-topo">');
const baseStart = markup.indexOf('<div class="lista-card-base">');
if (!(topStart >= 0 && baseStart > topStart)) {
    throw new Error('O card deve ter uma linha superior e outra linha inferior');
}

const topHtml = markup.slice(topStart, baseStart);
const baseHtml = markup.slice(baseStart);
const topOrder = [
    'class="nome-lista"',
    'class="meta-pill meta-invoice"',
    'lojaPill',
    'class="documentos-lista-cards"',
    'Commercial Invoice',
    'Packing List',
    'class="btn-cancelar lista-card-excluir">Excluir',
];
let lastPosition = -1;
for (const marker of topOrder) {
    const position = topHtml.indexOf(marker);
    if (position <= lastPosition) {
        throw new Error(`Ordem invalida na linha superior: ${marker}`);
    }
    lastPosition = position;
}

const baseOrder = [
    'class="status"',
    'lista-card-quantidade',
    'meta-m3',
    'meta-usd',
    'meta-brl',
    'lista-card-atualizacao',
];
lastPosition = -1;
for (const marker of baseOrder) {
    const position = baseHtml.indexOf(marker);
    if (position <= lastPosition) {
        throw new Error(`Ordem invalida na linha inferior: ${marker}`);
    }
    lastPosition = position;
}

for (const forbidden of ['meta-invoice', 'lojaPill', 'Commercial Invoice', 'Packing List', 'lista-card-excluir']) {
    if (baseHtml.includes(forbidden)) {
        throw new Error(`Elemento da linha superior apareceu na linha inferior: ${forbidden}`);
    }
}

const requiredSnippets = [
    '.lista-item--compacta {',
    '.lista-card-topo {',
    '.lista-card-acoes {',
    '.lista-card-base {',
    '.lista-card-excluir {',
    '.lista-card-atualizacao {',
    'flex-wrap: nowrap;',
    '.documentos-lista-cards {',
    'grid-template-columns: repeat(2, max-content);',
    'justify-content: end;',
    'width: auto;',
    'min-height: 22px;',
    'padding: 3px 6px;',
    'font-size: 0.62rem;',
    'function formatarDataHoraCompacta(isoTexto)',
    '.documento-lista-card--commercial {',
    '.documento-lista-card--packing {',
    'aria-label="Documentos da lista"',
    '.meta-loja-input, .documentos-lista-cards',
];

for (const snippet of requiredSnippets) {
    if (!html.includes(snippet)) {
        throw new Error(`Trecho obrigatorio ausente: ${snippet}`);
    }
}

if (!/@media \(max-width: 760px\)[\s\S]*\.lista-card-topo,[\s\S]*\.lista-card-base\s*\{[\s\S]*flex-wrap:\s*wrap;[\s\S]*\.lista-card-acoes\s*\{[\s\S]*width:\s*100%;/.test(html)) {
    throw new Error('As duas linhas do card devem se reorganizar em telas pequenas');
}

const inlineScripts = [...html.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/gi)]
    .map((match) => match[1])
    .filter((source) => source.trim());
for (const source of inlineScripts) {
    new Function(source);
}

console.log('OK: nome, Invoice, loja e acoes no topo; demais dados na linha inferior.');
