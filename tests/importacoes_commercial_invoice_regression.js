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
    'async function carregarListaCommercialInvoice(listaId)',
    'function pendenciasCommercialInvoice(lista, listaId)',
    'function avisarPendenciasCommercialInvoice(lista, listaId)',
    "btn.textContent = 'Verificando...'",
    'await carregarListaCommercialInvoice(listaId)',
    'if (avisarPendenciasCommercialInvoice(listaAtualizada, listaId)) return;',
    'A Commercial Invoice ainda não pode ser gerada.',
    'Deseja abrir o detalhe da lista agora?',
    "window.location.href = 'importacoes_lista.html?lista_id=' + encodeURIComponent(listaId);",
    'window.alert(mensagem);',
    "Aprove todos os SKUs: faltam ' + totalPendentes",
    '/commercial-invoice',
    "{ method: 'GET', headers: { ...obterAuthHeaders() } }",
    'await persistirInvoiceLegadoSeNecessario(listaAtualizada, listaId);',
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

const feedbackStart = importacoes.indexOf('function itemAprovadoCommercialInvoice(item)');
const feedbackEnd = importacoes.indexOf('async function persistirInvoiceLegadoSeNecessario', feedbackStart);
if (feedbackStart < 0 || feedbackEnd < 0) {
    throw new Error('Funcoes de pre-validacao visivel do Commercial Invoice nao foram encontradas');
}

const statusMessages = [];
const confirmMessages = [];
const fakeWindow = {
    location: { href: '' },
    confirm(message) {
        confirmMessages.push(message);
        return true;
    },
};
const feedbackFunctions = new Function(
    'obterInvoiceLista',
    'setStatus',
    'window',
    `${importacoes.slice(feedbackStart, feedbackEnd)}\nreturn { pendenciasCommercialInvoice, avisarPendenciasCommercialInvoice };`
)(
    (lista) => String(lista && lista.numero_invoice || '').trim(),
    (message) => statusMessages.push(message),
    fakeWindow,
);

const listaPendente = {
    numero_invoice: '',
    supplier: '',
    itens: [
        { compra_aprovada: true, compra_aprovada_em: '2026-08-18T10:00:00' },
        { compra_aprovada: false },
    ],
};
const pendencias = feedbackFunctions.pendenciasCommercialInvoice(listaPendente, 'lista-1');
if (pendencias.length !== 3 || !pendencias.some((item) => item.includes('faltam 1 SKU pendente de 2'))) {
    throw new Error('A pre-validacao deve reunir Invoice, fornecedor e SKUs pendentes em um unico aviso');
}
if (!feedbackFunctions.avisarPendenciasCommercialInvoice(listaPendente, 'lista-1')) {
    throw new Error('Lista pendente deve interromper a geracao do Commercial Invoice');
}
if (confirmMessages.length !== 1 || !confirmMessages[0].includes('Deseja abrir o detalhe da lista agora?')) {
    throw new Error('Pendencias devem aparecer em um dialogo visivel com opcao de abrir o detalhe');
}
if (fakeWindow.location.href !== 'importacoes_lista.html?lista_id=lista-1') {
    throw new Error('A confirmacao deve abrir diretamente o detalhe da lista');
}
if (!statusMessages[0] || !statusMessages[0].includes('Commercial Invoice indisponível')) {
    throw new Error('O status da pagina tambem deve registrar as pendencias');
}

const listaApta = {
    numero_invoice: 'INV-1',
    supplier: 'Fornecedor',
    itens: [{ compra_aprovada: true, compra_aprovada_em: '2026-08-18T10:00:00' }],
};
if (feedbackFunctions.avisarPendenciasCommercialInvoice(listaApta, 'lista-2')) {
    throw new Error('Lista apta nao deve exibir aviso nem bloquear o download');
}

console.log('OK: Commercial Invoice autenticado, validado e baixado em XLSX.');
