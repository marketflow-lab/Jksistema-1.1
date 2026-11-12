'use strict';

// Execute the real queue with controlled cache waits and synthetic authorization failures.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '..', 'static', 'importacoes_lista.html'), 'utf8');
const start = html.indexOf('        async function analisarMargensConcorrentesAutomaticamente(');
assert(start >= 0, 'Fila real não encontrada.');
const end = html.indexOf('\n        }', start) + '\n        }'.length;
assert(end > start);
const source = html.slice(start, end);

async function verificar(mode, status) {
    let readyResolve, unblock, timeout;
    const ready = new Promise(resolve => { readyResolve = resolve; });
    const items = [{ SKU: 'A' }, { SKU: 'B' }];
    const analysis = { revisao_fontes_custo: 'rev', custo_unitario: 10 };
    const fonte = { anuncios_loja_fonte: 'loja_lista_sku', anuncios_loja_store_id: 'store' };
    const record = { analise: analysis, fontes: fonte, calculadoEm: Date.now() };
    const state = { revoked: false, cacheWaits: 0, invalidations: 0, patches: 0, fallbackReads: 0 };
    const denied = () => Object.assign(new Error('Acesso indisponível.'), { status });

    async function bloquearCache(options) {
        state.cacheWaits++;
        const result = new Promise(resolve => { unblock = resolve; });
        readyResolve();
        await result;
        assert.equal(options.vigente(), false, 'A gravação/leitura deve retornar depois da revogação.');
        // The real cache helper returns null when its caller is no longer authorized.
        return null;
    }

    const helper = {
        fontesRecentes: () => true, identidadeFontes: () => ({}),
        mesmosInsumos: value => Boolean(value), financeiroRecente: () => true, analiseCompativel: () => false,
        salvar: async options => {
            assert.notEqual(mode, 'cache-read');
            return bloquearCache(options);
        },
        ler: async options => {
            assert.equal(mode, 'cache-read');
            return bloquearCache(options);
        },
    };
    const context = {
        setTimeout, Map, Date, Promise, document: { hidden: false },
        ultimaListaCarregada: { store_id: 'store' }, ultimosItensCarregados: items,
        calculosUnitariosLista: {
            A: { revisao: 'rev', assinatura: 'sig', calculo: { custoUnitario: 10 } },
            B: { revisao: 'rev', assinatura: 'sig', calculo: { custoUnitario: 10 } },
        },
        cacheMargensLista: mode === 'cache-read' ? {} : { A: record },
        filaMargensLista: null, cicloAnaliseMargensAutomaticas: 0, estadosAnaliseMargensAutomaticas: new Map(),
        contextoCarregamentoAtual: () => ({ geracao: 1 }), carregamentoVigente: () => true,
        chaveSkuCustoCadastro: value => value, obterConcorrenciaMargensLista: async () => 2,
        restaurarMargensCompartilhadasLista: async () => {}, priorizarPendentesMargens: () => {},
        definirEstadoMargensAutomaticas: () => {}, atualizarCelulaMargensAutomaticas: () => {},
        obterAuthHeaders: () => ({}), obterSkuItemMargensAutomaticas: item => item.SKU,
        montarPayloadMargensAutomaticas: () => ({ assinatura_calculo: 'sig', revisao_fontes_custo: 'rev' }),
        opcoesCacheMargensLista: () => ({}), salvarSnapshotLista: async () => {}, agendarAtualizacaoMargensLista: () => {},
        consultarAnaliseLista: async url => {
            if (!url.includes('sku=B')) return { dados: fonte };
            await ready;
            if (mode === 'fallback-denial') throw Object.assign(new Error('Fonte temporariamente indisponível.'), { status: 503 });
            throw denied();
        },
        carregarAnunciosMargensAutomaticas: async (_lista, sku) => {
            assert.equal(sku, 'B');
            assert.equal(mode, 'fallback-denial');
            state.fallbackReads++;
            throw denied();
        },
        salvarMargensAutomaticasSku: async () => {
            state.patches++;
            return { analise_concorrentes: analysis };
        },
        window: {
            JKImportacoesCacheMargens: helper,
            JKImportacoesCacheCustos: { comTravaAnalise: async (options, run) => {
                if (!options.vigente()) throw new Error('Análise substituída.');
                return run();
            } },
            JKCachePersistente: { invalidar: async tags => {
                assert.deepEqual(Array.from(tags), ['analises_importacao']);
                state.revoked = true;
                state.invalidations++;
                unblock();
            } },
        },
    };
    vm.createContext(context);
    vm.runInContext(source, context);
    try {
        await Promise.race([
            context.analisarMargensConcorrentesAutomaticamente('L', items),
            new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error('A fila não terminou após revogar o acesso.')), 3000); }),
        ]);
        assert.equal(state.cacheWaits, 1, 'A corrida deve atravessar a espera de cache real da fila.');
        assert.equal(state.revoked, true);
        assert.equal(state.invalidations, 1);
        assert.equal(state.fallbackReads, mode === 'fallback-denial' ? 1 : 0);
        assert.equal(state.patches, 0, 'A fila não pode iniciar PATCH após a negação de acesso.');
        assert.equal(Boolean(items[0].analise_simulacao_importacao_sku), false, 'Cache tardio não pode restaurar margem apagada.');
        assert.deepEqual(Object.keys(context.cacheMargensLista), []);
        assert.equal(context.filaMargensLista, null);
    } finally {
        clearTimeout(timeout);
    }
}

(async () => {
    for (const mode of ['cached-write', 'cache-read', 'fallback-denial']) {
        for (const status of [401, 403]) await verificar(mode, status);
    }
    console.log('Margens paralelas: 401/403 durante leitura/gravação do cache e consulta alternativa interrompem toda a fila: OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
