'use strict';

const assert = require('node:assert/strict');
const { chromium } = require('playwright');
const { accepted, compare, readVisiblePanel, runPilot, parseOptions } = require('../scripts/pesquisa_mercado_chrome_pilot');

function sampleRow(n, sellerId) {
    const id = 'MLB' + (1000000000 + n);
    return { id, sellerId, sellerProfile: 'https://lista.mercadolivre.com.br/_CustId_' + sellerId,
        url: 'https://produto.mercadolivre.com.br/MLB-' + id.slice(3) + '-teste-_JM?wid=' + id, catalogo: false };
}
function fixture(row) {
    const stat = (name, value) => '<div class="avt-rounded-lg avt-p-3 avt-text-center avt-border"><div class="avt-mb-1 avt-flex avt-items-center"><span>' +
        name + '</span></div><div class="avt-text-base avt-font-semibold">' + value + '</div></div>';
    return '<main><h1 class="ui-pdp-title">Anúncio</h1><div class="ui-pdp-seller"><a class="ui-pdp-seller__link-trigger" href="' +
        row.sellerProfile + '">Ver vendedor</a></div><div id="productInfo" class="avantpro-in-product-content" data-item-id="' +
        row.id + '" data-seller-id="' + row.sellerId + '">' + stat('Vendas estimadas', '188') +
        stat('Anúncio criado em', '12/03/2025') + stat('Vendas mensais', '5/Mês') + '</div></main>';
}

