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
    const closing = /^        }[ \t]*\r?$/m.exec(html.slice(match.index));
    assert(closing, 'Fim da função ausente: ' + name);
    return html.slice(match.index, match.index + closing.index + closing[0].length);
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

function ownRevision() {
    return { anuncios_versao: 2, anuncios_loja: [], anuncios_consultado_em_ms: Date.now() };
}

function installMarginCache(window) {
    const entries = new Map(), session = {};
    Object.assign(window, { setTimeout, CustomEvent: class { constructor(type, init) { this.type = type; this.detail = init.detail; } },
        dispatchEvent: () => {}, addEventListener: () => {}, localStorage: { setItem: () => {} },
        JKCachePersistente: { contexto: () => session, vigente: () => true, podeManterDados: () => true,
            chaveEscopo: async () => 'fixture', ler: async key => entries.has(key) ? { dados: structuredClone(entries.get(key)) } : null,
            atualizar: async (key, update) => { entries.set(key, structuredClone(update(entries.get(key)))); return true; } } });
    vm.runInNewContext(fs.readFileSync(path.join(root, 'static/medias_compras/importacoes-cache-margens.js'), 'utf8'), { window });
}

function sourceRow(version = 2) {
    const row = { fontes_versao: version, anuncios_loja_fonte: 'loja_lista_sku', anuncios_loja_store_id: 'store-lista',
        anuncios_loja_seller_id: 'seller-lista', anuncios_loja_site_id: 'MLB', concorrentes: {}, concorrentes_valores: {},
        concorrentes_mlb: {}, concorrentes_preco: {}, anuncios_versao: 2,
        anuncios_loja: [], anuncios_loja_variacoes: {} };
    for (let n = 1; n <= 5; n++) {
        const key = 'concorrente_' + n;
        row.concorrentes[key] = 'https://produto.mercadolivre.com.br/MLB-' + (2000000000 + n);
        row.concorrentes_valores[key] = 'R$ ' + (80 + n) + ',50';
        row.concorrentes_mlb[key] = 'https://www.mercadolivre.com.br/p/MLB99999999?wid=MLB' + (1000000000 + n);
        row.concorrentes_preco[key] = 'R$ ' + (90 + n) + ',00';
        row.anuncios_loja_variacoes[key] = String(100 + n);
        row.anuncios_loja.push({ chave: key, item_id: 'MLB' + (1000000000 + n),
            preco: 90 + n, variation_id: String(100 + n), status: 'active', sku: 'D104-32' });
    }
    return row;
}

