'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const origin = 'http://127.0.0.1:8395';
const script = fs.readFileSync(path.join(__dirname, '..', 'static', 'cache-persistente.js'));
const revisionKey = 'jk.ui.cache.revisao.v1:';
const analysisTag = 'analises_importacao';
const url = sku => '/api/medias-compras/listas-pedidos/A/skus/' + sku + '/analise-concorrentes';
const gate = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

async function ambiente(browser) {
    const context = await browser.newContext();
    const holds = new Map();
    const errors = [];
    await context.addInitScript(() => {
        localStorage.setItem('user_data', JSON.stringify({ client_id: 'synthetic', username: 'tester' }));
        localStorage.setItem('access_token', 'synthetic');
        localStorage.setItem('permissions', JSON.stringify({ importacoes: true }));
    });
    await context.route('**/*', async route => {
        const request = new URL(route.request().url());
        if (request.pathname === '/cache-persistente.js') return route.fulfill({ contentType: 'application/javascript', body: script });
        if (request.pathname.startsWith('/api/')) {
            const hold = holds.get(request.searchParams.get('hold'));
            if (hold) { hold.started.resolve(); await hold.release.promise; }
            return route.fulfill({ contentType: 'application/json', body: '{"success":true}' }).catch(() => {});
        }
        return route.fulfill({ contentType: 'text/html', body: '<!doctype html><script src="/cache-persistente.js"></script>' });
    });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin);
    await page.evaluate(() => {
        window.responses = {};
        window.pending = {};
        window.previous = JKCachePersistente.contexto();
    });
    return {
        page, errors,
        async start(name, sku) {
            const hold = { started: gate(), release: gate() };
            holds.set(name, hold);
            await page.evaluate(({ name, url }) => {
                pending[name] = fetch(url + '?hold=' + name, { method: 'PATCH' }).then(response => { responses[name] = response; });
            }, { name, url: url(sku) });
            await hold.started.promise;
        },
        async finish(name) {
            holds.get(name).release.resolve();
            await page.evaluate(name => pending[name], name);
        },
        async close() {
            for (const hold of holds.values()) hold.release.resolve();
            assert.deepEqual(errors, []);
            await context.close();
        },
    };
}

async function recibosConcorrentes(browser) {
    const env = await ambiente(browser);
    try {
        await env.start('primeiro', '001');
        await env.start('segundo', '002');
        await env.finish('segundo');
        assert.deepEqual(await env.page.evaluate(() => {
            window.intermediate = JKCachePersistente.confirmarAnaliseImportacao(previous, responses.segundo);
            return { specialized: !!intermediate, strict: !!JKCachePersistente.confirmarMutacao(previous, responses.segundo) };
        }), { specialized: true, strict: true });
        await env.finish('primeiro');
        assert.deepEqual(await env.page.evaluate(async () => {
            const first = JKCachePersistente.confirmarAnaliseImportacao(previous, responses.primeiro);
            const second = JKCachePersistente.confirmarAnaliseImportacao(previous, responses.segundo);
            const advanced = JKCachePersistente.confirmarAnaliseImportacao(intermediate, responses.primeiro);
            const olderAfterAdvance = JKCachePersistente.confirmarAnaliseImportacao(first, responses.segundo);
            return {
                first: !!first, second: !!second, advanced: !!advanced, olderAfterAdvance: !!olderAfterAdvance,
                strictFirst: !!JKCachePersistente.confirmarMutacao(previous, responses.primeiro),
                strictSecond: !!JKCachePersistente.confirmarMutacao(previous, responses.segundo),
                current: JKCachePersistente.dadosVigentes(first, ['analises_importacao', 'custos_importacao', 'importacoes', 'vendas']),
                saved: await JKCachePersistente.salvar('after-concurrent-analysis', { ok: true },
                    { contexto: first, tags: ['importacoes', 'analises_importacao'] }),
            };
        }), { first: true, second: true, advanced: true, olderAfterAdvance: true,
            strictFirst: false, strictSecond: false, current: true, saved: true });
    } finally { await env.close(); }
}

