'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const ranking = require('../static/pesquisa-mercado/shared-ranking');
const collector = require('../static/pesquisa-mercado/shared-collector');
const scripts = require('../static/pesquisa-mercado/page-scripts');
const { searchRankingSource } = require('./helpers/favoritos_search_ranking_sources');

const now = Date.parse('2026-10-05T12:00:00Z');
const created = new Date(now - 30.4375 * 86400000).toISOString();
assert.equal(ranking.calculateMetrics({ vendas: 100, data_criacao: created, media_mensal: 999 }, now).media, 100);
assert.equal(ranking.calculateMetrics({ vendas: 100, media_mensal: '7,5' }, now).media, 7.5);
assert.equal(ranking.calculateMetrics({ vendas: null, data_criacao: created, media_mensal: 4 }, now).media, 4);
assert.equal(ranking.calculateMetrics({ vendas: 1, data_criacao: new Date(now + 86400000).toISOString(), media_mensal: 3 }, now).media, 3);
assert.equal(collector.isComplete({ vendas: null, media_mensal: 4, rankable: true }, now), false, 'media sozinha nao representa coleta completa');
assert.equal(collector.isComplete({ vendas: 0, media_mensal: 0, rankable: true }, now), true, 'zeros informados continuam completos');
assert.equal(collector.isComplete({ vendas: 100, data_criacao: created, rankable: true }, now), true, 'total e data permitem media calculada');
assert.equal(collector.isComplete({ vendas: 100, media_mensal: 4, rankable: false }, now), false, 'seller ou catalogo bloqueado nao completa');
const derived = collector.finalizeListings([{ vendas: 100, data_criacao: created, vendasFonte: 'avantpro_dom_estruturado', rankable: true }], now)[0];
assert.equal(derived.media_vendas_mensal, 100);
assert.equal(derived.media_mensal_fonte, 'calculado_vendas_dias_avantpro');
assert.equal(derived.media_vendas_mensal_fonte, derived.media_mensal_fonte);
assert.equal(collector.finalizeListings([{ vendas: null, media_mensal: 4, rankable: true }], now)[0].rank, null);

const ranked = ranking.rankListings([
    { id: 'unknown', vendas: 999999, media_mensal: 9999, rankable: false },
    { id: 'b', vendas: 50, media_mensal: 10, posicao: 2, ordem_coleta: 2 },
    { id: 'a', vendas: 60, media_mensal: 10, posicao: 3, ordem_coleta: 1 },
    { id: 'c', vendas: 20, media_mensal: 20, posicao: 9 }
], now);
assert.deepEqual(ranked.map(item => [item.id, item.rank]), [['c', 1], ['a', 2], ['b', 3], ['unknown', null]]);

