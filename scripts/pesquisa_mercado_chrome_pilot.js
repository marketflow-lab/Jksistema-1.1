'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { createHash } = require('node:crypto');
const { PesquisaMercadoRunner, allowedUrl, sameSeller } = require('../electron_app/main/modules/pesquisa-mercado-runner');
const collector = require('../static/pesquisa-mercado/shared-collector');
const pageScripts = require('../static/pesquisa-mercado/page-scripts');
const { createMemoryApi } = require('./pesquisa_mercado_live_smoke');

const controls = ['MLB4484778552', 'MLB3219208972'];
const ekomHash = 'fcef6718c034e5e74b20ace498ee6651d59c43ce884843e5bc489019e00eb135';
const ROOT = '/api/pesquisa-mercado/jobs';
const fail = code => Object.assign(new Error(code), { code });

function readSample(file) {
    const sample = JSON.parse(fs.readFileSync(file, 'utf8'));
    if (!Array.isArray(sample.controls) || !Array.isArray(sample.ekom)
        || sample.controls.map(row => row.id).join() !== controls.join() || sample.ekom.length !== 48
        || createHash('sha256').update(sample.ekom.map(row => row.id).sort().join(',')).digest('hex') !== ekomHash) {
        throw fail('sample_mismatch');
    }
    for (const row of [...sample.controls, ...sample.ekom]) {
        const offer = new URL(row.url);
        const profile = new URL(row.sellerProfile);
        if (!allowedUrl(row.url) || !allowedUrl(row.sellerProfile) || !/^MLB\d+$/.test(row.id)
            || offer.searchParams.getAll('wid').join() !== row.id || offer.hash
            || !/^(?:seller_id:\d+|official_store:\d+(?:\|virtual_stores:MLB\d+)?)$/.test(offer.searchParams.getAll('pdp_filters').join())
            || offer.searchParams.has('virtual_store_id') && !/^MLB\d+$/.test(offer.searchParams.getAll('virtual_store_id').join())
            || [...offer.searchParams.keys()].some(key => !['pdp_filters', 'wid', 'virtual_store_id'].includes(key))
            || !/^\/(?:loja\/[\w-]+|perfil\/[\w-]+|_CustId_\d+)\/?$/i.test(profile.pathname)
            || profile.search || profile.hash || typeof row.sellerId !== 'string' || !/^\d*$/.test(row.sellerId)) {
            throw fail('sample_invalid');
        }
    }
    if (new Set(sample.ekom.map(row => row.sellerProfile)).size !== 1) throw fail('sample_seller_mismatch');
    return sample;
}

