'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static/favoritos/tabelas-layout/04-promocoes-busca-ranking.js'), 'utf8');
const mlBrowserSource = fs.readFileSync(path.join(root, 'static/favoritos/ml-browser.js'), 'utf8');

function extractFunction(name, context, sourceText = source) {
    const marker = `function ${name}`;
    const markerAt = sourceText.indexOf(marker);
    assert.ok(markerAt >= 0, `funcao ${name} ausente`);
    const start = sourceText.slice(Math.max(0, markerAt - 6), markerAt) === 'async ' ? markerAt - 6 : markerAt;
    const paramsOpen = sourceText.indexOf('(', markerAt);
    let paramsDepth = 0;
    let paramsClose = -1;
    for (let index = paramsOpen; index < sourceText.length; index += 1) {
        const char = sourceText[index];
        if (char === '(') paramsDepth += 1;
        if (char === ')' && --paramsDepth === 0) {
            paramsClose = index;
            break;
        }
    }
    const bodyOpen = sourceText.indexOf('{', paramsClose);
    let depth = 0;
    let quote = '';
    let escaped = false;
    let end = -1;
    for (let index = bodyOpen; index < sourceText.length; index += 1) {
        const char = sourceText[index];
        if (quote) {
            if (escaped) escaped = false;
            else if (char === '\\') escaped = true;
            else if (char === quote) quote = '';
            continue;
        }
        if (char === '"' || char === "'" || char === '`') {
            quote = char;
            continue;
        }
        if (char === '{') depth += 1;
        if (char === '}' && --depth === 0) {
            end = index + 1;
            break;
        }
    }
    assert.ok(end > bodyOpen, `fim de ${name} ausente`);
    return vm.runInNewContext(`(${sourceText.slice(start, end)})`, context);
}