function listHarness(loja = 'JK Peças') {
    const state = { generation: 1, listaId: 'JK61', gets: [], ownGets: [], patches: [], statuses: [],
        sources: {}, fail: false, beforeGet: null, beforePatch: null };
    const bindings = {
        AUXILIAR_TIMEOUT_MS: 20000,
        ATUALIZACAO_LISTA_LIMITE_MS: 120000,
        AbortController,
        setTimeout: (callback, delay) => setTimeout(callback, delay === 1000 ? 0 : delay),
        clearTimeout,
        controladoresAuxiliares: new Set(),
        estadosAnaliseMargensAutomaticas: new Map(),
        cicloAnaliseMargensAutomaticas: 0,
        cacheMargensLista: {}, filaMargensLista: null, ultimosItensCarregados: [],
        agendarAtualizacaoMargensLista: () => {},
        ultimaListaCarregada: { loja, store_id: 'store-lista' },
        calculosUnitariosLista: {},
        chaveSkuCustoCadastro: sku => String(sku || '').trim().toUpperCase(),
        document: { querySelectorAll: () => [] }, innerHeight: 900,
        window: { JKImportacoesCacheCustos: { comTravaAnalise: async (_options, executar) => executar() } },
        salvarSnapshotLista: async () => {},
        obterAuthHeaders: () => ({ Authorization: 'synthetic' }),
        obterSkuItemMargensAutomaticas: item => String(item.SKU || '').trim().toUpperCase(),
        contextoCarregamentoAtual: listaId => ({ listaId, generation: state.generation }),
        carregamentoVigente: context => context.listaId === state.listaId && context.generation === state.generation,
        confirmarMutacaoLista: () => {},
        definirEstadoMargensAutomaticas: (item, status, message) => state.statuses.push([item.SKU, status, message]),
        atualizarCelulaMargensAutomaticas: () => {},
        fetch: async (url, options) => {
            if (url.endsWith('/configuracao-margens')) return response({ concorrencia_lista: 2 });
            if (options.method === 'GET') {
                const query = new URL(url, 'http://fixture').searchParams;
                if (query.get('somente_proprios') === 'true') {
                    state.ownGets.push(url);
                    return response({ ...(state.sources[query.get('sku')] || {}), anuncios_loja_fonte: 'loja_lista_sku',
                        anuncios_loja_store_id: 'store-lista', anuncios_loja: state.sources[query.get('sku')]?.anuncios_loja || [] });
                }
                state.gets.push(url);
                if (state.beforeGet) await state.beforeGet();
                return state.fail ? response({ detail: 'Planilha indisponível' }, 503)
                    : response(state.sources[new URL(url, 'http://fixture').searchParams.get('sku')] || {
                        fontes_versao: 2, anuncios_loja_fonte: 'loja_lista_sku', anuncios_loja_store_id: 'store-lista',
                        concorrentes_valores: {},
                    });
            }
            assert.strictEqual(options.method, 'PATCH');
            const body = JSON.parse(options.body);
            state.patches.push({ url, body });
            if (state.beforePatch) await state.beforePatch();
            const sku = decodeURIComponent(url.split('/skus/')[1].split('/')[0]);
            const fonte = state.sources[sku] || {};
            const margens = precos => Object.fromEntries(Object.entries(precos || {}).map(([key, preco]) => [key, {
                    preco_venda: preco, margem_percentual: 25, item_id_loja: body.anuncios_loja[key], store_id: 'store-lista',
                    fonte_custo: body.fonte_custo, custo_unitario: body.custo_unitario, financeiro_exato: true,
                    variation_id: body.variacoes_anuncios_loja?.[key] ?? null,
                    seller_id: fonte.anuncios_loja_seller_id || '', site_id: fonte.anuncios_loja_site_id || '',
                }]));
            return response({ analise_concorrentes: { ...body, ...ownRevision(), store_id: 'store-lista', atualizado_em: new Date().toISOString(),
                anuncios_loja_store_id: 'store-lista', anuncios_loja: fonte.anuncios_loja || [],
                margens: margens(body.precos_concorrentes), margens_anuncios_loja: margens(body.precos_anuncios_loja) } });
        },
    };
    installMarginCache(bindings.window);
    const context = loadFunctions(listHtml, [
        'normTxt', 'valorNumerico', 'extrairCodigoMlbMargensAutomaticas',
        'montarPayloadMargensAutomaticas', 'consultarAnaliseLista', 'carregarFontesMargensAutomaticas',
        'analiseAnunciosLojaConfere', 'anunciosLojaAnaliseRecentes', 'carregarAnunciosMargensAutomaticas',
        'analiseSimuladaListaCompativel', 'analiseConfereFontesLista', 'salvarMargensAutomaticasSku',
        'obterConcorrenciaMargensLista', 'analisarMargensConcorrentesAutomaticamente',
        'opcoesCacheMargensLista', 'restaurarMargensCompartilhadasLista', 'priorizarPendentesMargens',
    ], bindings);
    const analisar = context.analisarMargensConcorrentesAutomaticamente;
    context.analisarMargensConcorrentesAutomaticamente = (id, itens) => {
        context.ultimosItensCarregados = itens;
        for (const item of itens) context.calculosUnitariosLista[item.SKU] ||= {
            calculo: { custoUnitario: 23.75 }, revisao: 'a'.repeat(64), assinatura: 'b'.repeat(64),
        };
        return analisar(id, itens);
    };
    return { state, context };
}

