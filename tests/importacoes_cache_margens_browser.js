'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const origin = 'http://127.0.0.1:8138';
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'jk-margens-cache-test-'));
const errors = [];
let context;
let patches = 0;

async function launch() {
    context = await chromium.launchPersistentContext(profile, { headless: true });
    await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.pathname.endsWith('.js')) return route.fulfill({ contentType: 'application/javascript',
            body: fs.readFileSync(path.join(root, 'static', url.pathname), 'utf8') });
        if (url.pathname.endsWith('/analise-concorrentes')) {
            patches++;
            return route.fulfill({ contentType: 'application/json', body: '{"success":true}' });
        }
        if (url.pathname.endsWith('/denied')) return route.fulfill({ status: 401,
            contentType: 'application/json', headers: { 'WWW-Authenticate': 'Bearer' }, body: '{"detail":"Sessão expirada"}' });
        return route.fulfill({ contentType: 'text/html', body: '<!doctype html>'
            + '<script src="/cache-persistente.js"></script>'
            + '<script src="/medias_compras/importacoes-cache-custos.js"></script>'
            + '<script src="/medias_compras/importacoes-cache-margens.js"></script>' });
    });
    return newPage();
}

async function newPage() {
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin);
    await page.evaluate(() => {
        if (!localStorage.getItem('user_data')) {
            localStorage.setItem('user_data', JSON.stringify({ client_id: 'tenant-a', username: 'reader', allowed_store_ids: ['store-a', 'store-b'] }));
            localStorage.setItem('access_token', 'synthetic');
            localStorage.setItem('permissions', JSON.stringify({ importacoes: true }));
        }
        window.args = () => ({ listaId: 'lista-a', storeId: 'store-a', contexto: JKCachePersistente.contexto() });
        window.fixture = () => {
            const payload = { fonte_custo: 'simulacao_importacao_sku', custo_unitario: 23.75,
                assinatura_calculo: 'a'.repeat(64), revisao_fontes_custo: 'rev-a', fontes_versao: 3,
                precos_concorrentes: { concorrente_1: 100 }, precos_anuncios_loja: { concorrente_1: 101 },
                anuncios_loja: { concorrente_1: 'MLB123456' }, variacoes_anuncios_loja: { concorrente_1: '10' } };
            const fontes = { anuncios_loja_fonte: 'loja_lista_sku', anuncios_loja_store_id: 'store-a',
                anuncios_loja_seller_id: 'seller-a', anuncios_loja_site_id: 'MLB', consultado_em_ms: Date.now(),
                fontes_versao: 3, anuncios_versao: 2, anuncios_loja: [{ chave: 'concorrente_1', item_id: 'MLB123456',
                    variation_id: '10', preco: 101, status: 'active', seller_id: 'seller-a', site_id: 'MLB', sku: '47' }],
                concorrentes_valores: { concorrente_1: 100 }, concorrentes_mlb: { concorrente_1: 'MLB123456' },
                concorrentes_preco: { concorrente_1: 101 }, anuncios_loja_variacoes: { concorrente_1: '10' } };
            const margin = price => ({ fonte_custo: payload.fonte_custo, custo_unitario: 23.75, preco_venda: price,
                margem_percentual: 30, financeiro_exato: true, item_id_loja: 'MLB123456', variation_id: '10',
                store_id: 'store-a', seller_id: 'seller-a', site_id: 'MLB' });
            const analise = { ...payload, store_id: 'store-a', atualizado_em: new Date().toISOString(),
                anuncios_loja_store_id: 'store-a', anuncios_loja_seller_id: 'seller-a', anuncios_loja_site_id: 'MLB',
                margens: { concorrente_1: margin(100) }, margens_anuncios_loja: { concorrente_1: margin(101) } };
            return { analise, fontes, payload };
        };
        window.patch = sku => fetch('/api/medias-compras/listas-pedidos/lista-a/skus/' + sku + '/analise-concorrentes', { method: 'PATCH' });
    });
    return page;
}