function createHarness() {
    const requests = [];
    const incompleteIds = new Set();
    const omitOnceIds = new Set();
    const keyFor = item => {
        const id = String(item && item.id || '').toUpperCase();
        return id ? `mlb:${id}` : `url:${String(item && item.url || '').toLowerCase()}`;
    };
    const keysFor = item => {
        const keys = [];
        const id = String(item && item.id || '').toUpperCase();
        const url = String(item && item.url || '').toLowerCase();
        if (id) keys.push(`id:${id}`);
        if (url) keys.push(`url:${url}`);
        keys.push(`chave:${keyFor(item)}`);
        return [...new Set(keys)];
    };
    const fill = (target, sourceItem, names) => {
        let changed = false;
        for (const name of names) {
            if ((target[name] === undefined || target[name] === null || target[name] === '') && sourceItem[name] !== undefined && sourceItem[name] !== null && sourceItem[name] !== '') {
                target[name] = sourceItem[name];
                changed = true;
            }
        }
        return changed;
    };
    const context = {
        Map,
        Set,
        Array,
        Number,
        String,
        Math,
        JSON,
        Promise,
        console,
        mlFavoritosCancelado: false,
        mlFavoritosEmExecucao: true,
        ML_FAVORITOS_HISTORICO_ANUNCIOS_MAX: 500,
        ML_API_WORKERS: 4,
        anuncioRankingHistoricoEstaticoFavoritos: () => false,
        normalizarFonte: value => String(value || '').toLowerCase(),
        obterTipoAnuncioFavoritos: item => String(item && item.tipo_anuncio || ''),
        obterParcelamentoSemJurosFavoritos: item => item && typeof item.parcelamento_sem_juros === 'boolean' ? item.parcelamento_sem_juros : null,
        obterCondicaoAnuncioFavoritos: item => String(item && item.condicao || ''),
        obterFullAnuncioFavoritos: item => !!(item && (item.is_full === true || item.full === true)),
        obterImagemAnuncioFavoritos: item => String(item && item.imagem || item && item.thumbnail || ''),
        normalizarUrlAnuncioFavoritosRanking: value => String(value || ''),
        tituloAnuncioFavoritosPrecisaComplemento: value => !String(value || '').trim(),
        precisaComplementoPrecoFavoritos: item => !Number.isFinite(Number(item && item.preco)),
        vendedorValido: value => !!String(value || '').trim(),
        fonteVendasConfiavel: value => String(value || '').toLowerCase() === 'avant',
        hasNumeroVendas: value => Number.isFinite(Number(value)),
        fullAnuncioDesconhecidoFavoritos: item => item && item._fullAnuncioVerificado !== true,
        chaveAnuncioFavoritos: keyFor,
        chavesAnuncioFavoritos: keysFor,
        aplicarMetadataBasicaAnuncioFavoritos: (target, info) => fill(target, info, ['id', 'url', 'titulo', 'imagem', 'thumbnail']),
        preencherPrecoAnuncioFavoritos: (target, info) => fill(target, info, ['preco', 'price', 'preco_original', 'preco_promocional', 'fonte_preco']),
        preencherTipoAnuncioFavoritos: (target, info) => {
            const changed = fill(target, info, ['tipo_anuncio', 'listing_type_id', 'listing_type_name', 'full', 'is_full']);
            if (info._fullAnuncioVerificado === true || info.full === false || info.is_full === false) target._fullAnuncioVerificado = true;
            return changed;
        },
        preencherCondicaoAnuncioFavoritos: (target, info) => fill(target, info, ['condicao', 'condition', 'item_condition']),
        deveAtualizarVendedor: (oldValue, _oldSource, newValue) => !String(oldValue || '').trim() && !!String(newValue || '').trim(),
        deveAtualizarVendas: (oldValue, oldSource, newValue, newSource) => (!Number.isFinite(Number(oldValue)) || oldSource !== 'avant') && Number.isFinite(Number(newValue)) && newSource === 'avant',
        parseNumeroVendas: value => Number.isFinite(Number(value)) ? Number(value) : null,
        parseNumeroDecimalFavoritos: value => Number.isFinite(Number(value)) ? Number(value) : NaN,
        headersJsonAutenticado: () => ({ Authorization: 'Bearer test' }),
        sinalFavoritosAtual: () => undefined,
        mostrarBalaoFavoritosStatus: () => {},
        tentarDataCriacaoPeloElectron: async () => {},
        executarComConcorrencia: async (items, _workers, callback) => Promise.all(items.map(callback)),
        verificarCancelamentoFavoritos: () => {},
        consultarItemApiMercadoLivre: async () => null,
        normalizarNomeVendedor: value => String(value || ''),
        fetch: async (_url, options) => {
            const body = JSON.parse(options.body);
            requests.push(body);
            return {
                ok: true,
                json: async () => ({
                    resultados: body.anuncios.map(item => {
                        if (omitOnceIds.has(item.id)) {
                            omitOnceIds.delete(item.id);
                            return null;
                        }
                        return ({
                        id: item.id,
                        url: item.url,
                        vendedor: `Vendedor ${item.id}`,
                        fonte_vendedor: 'pagina_produto',
                        vendas: incompleteIds.has(item.id) ? null : 25,
                        fonte_vendas: incompleteIds.has(item.id) ? '' : 'avant',
                        data_criacao: '2024-01-02T00:00:00Z',
                        tipo_anuncio: 'Classico',
                        listing_type_id: 'gold_special',
                        listing_type_name: 'Classico',
                        full: false,
                        is_full: false,
                        _fullAnuncioVerificado: true,
                        condicao: 'new',
                        condition: 'new',
                        item_condition: 'new'
                        });
                    }).filter(Boolean)
                })
            };
        }
    };
    for (const name of [
        'criarContextoEnriquecimentoFavoritosExecucao',
        'anuncioFavoritosEnriquecimentoCompleto',
        'aplicarInfoEnriquecimentoFavoritos',
        'aplicarCacheEnriquecimentoFavoritos',
        'registrarCacheEnriquecimentoFavoritos',
        'enriquecerAnunciosFavoritosRanking'
    ]) {
        context[name] = extractFunction(name, context);
    }
    return { context, requests, incompleteIds, omitOnceIds };
}

function item(id) {
    return {
        id,
        url: `https://produto.mercadolivre.com.br/${id}`,
        titulo: `Produto ${id}`,
        imagem: `https://http2.mlstatic.com/${id}.jpg`,
        preco: 100,
        price: 90,
        preco_original: 100,
        preco_promocional: 90,
        fonte_preco: 'mercado_livre_html'
    };
}

