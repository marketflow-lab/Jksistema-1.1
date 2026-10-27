'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const temporaryRoot = path.resolve(process.env.JK_TEST_TMP || os.tmpdir());
const profile = fs.mkdtempSync(path.join(temporaryRoot, 'jk-importacoes-cache-'));
const origin = 'http://127.0.0.1:8128';
let context;

function gate() {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    return { promise, resolve };
}

function lista(revisao = 1) {
    return { id: 'A', nome_lista: 'Pedido ' + revisao, store_id: 'store-A', loja: 'Loja A',
        status: 'Analisando orçamento', fornecedor_id: 'supplier-A', supplier: 'Empresa A', dolar_hoje: 5,
        total_itens: 1, total_quantidade: revisao === 1 ? 2 : 9, total_usd: 20,
        itens: [{ SKU: '001', Quantidade: revisao === 1 ? 2 : 9, 'M3 individual': 0.1,
            'Valor unidade': 10, 'Valor total': 20, Produto: 'Produto da lista', NCM: '00000001',
            II: 10, IPI: 5, PIS: 2, COFINS: 3, 'ST Minas': 'Não', 'ST Paraíba': 'Não',
            analise_concorrentes: { margens: {} } }] };
}

async function launch(mode = 'live') {
    context = await chromium.launchPersistentContext(profile, { headless: true });
    const state = { mode, revisao: 1, pending: gate(), complementsForbidden: false, costFailure: false, logistics: {},
        calls: { detail: 0, index: 0, cost: 0, patches: 0 }, errors: [] };
    await context.addInitScript(() => {
        if (location.origin !== 'http://127.0.0.1:8128') return;
        if (!localStorage.getItem('user_data')) {
            localStorage.setItem('user_data', JSON.stringify({ client_id: 'tenant-A', username: 'tester' }));
            localStorage.setItem('permissions', JSON.stringify({ medias_compras: true, vendas: true }));
            localStorage.setItem('access_token', 'synthetic-session');
        }
    });
    context.on('page', page => page.on('pageerror', error => state.errors.push(error.message)));
    const json = (route, body, status = 200) => route.fulfill({ status,
        contentType: 'application/json', body: JSON.stringify(body) }).catch(() => {});
    await context.route('**/*', async route => {
        const url = new URL(route.request().url());
        const pathname = url.pathname;
        if (url.hostname === 'economia.awesomeapi.com.br') return json(route, { USDBRL: { bid: '5' } });
        if (url.hostname === 'fonts.googleapis.com') return route.fulfill({ contentType: 'text/css', body: '' });
        assert.strictEqual(url.origin, origin, 'Todas as requisições permanecem nas fixtures.');
        if (pathname === '/auth.js') return route.fulfill({ contentType: 'application/javascript',
            body: 'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:"Bearer synthetic"});' });
        if (['/importacoes.html', '/importacoes_lista.html', '/importacoes_sku.html', '/cache-persistente.js'].includes(pathname)
            || pathname.startsWith('/medias_compras/')) return route.fulfill({
                contentType: pathname.endsWith('.html') ? 'text/html' : 'application/javascript',
                body: fs.readFileSync(path.join(root, 'static', pathname.slice(1))) });
        if (pathname.endsWith('/analise-concorrentes')) {
            state.calls.patches++;
            return json(route, { analise_concorrentes: { margens: {} } });
        }
        const primary = pathname === '/api/medias-compras/listas-pedidos' || pathname === '/api/medias-compras/listas-pedidos/A';
        if (primary) {
            state.calls[pathname.endsWith('/A') ? 'detail' : 'index']++;
            if (route.request().method() === 'PUT') {
                Object.assign(state.logistics, route.request().postDataJSON());
                return json(route, { lista: { ...lista(state.revisao), ...state.logistics } });
            }
            const current = { ...lista(state.revisao), ...state.logistics };
            if (state.mode === 'hold') await state.pending.promise;
            if (state.mode === 'fail') return json(route, { detail: 'Falha sintética' }, 500);
            if (state.mode === 'deny') return json(route, { detail: 'Sem acesso' }, 403);
            return json(route, pathname.endsWith('/A') ? { lista: current } : { listas: [current] });
        }
        if (pathname === '/api/cadastro/fornecedores') return json(route, [{ id: 'supplier-A', nome_empresa: 'Empresa A' }]);
        if (pathname === '/api/cadastro/nacionalizadores') return json(route, []);
        if (pathname === '/api/lojas') return json(route, [{ store_id: 'store-A', nome: 'Loja A' }]);
        if (pathname.endsWith('/preferencias-colunas')) return json(route, {});
        if (pathname.startsWith('/api/')) {
            if (pathname.endsWith('/landed-cost')) state.calls.cost++;
            if (pathname.endsWith('/landed-cost') && state.costFailure) return json(route, { detail: 'Custo indisponível' }, 500);
            if (state.complementsForbidden) return json(route, { detail: 'Permissão de complemento ausente' }, 403);
            if (pathname.endsWith('/landed-cost')) return json(route, { analysis: { order_id: 'A', tax_band_pct: 12 } });
            if (pathname === '/api/vendas/grafico') return json(route, { valores_vendas: [100], valores_devolucoes: [] });
            if (pathname === '/api/cadastro/lojas/store-A/produtos/001') return json(route, {
                produto: { store_id: 'store-A', sku: '001', nome: 'Cadastro confirmado', ncm: '00000002', 'M3 individual': 0.1 } });
            if (pathname === '/api/cadastro/lojas/store-A/produtos') return json(route, []);
            if (pathname === '/api/impostos/produtos') return json(route, { produtos: [] });
            if (pathname === '/api/medias-compras/concorrentes-links') return json(route, { concorrentes: {},
                concorrentes_mlb: {}, concorrentes_preco: {}, concorrentes_valores: { concorrente_1: 'R$ 200,00' } });
            return json(route, {});
        }
        return route.fulfill({ contentType: 'application/javascript', body: '' });
    });
    return state;
}