function compareRealFavoritosRanking() {
    const root = path.resolve(__dirname, '..');
    class FrozenDate extends Date {
        constructor(...args) { super(...(args.length ? args : [now])); }
        static now() { return now; }
    }
    const legacyContext = vm.createContext({
        console, Date: FrozenDate, FavoritosV2: { browser: {} },
        // No ignored listings in this fixture; calculation, source validation and sorting are real.
        filtrarAnunciosIgnoradosRanking: items => items.filter(Boolean)
    });
    legacyContext.window = legacyContext;
    legacyContext.globalThis = legacyContext;
    for (const relative of ['static/pesquisa-mercado/shared-ranking.js', 'static/favoritos/v2/browser/browser-ui.js']) {
        vm.runInContext(fs.readFileSync(path.join(root, relative), 'utf8'), legacyContext, { filename: relative });
    }
    const legacySource = fs.readFileSync(path.join(root, 'static/favoritos/ranking.js'), 'utf8');
    const salesStart = legacySource.indexOf('function parseNumeroVendas(');
    const salesEnd = legacySource.indexOf('function formatarVendasAvantPro(', salesStart);
    assert.ok(salesStart >= 0 && salesEnd > salesStart, 'bloco real de validacao de fontes de Favoritos disponivel');
    vm.runInContext(legacySource.slice(salesStart, salesEnd), legacyContext, { filename: 'favoritos-ranking-sales.js' });
    vm.runInContext(searchRankingSource(root), legacyContext, { filename: 'favoritos-search-ranking-components.js' });

    const monthsAgo = months => new Date(now - months * 30.4375 * 86400000).toISOString();
    const input = [
        { id: 'collection-b', vendas: 50, media_mensal: 10, posicao: 8 },
        { id: 'later-position', vendas: 50, media_mensal: 10, posicao: 9 },
        { id: 'higher-sales', vendas: 60, media_mensal: 10, posicao: 10 },
        { id: 'higher-average', vendas: 5, media_mensal: 11, posicao: 20 },
        { id: 'earlier-position', vendas: 50, data_criacao: monthsAgo(5), media_mensal: 999, posicao: 1 },
        { id: 'historical-average', vendas: 100, data_criacao: monthsAgo(10), media_mensal: 999, posicao: 30 },
        { id: 'collection-a', vendas: 50, media_mensal: 10, posicao: 8 },
        { id: 'missing-date-direct-average', vendas: 900, media_mensal: '7,5', posicao: 1 },
        { id: 'zero-sales', vendas: 0, data_criacao: monthsAgo(1), posicao: 1 },
        { id: 'missing-date-and-average', vendas: 90, posicao: 1 }
    ].map((item, index) => ({ ...item, vendasFonte: 'avantpro_dom', media_mensal_fonte: 'avantpro_dom', ordem_coleta: index }));
    const plain = value => JSON.parse(JSON.stringify(value));
    const metric = value => ({ vendas: value.vendas, media: value.media, meses: value.meses, dataCriacao: value.dataCriacao && value.dataCriacao.toISOString() });
    for (const item of input) {
        assert.deepStrictEqual(
            metric(legacyContext.calcularMetricasMediaVendas(plain(item))),
            metric(ranking.calculateMetrics(plain(item), now)),
            'mesmas metricas nas interfaces reais para ' + item.id
        );
    }
    const favorites = legacyContext.FavoritosV2.searchRanking.publicApi.ranking.ordenarAnunciosFavoritosRanking(plain(input), '');
    const market = ranking.rankListings(plain(input), now);
    const expectedIds = ['higher-average', 'historical-average', 'higher-sales', 'earlier-position', 'collection-b', 'collection-a', 'later-position', 'missing-date-direct-average', 'zero-sales', 'missing-date-and-average'];
    assert.deepStrictEqual(Array.from(favorites, item => item.id), expectedIds, 'Favoritos desempata por vendas, posicao e ordem de coleta');
    assert.deepStrictEqual(market.map(item => item.id), expectedIds, 'Pesquisa de Mercado conserva a mesma ordem');
    const rankedMetrics = items => Array.from(items, item => ({ id: item.id, media: item.media_vendas_mensal, meses: item.meses_desde_criacao }));
    assert.deepStrictEqual(rankedMetrics(favorites), rankedMetrics(market), 'interfaces reais retornam ordem e medias identicas');
    assert.deepStrictEqual(input.map(item => item.id), ['collection-b', 'later-position', 'higher-sales', 'higher-average', 'earlier-position', 'historical-average', 'collection-a', 'missing-date-direct-average', 'zero-sales', 'missing-date-and-average'], 'cada interface recebeu copia independente');

    // Source policy remains owned by Favoritos; PM's stricter seller/Avant checks happen before ranking.
    assert.equal(legacyContext.calcularMetricasMediaVendas({ vendas: 80, vendasFonte: 'unknown' }).vendas, null);
    assert.equal(legacyContext.calcularMetricasMediaVendas({ vendas: 80, vendasFonte: 'api_item_vendas' }).vendas, 80);
    assert.equal(legacyContext.calcularMetricasMediaVendas({ vendas: '1.000', vendasFonte: 'avantpro_dom' }).vendas, 1000);
    console.log('Favoritos x Pesquisa de Mercado: interfaces reais, relogio fixo, empates e datas ausentes OK');
}
compareRealFavoritosRanking();