(async () => {
    try {
        let page = await launch();
        const reuse = await page.evaluate(async () => {
            const h = JKImportacoesCacheMargens, f = fixture();
            await h.salvar({ ...args(), sku: '47', ...f });
            const record = await h.ler({ ...args(), sku: '47' });
            const oldPrice = { ...record.fontes, consultado_em_ms: Date.now() - 61000 };
            const checks = [h.mesmosInsumos(record, f.payload, h.identidadeFontes(f.fontes)),
                h.fontesRecentes(record.fontes), h.fontesRecentes(oldPrice), h.financeiroRecente(record)];
            const mutation = fn => { const p = structuredClone(f.payload); fn(p); return h.mesmosInsumos(record, p, h.identidadeFontes(f.fontes)); };
            checks.push(mutation(p => p.precos_concorrentes.concorrente_1++), mutation(p => p.precos_anuncios_loja.concorrente_1++),
                mutation(p => p.anuncios_loja.concorrente_1 = 'MLB999'), mutation(p => p.variacoes_anuncios_loja.concorrente_1 = '11'),
                mutation(p => p.custo_unitario++), mutation(p => p.assinatura_calculo = 'b'.repeat(64)),
                mutation(p => p.revisao_fontes_custo = 'rev-b'), mutation(p => p.fontes_versao++),
                mutation(p => p.consultadoEm = 123));
            for (const field of ['storeId', 'sellerId', 'siteId']) {
                const id = h.identidadeFontes(f.fontes); id[field] += 'changed'; checks.push(h.mesmosInsumos(record, f.payload, id));
            }
            return checks;
        });
        assert.deepEqual(reuse, [true, true, false, true, false, false, false, false, false, false, false, false, true, false, false, false],
            'Reutiliza apenas os mesmos insumos/identidade; idade dos preços não invalida cálculo');

        assert.deepEqual(await page.evaluate(async () => {
            const h = JKImportacoesCacheMargens, f = fixture();
            f.analise.atualizado_em = new Date(Date.now() - 901000).toISOString();
            const old = await h.salvar({ ...args(), sku: 'OLD', ...f, confirmadoEm: Date.now() });
            const recent = h.financeiroRecente(old);
            delete f.analise.atualizado_em;
            const unknown = await h.salvar({ ...args(), sku: 'UNKNOWN', ...f });
            f.analise.fonte_custo = 'cadastro';
            const legacy = await h.salvar({ ...args(), sku: 'LEGACY', ...f });
            const converted = h.normalizarFontes({ storeId: 'store-a', sellerId: 'seller-a', siteId: 'MLB', fonte: 'loja_lista_sku',
                consultadoEm: 123, anunciosVersao: 2, anuncios: f.fontes.anuncios_loja, valores: { concorrente_1: 100 },
                mlb: { concorrente_1: 'MLB123456' }, preco: { concorrente_1: 101 }, variacoes: { concorrente_1: '10' } });
            return [recent, h.financeiroRecente(unknown), unknown.calculadoEm, legacy,
                converted.consultado_em_ms, converted.anuncios_loja[0].variation_id, h.fontesRecentes({})];
        }), [false, false, 0, null, 123, '10', false], 'Nova consulta de preço não inventa atualização das tarifas');

        // Each PATCH changes the generic analysis revision. Earlier SKUs must remain atomically mergeable.
        const bulk = await page.evaluate(async () => {
            const real = JKCachePersistente;
            let writes = 0, reads = 0;
            window.JKCachePersistente = { ...real,
                atualizar: (...args) => { writes++; return real.atualizar(...args); },
                ler: (...args) => { reads++; return real.ler(...args); } };
            try {
                await Promise.all(Array.from({ length: 400 }, async (_, index) => {
                    const original = args();
                    await patch('B' + index);
                    return JKImportacoesCacheMargens.salvar({ ...original, sku: 'B' + index, ...fixture() });
                }));
                return { writes, reads, count: Object.keys(await JKImportacoesCacheMargens.lerLista({ ...args(), reler: true })).length };
            } finally { window.JKCachePersistente = real; }
        });
        assert.equal(bulk.count, 403, '400 revisões preservam também os registros anteriores');
        assert.ok(bulk.writes < 80, 'Agrupa gravações concorrentes de 400 SKUs');
        assert.ok(bulk.reads < 10, 'Leitura compartilhada evita 400 transações de lista');
        await context.close();
        page = await launch();
        assert.equal(await page.evaluate(async () => Object.keys(await JKImportacoesCacheMargens.lerLista(args())).length), 403,
            'Todos os SKUs sobrevivem ao reinício');

        const second = await newPage();
        await second.evaluate(() => { window.notices = 0; addEventListener('jk:importacoes-margens-atualizadas', event => { if (event.detail.externa) notices++; }); });
        const operation = target => target.evaluate(() => JKImportacoesCacheCustos.comTravaAnalise({ ...args(), sku: 'DEDUP' }, async () => {
            const options = { ...args(), sku: 'DEDUP' }, h = JKImportacoesCacheMargens;
            const existing = await h.ler({ ...options, reler: true });
            if (existing) return 'reuse';
            await patch('DEDUP');
            await h.salvar({ ...options, ...fixture() });
            return 'calculated';
        }));
        const before = patches;
        assert.deepEqual((await Promise.all([operation(page), operation(second)])).sort(), ['calculated', 'reuse']);
        assert.equal(patches - before, 1, 'Lock por SKU e releitura persistida impedem PATCH duplicado entre abas');
        await page.evaluate(async () => JKImportacoesCacheMargens.salvar({ ...args(), sku: 'DEDUP', ...fixture() }));
        await second.waitForFunction(() => notices > 0);

        assert.equal(await page.evaluate(async () => {
            let active = 0, max = 0;
            const operation = sku => JKImportacoesCacheCustos.comTravaAnalise({ ...args(), sku }, async () => {
                active++; max = Math.max(max, active); await new Promise(resolve => setTimeout(resolve, 30)); active--;
            });
            await Promise.all([operation('DIFF1'), operation('DIFF2')]);
            return max;
        }), 2, 'SKU diferente não fica bloqueado pelo cálculo de outro SKU da lista');

        assert.equal(await page.evaluate(async () => {
            await JKImportacoesCacheMargens.remover({ ...args(), sku: '47' });
            return await JKImportacoesCacheMargens.ler({ ...args(), sku: '47', reler: true });
        }), null, 'Remoção pontual preserva os demais resultados');
        assert.equal(await page.evaluate(async () => Object.keys(await JKImportacoesCacheMargens.lerLista(args())).length), 403);

        assert.deepEqual(await page.evaluate(async () => {
            const real = JKCachePersistente;
            let current = true, release, started;
            const gate = new Promise(resolve => { release = resolve; });
            const entered = new Promise(resolve => { started = resolve; });
            window.JKCachePersistente = { ...real, atualizar: async (...parameters) => {
                started(); await gate; return real.atualizar(...parameters);
            } };
            try {
                const saving = JKImportacoesCacheMargens.salvar({ ...args(), sku: 'LATE', ...fixture(), vigente: () => current });
                await entered;
                current = false;
                release();
                return [await saving, await JKImportacoesCacheMargens.ler({ ...args(), sku: 'LATE', reler: true })];
            } finally { window.JKCachePersistente = real; }
        }), [null, null], 'Geração substituída durante a transação não reaparece pela memória compartilhada');

        assert.deepEqual(await page.evaluate(async () => {
            const old = args();
            await JKCachePersistente.invalidar(['analises_importacao']);
            const staleRead = await JKImportacoesCacheMargens.ler({ ...old, sku: 'B0' });
            const staleWrite = await JKImportacoesCacheMargens.salvar({ ...old, sku: 'B0', ...fixture() });
            const freshRead = await JKImportacoesCacheMargens.ler({ ...args(), sku: 'B0' });
            return [staleRead, staleWrite, freshRead];
        }), [null, null, null], 'Revogação manual impede leitura, promoção e escrita de contexto anterior');

        await page.evaluate(async () => { await JKImportacoesCacheMargens.salvar({ ...args(), sku: '47', ...fixture() }); });
        assert.deepEqual(await page.evaluate(async () => {
            const h = JKImportacoesCacheMargens;
            const store = await h.ler({ ...args(), storeId: 'store-b', sku: '47' });
            const oldUser = localStorage.getItem('user_data');
            localStorage.setItem('user_data', JSON.stringify({ client_id: 'tenant-b', username: 'reader', allowed_store_ids: ['store-a'] }));
            const tenant = await h.ler({ ...args(), sku: '47' });
            localStorage.setItem('user_data', oldUser);
            await fetch('/api/medias-compras/denied');
            const denied = await h.ler({ ...args(), sku: '47' });
            return [store, tenant, denied];
        }), [null, null, null], 'Loja, cliente e sessão revogada nunca compartilham resultados');

        assert.deepEqual(await page.evaluate(async () => {
            const real = JKCachePersistente;
            window.JKCachePersistente = undefined;
            try {
                const options = { listaId: 'fallback', storeId: 'store-a', sku: '47' };
                const record = await JKImportacoesCacheMargens.salvar({ ...options, ...fixture() });
                const absent = await JKImportacoesCacheMargens.ler(options);
                const replaced = await JKImportacoesCacheMargens.salvar({ ...options, ...fixture(), vigente: () => false });
                return [record.analise.custo_unitario, absent, replaced];
            } finally { window.JKCachePersistente = real; }
        }), [23.75, null, null], 'Cache indisponível permite exibir cálculo autorizado sem reviver geração substituída');

        assert.deepEqual(errors, []);
        console.log('Cache compartilhado de margens: persistência, 400 SKUs, revisões, TTLs, locks e isolamento OK.');
    } finally {
        await context?.close();
        const resolved = path.resolve(profile);
        assert.ok(resolved.startsWith(path.resolve(os.tmpdir()) + path.sep) && path.basename(resolved).startsWith('jk-margens-cache-test-'));
        fs.rmSync(resolved, { recursive: true, force: true });
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
