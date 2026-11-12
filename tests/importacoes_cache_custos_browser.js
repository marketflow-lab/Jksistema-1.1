'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const origin = 'http://127.0.0.1:8137';
const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'jk-custos-cache-test-'));
let context;
const errors = [];
const state = { cadastro: 'c1', base: 'b1', fullCadastro: 0, fullBase: 0, status: 200, baseRetry: 1 };
const base = () => ({ success: true, versao: 1, tipo: 'importacoes_base_custos', revisao: state.base,
    lista: { id: 'lista-a', store_id: 'store-a', exchange_rate: 5 },
    revisoes_sku: { '001': 'sku1' }, rateio: { total_m3: 1, valor_por_m3: 100 },
    itens: [{ SKU: '001', Quantidade: 2, fiscal: {}, fiscal_pronto: true }] });

async function launch() {
    context = await chromium.launchPersistentContext(profile, { headless: true });
    await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.pathname.endsWith('.js')) return route.fulfill({ contentType: 'application/javascript',
            body: fs.readFileSync(path.join(root, 'static', url.pathname), 'utf8') });
        const json = (data, headers = {}) => route.fulfill({ status: state.status,
            contentType: 'application/json', headers, body: JSON.stringify(data) });
        if (url.pathname.endsWith('/base-custos')) {
            if (state.baseRetry > 0) {
                state.baseRetry--;
                return route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"Temporariamente ocupado"}' });
            }
            const data = base();
            if (url.searchParams.get('somente_revisao')) { delete data.itens; delete data.rateio; }
            else state.fullBase++;
            return json(data);
        }
        if (url.pathname.endsWith('/produtos')) {
            if (url.searchParams.get('somente_revisao')) return json({ store_id: 'store-a', revisao: state.cadastro });
            state.fullCadastro++;
            return json([{ store_id: 'store-a', sku: '001', custo: state.cadastro === 'c1' ? 0 : 19.75 },
                { store_id: 'store-a', sku: 'SEM-CUSTO', custo: null }], { 'X-JK-Custos-Revisao': state.cadastro });
        }
        if (url.pathname.startsWith('/api/')) return json({ success: true }, state.status === 401 ? { 'WWW-Authenticate': 'Bearer' } : {});
        return route.fulfill({ contentType: 'text/html', body: '<!doctype html>'
            + '<script src="/cache-persistente.js"></script>'
            + '<script src="/medias_compras/importacoes-cache-custos.js"></script><p>Custos</p>' });
    });
    const page = await context.newPage();
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(origin);
    await page.evaluate(() => {
        if (!localStorage.getItem('user_data')) {
            localStorage.setItem('user_data', JSON.stringify({ client_id: 'tenant-a', username: 'reader', allowed_store_ids: ['store-a'] }));
            localStorage.setItem('access_token', 'synthetic');
            localStorage.setItem('permissions', JSON.stringify({ importacoes: true }));
        }
        window.options = { listaId: 'lista-a', storeId: 'store-a', contexto: JKCachePersistente.contexto() };
        window.inputs = { totalUsd: 100, freteUsd: 10, cotacao: 5, quantidade: 2,
            iiPct: 10, ipiPct: 5, pisPct: 2, cofinsPct: 3, proporcaoPct: null };
        window.calls = 0;
    });
    return page;
}