// This observation intentionally does not call the production parser or inspect API responses.
function readVisiblePanel() {
    const visible = node => node && node.getClientRects().length && getComputedStyle(node).visibility !== 'hidden';
    const outside = '[class*="recommend"], [class*="carousel"], aside, [role="dialog"]';
    const roots = [...document.querySelectorAll('#productInfo.avantpro-in-product-content')].filter(node => visible(node) && !node.closest(outside));
    const normalize = value => String(value || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().replace(/\s+/g, ' ').trim();
    const here = new URL(location.href);
    const ids = [...here.searchParams.getAll('wid'), ...new URLSearchParams(here.hash.slice(1)).getAll('wid')];
    const direct = here.pathname.match(/^\/MLB-(\d+)(?:-|$)/i);
    if (direct) ids.push('MLB' + direct[1]);
    if (!roots.length) return { ids: [...new Set(ids)], panelMissing: true };
    if (roots.length !== 1) return null;
    const panel = roots[0];
    if (panel.dataset.itemId) ids.push(panel.dataset.itemId.replace(/^MLB-/, 'MLB'));
    const sellers = [...document.querySelectorAll('.ui-pdp-seller a[href], [data-testid="seller-info"] a[href], #productInfo a[href]')]
        .filter(node => visible(node) && !node.closest(outside))
        .map(node => new URL(node.href))
        .filter(url => url.protocol === 'https:' && /^(www|lista)\.mercadolivre\.com\.br$/.test(url.hostname))
        .filter(url => /^\/(?:loja\/[^/]+|perfil\/[^/]+|_CustId_\d+)\/?$/i.test(url.pathname))
        .map(url => url.origin + url.pathname);
    const fields = new Map();
    for (const leaf of panel.querySelectorAll('span, div, dt, b, strong')) {
        if (!visible(leaf) || leaf.childElementCount || leaf.closest('[role="tooltip"]')) continue;
        const label = normalize(leaf.innerText);
        if (!/^(vendas (?:do anuncio|do item|estimadas|do produto|mensais)|(?:anuncio )?criado em)$/.test(label)) continue;
        const sibling = leaf.nextElementSibling || leaf.parentElement?.nextElementSibling;
        if (visible(sibling)) {
            const values = fields.get(label) || new Set();
            values.add(sibling.innerText.trim());
            fields.set(label, values);
        }
    }
    const read = (...labels) => {
        for (const label of labels) {
            const values = fields.get(label);
            if (values) return values.size === 1 ? [...values][0] : '';
        }
        return '';
    };
    const pill = [...panel.querySelectorAll('span')].filter(node => visible(node) && !node.childElementCount)
        .map(node => node.innerText.trim()).find(value => /^[\d.,]+\s+vendidos?$/i.test(value));
    return { ids: [...new Set(ids)], sellers, sellerId: panel.dataset.sellerId || '',
        offerSales: !!read('vendas do anuncio', 'vendas do item'), panelSellers: [...panel.querySelectorAll('a[href]')]
            .filter(node => visible(node)).map(node => node.href).filter(href => sellers.includes(href)),
        sales: read('vendas do anuncio', 'vendas do item', 'vendas estimadas', 'vendas do produto') || (pill || '').split(/\s/)[0],
        monthly: read('vendas mensais'), date: read('anuncio criado em', 'criado em') };
}

const normalize = s => String(s || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase().trim();
function dateValue(value) {
    const s = normalize(value);
    const month = /^([a-z]{3,})(?:\/|\s+de\s+|\s+)(\d{4})(?:\s*\(aprox\.?\))?$/.exec(s);
    if (month) return month[1].slice(0, 3) + '/' + month[2];
    return /^\d{2}\/\d{2}\/\d{4}$/.test(s) ? s : '';
}
function numberValue(value) {
    const text = normalize(value).replace(/\s*\/\s*mes$/, '');
    if (!/^(?:\d+|\d{1,3}(?:\.\d{3})+)(?:,\d+)?$/.test(text)) return null;
    return Number(text.replace(/\./g, '').replace(',', '.'));
}
function compare(row, item, dom) {
    const ranked = collector.isComplete(item);
    if (!dom || dom.ids.length !== 1 || dom.ids[0] !== row.id) return 'offer_unconfirmed';
    if (dom.panelMissing) return ranked ? 'ranking_unverified' : 'pending';
    const expected = { id: row.sellerId, perfilUrl: row.sellerProfile };
    const numeric = new Set([dom.sellerId, ...dom.sellers.map(p => (p.match(/_CustId_(\d+)/i) || [])[1])].filter(Boolean));
    const expectedNumeric = row.sellerId || (row.sellerProfile.match(/_CustId_(\d+)/i) || [])[1];
    const names = dom.sellers.filter(p => /\/(?:loja|perfil)\//i.test(p));
    const otherStorefront = /\/(?:loja|perfil)\//i.test(row.sellerProfile) && names.some(p => !sameSeller(expected, { perfilUrl: p }));
    if (otherStorefront || numeric.size > 1
        || expectedNumeric && numeric.size && !numeric.has(expectedNumeric)
        || !(dom.sellers.some(p => sameSeller(expected, { perfilUrl: p })) || expectedNumeric && numeric.has(expectedNumeric))) {
        return ranked ? 'seller_unconfirmed' : 'pending';
    }
    const panelSeller = expectedNumeric && dom.sellerId === expectedNumeric || dom.panelSellers?.some(p => sameSeller(expected, { perfilUrl: p }));
    if (row.catalogo && (!dom.offerSales || !panelSeller)) return ranked ? 'catalog_ranking_unverified' : 'pending';
    const sales = numberValue(dom.sales), monthly = numberValue(dom.monthly), date = dateValue(dom.date);
    if (sales === null || monthly === null || !date) return ranked ? 'visible_metrics_missing' : 'pending';
    if (!ranked) return 'visible_metrics_lost';
    if (item.id !== row.id || !item.seller_confirmed || !sameSeller(expected, item)) return 'seller_unconfirmed';
    return sales === item.vendas && monthly === item.media_vendas_mensal && date === dateValue(item.data_criacao)
        && normalize(item.media_mensal_rotulo) === 'vendas mensais' ? 'matched' : 'mismatch';
}

async function runPilot(sample, session, { productionCollector = collector, signal, onProgress = () => {} } = {}) {
    const observed = new Map();
    const memory = createMemoryApi();
    let attempts = 0, active = 0, peak = 0;
    const witnessedScripts = { ...pageScripts, snapshotScript(options) {
        return '/*pilot:observation*/(async()=>{const snapshot=await (' + pageScripts.snapshotScript(options) +
            ');let visual=null;try{visual=(' + readVisiblePanel.toString() + ')();}catch{}return {snapshot,visual};})()';
    } };
    const observedCollector = { ...productionCollector, async collectDetail(options) {
        attempts++; active++; peak = Math.max(peak, active);
        observed.delete(options.expectedItem.id);
        try {
            const instrumented = Object.create(options.browser);
            instrumented.executeJavaScript = async code => {
                const result = await options.browser.executeJavaScript(code);
                if (!code.startsWith('/*pilot:observation*/')) return result;
                if (!options.signal.aborted) observed.set(options.expectedItem.id, result.visual);
                return result.snapshot;
            };
            const result = await productionCollector.collectDetail({ ...options, pageScripts: witnessedScripts, browser: instrumented });
            if (result.authRequired || result.reason === 'page_changed') observed.delete(options.expectedItem.id);
            return result;
        } finally { active--; }
    } };
    const browser = session.createPage();
    const runner = new PesquisaMercadoRunner({ browser, createDetailBrowser: () => session.createPage({ detail: true }), api: memory.api,
        collector: observedCollector, pageScripts });
    runner.controller = new AbortController();
    const interrupt = () => runner.controller.abort();
    signal?.addEventListener('abort', interrupt, { once: true });
    const scope = { key: 'pilot', nome: 'Piloto', url: 'https://www.mercadolivre.com.br/categorias' };
    const totals = { controls: {}, ekom: {}, attempts: 0, peak: 0, status: 'completed' };
    try {
        if (signal?.aborted) throw fail('interrupted');
        runner.job = (await memory.api('POST', ROOT, { category: scope })).job;
        runner.checkpoint = { ...runner.job.checkpoint, currentCategory: scope, progress: {}, incompleteCategories: [], seenSellerKeys: [] };
        runner.seenSellers = new Set();
        await browser.prepare();
        await browser.navigate(sample.controls[0].url, runner.controller.signal);
        const auth = await browser.ensureAvantSession({ signal: runner.controller.signal });
        if (!auth || !auth.authenticated && !auth.collectionAllowed) {
            const safe = new Set(['ml_login_required', 'ml_verification_required', 'avant_login_required',
                'avant_extension_missing', 'avant_manual_login_required', 'avant_session_unconfirmed']);
            totals.status = safe.has(auth?.reason) ? auth.reason : 'authentication_unconfirmed';
            return totals;
        }
        for (const [phase, rows] of Object.entries({ controls: sample.controls, ekom: sample.ekom })) {
            const started = Date.now();
            const groups = new Map();
            for (const row of rows) {
                if (!groups.has(row.sellerProfile)) groups.set(row.sellerProfile, []);
                groups.get(row.sellerProfile).push(row);
            }
            for (const [profile, group] of groups) {
                const seller = { id: group[0].sellerId, perfilUrl: profile, key: group[0].sellerId ? 'id:' + group[0].sellerId : 'perfil:' + profile };
                const initial = group.map(row => ({ id: row.id, url: row.url, seller_id: row.sellerId, seller_perfil_url: profile, catalogo: row.catalogo }));
                const result = await runner.complementSellerListings(seller, { anuncios: initial, reason: 'avant_data_incomplete', incomplete: true }, profile);
                await runner.publishSeller(seller, result);
                delete runner.checkpoint.sellerCollection;
                const ranked = (await memory.api('GET', ROOT + '/' + runner.job.id + '/ranking?seller_key=' + encodeURIComponent(seller.key))).items;
                for (const row of group) {
                    const outcome = compare(row, ranked.find(item => item.id === row.id), observed.get(row.id));
                    totals[phase][outcome] = (totals[phase][outcome] || 0) + 1;
                }
                onProgress({ phase, counts: { ...totals[phase] }, elapsedMs: Date.now() - started, attempts, peak });
            }
            if (phase === 'controls' && !totals.controls.matched) { totals.status = 'controls_unconfirmed'; break; }
        }
        return totals;
    } catch (error) {
        if (runner.controller.signal.aborted) totals.status = 'interrupted';
        else if (error?.waitingLogin) totals.status = 'login_required';
        else throw error;
        return totals;
    } finally {
        totals.peak = peak; totals.attempts = attempts;
        signal?.removeEventListener('abort', interrupt);
        await session.close();
    }
}

async function main(args) {
    if (args.length !== 5 || args[0] !== '--live' || args[1] !== '--sample' || args[3] !== '--profile') throw fail('usage');
    const sample = readSample(args[2]);
    const profile = path.resolve(args[4]);
    if (!process.env.LOCALAPPDATA || profile !== path.resolve(process.env.LOCALAPPDATA, 'JK Pesquisa Avant Teste')) {
        throw fail('dedicated_profile_required');
    }
    if (!fs.existsSync(path.join(profile, 'Local State'))) throw fail('prepared_profile_missing');
    const { createChromeResearchSession } = require('../electron_app/main/modules/pesquisa-mercado-chrome');
    const { createMediaScript } = require('../electron_app/main/modules/pesquisa-mercado-media-preload');
    const session = createChromeResearchSession({ profileDir: profile, allowedUrl, createMediaScript });
    const controller = new AbortController();
    const stop = () => controller.abort();
    process.once('SIGINT', stop);
    try {
        const report = await runPilot(sample, session, { signal: controller.signal,
            onProgress: summary => process.stdout.write(JSON.stringify(summary) + '\n') });
        console.log(JSON.stringify(report));
        if (!accepted(report)) process.exitCode = 2;
    } finally { process.removeListener('SIGINT', stop); }
}

function accepted(report) {
    const accounted = group => (group.matched || 0) + (group.pending || 0);
    return report.status === 'completed' && report.controls.matched >= 1
        && accounted(report.controls) === 2 && accounted(report.ekom) === 48;
}

module.exports = { readSample, readVisiblePanel, compare, runPilot, accepted };
if (require.main === module) main(process.argv.slice(2)).catch(error => {
    const publicCodes = ['usage', 'sample_mismatch', 'sample_invalid', 'sample_seller_mismatch', 'prepared_profile_missing', 'dedicated_profile_required'];
    console.error(JSON.stringify({ status: publicCodes.includes(error?.code) ? error.code : 'pilot_failed' }));
    process.exitCode = 1;
});
