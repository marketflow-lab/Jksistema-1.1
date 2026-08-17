'use strict';

const fs = require('fs');
const path = require('path');

const htmlPath = path.join(__dirname, '..', 'static', 'importacoes_lista.html');
const html = fs.readFileSync(htmlPath, 'utf8');

const inlineScripts = [...html.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/gi)]
    .map((match) => match[1])
    .filter((source) => source.trim());
for (const source of inlineScripts) {
    new Function(source);
}

const requiredSnippets = [
    '.sku-actions {',
    '.btn-aprovar-sku {',
    'function itemComCompraAprovada(item)',
    'function aprovarSkuDaLista(sku, btn)',
    'data-aprovar-sku=',
    "method: 'PATCH'",
    'body: JSON.stringify({ aprovada: true })',
    'Desfaça a aprovação dentro do SKU para permitir a exclusão',
    'itemComCompraAprovada(itemAtual)',
];

for (const snippet of requiredSnippets) {
    if (!html.includes(snippet)) {
        throw new Error(`Trecho obrigatorio ausente: ${snippet}`);
    }
}

const actionStart = html.indexOf("acao: sku");
const actionEnd = html.indexOf(" : '-',", actionStart);
const actionHtml = html.slice(actionStart, actionEnd);
const deletePosition = actionHtml.indexOf('data-excluir-sku=');
const approvePosition = actionHtml.indexOf('data-aprovar-sku=');
if (!(deletePosition >= 0 && approvePosition > deletePosition)) {
    throw new Error('Aprovar deve ficar logo abaixo de Excluir SKU na coluna de acao');
}

if (!/compraAprovada\s*\?\s*' disabled aria-disabled="true"/.test(actionHtml)) {
    throw new Error('Excluir SKU deve ficar desabilitado quando a compra estiver aprovada');
}

console.log('OK: aprovacao por linha e bloqueio visual de exclusao validados.');
