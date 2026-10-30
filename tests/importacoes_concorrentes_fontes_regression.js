'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const root = path.resolve(__dirname, '..');
const listHtml = fs.readFileSync(path.join(root, 'static/importacoes_lista.html'), 'utf8');
const skuHtml = fs.readFileSync(path.join(root, 'static/importacoes_sku.html'), 'utf8');

function sourceFunction(html, name) {
    const match = new RegExp('^        (?:async )?function ' + name + '\\(', 'm').exec(html);
    assert(match, 'Função real ausente: ' + name);
    const end = html.indexOf('\n        }', match.index);
    assert(end > match.index, 'Fim da função ausente: ' + name);
    return html.slice(match.index, end + '\n        }'.length);
}

function loadFunctions(html, names, bindings) {
    const context = vm.createContext(bindings);
    vm.runInContext(names.map(name => sourceFunction(html, name)).join('\n'), context);
    return context;
}

function response(body, status = 200) {
    return { ok: status >= 200 && status < 300, status, json: async () => body };
}

function deferred() {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    return { promise, resolve };
}

function sourceRow(version = 2) {
    const row = { fontes_versao: version, concorrentes: {}, concorrentes_valores: {},
        concorrentes_mlb: {}, concorrentes_preco: {} };
    for (let n = 1; n <= 5; n++) {
        const key = 'concorrente_' + n;
        row.concorrentes[key] = 'https://produto.mercadolivre.com.br/MLB-' + (2000000000 + n);
        row.concorrentes_valores[key] = 'R$ ' + (80 + n) + ',50';
        row.concorrentes_mlb[key] = 'https://www.mercadolivre.com.br/p/MLB99999999?wid=MLB' + (1000000000 + n);
        row.concorrentes_preco[key] = 'R$ ' + (90 + n) + ',00';
    }
    return row;
}

function listHarness(loja = 'JK Peças') {
    const state = { generation: 1, listaId: 'JK61', gets: [], patches: [], statuses: [],
        sources: {}, fail: false, beforeGet: null, beforePatch: null };
    const bindings = {
        AUXILIAR_TIMEOUT_MS: 20000,
        ATUALIZACAO_LISTA_LIMITE_MS: 120000,
        AbortController,
        setTimeout,
        clearTimeout,
        controladoresAuxiliares: new Set(),
        estadosAnaliseMargensAutomaticas: new Map(),
        cicloAnaliseMargensAutomaticas: 0,
        ultimaListaCarregada: { loja },
        obterAuthHeaders: () => ({ Authorization: 'synthetic' }),
        obterSkuItemMargensAutomaticas: item => String(item.SKU || '').trim().toUpperCase(),
        contextoCarregamentoAtual: listaId => ({ listaId, generation: state.generation }),
        carregamentoVigente: context => context.listaId === state.listaId && context.generation === state.generation,
        confirmarMutacaoLista: () => {},
        definirEstadoMargensAutomaticas: (item, status, message) => state.statuses.push([item.SKU, status, message]),
        fetch: async (url, options) => {
            if (options.method === 'GET') {
                state.gets.push(url);
                if (state.beforeGet) await state.beforeGet();
                return state.fail ? response({ detail: 'Planilha indisponível' }, 503)
                    : response({ resultados: state.sources });
            }
            assert.strictEqual(options.method, 'PATCH');
            const body = JSON.parse(options.body);
            state.patches.push({ url, body });
            if (state.beforePatch) await state.beforePatch();
            return response({ analise_concorrentes: { fontes_versao: body.fontes_versao, margens: {} } });
        },
    };
    const context = loadFunctions(listHtml, [
        'normTxt', 'valorNumerico', 'extrairCodigoMlbMargensAutomaticas',
        'montarPayloadMargensAutomaticas', 'consultarAnaliseLista', 'carregarFontesMargensAutomaticas',
        'salvarMargensAutomaticasSku', 'analisarMargensConcorrentesAutomaticamente',
    ], bindings);
    return { state, context };
}

