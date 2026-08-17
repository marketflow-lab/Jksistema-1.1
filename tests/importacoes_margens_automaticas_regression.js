'use strict';

const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'static', 'importacoes_lista.html'), 'utf8');
const scripts = [...html.matchAll(/<script(?: [^>]*)?>([\s\S]*?)<\/script>/gi)]
    .map((match) => match[1])
    .filter((source) => source.trim());

for (const source of scripts) {
    try {
        new Function(source);
    } catch (error) {
        throw new Error(`JavaScript inline inválido em importacoes_lista.html: ${error.message}`);
    }
}

const obrigatorios = [
    'function analisarMargensConcorrentesAutomaticamente(listaId, itens)',
    'function carregarFontesMargensAutomaticas(listaId)',
    "'/concorrentes-links'",
    "definirEstadoMargensAutomaticas(item, 'fila')",
    "definirEstadoMargensAutomaticas(item, 'analisando')",
    'for (const item of pendentes)',
    'await salvarMargensAutomaticasSku(',
    "item.analise_concorrentes = resultado && resultado.analise_concorrentes",
    'data-coluna="margens_concorrentes"',
    'void analisarMargensConcorrentesAutomaticamente(listaId, ultimosItensCarregados);',
    "(!item.analise_concorrentes || typeof item.analise_concorrentes !== 'object')",
    'Falha na análise',
    'Analisando...',
];

for (const trecho of obrigatorios) {
    if (!html.includes(trecho)) {
        throw new Error(`Contrato da análise automática ausente: ${trecho}`);
    }
}

const renderInicial = html.indexOf('renderItens(itens, lista);');
const disparoAutomatico = html.indexOf(
    'void analisarMargensConcorrentesAutomaticamente(listaId, ultimosItensCarregados);',
    renderInicial
);
if (!(renderInicial >= 0 && disparoAutomatico > renderInicial)) {
    throw new Error('A lista deve ser renderizada antes de iniciar a análise automática.');
}

const inicioFila = html.indexOf('async function analisarMargensConcorrentesAutomaticamente');
const inicioLoop = html.indexOf('for (const item of pendentes)', inicioFila);
const esperaSku = html.indexOf('await salvarMargensAutomaticasSku(', inicioLoop);
if (!(inicioFila >= 0 && inicioLoop > inicioFila && esperaSku > inicioLoop)) {
    throw new Error('Os SKUs devem ser processados sequencialmente para preservar a persistência da lista.');
}

console.log('OK: margens não analisadas são calculadas automaticamente e atualizadas linha a linha.');