async function saved(page, key, predicate) {
    await page.waitForFunction(async ({ key, predicate }) => {
        const copia = await JKCachePersistente.ler(key);
        return copia && (!predicate || Boolean(copia.dados[predicate]));
    }, { key, predicate }, { timeout: 10000 });
}

(async () => {
    try {
        let state = await launch();
        let sku = await context.newPage();
        await sku.goto(origin + '/importacoes_sku.html?lista_id=A&sku=001');
        await saved(sku, 'importacoes:SKU:A:001');
        await sku.waitForFunction(() => document.getElementById('vCustoTotalProduto').textContent !== '-');
        const custo = await sku.locator('#vCustoTotalProduto').textContent();
        assert.notStrictEqual(custo, '-');
        let detalhe = await context.newPage();
        await detalhe.goto(origin + '/importacoes_lista.html?lista_id=A');
        await saved(detalhe, 'importacoes:GET:/api/medias-compras/listas-pedidos/A', 'complementosLista');
        let indice = await context.newPage();
        await indice.goto(origin + '/importacoes.html');
        await saved(indice, 'importacoes:GET:/api/medias-compras/listas-pedidos');
        assert.strictEqual(await indice.evaluate(async () => Boolean(await JKCachePersistente.ler('importacoes:GET:/api/medias-compras/listas-pedidos/A'))), true,
            'O detalhe continua salvo depois de abrir o índice.');
        assert.strictEqual(await indice.evaluate(async () => Boolean(await JKCachePersistente.ler('importacoes:SKU:A:001'))), true,
            'O SKU continua salvo depois de abrir o índice.');
        assert.deepStrictEqual(state.errors, []);
        await context.close();

        state = await launch('hold');
        detalhe = await context.newPage();
        await detalhe.goto(origin + '/importacoes_lista.html?lista_id=A');
        await detalhe.locator('#tbodyLista tr[data-sku="001"]').waitFor({ timeout: 10000 });
        await detalhe.waitForFunction(() => document.getElementById('cacheListaStatus').textContent.includes('Cópia salva'));
        assert.strictEqual(state.calls.cost, 0, 'Restaurar cópia não repete custo antes do detalhe atualizado.');
        assert.strictEqual(await detalhe.locator('tr[data-sku="001"] [data-col-key="quantidade"]').textContent(), '2');
        sku = await context.newPage();
        await sku.goto(origin + '/importacoes_sku.html?lista_id=A&sku=001');
        await sku.waitForFunction(() => document.getElementById('cacheSkuStatus').textContent.includes('Cópia salva'));
        assert.strictEqual(await sku.locator('#vTituloPt').textContent(), 'Cadastro confirmado');
        assert.strictEqual(await sku.locator('#vCustoTotalProduto').textContent(), custo);
        assert.strictEqual(state.calls.patches, 0, 'Restaurar SKU não salva análise nem inicia consulta de preços.');
        indice = await context.newPage();
        await indice.goto(origin + '/importacoes.html');
        await indice.waitForFunction(() => document.getElementById('cacheImportacoesStatus').textContent.includes('Cópia salva'));
        assert.strictEqual(await indice.locator('#listaWrap .lista-item').count(), 1);

        state.mode = 'fail';
        state.pending.resolve();
        await detalhe.waitForFunction(() => document.getElementById('cacheListaStatus').textContent.includes('Não foi possível atualizar'));
        await sku.waitForFunction(() => document.getElementById('cacheSkuStatus').textContent.includes('Não foi possível atualizar'));
        await indice.waitForFunction(() => document.getElementById('cacheImportacoesStatus').textContent.includes('Não foi possível atualizar'));
        assert.strictEqual(await detalhe.locator('#tbodyLista tr[data-sku]').count(), 1);
        assert.strictEqual(await sku.locator('#vCustoTotalProduto').textContent(), custo);
        assert.strictEqual(await indice.locator('#listaWrap .lista-item').count(), 1);

        state.mode = 'live';
        state.revisao = 2;
        state.costFailure = true;
        await detalhe.evaluate(() => carregarDetalheLista());
        await detalhe.waitForFunction(async () => {
            const copia = await JKCachePersistente.ler('importacoes:GET:/api/medias-compras/listas-pedidos/A');
            return copia?.dados.lista.itens[0].Quantidade === 9 && copia.dados.complementosLista
                && copia.dados.complementosLista.custo === null;
        });
        assert.strictEqual(await detalhe.evaluate(() => ultimaAnaliseCustoPosto), null, 'Primário novo não reutiliza custo da quantidade anterior.');
        await detalhe.waitForFunction(() => document.getElementById('cacheListaStatus').textContent.includes('Custo complementar pendente'));
        assert.match(await detalhe.locator('#cacheListaStatus').textContent(), /Custo complementar pendente/);
        state.costFailure = false;
        await detalhe.evaluate(() => carregarDetalheLista());
        await detalhe.waitForFunction(() => ultimaAnaliseCustoPosto !== null);
        const custosAntesDaEdicao = state.calls.cost;
        await detalhe.evaluate(() => atualizarCampoLogisticoLista('A', 'exchange_rate', '6'));
        assert.strictEqual(state.calls.cost, custosAntesDaEdicao + 1, 'PUT confirmado renova contexto e consulta o custo.');
        assert.strictEqual(await detalhe.evaluate(async () => (await JKCachePersistente.ler('importacoes:GET:/api/medias-compras/listas-pedidos/A'))?.dados.lista.exchange_rate), 6);

        state.mode = 'hold';
        state.pending = gate();
        const detalhesAntes = state.calls.detail;
        await detalhe.evaluate(() => { void carregarDetalheLista(); });
        await detalhe.waitForFunction(() => document.getElementById('cacheListaStatus').textContent.includes('Cópia salva'));
        for (let attempt = 0; state.calls.detail === detalhesAntes && attempt < 100; attempt++) await new Promise(resolve => setTimeout(resolve, 10));
        assert.ok(state.calls.detail > detalhesAntes);
        await detalhe.evaluate(() => atualizarCampoLogisticoLista('A', 'numero_invoice', 'INV-NOVA'));
        state.mode = 'live';
        state.pending.resolve();
        await detalhe.waitForLoadState('networkidle');
        assert.strictEqual(await detalhe.evaluate(() => ultimaListaCarregada.numero_invoice), 'INV-NOVA', 'GET antigo não reverte a edição confirmada.');
        assert.strictEqual(await detalhe.evaluate(async () => (await JKCachePersistente.ler('importacoes:GET:/api/medias-compras/listas-pedidos/A'))?.dados.lista.numero_invoice), 'INV-NOVA');

        state.complementsForbidden = true;
        await detalhe.evaluate(() => carregarDetalheLista());
        await detalhe.waitForFunction(() => document.querySelector('tr[data-sku="001"] [data-col-key="quantidade"]').textContent === '9');
        await detalhe.waitForFunction(() => controladoresAuxiliares.size === 0);
        assert.strictEqual(await detalhe.locator('#tbodyLista tr[data-sku]').count(), 1, '403 de complemento preserva a lista autorizada.');
        await detalhe.waitForFunction(() => document.getElementById('cacheListaStatus').textContent === 'Dados atualizados.');
        assert.strictEqual(await detalhe.locator('#cacheListaStatus').textContent(), 'Dados atualizados.');

        state.complementsForbidden = false;
        await indice.evaluate(() => JKCachePersistente.salvar('importacoes:GET:/api/medias-compras/listas-pedidos',
            { listas: [null] }, { tags: ['importacoes'] }));
        const indicesAntes = state.calls.index;
        await indice.evaluate(() => carregarListasImportacoes());
        assert.ok(state.calls.index > indicesAntes, 'Cópia inválida não impede a consulta canônica.');
        assert.strictEqual(await indice.locator('#listaWrap .lista-item').count(), 1);

        state.mode = 'deny';
        await detalhe.evaluate(() => carregarDetalheLista());
        assert.strictEqual(await detalhe.locator('#tbodyLista tr[data-sku]').count(), 0, '403 principal remove dados salvos da página.');
        assert.match(await detalhe.locator('#cacheListaStatus').textContent(), /Acesso expirado ou negado/);

        await indice.evaluate(() => localStorage.setItem('access_token', 'synthetic-renewed'));
        assert.strictEqual(await sku.locator('.layout .card').count(), 3, 'Renovar somente o token preserva a cópia visível.');
        await indice.evaluate(() => {
            localStorage.setItem('user_data', JSON.stringify({ client_id: 'tenant-B', username: 'tester' }));
            window.dispatchEvent(new CustomEvent('jk-tenant-changed'));
        });
        await sku.waitForFunction(() => document.querySelector('.layout').children.length === 0);
        assert.strictEqual(await indice.locator('#listaWrap .lista-item').count(), 0, 'Troca de cliente remove as linhas exibidas.');
        assert.deepStrictEqual(state.errors, []);
        console.log('Importações: reinício índice/lista/SKU, custos por conteúdo, rede lenta/falha, cópia sem recálculo, PUT e GET antigo, cópia inválida, 403 principal/complementar e troca de cliente/token: OK');
    } finally {
        await context?.close();
        const resolvedProfile = path.resolve(profile);
        assert.strictEqual(path.dirname(resolvedProfile), temporaryRoot);
        assert.ok(path.basename(resolvedProfile).startsWith('jk-importacoes-cache-'));
        fs.rmSync(profile, { recursive: true, force: true });
    }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