async function verifySkuContextAndVersion() {
    const calls = [];
    const cells = new Map();
    const current = { listaId: 'JK61 / A&B', sku: 'D104-32' };
    const validity = { data: true };
    const bindings = {
        ultimaVersaoFontesConcorrentes: null,
        ultimosAnunciosLojaConcorrentes: {},
        contextoDetalheSku: current,
        analiseComplementosPronta: true,
        custoComplementosPendente: false,
        detalheSkuVigente: context => context === bindings.contextoDetalheSku,
        dadosSkuVigentes: context => context === bindings.contextoDetalheSku && validity.data,
        obterAuthHeaders: () => ({}),
        document: { getElementById: id => cells.get(id) || null },
        setLinkConcorrente: () => {},
        normalizarDetalhePrecoOferta: value => value,
        aplicarDetalhePrecoOferta: (kind, number, value) => {
            cells.set((kind === 'mlb' ? 'vMlb' : 'vConcorrente') + number, { textContent: value });
        },
        consultarJsonDetalheSku: async (url, context, options) => {
            assert.strictEqual(context, bindings.contextoDetalheSku);
            calls.push({ url, context, options });
            return options ? { analise_concorrentes: {} } : sourceRow(context === current ? 2 : 1);
        },
        fetch: async url => {
            calls.push({ url });
            return response(sourceRow(1));
        },
    };
    const context = loadFunctions(skuHtml, [
        'normSku', 'getNumber', 'extrairCodigoMlbOferta', 'buscarLinksConcorrentes',
        'aplicarLinksCacheSku', 'coletarAnaliseConcorrentes', 'salvarAnaliseConcorrentes',
    ], bindings);
    const sources = await context.buscarLinksConcorrentes('D104-32', current);
    const requested = new URL(calls[0].url, 'http://fixture');
    assert.strictEqual(requested.searchParams.get('lista_id'), current.listaId);
    assert.strictEqual(requested.searchParams.get('sku'), 'D104-32');
    assert.strictEqual(sources.fontes_versao, 2);
    context.aplicarLinksCacheSku(sources);
    await context.salvarAnaliseConcorrentes(current.listaId, current.sku);
    const patch = JSON.parse(calls[1].options.body);
    assert.strictEqual(calls[1].options.method, 'PATCH');
    assert.strictEqual(patch.fontes_versao, 2, 'A origem consultada acompanha a análise salva.');
    for (let n = 1; n <= 5; n++) {
        assert.strictEqual(patch.anuncios_loja['concorrente_' + n], 'MLB' + (1000000000 + n));
        assert.strictEqual(patch.precos_concorrentes['concorrente_' + n], 80.5 + n);
        assert.strictEqual(patch.precos_anuncios_loja['concorrente_' + n], 90 + n);
    }
    await assert.rejects(context.salvarAnaliseConcorrentes('outra-lista', current.sku), /carregamento completo/);
    validity.data = false;
    await assert.rejects(context.salvarAnaliseConcorrentes(current.listaId, current.sku), /carregamento completo/);
    validity.data = true;
    current.podeSalvar = false;
    await assert.rejects(context.salvarAnaliseConcorrentes(current.listaId, current.sku), /carregamento completo/);
    delete current.podeSalvar;
    assert.strictEqual(calls.length, 2, 'Outro contexto não pode salvar a análise carregada.');

    const legacy = await context.buscarLinksConcorrentes('001');
    assert.strictEqual(new URL(calls[2].url, 'http://fixture').searchParams.get('lista_id'), null);
    assert.strictEqual(legacy.fontes_versao, 1, 'A chamada antiga sem lista continua suportada.');
    context.aplicarLinksCacheSku({ ...sources, fontes_versao: undefined });
    assert.strictEqual(context.coletarAnaliseConcorrentes().fontes_versao, null,
        'Snapshot antigo não pode herdar a versão da consulta anterior.');
}

async function verifyRefreshAndReuse() {
    const { state, context } = listHarness();
    const empty = { SKU: 'D104-32', analise_concorrentes: { margens: {}, atualizado_em: 'anterior' } };
    const noMatch = { SKU: 'SEM-FONTE', analise_concorrentes: { margens: {} } };
    const populated = { SKU: '167', analise_concorrentes: { margens: { concorrente_1: { margem_percentual: 17.65 } } } };
    const alreadyRead = { SKU: 'ATUAL', analise_concorrentes: { fontes_versao: 2, margens: {} } };
    const missing = { SKU: 'NOVO' };
    state.sources = { 'D104-32': sourceRow(), 'SEM-FONTE': { fontes_versao: 2 },
        NOVO: sourceRow() };
    const oldPopulated = populated.analise_concorrentes;
    const items = [empty, noMatch, populated, alreadyRead, missing];
    await context.analisarMargensConcorrentesAutomaticamente('JK61', items);
    assert.strictEqual(state.gets.length, 1);
    assert.strictEqual(state.patches.length, 3);
    assert.deepStrictEqual(state.patches.map(call => decodeURIComponent(call.url.split('/skus/')[1].split('/')[0])),
        ['D104-32', 'SEM-FONTE', 'NOVO']);
    assert.strictEqual(empty.analise_concorrentes.fontes_versao, 2);
    assert.strictEqual(noMatch.analise_concorrentes.fontes_versao, 2);
    assert.deepStrictEqual(state.patches[1].body.precos_concorrentes, {},
        'Sem preço na fonte continua sem preço; não cria zero.');
    assert.strictEqual(populated.analise_concorrentes, oldPopulated,
        'Margens preenchidas da fonte atual não são substituídas.');
    await context.analisarMargensConcorrentesAutomaticamente('JK61', items);
    assert.strictEqual(state.gets.length, 1, 'A revisão salva evita consultas repetidas na reabertura.');
    assert.strictEqual(state.patches.length, 3);
}

