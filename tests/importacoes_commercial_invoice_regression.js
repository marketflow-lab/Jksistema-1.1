'use strict';

const fs = require('fs');
const path = require('path');

const importacoesPath = path.join(__dirname, '..', 'static', 'importacoes.html');
const detalhePath = path.join(__dirname, '..', 'static', 'importacoes_lista.html');
const importacoes = fs.readFileSync(importacoesPath, 'utf8');
const detalhe = fs.readFileSync(detalhePath, 'utf8');

const requiredImportacoes = [
    'data-commercial-invoice',
    'async function baixarCommercialInvoice(lista, btn)',
    '/commercial-invoice',
    "{ method: 'GET', headers: { ...obterAuthHeaders() } }",
    'await persistirInvoiceLegadoSeNecessario(lista, listaId);',
    'body: JSON.stringify({ numero_invoice: invoiceLocal })',
    "const blob = await resp.blob();",
    'URL.createObjectURL(blob)',
    'Informe o Fornecedor no detalhe da lista',
    'Informe o N° de Invoice no detalhe da lista',
];

for (const snippet of requiredImportacoes) {
    if (!importacoes.includes(snippet)) {
        throw new Error(`Contrato do Commercial Invoice ausente: ${snippet}`);
    }
}

if (!detalhe.includes("'numero_invoice', 'supplier', 'currency'")) {
    throw new Error('O numero da Invoice deve ser persistido no backend junto aos dados logisticos');
}

for (const html of [importacoes, detalhe]) {
    const inlineScripts = [...html.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/gi)]
        .map((match) => match[1])
        .filter((source) => source.trim());
    for (const source of inlineScripts) new Function(source);
}

console.log('OK: Commercial Invoice autenticado, validado e baixado em XLSX.');
