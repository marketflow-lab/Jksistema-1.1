'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '..', 'static', 'importacoes_lista.html'), 'utf8');

function source(name) {
    const match = new RegExp('^        (?:async )?function ' + name + '\\(', 'm').exec(html);
    assert(match, name);
    const end = html.indexOf('\n        }', match.index);
    return html.slice(match.index, end + '\n        }'.length);
}

const names = ['escaparHtml', 'normTxt', 'formatarPct', 'formatarBRL', 'obterCampo',
    'normalizarSkuComparacao', 'chaveSkuCustoCadastro', 'valorNumerico', 'extrairCodigoMlbMargensAutomaticas',
    'obterSkuItemMargensAutomaticas', 'analiseAnunciosLojaConfere', 'anunciosLojaAnaliseRecentes',
    'analiseSimuladaListaCompativel', 'analiseConfereFontesLista', 'formatarAnunciosLoja', 'montarPayloadMargensAutomaticas',
    'consultarAnaliseLista', 'carregarFontesMargensAutomaticas', 'carregarAnunciosMargensAutomaticas',
    'salvarMargensAutomaticasSku', 'analisarMargensConcorrentesAutomaticamente'];

function ads() {
    return Array.from({ length: 8 }, (_, index) => ({
        chave: 'concorrente_' + (index + 1), item_id: 'MLB100000000' + index,
        url: 'https://produto.mercadolivre.com.br/MLB-100000000' + index + '-produto-_JM?variation=' + (100 + index),
        sku: '385-4', variation_id: String(100 + index), preco: index === 6 ? null : 100 + index,
        seller_id: 'seller-A', site_id: 'MLB',
        status: index === 7 ? 'paused' : 'active',
    }));
}

function harness() {
    const state = { generation: 1, gets: [], patches: [], statuses: [], ownAds: ads(), ownWait: null, sheetFailure: false };
    const context = vm.createContext({
        URL, AbortController, setTimeout: (callback, delay) => setTimeout(callback, delay === 1000 ? 0 : delay), clearTimeout,
        AUXILIAR_TIMEOUT_MS: 20000, ATUALIZACAO_LISTA_LIMITE_MS: 120000,
        controladoresAuxiliares: new Set(), estadosAnaliseMargensAutomaticas: new Map(),
        cicloAnaliseMargensAutomaticas: 0,
        calculosUnitariosLista: { '385-4': { revisao: 'a'.repeat(64), assinatura: 'b'.repeat(64), calculo: { custoUnitario: 36.15 } } },
        document: { querySelectorAll: () => [] }, innerHeight: 900,
        window: { JKImportacoesCacheCustos: { comTravaAnalise: async (options, executar) => {
            assert.equal(options.listaId, 'C59');
            assert.equal(typeof executar, 'function');
            return executar();
        } } },
        salvarSnapshotLista: async () => {},
        ultimaListaCarregada: { id: 'C59', store_id: 'store-A', loja: 'Loja A', analise_concorrentes_fontes_versao: 7 },
        obterAuthHeaders: () => ({}),
        contextoCarregamentoAtual: listaId => ({ listaId, generation: state.generation }),
        carregamentoVigente: atual => atual.generation === state.generation,
        confirmarMutacaoLista: () => {}, atualizarCelulaMargensAutomaticas: () => {},
        definirEstadoMargensAutomaticas: (item, status, message) => {
            state.statuses.push({ sku: item.SKU, status, message });
            if (status) context.estadosAnaliseMargensAutomaticas.set(item.SKU, {
                ...context.estadosAnaliseMargensAutomaticas.get(item.SKU), status, mensagem: message,
            });
            else context.estadosAnaliseMargensAutomaticas.delete(item.SKU);
        },
        fetch: async (url, options) => {
            if (options.method === 'GET') {
                state.gets.push(url);
                if (url.startsWith('/api/medias-compras/concorrentes-links?')) {
                    if (state.ownWait) await state.ownWait;
                    const onlyOwn = new URL(url, 'http://fixture').searchParams.get('somente_proprios') === 'true';
                    if (state.sheetFailure && !onlyOwn) return { ok: false, status: 503, json: async () => ({ detail: 'Planilha indisponível' }) };
                    return { ok: true, status: 200, json: async () => ({ anuncios_versao: 2,
                        anuncios_loja_fonte: 'loja_lista_sku', anuncios_loja_store_id: 'store-A',
                        anuncios_loja_seller_id: 'seller-A', anuncios_loja_site_id: 'MLB',
                        anuncios_loja: structuredClone(state.ownAds), fontes_versao: 7,
                        concorrentes_valores: onlyOwn ? {} : { concorrente_1: 99 },
                        concorrentes_mlb: { concorrente_1: 'MLB9999999999' },
                    }) };
                }
                throw new Error('Consulta inesperada: ' + url);
            }
            const body = JSON.parse(options.body);
            state.patches.push({ url, body });
            const margins = prices => Object.fromEntries(Object.entries(prices).map(([key, price]) => [key, {
                item_id_loja: body.anuncios_loja[key], variation_id: body.variacoes_anuncios_loja[key],
                store_id: 'store-A', seller_id: 'seller-A', site_id: 'MLB', preco_venda: price,
                margem_percentual: 21, financeiro_exato: true,
                fonte_custo: body.fonte_custo, custo_unitario: body.custo_unitario,
            }]));
            return { ok: true, status: 200, json: async () => ({ analise_concorrentes: {
                ...body, store_id: 'store-A',
                anuncios_versao: 2, anuncios_loja_store_id: 'store-A', anuncios_consultado_em_ms: Date.now(),
                anuncios_loja: structuredClone(state.ownAds), fontes_versao: 7,
                margens: margins(body.precos_concorrentes), margens_anuncios_loja: margins(body.precos_anuncios_loja),
            } }) };
        },
    });
    vm.runInContext(names.map(source).join('\n'), context);
    return { state, context };
}