function fakeBrowser({ count = 121, lazy = true, delayed = true, login = false, absent = false, changed = false } = {}) {
    let clock = 0, scans = 0, scrolled = 0, clicked = 0;
    const url = 'https://lista.mercadolivre.com.br/_CustId_123456';
    return {
        now: () => clock,
        wait: async ms => { clock += ms; },
        getURL: () => url,
        navigate() { throw new Error('A coleta da primeira pagina nao pode navegar.'); },
        async executeJavaScript(code) {
            if (code.includes('/*pm:prepare*/')) return true;
            if (code.includes('/*pm:scroll*/')) { scrolled += 1; return {}; }
            if (code.includes('/*pm:avant-click*/')) { clicked += 1; return { clicked: clicked <= 2 ? 1 : 0, eligiblePending: 0 }; }
            assert.ok(code.includes('/*pm:snapshot*/'));
            scans += 1;
            const visible = lazy && scrolled < 2 ? 20 : count;
            return { url: changed ? url + '_Desde_51' : url, height: visible * 40, atBottom: !lazy || scrolled >= 2, authRequired: login,
                anuncios: Array.from({ length: visible }, (_, index) => ({ id: 'MLB' + (1000000 + index), vendas: 20, vendasFonte: 'avantpro_dom', media_mensal: 4, media_mensal_fonte: 'avantpro_dom', rankable: !absent && (!delayed || clicked >= 2), status: 'sem_dados_avant' })) };
        },
        get scans() { return scans; }
    };
}

function slowAvantLazyBrowser() {
    let clock = 0, visible = 1, clicked = 0, firstClickVisible = null;
    const url = 'https://lista.mercadolivre.com.br/_CustId_123456';
    return {
        now: () => clock,
        wait: async ms => { clock += ms; },
        getURL: () => url,
        navigate() { throw new Error('A coleta nunca deve navegar para a segunda pagina.'); },
        async executeJavaScript(code) {
            if (code.includes('/*pm:prepare*/')) return true;
            if (code.includes('/*pm:scroll*/')) {
                if (!code.includes('const y=0;')) visible = Math.min(121, visible + 1);
                return {};
            }
            if (code.includes('/*pm:avant-click*/')) {
                if (firstClickVisible === null) firstClickVisible = visible;
                clock += 4500;
                clicked += 1;
                return { clicked: 1, eligiblePending: 121 - clicked };
            }
            assert.ok(code.includes('/*pm:snapshot*/'));
            return { url, height: visible * 100, atBottom: visible === 121 && clicked === 0,
                anuncios: Array.from({ length: visible }, (_, index) => ({
                    id: 'MLB' + (2000000 + index), rankable: index < clicked,
                    vendas: index < clicked ? 20 : null, vendasFonte: index < clicked ? 'avantpro_dom' : '',
                    media_mensal: index < clicked ? 4 : null, media_mensal_fonte: index < clicked ? 'avantpro_dom' : '',
                    status: index < clicked ? 'analisado' : 'sem_dados_avant'
                })) };
        },
        get firstClickVisible() { return firstClickVisible; }
    };
}