async function run() {
    const sample = { controls: [sampleRow(1, '1001'), sampleRow(2, '1002')],
        ekom: Array.from({ length: 48 }, (_, n) => sampleRow(3 + n, '1003')) };
    const cli = ['--live', '--sample', 'sample.json', '--profile', 'dedicated'];
    assert.equal(parseOptions(cli).oneControl, false);
    assert.equal(parseOptions(['--one-control', ...cli]).oneControl, true);
    assert.equal(parseOptions([...cli, '--controls-only', '--one-control']).controlsOnly, true);
    assert.throws(() => parseOptions(cli.filter(arg => arg !== '--live')), /usage/);
    assert.throws(() => parseOptions([...cli, '--controls']), /usage/);
    for (const statusCode of [403, 429]) {
        const navigations = [];
        const page = { async prepare() {}, async navigate(url) { navigations.push(url); }, getURL: () => navigations.at(-1),
            getNavigationStatus: () => ({ url: navigations.at(-1), statusCode, retryAfterMs: 120000 }),
            async ensureAvantSession() { assert.fail('a blocked initial document must not be checked for login'); } };
        const started = Date.now();
        let closed = false;
        const report = await runPilot(sample, { createPage: () => page, async close() { closed = true; } }, { oneControl: true });
        assert.deepEqual(navigations, [sample.controls[0].url]);
        assert(closed);
        assert.equal(report.status, 'marketplace_http_' + statusCode);
        assert.equal(report.httpStatus, statusCode);
        assert.equal(report.stage, 'product');
        assert.equal(report.attempts, 0);
        assert(!JSON.stringify(report).includes('mercadolivre.com.br'));
        if (statusCode === 429) {
            assert(Date.parse(report.retryAt) >= started + 120000 && Date.parse(report.retryAt) <= Date.now() + 120000);
        } else assert.equal(report.retryAt, undefined);
        assert(!accepted(report));
    }
    const row = sample.controls[0], observed = { ids: [row.id], sellerId: row.sellerId, sellers: [row.sellerProfile],
        sales: '188', monthly: '5/Mês', date: 'Março de 2025' };
    const ranked = { id: row.id, rankable: true, seller_id: row.sellerId, seller_confirmed: true,
        vendas: 188, vendasFonte: 'avantpro_dom_estruturado', media_vendas_mensal: 5, media_mensal: 5, media_mensal_rotulo: 'vendas mensais',
        data_criacao: 'mar/2025 (aprox.)', avant_metrics_version: 3 };
    assert.equal(compare(row, ranked, observed), 'matched');
    assert.equal(compare(row, ranked, { ...observed, sales: '189' }), 'mismatch');
    assert.equal(compare(row, ranked, { ...observed, sales: '+100' }), 'visible_metrics_missing');
    assert.equal(compare(row, ranked, { ...observed, sellerId: '999' }), 'seller_unconfirmed');
    assert.equal(compare(row, ranked, { ...observed, ids: ['MLB999999999'] }), 'offer_unconfirmed');
    assert.equal(compare(row, { ...ranked, rankable: false }, observed), 'visible_metrics_lost');
    assert.equal(compare(row, { ...ranked, rankable: false }, { ...observed, sales: '' }), 'pending');
    assert.equal(compare({ ...row, catalogo: true }, { ...ranked, rankable: false }, observed), 'pending');
    assert.equal(compare({ ...row, catalogo: true }, ranked, observed), 'catalog_ranking_unverified');
    assert(accepted({ status: 'completed', controls: { matched: 1, pending: 1 }, ekom: { matched: 15, pending: 33 } }));
    assert(!accepted({ status: 'completed', controls: { matched: 1, pending: 1 }, ekom: { matched: 47, visible_metrics_lost: 1 } }));
    const browser = await chromium.launch({ headless: true });
    const byId = new Map([...sample.controls, ...sample.ekom].map(row => [row.id, row]));
    let authenticated = false;
    try {
        const context = await browser.newContext();
        await context.route('**/*', route => {
            const row = byId.get(new URL(route.request().url()).searchParams.get('wid'));
            return route.fulfill({ contentType: 'text/html; charset=utf-8', body: row ? fixture(row) : '<main>Nenhum produto</main>' });
        });
        const catalog = { ...row, catalogo: true, sellerProfile: 'https://www.mercadolivre.com.br/pagina/loja_alvo' };
        const rankedCatalog = { ...ranked, seller_perfil_url: catalog.sellerProfile, data_criacao: '12/03/2025' };
        const panelLink = '<a href="' + catalog.sellerProfile + '?seller_id=' + row.sellerId + '&amp;utm_source=fixture">Loja</a>';
        const panelFixture = fixture(catalog).replace(' data-seller-id="' + row.sellerId + '"', '')
            .replace('Vendas estimadas', 'Vendas do anúncio');
        const detailPage = await context.newPage();
        await detailPage.goto(row.url);
        const readFixture = async link => {
            await detailPage.setContent(panelFixture.replace('</div></main>', link + '</div></main>'));
            return detailPage.evaluate(readVisiblePanel);
        };
        const linked = await readFixture(panelLink);
        assert.deepEqual(linked.panelSellers, [catalog.sellerProfile + '?seller_id=' + row.sellerId]);
        assert.equal(compare(catalog, rankedCatalog, linked), 'matched');
        assert.equal(compare({ ...row, catalogo: true }, rankedCatalog, linked), 'matched', 'panel link retains numeric evidence');
        const outsideOnly = await readFixture('<div class="ui-recommendations">' + panelLink + '</div>');
        assert.deepEqual(outsideOnly.panelSellers, []);
        assert.equal(compare(catalog, rankedCatalog, outsideOnly), 'catalog_ranking_unverified');
        const conflicting = await readFixture(panelLink.replace('loja_alvo', 'outra_loja'));
        assert.equal(compare(catalog, rankedCatalog, conflicting), 'seller_unconfirmed');
        await detailPage.close();
        const makeSession = () => {
            const pages = [];
            return {
                createPage() {
                    let page;
                    return { async prepare() { if (!page) { page = await context.newPage(); pages.push(page); } },
                        getURL() { return page?.url() || ''; }, async navigate(url) { await page.goto(url); },
                        async executeJavaScript(code) { return page.evaluate(code); },
                        async ensureAvantSession() { return authenticated ? { collectionAllowed: true } : { reason: 'ml_login_required' }; } };
                },
                async close() { await Promise.all(pages.map(page => page.close())); }
            };
        };
        const noLogin = await runPilot(sample, makeSession());
        assert.equal(noLogin.status, 'ml_login_required');
        assert.equal(noLogin.attempts, 0);
        authenticated = true;
        for (const options of [{ oneControl: true }, { controlsOnly: true }]) {
            const small = await runPilot(sample, makeSession(), options);
            assert.equal(small.controls.matched, options.oneControl ? 1 : 2);
            assert.equal(small.attempts, small.controls.matched);
            assert.deepEqual(small.ekom, {});
            assert(accepted(small));
        }
        const progress = [];
        const result = await runPilot(sample, makeSession(), { onProgress: update => progress.push(update) });
        assert.deepEqual(result, { controls: { matched: 2 }, ekom: { matched: 48 },
            peak: 8, attempts: 50, status: 'completed' });
        assert.deepEqual(progress.map(item => item.phase), ['controls', 'controls', 'ekom']);
        console.log('Pesquisa de Mercado Chrome pilot: 50 independent DOM comparisons, 8 pages, login and mismatches OK');
    } finally { await browser.close(); }
}
run().catch(error => { console.error(error); process.exitCode = 1; });
