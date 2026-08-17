'use strict';

const fs = require('fs');
const path = require('path');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'static', 'importacoes_sku.html'), 'utf8');
const functionStart = html.indexOf('async function buscarLinksConcorrentes(sku)');
const functionEnd = html.indexOf('async function carregarDetalheSku()', functionStart);
if (functionStart < 0 || functionEnd <= functionStart) {
    throw new Error('Funcao buscarLinksConcorrentes nao encontrada.');
}
const functionSource = html.slice(functionStart, functionEnd);
if (functionSource.includes('if (!resp.ok) return vazio;')) {
    throw new Error('Falha da rota de concorrentes ainda esta sendo convertida em dados vazios.');
}
if (!functionSource.includes("const erro = await resp.json().catch(() => ({}));")) {
    throw new Error('A tela deve ler e apresentar o detalhe devolvido pela rota de concorrentes.');
}
if (!functionSource.includes('throw new Error(')) {
    throw new Error('A tela deve propagar a falha para o aviso visivel da pagina.');
}

const buscarLinksConcorrentes = new Function(
    'fetch',
    'normSku',
    'obterAuthHeaders',
    `${functionSource}; return buscarLinksConcorrentes;`,
)(
    async () => ({
        ok: false,
        async json() {
            return { detail: 'Falha controlada da planilha' };
        },
    }),
    (sku) => String(sku || '').trim(),
    () => ({ Authorization: 'Bearer teste' }),
);

buscarLinksConcorrentes('001').then(
    () => {
        throw new Error('Erro HTTP da rota foi convertido indevidamente em precos vazios.');
    },
    (error) => {
        if (error.message !== 'Falha controlada da planilha') {
            throw new Error(`Mensagem da rota nao foi preservada: ${error.message}`);
        }
    },
).then(() => {
    console.log('OK: falhas da consulta de concorrentes tratadas explicitamente.');
}).catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
