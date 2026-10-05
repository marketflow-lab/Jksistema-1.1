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

async function run() {
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
    const abort = new AbortController();
    abort.abort();
    await assert.rejects(collector.collectFirstPage({ browser: fakeBrowser(), signal: abort.signal }), { name: 'AbortError' });
    for (const code of [scripts.discoveryScript(), scripts.snapshotScript({ seller: { id: '123456' } }), scripts.clickAvantScript(), scripts.prepareScript()]) new Function('return ' + code);
    console.log('Pesquisa de Mercado: ranking compartilhado, 121 cards, lazy load, dados atrasados, login e cancelamento OK');
}
run().catch(error => { console.error(error); process.exitCode = 1; });
