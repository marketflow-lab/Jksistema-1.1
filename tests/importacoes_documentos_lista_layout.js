'use strict';

const fs = require('fs');
const path = require('path');

const htmlPath = path.join(__dirname, '..', 'static', 'importacoes.html');
const html = fs.readFileSync(htmlPath, 'utf8');

const actionColumnStart = html.indexOf('<div class="acoes-lista-coluna">');
const actionColumnEnd = html.indexOf('</div>', html.indexOf('</div>', actionColumnStart) + 1);
if (actionColumnStart < 0 || actionColumnEnd < 0) {
    throw new Error('Coluna de acoes e documentos da lista nao foi encontrada');
}

const actionColumnHtml = html.slice(actionColumnStart, actionColumnEnd);
const deletePosition = actionColumnHtml.indexOf('class="btn-cancelar"');
const documentsPosition = actionColumnHtml.indexOf('class="documentos-lista-cards"');
const commercialPosition = actionColumnHtml.indexOf('Commercial Invoice');
const packingPosition = actionColumnHtml.indexOf('Packing List');

if (!(deletePosition >= 0 && documentsPosition > deletePosition)) {
    throw new Error('Os cards de documentos devem ficar logo abaixo de Excluir lista');
}
if (!(commercialPosition > documentsPosition && packingPosition > commercialPosition)) {
    throw new Error('Commercial Invoice e Packing List devem aparecer lado a lado nessa ordem');
}

const requiredSnippets = [
    '.acoes-lista-coluna {',
    '.documentos-lista-cards {',
    'grid-template-columns: repeat(2, max-content);',
    'justify-content: end;',
    'align-self: flex-end;',
    'width: auto;',
    'min-height: 22px;',
    'padding: 3px 6px;',
    'font-size: 0.62rem;',
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

if (!/@media \(max-width: 760px\)[\s\S]*\.acoes-direita\s*\{[\s\S]*flex-direction:\s*column;[\s\S]*\.acoes-lista-coluna\s*\{[\s\S]*width:\s*100%;/.test(html)) {
    throw new Error('A coluna de acoes deve se reorganizar na largura total em telas pequenas');
}

const inlineScripts = [...html.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/gi)]
    .map((match) => match[1])
    .filter((source) => source.trim());
for (const source of inlineScripts) {
    new Function(source);
}

console.log('OK: Commercial Invoice e Packing List abaixo de Excluir lista.');
