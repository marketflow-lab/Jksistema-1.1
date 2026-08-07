'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static', 'vendas', 'general-sku-report.js'), 'utf8');
const staticHtml = fs.readFileSync(path.join(root, 'static', 'vendas.html'), 'utf8');
const rootHtml = fs.readFileSync(path.join(root, 'vendas.html'), 'utf8');

assert.strictEqual(rootHtml, staticHtml, 'vendas.html raiz e static devem permanecer idênticos');
assert.match(staticHtml, /id="btnRelatorioGeralSkus"/);
assert.match(staticHtml, /\/vendas\/general-sku-report\.js/);
assert.match(staticHtml, /vendas-relatorios-v2/);
assert.match(source, /estoque usa somente o saldo da Loja e nunca soma o Full/);
assert.match(source, /Leitura executiva/);
assert.doesNotMatch(source, /name="sku"|id="generalSkuSku"/i, 'o relatório geral não pode pedir seleção de SKU');

const documentListeners = new Map();
const documentMock = {
    readyState: 'loading',
    addEventListener(type, listener) { documentListeners.set(type, listener); },
    getElementById() { return null; },
    createElement() { throw new Error('DOM não deveria ser criado neste teste'); },
    body: {}
};
const windowMock = {};
const context = {
    window: windowMock,
    document: documentMock,
    URLSearchParams,
    URL,
    console,
    setTimeout,
    clearTimeout
};
vm.createContext(context);
vm.runInContext(source, context, { filename: 'general-sku-report.js' });

const api = windowMock.__jkVendasGeneralSkuReport;
assert.ok(api, 'API de teste do Relatório Geral deve ser publicada');
assert.throws(
    () => api.buildParams({ loja: '__todas', periodo: '12m' }),
    /conta específica/
);
assert.throws(
    () => api.buildParams({ loja: 'JK Peças', periodo: 'personalizado' }),
    /datas inicial e final/
);
assert.throws(
    () => api.buildParams({
        loja: 'JK Peças',
        periodo: 'personalizado',
        dataInicio: '2026-07-31',
        dataFim: '2026-07-01'
    }),
    /posterior/
);

const custom = api.buildParams({
    loja: 'JK Peças',
    periodo: 'personalizado',
    dataInicio: '2026-01-01',
    dataFim: '2026-07-28'
});
assert.strictEqual(custom.get('loja'), 'JK Peças');
assert.strictEqual(custom.get('periodo'), 'personalizado');
assert.strictEqual(custom.get('data_inicio'), '2026-01-01');
assert.strictEqual(custom.get('data_fim'), '2026-07-28');

(async () => {
    const requested = [];
    const saved = [];
    const results = await api.downloadFormats({
        formatos: ['xlsx', 'pdf'],
        params: api.buildParams({ loja: 'JK Peças', periodo: '12m' }),
        headers: { Authorization: 'Bearer test' },
        fetcher: async (url, options) => {
            requested.push({ url, options });
            if (url.includes('formato=xlsx')) {
                return {
                    ok: true,
                    status: 200,
                    headers: { get: () => 'attachment; filename="geral.xlsx"' },
                    blob: async () => ({ size: 123 })
                };
            }
            return {
                ok: false,
                status: 500,
                json: async () => ({ detail: 'Falha controlada no PDF geral' })
            };
        },
        saver: async (_response, fallback) => { saved.push(fallback); }
    });

    assert.strictEqual(requested.length, 2, 'os formatos devem ser tentados separadamente');
    assert.ok(requested[0].url.includes('/api/vendas/relatorios/vendas-estoque-devolucoes/exportar'));
    assert.deepStrictEqual(requested[0].options.headers, { Authorization: 'Bearer test' });
    assert.deepStrictEqual(saved, ['relatorio_geral_skus.xlsx']);
    assert.strictEqual(results[0].success, true);
    assert.strictEqual(results[1].success, false, 'falha do PDF não pode invalidar o XLSX');
    assert.match(results[1].error, /Falha controlada no PDF geral/);
    console.log('vendas general sku report regression: ok');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