async function run() {
    assert(html.includes("{ key: 'anuncios_loja', label: 'Anúncios da loja'"));
    const { state, context } = harness();
    const item = { SKU: '385-4', analise_concorrentes: { fontes_versao: 7, margens: { concorrente_1: 35 } } };
    await context.analisarMargensConcorrentesAutomaticamente('C59', [item]);
    assert.equal(state.patches.length, 1, 'A análise antiga deve ser renovada mesmo quando as margens de mercado existem.');
    const payload = state.patches[0].body;
    assert.equal(Object.keys(payload.anuncios_loja).length, 8, 'Todos os anúncios entram na análise, inclusive além do quinto.');
    assert.equal(Object.keys(payload.precos_anuncios_loja).length, 7, 'Preço ausente não vira zero nem preço de outra variação.');
    assert.equal(payload.anuncios_loja.concorrente_7, 'MLB1000000006');
    assert.equal(payload.variacoes_anuncios_loja.concorrente_8, '107');
    assert.equal(payload.precos_anuncios_loja.concorrente_8, 107, 'O pausado permanece junto dos ativos.');
    assert.equal(payload.anuncios_loja.concorrente_1, 'MLB1000000000', 'A análise usa a loja da lista, sem reutilizar anúncio da planilha.');
    assert.equal(payload.fonte_custo, 'simulacao_importacao_sku');
    assert.equal(payload.custo_unitario, 36.15);
    assert.equal(payload.revisao_fontes_custo, 'a'.repeat(64));
    assert.equal(payload.assinatura_calculo, 'b'.repeat(64));
    const request = new URL(state.gets[0], 'http://fixture');
    assert.equal(request.searchParams.get('lista_id'), 'C59');
    assert.equal(request.searchParams.get('sku'), '385-4');
    assert.equal(request.searchParams.get('anuncios_da_lista'), 'true');
    assert.equal(request.searchParams.get('somente_proprios'), null, 'A consulta completa usa a mesma fonte do detalhe.');
    await context.analisarMargensConcorrentesAutomaticamente('C59', [item]);
    assert.equal(state.patches.length, 1, 'Revisão e preços iguais dispensam novo cálculo financeiro.');

    const analysis = item.analise_simulacao_importacao_sku;
    const validAnalysis = structuredClone(analysis);
    Object.assign(analysis.margens_anuncios_loja.concorrente_2, { variation_id: 'outra-variacao', margem_percentual: 88 });
    Object.assign(analysis.margens_anuncios_loja.concorrente_3, { store_id: 'outra-loja', margem_percentual: 99 });
    const rendered = context.formatarAnunciosLoja(item);
    assert.equal((rendered.match(/data-own-ad=/g) || []).length, 8);
    assert.match(rendered, /variation=107/);
    assert.match(rendered, /Pausado/);
    assert.match(rendered, /Preço a confirmar/);
    assert.match(rendered, /Margem 21,00%/);
    assert(!rendered.includes('88,00%'), 'Margem de outra variação não pode aparecer.');
    assert(!rendered.includes('99,00%'), 'Margem de outra loja não pode aparecer.');
    analysis.anuncios_loja_store_id = 'store-anterior';
    context.estadosAnaliseMargensAutomaticas.clear();
    assert(!context.formatarAnunciosLoja(item).includes('MLB1000000000'), 'Snapshot de outra loja não aparece na lista.');
    item.analise_simulacao_importacao_sku = validAnalysis;
    validAnalysis.anuncios_consultado_em_ms = Date.now() - 61000;
    await context.analisarMargensConcorrentesAutomaticamente('C59', [item]);
    assert.equal(state.patches.length, 1, 'Tempo decorrido sozinho não invalida a margem com os mesmos insumos confirmados.');
    state.ownAds[0].preco = 115;
    await context.analisarMargensConcorrentesAutomaticamente('C59', [item]);
    assert.equal(state.patches.length, 2, 'Preço alterado na mesma fonte recalcula as margens.');

    const emptyPrices = harness();
    emptyPrices.state.ownAds.forEach(ad => { ad.preco = null; });
    const withoutPrice = { SKU: '385-4' };
    await emptyPrices.context.analisarMargensConcorrentesAutomaticamente('C59', [withoutPrice]);
    assert.equal(Object.keys(emptyPrices.state.patches[0].body.precos_anuncios_loja).length, 0);
    assert.equal((emptyPrices.context.formatarAnunciosLoja(withoutPrice).match(/Preço a confirmar/g) || []).length, 8);

    const noSheet = harness();
    noSheet.state.sheetFailure = true;
    const savedAnalysis = { anuncios_versao: 1, fontes_versao: 7, margens: { concorrente_1: { margem_percentual: 22 } },
        margens_anuncios_loja: { concorrente_1: { item_id_loja: 'MLB1000000000', variation_id: '100',
            store_id: 'store-A', preco_venda: 100, margem_percentual: 21, financeiro_exato: true } } };
    const unavailableSheet = { SKU: '385-4', analise_simulacao_importacao_sku: savedAnalysis };
    await noSheet.context.analisarMargensConcorrentesAutomaticamente('C59', [unavailableSheet]);
    assert.equal(noSheet.state.gets.length, 4, 'As duas tentativas completas com falha usam a consulta própria como alternativa.');
    assert.equal(noSheet.state.gets.filter(url => url.includes('somente_proprios=true')).length, 2);
    assert.equal(noSheet.state.patches.length, 0, 'Falha da planilha não publica análise vazia.');
    assert.equal(unavailableSheet.analise_simulacao_importacao_sku, savedAnalysis, 'A análise persistida deve permanecer intacta.');
    const noSheetHtml = noSheet.context.formatarAnunciosLoja(unavailableSheet);
    assert.equal((noSheetHtml.match(/data-own-ad=/g) || []).length, 8, 'Todos os anúncios continuam visíveis.');
    assert.match(noSheetHtml, /Planilha indisponível/);
    assert(!noSheetHtml.includes('Margem 21,00%'), 'A consulta com falha mostra margem pendente.');

    const late = harness();
    let release;
    late.state.ownWait = new Promise(resolve => { release = resolve; });
    const pending = late.context.analisarMargensConcorrentesAutomaticamente('C59', [{ SKU: '385-4' }]);
    while (late.state.gets.length < 1) await new Promise(resolve => setImmediate(resolve));
    late.state.generation++;
    release();
    await pending;
    assert.equal(late.state.patches.length, 0, 'Anúncios de uma geração anterior não iniciam uma análise.');
    console.log('OK: lista mostra todos os anúncios, variações e preços pendentes, renova cache antigo e rejeita resposta tardia.');
}

run().catch(error => { console.error(error); process.exitCode = 1; });