async function verifySkuContextAndVersion() {
    const calls = [];
    const cells = new Map();
    const current = { listaId: 'JK61 / A&B', sku: 'D104-32', storeId: 'store-lista', lojaNome: 'Loja da lista' };
    const validity = { data: true };
    const custoSimuladoPath = path.join(root, 'static', 'medias_compras', 'importacoes-custo-simulado.js');
    const contextoCusto = { window: {} };
    vm.runInNewContext(fs.readFileSync(custoSimuladoPath, 'utf8'), contextoCusto, { filename: custoSimuladoPath });
    const bindings = {
        ultimaVersaoFontesConcorrentes: null,
        ultimosAnunciosLojaConcorrentes: {},
        ultimasVariacoesAnunciosLoja: {},
        ultimoAnuncioPausadoReferencia: '',
        contextoDetalheSku: current,
        analiseComplementosPronta: true,
        custoComplementosPendente: false,
        ultimoValorUnitarioProduto: 14.20,
        detalheSkuVigente: context => context === bindings.contextoDetalheSku,
        dadosSkuVigentes: context => context === bindings.contextoDetalheSku && validity.data,
        obterAuthHeaders: () => ({}),
        document: { getElementById: id => cells.get(id) || null, querySelector: () => null },
        getNumber: contextoCusto.window.JKImportacoesCustoSimulado.numero,
        window: { JKImportacoesCacheCustos: { comTravaAnalise: async (options, executar) => {
            assert.strictEqual(options.listaId, current.listaId);
            assert.strictEqual(typeof executar, 'function');
            return executar();
        } } },
        setLinkConcorrente: () => {},
        atualizarTodasMargensConcorrentes: () => {},
        normalizarDetalhePrecoOferta: value => value,
        aplicarDetalhePrecoOferta: (kind, number, value) => {
            cells.set((kind === 'mlb' ? 'vMlb' : 'vConcorrente') + number, { textContent: value });
        },
        consultarJsonDetalheSku: async (url, context, options) => {
            assert.strictEqual(context, bindings.contextoDetalheSku);
            calls.push({ url, context, options });
            return options?.method === 'PATCH' ? { analise_concorrentes: {} } : {
                ...sourceRow(context === current ? 2 : 1), anuncios_loja_store_id: current.storeId,
                anuncios_loja_nome: current.lojaNome, anuncios_loja_fonte: 'loja_lista_sku',
            };
        },
        fetch: async url => {
            calls.push({ url });
            return response(sourceRow(1));
        },
    };
    const context = loadFunctions(skuHtml, [
        'normSku', 'extrairCodigoMlbOferta', 'buscarLinksConcorrentes', 'fontesDadosParaLinksSku',
        'fontesAnunciosDaLista', 'custoSimuladoDisponivel',
        'numerosComparacoesAnalise', 'prepararCartoesAnalise',
        'aplicarLinksCacheSku', 'coletarAnaliseConcorrentes', 'salvarAnaliseConcorrentes',
        'opcoesCacheMargensSku', 'identidadeFontesSku',
    ], bindings);
    const sources = await context.buscarLinksConcorrentes('D104-32', current);
    const requested = new URL(calls[0].url, 'http://fixture');
    assert.strictEqual(requested.searchParams.get('lista_id'), current.listaId);
    assert.strictEqual(requested.searchParams.get('sku'), 'D104-32');
    assert.strictEqual(requested.searchParams.get('anuncios_da_lista'), 'true');
    assert.strictEqual(sources.fontes_versao, 2);
    context.aplicarLinksCacheSku(sources);
    await context.salvarAnaliseConcorrentes(current.listaId, current.sku);
    const patch = JSON.parse(calls[1].options.body);
    assert.strictEqual(calls[1].options.method, 'PATCH');
    assert.strictEqual(patch.fontes_versao, 2, 'A origem consultada acompanha a análise salva.');
    assert.strictEqual(patch.fonte_custo, 'simulacao_importacao_sku');
    assert.strictEqual(patch.custo_unitario, 14.20);
    for (let n = 1; n <= 5; n++) {
        assert.strictEqual(patch.anuncios_loja['concorrente_' + n], 'MLB' + (1000000000 + n));
        assert.strictEqual(patch.precos_concorrentes['concorrente_' + n], 80.5 + n);
        assert.strictEqual(patch.precos_anuncios_loja['concorrente_' + n], 90 + n);
        assert.strictEqual(patch.variacoes_anuncios_loja['concorrente_' + n], String(100 + n));
    }
    await assert.rejects(context.salvarAnaliseConcorrentes('outra-lista', current.sku), /custo unitário simulado/);
    validity.data = false;
    await assert.rejects(context.salvarAnaliseConcorrentes(current.listaId, current.sku), /custo unitário simulado/);
    validity.data = true;
    current.podeSalvar = false;
    await assert.rejects(context.salvarAnaliseConcorrentes(current.listaId, current.sku), /custo unitário simulado/);
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
    const populated = { SKU: '167', analise_concorrentes: { ...ownRevision(), margens: { concorrente_1: { margem_percentual: 17.65 } } } };
    const alreadyRead = { SKU: 'ATUAL', analise_concorrentes: { ...ownRevision(), fontes_versao: 2, margens: {} } };
    const missing = { SKU: 'NOVO' };
    state.sources = { 'D104-32': sourceRow(), 'SEM-FONTE': { ...sourceRow(), concorrentes_valores: {} },
        NOVO: sourceRow(), '167': sourceRow(), ATUAL: sourceRow() };
    const oldPopulated = populated.analise_concorrentes;
    const items = [empty, noMatch, populated, alreadyRead, missing];
    await context.analisarMargensConcorrentesAutomaticamente('JK61', items);
    assert.strictEqual(state.gets.length, 5);
    assert.strictEqual(state.patches.length, 5);
    assert.deepStrictEqual(state.patches.map(call => decodeURIComponent(call.url.split('/skus/')[1].split('/')[0])),
        ['D104-32', 'SEM-FONTE', '167', 'ATUAL', 'NOVO']);
    assert.strictEqual(empty.analise_simulacao_importacao_sku.fontes_versao, 2);
    assert.strictEqual(noMatch.analise_simulacao_importacao_sku.fontes_versao, 2);
    assert.strictEqual(state.patches[0].body.fonte_custo, 'simulacao_importacao_sku');
    assert.strictEqual(state.patches[0].body.custo_unitario, 23.75);
    assert.strictEqual(state.patches[0].body.revisao_fontes_custo, 'a'.repeat(64));
    assert.strictEqual(state.patches[0].body.variacoes_anuncios_loja.concorrente_5, '105');
    assert.strictEqual(state.patches[0].body.precos_anuncios_loja.concorrente_5, 95);
    assert.strictEqual(state.ownGets.length, 0, 'A consulta por SKU fornece fontes e anúncios juntos quando disponível.');
    assert.deepStrictEqual(state.patches[1].body.precos_concorrentes, {},
        'Sem preço na fonte continua sem preço; não cria zero.');
    assert.strictEqual(populated.analise_concorrentes, oldPopulated,
        'A simulação preserva análises legadas de Cadastro para outros consumidores.');
    await context.analisarMargensConcorrentesAutomaticamente('JK61', items);
    assert.strictEqual(state.gets.length, 10, 'Preços consultados confirmam a assinatura das margens salvas.');
    assert.strictEqual(state.patches.length, 5, 'Mesmo custo, fonte e preços dispensam novo cálculo financeiro.');
}

