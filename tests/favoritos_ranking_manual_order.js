'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(
    path.join(root, 'static/favoritos/tabelas-layout/05-resultados-historico.js'),
    'utf8'
);

function extractFunction(name, context) {
    const marker = `function ${name}`;
    const markerAt = source.indexOf(marker);
    assert.ok(markerAt >= 0, `funcao ${name} ausente`);
    const paramsOpen = source.indexOf('(', markerAt);
    let paramsDepth = 0;
    let paramsClose = -1;
    for (let index = paramsOpen; index < source.length; index += 1) {
        const char = source[index];
        if (char === '(') paramsDepth += 1;
        if (char === ')' && --paramsDepth === 0) {
            paramsClose = index;
            break;
        }
    }
    const bodyOpen = source.indexOf('{', paramsClose);
    let depth = 0;
    let quote = '';
    let escaped = false;
    let end = -1;
    for (let index = bodyOpen; index < source.length; index += 1) {
        const char = source[index];
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
    return vm.runInNewContext(`(${source.slice(markerAt, end)})`, context);
}

function anuncio(id, vendedor = '') {
    return { id, mlb: id, vendedor, titulo: `Produto ${id}` };
}

function ids(grupo) {
    return grupo.anuncios.map(item => item.id);
}

function createHarness({
    current = ['MLB1000000001', 'MLB1000000002', 'MLB1000000003'],
    histories = [
        { id: 'hist-mais-recente', sku: 'SKU-1', ids: ['MLB1000000001', 'MLB1000000002', 'MLB1000000003'] },
        { id: 'hist-antigo', sku: 'SKU-1', ids: ['MLB2000000001', 'MLB2000000002', 'MLB2000000003'] }
    ],
    ignoredSellers = []
} = {}) {
    const currentGroup = { sku: 'SKU-1', anuncios: current.map(id => anuncio(id)), ordem_manual: false };
    const history = histories.map(item => ({
        id: item.id,
        data_iso: item.id === 'hist-mais-recente' ? '2026-07-21T12:00:00Z' : '2026-07-20T12:00:00Z',
        grupos: [{
            sku: item.sku,
            anuncios: item.ids.map(id => anuncio(id)),
            ordem_manual: false
        }]
    }));
    const saves = [];
    const renders = { ranking: 0, mercadoLivre: 0, historico: 0 };
    const status = { textContent: '' };
    const ignored = new Set(ignoredSellers.map(value => String(value).toLowerCase()));
    const skuKey = value => String(value || '').trim().toLowerCase();
    const itemKeys = item => new Set(item && item.id ? [`id:${String(item.id).toUpperCase()}`] : []);
    const matches = (item, keys) => [...itemKeys(item)].some(key => keys.has(key));

    const moveRelativeInGroup = (group, sourceKeys, targetKeys, placeAfter) => {
        if (!group || !Array.isArray(group.anuncios)) return false;
        const from = group.anuncios.findIndex(item => matches(item, sourceKeys));
        const target = group.anuncios.findIndex(item => matches(item, targetKeys));
        if (from < 0 || target < 0 || from === target) return false;
        const [moving] = group.anuncios.splice(from, 1);
        let insertion = group.anuncios.findIndex(item => matches(item, targetKeys));
        if (insertion < 0) return false;
        if (placeAfter) insertion += 1;
        group.anuncios.splice(insertion, 0, moving);
        group.ordem_manual = true;
        group.total_anuncios = group.anuncios.length;
        return true;
    };

    const context = {
        Array,
        Boolean,
        Map,
        Math,
        Number,
        Set,
        String,
        FAV_ML_RANKING_ATUAL_ID: '__ranking_atual__',
        favMlSkuSelecionado: 'SKU-1',
        favMlHistoricoExecucaoSelecionadaId: '__ranking_atual__',
        favMlAnunciosSkuAtual: [],
        mlFavoritosResultadosPorSku: new Map([['sku-1', currentGroup]]),
        favMlStatusEl: status,
        mlHistoricoFavoritosStatusEl: status,
        document: { getElementById: () => ({ classList: { contains: () => true } }) },
        skuChaveSku: skuKey,
        chavesRemocaoAnuncioRankingFavoritos: item => [...itemKeys(item)],
        chavesAnuncioFavoritos: item => [...itemKeys(item)],
        extrairItemIdAnuncio: value => {
            const match = String(value || '').toUpperCase().match(/MLB-?\d+/);
            return match ? match[0].replace('-', '') : '';
        },
        normalizarNomeVendedor: value => String(value || '').trim(),
        obterPrecosAnuncioFavoritos: item => ({ preco: item && item.preco || null, promocional: null }),
        anuncioCorrespondeRemocaoRankingFavoritos: matches,
        vendedorIgnoradoNoRanking: value => ignored.has(String(value || '').toLowerCase()),
        lerHistoricoFavoritos: () => history,
        filtrarHistoricoFavoritosPorLojaAtual: lista => lista,
        idEntradaHistoricoFavoritos: entry => String(entry && entry.id || ''),
        encontrarGrupoHistoricoRankingFavoritos: (lista, key, entryId = '') => {
            const requested = String(entryId || '');
            for (const entry of lista) {
                if (requested && entry.id !== requested) continue;
                const group = entry.grupos.find(item => skuKey(item.sku) === key);
                if (group) return { entrada: entry, grupo: group };
            }
            return null;
        },
        recalcularTotaisHistoricoFavoritos: entry => {
            entry.total_skus = entry.grupos.length;
            entry.total_anuncios = entry.grupos.reduce((sum, group) => sum + group.anuncios.length, 0);
        },
        salvarHistoricoFavoritos: (lista, options = {}) => saves.push({ lista, options }),
        renderizarFavoritosOutrosAnuncios: () => { renders.ranking += 1; },
        renderizarFavoritosAnunciosMl: () => { renders.mercadoLivre += 1; },
        renderizarHistoricoFavoritos: () => { renders.historico += 1; },
        moverAnuncioEmGrupoRankingFavoritosParaReferencia: moveRelativeInGroup,
        reordenarAnuncioEmGrupoRankingFavoritos: moveRelativeInGroup
    };
    context.chavesRemocaoAnuncioRankingFavoritos = extractFunction('chavesRemocaoAnuncioRankingFavoritos', context);
    context.anuncioCorrespondeRemocaoRankingFavoritos = extractFunction('anuncioCorrespondeRemocaoRankingFavoritos', context);
    context.removerAnuncioDeGrupoRankingFavoritos = extractFunction('removerAnuncioDeGrupoRankingFavoritos', context);
    context.moverAnuncioEmGrupoRankingFavoritos = extractFunction('moverAnuncioEmGrupoRankingFavoritos', context);
    context.moverAnuncioParaReferenciaEmGrupoRankingFavoritos = extractFunction('moverAnuncioParaReferenciaEmGrupoRankingFavoritos', context);
    context.recalcularTotaisHistoricoFavoritos = extractFunction('recalcularTotaisHistoricoFavoritos', context);
    context.encontrarGrupoHistoricoRankingFavoritos = extractFunction('encontrarGrupoHistoricoRankingFavoritos', context);
    context.normalizarEntradaIdMutacaoRankingFavoritos = extractFunction('normalizarEntradaIdMutacaoRankingFavoritos', context);
    context.aplicarMutacaoGrupoRankingFavoritos = extractFunction('aplicarMutacaoGrupoRankingFavoritos', context);
    context.renderizarMutacaoRankingFavoritos = extractFunction('renderizarMutacaoRankingFavoritos', context);
    context.atualizarStatusRankingFavoritosReordenado = extractFunction('atualizarStatusRankingFavoritosReordenado', context);
    context.moverAnuncioRankingFavoritosParaReferencia = extractFunction('moverAnuncioRankingFavoritosParaReferencia', context);
    context.moverAnuncioRankingFavoritos = extractFunction('moverAnuncioRankingFavoritos', context);
    return { context, currentGroup, history, saves, renders };
}

function assertSinglePersistenceAndRender(harness) {
    assert.strictEqual(harness.saves.length, 1, 'mudanca manual deve persistir uma unica vez');
    assert.strictEqual(harness.saves[0].options.imediato, true, 'ordem manual deve usar persistencia imediata');
    assert.strictEqual(harness.renders.ranking, 1, 'ranking deve ser renderizado uma unica vez');
    assert.strictEqual(harness.renders.mercadoLivre, 1, 'tabela Mercado Livre deve ser renderizada uma unica vez');
    assert.strictEqual(harness.renders.historico, 1, 'historico deve ser renderizado uma unica vez');
}

function run() {
    {
        const harness = createHarness();
        const sourceItem = harness.currentGroup.anuncios[1];
        const targetItem = harness.currentGroup.anuncios[0];
        harness.context.moverAnuncioRankingFavoritosParaReferencia(
            'SKU-1',
            sourceItem,
            targetItem,
            false,
            { entradaId: '__ranking_atual__' }
        );
        assert.deepStrictEqual(ids(harness.currentGroup), ['MLB1000000002', 'MLB1000000001', 'MLB1000000003'], 'sentinela atual deve mover o grupo em memoria');
        assert.deepStrictEqual(ids(harness.history[0].grupos[0]), ['MLB1000000002', 'MLB1000000001', 'MLB1000000003'], 'ranking atual e historico mais recente devem permanecer alinhados');
        assert.strictEqual(harness.currentGroup.ordem_manual, true);
        assert.strictEqual(harness.history[0].grupos[0].ordem_manual, true);
        assertSinglePersistenceAndRender(harness);
    }

    {
        const harness = createHarness();
        const selected = harness.history[1].grupos[0];
        harness.context.moverAnuncioRankingFavoritosParaReferencia(
            'SKU-1',
            selected.anuncios[2],
            selected.anuncios[0],
            false,
            { entradaId: 'hist-antigo' }
        );
        assert.deepStrictEqual(ids(selected), ['MLB2000000003', 'MLB2000000001', 'MLB2000000002']);
        assert.deepStrictEqual(ids(harness.history[0].grupos[0]), ['MLB1000000001', 'MLB1000000002', 'MLB1000000003'], 'outra execucao historica nao pode ser alterada');
        assert.deepStrictEqual(ids(harness.currentGroup), ['MLB1000000001', 'MLB1000000002', 'MLB1000000003'], 'historico isolado nao deve alterar o ranking atual');
        assert.strictEqual(selected.ordem_manual, true);
        assertSinglePersistenceAndRender(harness);
    }

    {
        const beforeHarness = createHarness();
        beforeHarness.context.moverAnuncioRankingFavoritosParaReferencia(
            'SKU-1',
            beforeHarness.history[1].grupos[0].anuncios[2],
            beforeHarness.history[1].grupos[0].anuncios[0],
            false,
            { entradaId: 'hist-antigo' }
        );
        assert.deepStrictEqual(ids(beforeHarness.history[1].grupos[0]), ['MLB2000000003', 'MLB2000000001', 'MLB2000000002'], 'drop antes deve inserir antes do alvo');

        const afterHarness = createHarness();
        afterHarness.context.moverAnuncioRankingFavoritosParaReferencia(
            'SKU-1',
            afterHarness.history[1].grupos[0].anuncios[0],
            afterHarness.history[1].grupos[0].anuncios[2],
            true,
            { entradaId: 'hist-antigo' }
        );
        assert.deepStrictEqual(ids(afterHarness.history[1].grupos[0]), ['MLB2000000002', 'MLB2000000003', 'MLB2000000001'], 'drop depois deve inserir depois do alvo');
    }

    {
        const harness = createHarness();
        const selected = harness.history[1].grupos[0];
        harness.context.moverAnuncioRankingFavoritosParaReferencia(
            'SKU-1',
            selected.anuncios[1],
            selected.anuncios[1],
            false,
            { entradaId: 'hist-antigo' }
        );
        assert.deepStrictEqual(ids(selected), ['MLB2000000001', 'MLB2000000002', 'MLB2000000003']);
        assert.strictEqual(harness.saves.length, 0, 'drop na mesma linha deve ser no-op');
        assert.strictEqual(harness.renders.ranking, 0);
    }

    {
        const harness = createHarness();
        const sourceItem = harness.history[1].grupos[0].anuncios[0];
        const foreignTarget = harness.history[0].grupos[0].anuncios[0];
        harness.context.moverAnuncioRankingFavoritosParaReferencia(
            'SKU-1',
            sourceItem,
            foreignTarget,
            false,
            { entradaId: 'hist-antigo' }
        );
        assert.strictEqual(harness.saves.length, 0, 'drop entre entradas deve ser rejeitado');

        harness.context.moverAnuncioRankingFavoritosParaReferencia(
            'SKU-OUTRO',
            sourceItem,
            harness.history[1].grupos[0].anuncios[1],
            false,
            { entradaId: 'hist-antigo' }
        );
        assert.strictEqual(harness.saves.length, 0, 'drop entre SKUs deve ser rejeitado');
    }

    {
        const harness = createHarness();
        harness.context.moverAnuncioRankingFavoritos('SKU-1', harness.history[1].grupos[0].anuncios[0], -1, { entradaId: 'hist-antigo' });
        harness.context.moverAnuncioRankingFavoritos('SKU-1', harness.history[1].grupos[0].anuncios[2], 1, { entradaId: 'hist-antigo' });
        assert.strictEqual(harness.saves.length, 0, 'primeiro para cima e ultimo para baixo devem ser no-op');
    }

    {
        const harness = createHarness({
            histories: [{
                id: 'hist-mais-recente',
                sku: 'SKU-1',
                ids: ['MLB3000000001', 'MLB3000000002', 'MLB3000000003']
            }],
            ignoredSellers: ['ignorado']
        });
        const group = harness.history[0].grupos[0];
        group.anuncios[1].vendedor = 'ignorado';
        harness.context.moverAnuncioRankingFavoritos('SKU-1', group.anuncios[0], 1, { entradaId: 'hist-mais-recente' });
        assert.deepStrictEqual(ids(group), ['MLB3000000003', 'MLB3000000002', 'MLB3000000001'], 'movimento relativo deve pular vendedor ignorado entre posicoes visiveis');
        assertSinglePersistenceAndRender(harness);
    }

    assert.match(source, /function moverAnuncioRankingFavoritosParaReferencia\(sku, anuncioOrigem, anuncioDestino, colocarDepois, opcoes = \{\}\)/, 'contrato publico da reordenacao por referencia deve permanecer estavel');
    console.log('Favoritos manual ranking order checks passed');
}

run();