async function testDetailObservationHooks() {
    const url = 'https://produto.mercadolivre.com.br/MLB-1000000-fixture';
    const target = { id: 'MLB1000000', url, seller_id: '123456', seller_confirmed: true,
        vendas: 100, vendasFonte: 'avantpro_dom_estruturado', media_mensal: 5,
        media_mensal_fonte: 'avantpro_dom_estruturado', rankable: true, status: 'analisado' };
    const pending = { ...target, vendas: null, media_mensal: null, rankable: false, status: 'sem_quantidade_avant' };
    const conflict = { ...target, seller_id: '999999', seller_confirmed: false, rankable: false,
        status: 'vendedor_nao_confirmado', erro: 'Identidade divergente.' };
    const frames = [[{ ...target, id: 'MLB9999999', vendas: 999999 }, pending], [target], [conflict]];
    let clock = 0, reads = 0, navigationComplete = false;
    const events = [], observations = [];
    const browser = {
        getURL: () => url,
        navigate: async () => { events.push('navigate'); clock += 3500; },
        executeJavaScript: async code => {
            assert.equal(navigationComplete, true, 'nenhum script pode rodar antes do hook de navegacao concluida');
            if (code.includes('/*pm:snapshot*/')) {
                events.push('snapshot');
                const anuncios = frames[Math.min(reads++, frames.length - 1)];
                return { url, height: 100, atBottom: true, anuncios };
            }
            events.push(code.includes('/*pm:prepare*/') ? 'prepare' : 'control');
            return {};
        }
    };
    const result = await collector.collectDetail({ browser, url, expectedItem: { id: target.id }, now: () => clock,
        wait: async ms => { clock += ms; }, settleMs: 20, missingWaitMs: 40, pollMs: 10,
        onNavigationStart: async () => { await Promise.resolve(); events.push('start'); },
        onNavigationComplete: async () => { await Promise.resolve(); events.push('complete'); navigationComplete = true; },
        onPartial: async partial => { await Promise.resolve(); events.push('partial'); observations.push(JSON.parse(JSON.stringify(partial))); }
    });
    assert.deepEqual(events.slice(0, 6), ['start', 'navigate', 'complete', 'prepare', 'control', 'snapshot']);
    assert.ok(observations.length >= 3, 'cada snapshot observado deve produzir um parcial atualizado');
    assert.ok(observations.every(item => item.reason === 'partial' && item.primeira_pagina === false && item.total === 1));
    assert.ok(observations.every(item => item.anuncios[0].id === target.id), 'parcial aplica expectedItem antes do callback');
    assert.equal(observations[0].anuncios[0].rank, null);
    assert.equal(observations[1].anuncios[0].media_vendas_mensal, 5, 'parcial usa o resultado normalizado pelo ranking');
    assert.equal(observations[1].anuncios[0].rank, 1);
    assert.equal(observations[1].com_dados_avant, 1);
    const last = observations.at(-1);
    assert.equal(last.anuncios[0].status, 'vendedor_nao_confirmado');
    assert.equal(last.anuncios[0].rankable, false, 'identidade contraditoria substitui o parcial confirmado anterior');
    assert.equal(last.anuncios[0].rank, null);
    assert.equal(last.incompletos, 1);
    assert.equal(result.anuncios[0].rankable, false);
    assert.ok(last.elapsedMs < clock && last.elapsedMs < 3500, 'elapsed do coletor conta somente a etapa depois da navegacao');

    const noNavigation = fakeBrowser({ count: 1, lazy: false, delayed: false });
    let started = 0, completed = 0;
    await collector.collectDetail({ browser: noNavigation, now: noNavigation.now, wait: noNavigation.wait,
        settleMs: 20, pollMs: 10, onNavigationStart: () => { started += 1; }, onNavigationComplete: () => { completed += 1; } });
    assert.equal(started, 0, 'sem url nao inicia uma navegacao ficticia');
    assert.equal(completed, 1, 'sem url inicia o orcamento de coleta da pagina atual');

    for (const hook of ['onNavigationStart', 'onNavigationComplete', 'onPartial']) {
        const controller = new AbortController();
        let navigated = 0, executed = 0, waited = 0, published = 0;
        const cancellationBrowser = {
            getURL: () => url,
            navigate: async () => { navigated += 1; },
            executeJavaScript: async code => {
                executed += 1;
                return code.includes('/*pm:snapshot*/') ? { url, height: 100, atBottom: true, anuncios: [target] } : {};
            }
        };
        const hooks = { [hook]: async () => { published += 1; controller.abort(); } };
        await assert.rejects(collector.collectDetail({ browser: cancellationBrowser, url, signal: controller.signal,
            wait: async () => { waited += 1; }, ...hooks }), { name: 'AbortError' });
        assert.equal(published, 1);
        assert.equal(navigated, hook === 'onNavigationStart' ? 0 : 1);
        assert.equal(executed, hook === 'onPartial' ? 3 : 0, 'cancelamento no hook impede a proxima etapa');
        assert.equal(waited, 0, 'cancelamento no parcial impede espera ou clique subsequente');
    }

    for (const [frame, reason] of [[null, 'invalid_page_result'],
        [{ url, authRequired: true, anuncios: [target] }, 'ml_login_required'],
        [{ url, avantAuthRequired: true, anuncios: [target] }, 'avant_login_required'],
        [{ url: url + '-outra', anuncios: [target] }, 'page_changed']]) {
        let scan = 0;
        const invalidations = [];
        const rejectedBrowser = { getURL: () => url, executeJavaScript: async code => code.includes('/*pm:snapshot*/')
            ? scan++ === 0 ? { url, height: 100, atBottom: true, anuncios: [target] } : frame : {} };
        const rejected = await collector.collectDetail({ browser: rejectedBrowser,
            onPartial: partial => { invalidations.push(partial); }, wait: async () => {} });
        assert.equal(invalidations.length, 2, 'observacao confirmada e depois invalidada pelo proximo snapshot');
        assert.equal(invalidations[0].reason, 'partial');
        assert.equal(invalidations[0].com_dados_avant, 1);
        assert.equal(invalidations[1].reason, reason, 'caller recebe motivo que veta reutilizar observacao anterior');
        assert.equal(invalidations[1].authRequired, reason.endsWith('login_required'));
        assert.deepEqual(invalidations[1], rejected, 'invalidacao usa o mesmo resultado final de compatibilidade');
    }

    let blockedClock = 0;
    const blockedObservations = [];
    const blockedQueueBrowser = { getURL: () => url, executeJavaScript: async code => {
        if (code.includes('/*pm:snapshot*/')) return { url, height: 100, atBottom: true, anuncios: [pending] };
        return code.includes('/*pm:avant-click*/') ? { loginBlocked: true } : {};
    } };
    const queueLogin = await collector.collectDetail({ browser: blockedQueueBrowser, now: () => blockedClock,
        wait: async ms => { blockedClock += ms; }, settleMs: 20, pollMs: 10,
        onPartial: partial => { blockedObservations.push(partial); } });
    assert.equal(queueLogin.reason, 'avant_login_required');
    assert.equal(blockedObservations.at(-1).authRequired, true, 'login detectado pela fila tambem invalida observacoes anteriores');
    assert.equal(blockedObservations.at(-1).reason, 'avant_login_required');

    let catalogClock = 0, lastCatalog;
    const catalog = { ...target, catalogo: true, rankable: false, status: 'catalogo_sem_evidencia_vendedor',
        erro: 'O catalogo nao comprovou vendas deste vendedor.' };
    const catalogBrowser = { getURL: () => url, executeJavaScript: async code => code.includes('/*pm:snapshot*/')
        ? { url, height: 100, atBottom: true, anuncios: [catalog] } : {} };
    await collector.collectDetail({ browser: catalogBrowser, now: () => catalogClock, wait: async ms => { catalogClock += ms; },
        settleMs: 20, pollMs: 10, missingWaitMs: 40, onPartial: partial => { lastCatalog = partial; } });
    assert.equal(lastCatalog.anuncios[0].vendas, 100, 'parcial conserva valores observados do catalogo');
    assert.equal(lastCatalog.anuncios[0].media_vendas_mensal, 5);
    assert.equal(lastCatalog.anuncios[0].rankable, false, 'parcial nao afrouxa prova de vendedor no catalogo');
    assert.equal(lastCatalog.anuncios[0].rank, null);
    console.log('Pesquisa de Mercado: hooks de navegacao, parcial filtrado, conflito, catalogo e cancelamento OK');
}