(async () => {
    try {
        let page = await launch();
        assert.deepEqual(await page.evaluate(async () => {
            const helper = JKImportacoesCacheCustos;
            const args = { ...options, sku: '001', entradas: inputs, revisao: 'sku1',
                calcular: () => { calls++; return { custoUnitario: 23.75, custoTotal: 47.5 }; } };
            const first = await helper.calcular(args);
            const second = await helper.calcular({ ...args, entradas: { ...inputs, proporcaoPct: 100, stMg: false,
                stPb: false, icmsFallback: null } });
            return { equal: first.assinatura === second.assinatura, calls, unit: second.calculo.custoUnitario };
        }), { equal: true, calls: 1, unit: 23.75 }, 'Entradas equivalentes das duas telas usam o mesmo resultado');

        await page.evaluate(async () => {
            await JKImportacoesCacheCustos.consultarBase(options);
            await JKImportacoesCacheCustos.consultarCadastro(options);
            const initial = Date.now;
            Date.now = () => initial() + 180000;
            dispatchEvent(new CustomEvent('jk:vendas-sync-finished'));
            dispatchEvent(new CustomEvent('jk-photo-policy-changed'));
            await fetch('/api/medias-compras/listas-pedidos/lista-a/skus/001/analise-concorrentes', { method: 'PATCH' });
            options.contexto = JKCachePersistente.contexto();
            await JKImportacoesCacheCustos.consultarBase(options);
            await JKImportacoesCacheCustos.consultarCadastro(options);
            Date.now = initial;
        });
        assert.equal(state.baseRetry, 0, 'Primeira consulta sem cache recupera falha transitória automaticamente');
        assert.equal(state.fullBase, 1, 'Vendas, fotos e salvar análise não baixam novamente base válida');
        assert.equal(state.fullCadastro, 1, 'Mais de 120 s não baixa novamente os custos do Cadastro');
        assert.equal(await page.evaluate(async () => (await JKImportacoesCacheCustos.lerCadastro(options)).produtos[0].custo), 0,
            'Custo zero é preservado');

        state.cadastro = 'c2';
        state.base = 'b2';
        await page.evaluate(async () => {
            await JKImportacoesCacheCustos.consultarBase(options);
            await JKImportacoesCacheCustos.consultarCadastro(options);
        });
        assert.equal(state.fullBase, 2);
        assert.equal(state.fullCadastro, 2);

        const other = await context.newPage();
        await other.goto(origin);
        const write = (target, sku) => target.evaluate(async sku => {
            const ctx = JKCachePersistente.contexto();
            const entradas = { totalUsd: 10, quantidade: 1 };
            return JKImportacoesCacheCustos.salvarCalculo({ listaId: 'lista-a', storeId: 'store-a', sku,
                contexto: ctx, assinatura: await JKImportacoesCacheCustos.assinatura(entradas), entradas,
                revisao: 'sku1', calculo: { custoUnitario: 10, custoTotal: 10 } });
        }, sku);
        assert.deepEqual(await Promise.all([write(page, 'A'), write(other, 'B')]), [true, true]);
        await other.close();
        await context.close();
        page = await launch();
        assert.deepEqual(await page.evaluate(async () => Object.keys(await JKImportacoesCacheCustos.restaurarCalculos(options)).sort()),
            ['001', 'A', 'B'], 'Gravações concorrentes entre abas sobrevivem ao reinício sem perder SKUs');
        assert.equal(await page.evaluate(async () => {
            const data = await JKImportacoesCacheCustos.consultarCadastro(options);
            return data.produtos[0].custo;
        }), 19.75);
        assert.equal(state.fullCadastro, 2, 'Reinício usa cache persistente com revisão igual');

        assert.equal(await page.evaluate(async () => {
            const helper = JKImportacoesCacheCustos;
            const subtle = crypto.subtle;
            const vectors = [{}, { totalUsd: 100, freteUsd: 2, proporcaoPct: 50 },
                { totalUsd: -1, mvaMgPct: 123.456, stPb: true, quantidade: 0 }];
            const expected = await Promise.all(vectors.map(value => helper.assinatura(value)));
            Object.defineProperty(crypto, 'subtle', { value: undefined, configurable: true });
            try {
                const actual = await Promise.all(vectors.map(value => helper.assinatura(value)));
                return JSON.stringify(expected) === JSON.stringify(actual);
            } finally { Object.defineProperty(crypto, 'subtle', { value: subtle, configurable: true }); }
        }), true, 'Fallback sem WebCrypto produz o mesmo SHA256');

        const bulk = await page.evaluate(async () => {
            const real = JKCachePersistente;
            let writes = 0, formulas = 0;
            window.JKCachePersistente = { ...real, atualizar(...args) { writes++; return real.atualizar(...args); } };
            try {
                const rows = Array.from({ length: 400 }, (_, i) => ({ ...options, sku: 'BULK-' + i,
                    entradas: { ...inputs, quantidade: i + 1 }, revisao: 'first',
                    calcular: () => { formulas++; return { custoUnitario: 10, custoTotal: 20 }; } }));
                await Promise.all(rows.map(row => JKImportacoesCacheCustos.calcular(row)));
                const first = formulas;
                await Promise.all(rows.map(row => JKImportacoesCacheCustos.calcular({ ...row, revisao: 'second' })));
                return { writes, first, formulas, count: Object.keys(await JKImportacoesCacheCustos.restaurarCalculos(options)).length };
            } finally { window.JKCachePersistente = real; }
        });
        assert.equal(bulk.first, 400);
        assert.equal(bulk.formulas, 400, 'Nova revisão com os mesmos insumos revalida sem repetir fórmula');
        assert.equal(bulk.count, 403);
        assert.ok(bulk.writes < 40, 'Gravações de 400 SKUs são agrupadas por lista');

        assert.equal(await page.evaluate(async () => {
            let active = 0, maximum = 0;
            const operation = () => JKImportacoesCacheCustos.comTravaAnalise(options, async () => {
                active++; maximum = Math.max(maximum, active);
                await new Promise(resolve => setTimeout(resolve, 15));
                active--;
            });
            await Promise.all([operation(), operation(), operation()]);
            return maximum;
        }), 1, 'Análises simultâneas da mesma lista aguardam a gravação anterior');

        assert.equal(await page.evaluate(async () => {
            const real = JKCachePersistente;
            window.JKCachePersistente = undefined;
            try {
                const value = await JKImportacoesCacheCustos.calcular({ listaId: 'sem-cache', storeId: 'store-a', sku: '1',
                    entradas: inputs, revisao: 'r1', calcular: () => ({ custoUnitario: 12 }) });
                return value.calculo.custoUnitario;
            } finally { window.JKCachePersistente = real; }
        }), 12, 'Indisponibilidade de cache não impede o cálculo atual');

        assert.equal(await page.evaluate(async () => {
            await JKCachePersistente.invalidar(['custos_importacao']);
            return Object.keys(await JKImportacoesCacheCustos.restaurarCalculos(options)).length;
        }), 0, 'Revogação da família remove também acesso à memória da geração anterior');
        await page.evaluate(async () => {
            options.contexto = JKCachePersistente.contexto();
            await JKImportacoesCacheCustos.consultarBase(options);
        });

        state.status = 503;
        assert.deepEqual(await page.evaluate(async () => {
            let restored;
            const value = await JKImportacoesCacheCustos.consultarCadastro({ ...options,
                aoRestaurar: async saved => { await Promise.resolve(); restored = saved.produtos[0].custo; } });
            return { restored, stale: value.desatualizado, value: value.produtos[0].custo,
                missing: value.produtos[1].custo };
        }), { restored: 19.75, stale: true, value: 19.75, missing: null }, 'Falha conserva cópia e diferencia ausência confirmada');

        assert.equal(await page.evaluate(async () => {
            const user = localStorage.getItem('user_data');
            localStorage.setItem('user_data', JSON.stringify({ client_id: 'tenant-b', username: 'reader' }));
            const data = await JKImportacoesCacheCustos.lerCadastro({ storeId: 'store-a' });
            localStorage.setItem('user_data', user);
            options.contexto = JKCachePersistente.contexto();
            return data;
        }), null, 'Outro cliente não restaura os custos');

        state.status = 403;
        assert.equal(await page.evaluate(async () => {
            try { await JKImportacoesCacheCustos.consultarCadastro(options); return false; }
            catch (error) { return error.status === 403 && await JKImportacoesCacheCustos.lerCadastro({ storeId: 'store-a' }) === null; }
        }), true, '403 revoga cópia deste recurso e não recorre ao último custo');
        state.status = 401;
        await page.evaluate(() => fetch('/api/medias-compras/listas-pedidos/lista-a'));
        assert.equal(await page.evaluate(async () => {
            localStorage.setItem('access_token', 'synthetic-renewed');
            return await JKImportacoesCacheCustos.lerBase({ listaId: 'lista-a', storeId: 'store-a' });
        }), null, 'Sessão negada revoga também famílias novas após renovar token');
        assert.deepEqual(errors, []);
        console.log('Cache custos: persistência, assinatura, revisão, reabertura, abas concorrentes, isolamento e falhas: OK');
    } finally {
        if (context) await context.close();
        fs.rmSync(profile, { recursive: true, force: true });
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
