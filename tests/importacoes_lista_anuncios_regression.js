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
    'normalizarSkuComparacao', 'valorNumerico', 'extrairCodigoMlbMargensAutomaticas',
    'obterSkuItemMargensAutomaticas', 'analiseAnunciosLojaConfere', 'anunciosLojaAnaliseRecentes',
    'formatarAnunciosLoja', 'montarPayloadMargensAutomaticas',
    'consultarAnaliseLista', 'carregarFontesMargensAutomaticas', 'carregarAnunciosMargensAutomaticas',
    'salvarMargensAutomaticasSku', 'analisarMargensConcorrentesAutomaticamente'];

function ads() {
    return Array.from({ length: 8 }, (_, index) => ({
        chave: 'concorrente_' + (index + 1), item_id: 'MLB100000000' + index,
        url: 'https://produto.mercadolivre.com.br/MLB-100000000' + index + '-produto-_JM?variation=' + (100 + index),
        sku: '385-4', variation_id: String(100 + index), preco: index === 6 ? null : 100 + index,
        status: index === 7 ? 'paused' : 'active',
    }));
}

function harness() {
    const state = { generation: 1, gets: [], patches: [], statuses: [], ownAds: ads(), ownWait: null, sheetFailure: false };
    const context = vm.createContext({
        URL, AbortController, setTimeout, clearTimeout,
        AUXILIAR_TIMEOUT_MS: 20000, ATUALIZACAO_LISTA_LIMITE_MS: 120000,
        controladoresAuxiliares: new Set(), estadosAnaliseMargensAutomaticas: new Map(),
        cicloAnaliseMargensAutomaticas: 0,
        ultimaListaCarregada: { id: 'C59', store_id: 'store-A', loja: 'Loja A', analise_concorrentes_fontes_versao: 7 },
        obterAuthHeaders: () => ({}),
        contextoCarregamentoAtual: listaId => ({ listaId, generation: state.generation }),
        carregamentoVigente: atual => atual.generation === state.generation,
        confirmarMutacaoLista: () => {}, atualizarCelulaMargensAutomaticas: () => {},
        definirEstadoMargensAutomaticas: (item, status, message) => state.statuses.push({ sku: item.SKU, status, message }),
        fetch: async (url, options) => {
            if (options.method === 'GET') {
                state.gets.push(url);
                if (url.startsWith('/api/medias-compras/concorrentes-links?')) {
                    if (state.ownWait) await state.ownWait;
                    return { ok: true, json: async () => ({ anuncios_loja_fonte: 'loja_lista_sku',
                        anuncios_loja_store_id: 'store-A', anuncios_loja: state.ownAds }) };
                }
                if (state.sheetFailure) return { ok: false, json: async () => ({ detail: 'Planilha indisponível' }) };
                return { ok: true, json: async () => ({ resultados: { '385-4': {
                    fontes_versao: 7, concorrentes_valores: { concorrente_1: 99 },
                    concorrentes_mlb: { concorrente_1: 'MLB9999999999' },
                } } }) };
            }
            const body = JSON.parse(options.body);
            state.patches.push({ url, body });
            return { ok: true, json: async () => ({ analise_concorrentes: {
                anuncios_versao: 2, anuncios_loja_store_id: 'store-A', anuncios_consultado_em_ms: Date.now(),
                anuncios_loja: state.ownAds, fontes_versao: 7, margens_anuncios_loja: {},
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
    const request = new URL(state.gets[1], 'http://fixture');
    assert.equal(request.searchParams.get('lista_id'), 'C59');
    assert.equal(request.searchParams.get('sku'), '385-4');
    assert.equal(request.searchParams.get('somente_proprios'), 'true');
    await context.analisarMargensConcorrentesAutomaticamente('C59', [item]);
    assert.equal(state.patches.length, 1, 'A revisão completa evita nova leitura ao reabrir sem alterações.');

    const analysis = item.analise_concorrentes;
    analysis.margens_anuncios_loja.concorrente_1 = { item_id_loja: 'MLB1000000000', variation_id: '100',
        store_id: 'store-A', preco_venda: 100, margem_percentual: 21, financeiro_exato: true };
    analysis.margens_anuncios_loja.concorrente_2 = { item_id_loja: 'MLB1000000001', variation_id: 'outra-variacao',
        store_id: 'store-A', preco_venda: 101, margem_percentual: 88, financeiro_exato: true };
    analysis.margens_anuncios_loja.concorrente_3 = { item_id_loja: 'MLB1000000002', variation_id: '102',
        store_id: 'outra-loja', preco_venda: 102, margem_percentual: 99, financeiro_exato: true };
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
    analysis.anuncios_loja_store_id = 'store-A';
    analysis.anuncios_consultado_em_ms = Date.now() - 61000;
    await context.analisarMargensConcorrentesAutomaticamente('C59', [item]);
    assert.equal(state.patches.length, 2, 'A reabertura renova anúncios após um minuto.');

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
    const unavailableSheet = { SKU: '385-4', analise_concorrentes: savedAnalysis };
    await noSheet.context.analisarMargensConcorrentesAutomaticamente('C59', [unavailableSheet]);
    assert.equal(noSheet.state.gets.length, 2, 'Mesmo com planilha503, os anúncios próprios são consultados.');
    assert.equal(noSheet.state.patches.length, 0, 'Falha da planilha não publica análise vazia.');
    assert.equal(unavailableSheet.analise_concorrentes, savedAnalysis, 'A análise persistida deve permanecer intacta.');
    const noSheetHtml = noSheet.context.formatarAnunciosLoja(unavailableSheet);
    assert.equal((noSheetHtml.match(/data-own-ad=/g) || []).length, 8, 'Todos os anúncios continuam visíveis.');
    assert.match(noSheetHtml, /Planilha indisponível/);
    assert(!noSheetHtml.includes('Margem 21,00%'), 'A consulta com falha mostra margem pendente.');

    const late = harness();
    let release;
    late.state.ownWait = new Promise(resolve => { release = resolve; });
    const pending = late.context.analisarMargensConcorrentesAutomaticamente('C59', [{ SKU: '385-4' }]);
    while (late.state.gets.length < 2) await new Promise(resolve => setImmediate(resolve));
    late.state.generation++;
    release();
    await pending;
    assert.equal(late.state.patches.length, 0, 'Anúncios de uma geração anterior não iniciam uma análise.');
    console.log('OK: lista mostra todos os anúncios, variações e preços pendentes, renova cache antigo e rejeita resposta tardia.');
}

run().catch(error => { console.error(error); process.exitCode = 1; });