async function run() {
    await testDetailObservationHooks();
    const fake = fakeBrowser();
    let updates = 0;
    const result = await collector.collectFirstPage({ browser: fake, now: fake.now, wait: fake.wait, settleMs: 20, pollMs: 10, onProgress: () => { updates += 1; } });
    assert.equal(result.anuncios.length, 121, 'todos os cards da primeira pagina, inclusive apos lazy loading');
    assert.equal(result.incomplete, false);
    assert.equal(result.reason, 'complete');
    assert.equal(result.anuncios[0].media_vendas_mensal, 4);
    assert.equal(result.anuncios[120].rank, 121);
    assert.ok(updates > 2, 'dados atrasados do Avant precisam ser aguardados');
    const slow = slowAvantLazyBrowser();
    const partial = await collector.collectFirstPage({ browser: slow, now: slow.now, wait: slow.wait, timeoutMs: 180000 });
    assert.equal(slow.firstClickVisible, 121, 'toda a primeira pagina deve estar materializada antes do primeiro clique Avant');
    assert.equal(partial.anuncios.length, 121, 'cliques de 4,5 segundos nao podem impedir a descoberta dos 121 cards lazy');
    assert.equal(partial.reason, 'timeout');
    assert.equal(partial.incomplete, true);
    assert.ok(partial.com_dados_avant > 0 && partial.incompletos > 0, 'prazo preserva tanto dados obtidos quanto cards pendentes');
    assert.ok(partial.anuncios.filter(item => !item.rankable).every(item => item.rank === null), 'pendentes nao recebem posicao no ranking');
    const absent = fakeBrowser({ count: 3, lazy: false, absent: true });
    const incomplete = await collector.collectFirstPage({ browser: absent, now: absent.now, wait: absent.wait, settleMs: 20, missingWaitMs: 40, pollMs: 10 });
    assert.equal(incomplete.reason, 'avant_data_incomplete');
    assert.equal(incomplete.incomplete, true);
    assert.ok(incomplete.anuncios.every(item => !item.rankable));
    const login = fakeBrowser({ login: true });
    assert.equal((await collector.collectFirstPage({ browser: login, now: login.now, wait: login.wait })).authRequired, true);
    const changed = fakeBrowser({ changed: true });
    assert.equal((await collector.collectFirstPage({ browser: changed, now: changed.now, wait: changed.wait })).reason, 'page_changed');
    const wrongItem = fakeBrowser({ count: 1, lazy: false, delayed: false });
    const wrongDetail = await collector.collectDetail({ browser: wrongItem, expectedItem: { id: 'MLB9999999' }, now: wrongItem.now, wait: wrongItem.wait, settleMs: 20, missingWaitMs: 40, pollMs: 10 });
    assert.equal(wrongDetail.anuncios.length, 0, 'complemento nao aceita dados de outro anuncio');
    const abortAfterNavigation = new AbortController();
    let afterNavigationReads = 0;
    await assert.rejects(collector.collectDetail({ browser: {
        navigate: async () => abortAfterNavigation.abort(),
        getURL: () => 'https://produto.mercadolivre.com.br/MLB-1000000-fixture',
        executeJavaScript: async () => { afterNavigationReads += 1; return {}; }
    }, url: 'https://produto.mercadolivre.com.br/MLB-1000000-fixture', signal: abortAfterNavigation.signal }), { name: 'AbortError' });
    assert.equal(afterNavigationReads, 0, 'cancelamento durante navigate impede scripts de complemento');
    const abort = new AbortController();
    abort.abort();
    await assert.rejects(collector.collectFirstPage({ browser: fakeBrowser(), signal: abort.signal }), { name: 'AbortError' });
    for (const code of [scripts.discoveryScript(), scripts.snapshotScript({ seller: { id: '123456' } }), scripts.clickAvantScript(), scripts.prepareScript()]) new Function('return ' + code);
    console.log('Pesquisa de Mercado: ranking compartilhado, 121 cards, lazy load, dados atrasados, login e cancelamento OK');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