async function verifyOtherStoresAndFailure() {
    const other = listHarness('Outra loja');
    await other.context.analisarMargensConcorrentesAutomaticamente('JK61',
        [{ SKU: 'D104-32', analise_concorrentes: { margens: {} } }]);
    assert.strictEqual(other.state.gets.length, 0, 'A migração da fonte JK não renova análises de outra loja.');

    const { state, context } = listHarness();
    const saved = { margens: {}, atualizado_em: 'original' };
    const item = { SKU: 'D104-32', analise_concorrentes: saved };
    state.fail = true;
    await context.analisarMargensConcorrentesAutomaticamente('JK61', [item]);
    assert.strictEqual(state.patches.length, 0, 'Falha da fonte não pode persistir uma análise vazia.');
    assert.strictEqual(item.analise_concorrentes, saved);
    assert(state.statuses.some(row => row[1] === 'erro' && row[2] === 'Planilha indisponível'));
    state.fail = false;
    state.sources = { 'D104-32': sourceRow() };
    await context.analisarMargensConcorrentesAutomaticamente('JK61', [item]);
    assert.strictEqual(state.patches.length, 1, 'Uma consulta que falhou permanece disponível para nova tentativa.');
}

async function verifyCanonicalStoreRevision() {
    const cases = [
        { loja: 'Nome anterior da loja', revision: 2, expectedReads: 1 },
        { loja: 'JK Peças', revision: 1, expectedReads: 0 },
        { loja: 'JK Peças', revision: 0, expectedReads: 0 },
    ];
    for (const testCase of cases) {
        const { state, context } = listHarness(testCase.loja);
        context.ultimaListaCarregada.analise_concorrentes_fontes_versao = testCase.revision;
        state.sources = { 'D104-32': sourceRow() };
        const saved = { margens: {} };
        const item = { SKU: 'D104-32', analise_concorrentes: saved };
        await context.analisarMargensConcorrentesAutomaticamente('JK61', [item]);
        assert.strictEqual(state.gets.length, testCase.expectedReads,
            'A revisão canônica ' + testCase.revision + ' prevalece sobre o nome salvo da loja.');
        assert.strictEqual(state.patches.length, testCase.expectedReads);
        if (testCase.expectedReads) {
            assert.strictEqual(item.analise_concorrentes.fontes_versao, 2,
                'JK Peças com nome legado recebe a nova fonte pela identidade canônica.');
        } else {
            assert.strictEqual(item.analise_concorrentes, saved,
                'Outra loja ou escopo irresolvido não recebe a migração da JK pelo nome antigo.');
        }
    }
}

async function verifyLateResponses() {
    const { state, context } = listHarness();
    const wait = deferred();
    const saved = { margens: {} };
    const item = { SKU: 'D104-32', analise_concorrentes: saved };
    state.sources = { 'D104-32': sourceRow() };
    state.beforeGet = () => wait.promise;
    const pending = context.analisarMargensConcorrentesAutomaticamente('JK61', [item]);
    state.listaId = 'OUTRA';
    state.generation++;
    wait.resolve();
    await pending;
    assert.strictEqual(state.patches.length, 0, 'Fonte de um contexto abandonado não pode iniciar PATCH.');
    assert.strictEqual(item.analise_concorrentes, saved);

    const second = listHarness();
    const patchWait = deferred();
    const patchStarted = deferred();
    second.state.sources = { 'D104-32': sourceRow() };
    second.state.beforePatch = () => { patchStarted.resolve(); return patchWait.promise; };
    const latePatch = second.context.analisarMargensConcorrentesAutomaticamente('JK61', [item]);
    await patchStarted.promise;
    second.state.generation++;
    patchWait.resolve();
    await latePatch;
    assert.strictEqual(item.analise_concorrentes, saved, 'PATCH tardio não altera a tela de outra geração.');
}

(async () => {
    await verifySkuContextAndVersion();
    await verifyRefreshAndReuse();
    await verifyOtherStoresAndFailure();
    await verifyCanonicalStoreRevision();
    await verifyLateResponses();
    console.log('OK: fontes por lista, cinco pares, revisão de análises vazias, reutilização e respostas tardias.');
})().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