async function run() {
    const { context, requests, incompleteIds } = createHarness();
    const execution = context.criarContextoEnriquecimentoFavoritosExecucao();
    const aliasA1 = item('MLB1');
    const aliasA2 = item('MLB1');
    const term1 = [aliasA1, aliasA2, item('MLB2')];
    await context.enriquecerAnunciosFavoritosRanking(term1, execution);
    assert.deepStrictEqual(requests.map(call => call.anuncios.length), [2], 'mesmo MLB no lote deve ser enviado uma vez');
    assert.strictEqual(aliasA1.vendedor, 'Vendedor MLB1');
    assert.strictEqual(aliasA2.vendedor, 'Vendedor MLB1', 'resposta deve ser aplicada a todos os aliases');

    const term2 = [item('MLB1'), item('MLB3')];
    await context.enriquecerAnunciosFavoritosRanking(term2, execution);
    assert.deepStrictEqual(requests.map(call => call.anuncios.map(itemReq => itemReq.id)), [['MLB1', 'MLB2'], ['MLB3']]);
    assert.strictEqual(term2[0].vendedor, 'Vendedor MLB1', 'cache da execucao deve preencher sobreposicao sem novo POST');

    await context.enriquecerAnunciosFavoritosRanking([item('MLB1'), item('MLB2'), item('MLB3')], execution);
    assert.strictEqual(requests.length, 2, 'uniao final completa nao pode repetir enriquecimento');
    assert.ok(execution.estatisticas.cache_hits >= 4);

    const newExecution = context.criarContextoEnriquecimentoFavoritosExecucao();
    await context.enriquecerAnunciosFavoritosRanking([item('MLB1')], newExecution);
    assert.strictEqual(requests.length, 3, 'nova execucao deve consultar novamente');

    incompleteIds.add('MLB4');
    const incompleteExecution = context.criarContextoEnriquecimentoFavoritosExecucao();
    await context.enriquecerAnunciosFavoritosRanking([item('MLB4')], incompleteExecution);
    await context.enriquecerAnunciosFavoritosRanking([item('MLB4')], incompleteExecution);
    await context.enriquecerAnunciosFavoritosRanking([item('MLB4')], incompleteExecution, { fechamento: true });
    assert.strictEqual(requests.filter(call => call.anuncios.some(row => row.id === 'MLB4')).length, 1, 'resposta backend incompleta nao deve ser repetida imediatamente');

    const transientHarness = createHarness();
    transientHarness.omitOnceIds.add('MLB5');
    const transientExecution = transientHarness.context.criarContextoEnriquecimentoFavoritosExecucao();
    await transientHarness.context.enriquecerAnunciosFavoritosRanking([item('MLB5')], transientExecution);
    await transientHarness.context.enriquecerAnunciosFavoritosRanking([item('MLB5')], transientExecution);
    assert.strictEqual(transientHarness.requests.length, 1, 'falha transitoria nao deve repetir entre pesquisas');
    await transientHarness.context.enriquecerAnunciosFavoritosRanking([item('MLB5')], transientExecution, { fechamento: true });
    assert.strictEqual(transientHarness.requests.length, 2, 'falha sem resposta deve ter uma unica contingencia no fechamento');
    assert.strictEqual(transientExecution.estatisticas.backend_retries, 1);

    const completeHarness = createHarness();
    const completeItem = {
        ...item('MLB6'),
        vendedor: 'Vendedor completo',
        vendas: 25,
        vendasFonte: 'avant',
        data_criacao: '2024-01-02T00:00:00Z',
        tipo_anuncio: 'Classico',
        listing_type_id: 'gold_special',
        is_full: false,
        full: false,
        _fullAnuncioVerificado: true,
        condicao: 'new'
    };
    const completeExecution = completeHarness.context.criarContextoEnriquecimentoFavoritosExecucao();
    await completeHarness.context.enriquecerAnunciosFavoritosRanking([completeItem], completeExecution);
    assert.strictEqual(completeHarness.requests.length, 0, 'anuncio ja completo na pagina nao deve chamar o backend');
    assert.strictEqual(completeExecution.estatisticas.completos_na_coleta, 1);

    const batchHarness = createHarness();
    const batchExecution = batchHarness.context.criarContextoEnriquecimentoFavoritosExecucao();
    await batchHarness.context.enriquecerAnunciosFavoritosRanking(
        Array.from({ length: 240 }, (_, index) => item(`MLB${1000 + index}`)),
        batchExecution
    );
    assert.deepStrictEqual(batchHarness.requests.map(call => call.anuncios.length), [200, 40], '240 IDs devem ser enriquecidos em lotes sequenciais completos');
    assert.strictEqual(batchHarness.requests.every(call => call.max_anuncios === call.anuncios.length), true);

    const emptyHarness = createHarness();
    await emptyHarness.context.enriquecerAnunciosFavoritosRanking([], emptyHarness.context.criarContextoEnriquecimentoFavoritosExecucao());
    assert.strictEqual(emptyHarness.requests.length, 0);

    const plateauContext = {
        Array,
        Number,
        String,
        JSON,
        chaveCanonicaAnuncioFavoritos: value => String(value && value.id || '').toLowerCase(),
        obterImagemAnuncioFavoritos: value => String(value && value.imagem || '')
    };
    const assinaturaEstabilidade = extractFunction(
        'assinaturaEstabilidadePrimeiraPaginaFavoritos',
        plateauContext,
        mlBrowserSource
    );
    plateauContext.assinaturaEstabilidadePrimeiraPaginaFavoritos = assinaturaEstabilidade;
    const deveEncerrarPlateau = extractFunction(
        'deveEncerrarPlateauPrimeiraPaginaFavoritos',
        plateauContext,
        mlBrowserSource
    );
    const anunciosEstaveis = [
        { id: 'MLB2', titulo: 'B', imagem: 'b.jpg', preco: 20, vendas: 3, vendasFonte: 'avant' },
        { id: 'MLB1', titulo: 'A', imagem: 'a.jpg', preco: 10, vendas: 2, vendasFonte: 'avant' }
    ];
    const assinaturaA = assinaturaEstabilidade(anunciosEstaveis);
    const assinaturaReordenada = assinaturaEstabilidade([...anunciosEstaveis].reverse());
    assert.strictEqual(assinaturaA, assinaturaReordenada, 'ordem dos cards nao pode reiniciar a estabilidade');
    const assinaturaAtualizada = assinaturaEstabilidade([
        { ...anunciosEstaveis[0], vendas: 4 },
        anunciosEstaveis[1]
    ]);
    assert.notStrictEqual(assinaturaA, assinaturaAtualizada, 'campo ML/Avant tardio deve reiniciar a estabilidade');
    assert.strictEqual(deveEncerrarPlateau({ passada: 1, passadaCompleta: true, assinaturaAtual: assinaturaA, assinaturaAnterior: assinaturaA, pendentesAvant: 0 }), false);
    assert.strictEqual(deveEncerrarPlateau({ passada: 2, passadaCompleta: false, assinaturaAtual: assinaturaA, assinaturaAnterior: assinaturaA, pendentesAvant: 0 }), false);
    assert.strictEqual(deveEncerrarPlateau({ passada: 2, passadaCompleta: true, assinaturaAtual: assinaturaA, assinaturaAnterior: assinaturaA, pendentesAvant: 1 }), false);
    assert.strictEqual(deveEncerrarPlateau({ passada: 2, passadaCompleta: true, assinaturaAtual: assinaturaA, assinaturaAnterior: assinaturaA, pendentesAvant: 0 }), true);
    const montarPosicoesVarredura = extractFunction(
        'montarPosicoesVarreduraPrimeiraPaginaFavoritos',
        { Array, Set, Number, Math },
        mlBrowserSource
    );
    const posicoesVarredura = montarPosicoesVarredura(14800, 800, 80);
    assert.strictEqual(posicoesVarredura[0], 0, 'varredura deve comecar no topo da primeira pagina');
    assert.strictEqual(posicoesVarredura.at(-1), 13980, 'varredura deve sempre chegar ao fim da primeira pagina');
    assert.strictEqual(posicoesVarredura.length, 19, 'limite historico de 80 anuncios deve manter no maximo 19 posicoes');
    assert.strictEqual(
        posicoesVarredura.slice(1).every((posicao, indice) => posicao > posicoesVarredura[indice]),
        true,
        'posicoes amostradas devem permanecer ordenadas e sem repeticao'
    );
    const alturaVarredura = extractFunction(
        'alturaVarreduraPrimeiraPaginaFavoritos',
        { Number, Math },
        mlBrowserSource
    );
    assert.strictEqual(alturaVarredura(20000, 11800, 800), 12080, 'rodape nao deve ampliar a varredura alem dos resultados');
    assert.strictEqual(alturaVarredura(20000, 0, 800), 20000, 'sem marcador confiavel deve preservar a altura integral como contingencia');
    assert.match(mlBrowserSource, /tempoLimiteMs[\s\S]*180000[\s\S]*maxPassadas[\s\S]*3/, 'contingencia de 180 segundos e tres passadas deve permanecer');
    assert.match(mlBrowserSource, /passadaCompleta[\s\S]*pendentesAvantPassada[\s\S]*stable_plateau/, 'plateau deve exigir pagina completa e nenhum Avant pendente');

    console.log('Favoritos enrichment dedup and stable plateau checks passed');
}

run().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