async function verifyOtherStoresAndFailure() {
    const other = listHarness('Outra loja');
    await other.context.analisarMargensConcorrentesAutomaticamente('JK61',
        [{ SKU: 'D104-32', analise_concorrentes: { margens: {} } }]);
    assert.strictEqual(other.state.gets.length, 1, 'A identidade da loja, e não seu nome, governa a simulação.');

    const { state, context } = listHarness();
    const saved = { margens: {}, atualizado_em: 'original' };
    const item = { SKU: 'D104-32', analise_simulacao_importacao_sku: saved };
    state.fail = true;
    await context.analisarMargensConcorrentesAutomaticamente('JK61', [item]);
    assert.strictEqual(state.patches.length, 0, 'Falha da fonte não pode persistir uma análise vazia.');
    assert.strictEqual(state.ownGets.length, 2, 'Retentativas da planilha preservam a leitura independente dos anúncios próprios.');
    assert.strictEqual(item.analise_simulacao_importacao_sku, saved);
    assert(state.statuses.some(row => row[1] === 'erro' && row[2] === 'Planilha indisponível'));
    state.fail = false;
    state.sources = { 'D104-32': sourceRow() };
    await context.analisarMargensConcorrentesAutomaticamente('JK61', [item]);
    assert.strictEqual(state.patches.length, 1, 'Uma consulta que falhou permanece disponível para nova tentativa.');
}