async function alterar(page, change) {
    await page.evaluate(async ({ change, revisionKey, analysisTag }) => {
        if (change.startsWith('revision:')) {
            localStorage.setItem(revisionKey + change.slice('revision:'.length), 'data:synthetic-concurrent-source');
        } else if (change === 'auth') {
            localStorage.setItem('access_token', 'synthetic-new-session');
        } else if (change === 'tenant') {
            localStorage.setItem('user_data', JSON.stringify({ client_id: 'other-tenant', username: 'tester' }));
        } else if (change === 'manual-analysis') {
            await JKCachePersistente.invalidar([analysisTag]);
        } else if (change === 'revocation-only-analysis' || change === 'revocation-only-import') {
            const tag = change === 'revocation-only-analysis' ? analysisTag : 'importacoes';
            const before = localStorage.getItem(revisionKey + tag);
            await JKCachePersistente.invalidar([tag]);
            if (before === null) localStorage.removeItem(revisionKey + tag);
            else localStorage.setItem(revisionKey + tag, before);
        } else if (change === 'unknown-analysis') {
            localStorage.setItem(revisionKey + analysisTag, 'unknown-legacy-revision');
        } else if (change === 'erased-analysis') {
            localStorage.removeItem(revisionKey + analysisTag);
        }
    }, { change, revisionKey, analysisTag });
}

async function rejeitarMudanca(browser, change, phase) {
    const env = await ambiente(browser);
    try {
        if (change === 'erased-analysis' && phase === 'before') {
            await env.page.evaluate(async url => {
                await fetch(url, { method: 'PATCH' });
                window.previous = JKCachePersistente.contexto();
            }, url('warmup'));
        }
        await env.start('alvo', '001');
        if (phase === 'before') await alterar(env.page, change);
        await env.finish('alvo');
        if (phase === 'after') await alterar(env.page, change);
        assert.equal(await env.page.evaluate(() =>
            JKCachePersistente.confirmarAnaliseImportacao(previous, responses.alvo) === null), true,
        change + ' alterado ' + phase + ' deve rejeitar o recibo.');
    } finally { await env.close(); }
}

async function contratoRestrito(browser) {
    const env = await ambiente(browser);
    try {
        assert.deepEqual(await env.page.evaluate(async url => {
            const general = await fetch('/api/medias-compras/listas-pedidos/A', { method: 'PUT' });
            const generalStrict = !!JKCachePersistente.confirmarMutacao(previous, general);
            window.previous = JKCachePersistente.contexto();
            const preview = await fetch(url + '/previa', { method: 'POST' });
            return {
                unrelated: JKCachePersistente.confirmarAnaliseImportacao(previous, general),
                preview: JKCachePersistente.confirmarAnaliseImportacao(previous, preview),
                fabricated: JKCachePersistente.confirmarAnaliseImportacao(previous, new Response('{}')),
                generalStrict,
            };
        }, url('001')), { unrelated: null, preview: null, fabricated: null, generalStrict: true });

        await env.page.evaluate(({ revisionKey, analysisTag }) => {
            localStorage.setItem(revisionKey + analysisTag, 'unknown-legacy-before-context');
            window.previous = JKCachePersistente.contexto();
        }, { revisionKey, analysisTag });
        await env.start('legacy', '001');
        await env.finish('legacy');
        assert.deepEqual(await env.page.evaluate(() => ({
            specialized: JKCachePersistente.confirmarAnaliseImportacao(previous, responses.legacy),
            strict: !!JKCachePersistente.confirmarMutacao(previous, responses.legacy),
        })), { specialized: null, strict: true }, 'A regra geral de promoção legada permanece; o helper especializado rejeita revisão desconhecida.');
    } finally { await env.close(); }
}

async function contextoNovoNaoAbsorveConflitoDoRequest(browser, change) {
    const env = await ambiente(browser);
    try {
        await env.start('antigo', '001');
        await alterar(env.page, change);
        await env.page.evaluate(async url => {
            await fetch(url, { method: 'PATCH' });
            window.previous = JKCachePersistente.contexto();
        }, url('002'));
        await env.finish('antigo');
        assert.equal(await env.page.evaluate(() =>
            JKCachePersistente.confirmarAnaliseImportacao(previous, responses.antigo) === null), true,
        'Contexto novo não absorve conflito de ' + change + ' após o início do request.');
    } finally { await env.close(); }
}

(async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        await recibosConcorrentes(browser);
        await contratoRestrito(browser);
        await contextoNovoNaoAbsorveConflitoDoRequest(browser, 'manual-analysis');
        await contextoNovoNaoAbsorveConflitoDoRequest(browser, 'revision:custos_importacao');
        for (const change of ['revision:*', 'revision:vendas', 'revision:importacoes', 'revision:custos_importacao',
            'revision:cadastro_custos', 'auth', 'tenant', 'manual-analysis', 'revocation-only-analysis',
            'revocation-only-import', 'unknown-analysis', 'erased-analysis']) {
            for (const phase of ['before', 'after']) await rejeitarMudanca(browser, change, phase);
        }
        console.log('Recibos concorrentes: SKUs fora de ordem confirmam só análises; fontes, revogações e sessão continuam estritas: OK');
    } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