async function verifyCanonicalStoreRevision() {
    const { state, context } = listHarness('Nome antigo da loja');
    state.sources = { 'D104-32': sourceRow() };
    const item = { SKU: 'D104-32' };
    await context.analisarMargensConcorrentesAutomaticamente('JK61', [item]);
    state.sources['D104-32'].fontes_versao = 3;
    await context.analisarMargensConcorrentesAutomaticamente('JK61', [item]);
    assert.strictEqual(state.patches.length, 2, 'Troca de fonte invalida a análise.');
    state.sources['D104-32'].concorrentes_valores.concorrente_1 = '99,90';
    await context.analisarMargensConcorrentesAutomaticamente('JK61', [item]);
    assert.strictEqual(state.patches.length, 3, 'Preço alterado na mesma planilha invalida a análise.');
    state.sources['D104-32'].anuncios_loja[0].variation_id = 'nova-variacao';
    await context.analisarMargensConcorrentesAutomaticamente('JK61', [item]);
    assert.strictEqual(state.patches.length, 4, 'Variação diferente do mesmo anúncio invalida a análise.');
    const salvo = item.analise_simulacao_importacao_sku;
    state.sources['D104-32'].anuncios_loja_store_id = 'outra-loja';
    await context.analisarMargensConcorrentesAutomaticamente('JK61', [item]);
    assert.strictEqual(state.patches.length, 4, 'Uma fonte de outra loja não gera PATCH.');
    assert.deepStrictEqual(JSON.parse(JSON.stringify(item.analise_simulacao_importacao_sku)), JSON.parse(JSON.stringify(salvo)),
        'Fonte de outra loja conserva o conteúdo da última análise autorizada, inclusive após restaurar sua cópia.');
}

async function verifyLateResponses() {
    const { state, context } = listHarness();
    const wait = deferred();
    const saved = { margens: {} };
    const item = { SKU: 'D104-32', analise_simulacao_importacao_sku: saved };
    state.sources = { 'D104-32': sourceRow() };
    state.beforeGet = () => wait.promise;
    const pending = context.analisarMargensConcorrentesAutomaticamente('JK61', [item]);
    state.listaId = 'OUTRA';
    state.generation++;
    wait.resolve();
    await pending;
    assert.strictEqual(state.patches.length, 0, 'Fonte de um contexto abandonado não pode iniciar PATCH.');
    assert.strictEqual(item.analise_simulacao_importacao_sku, saved);

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
    assert.strictEqual(item.analise_simulacao_importacao_sku, saved, 'PATCH tardio não altera a tela de outra geração.');
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
