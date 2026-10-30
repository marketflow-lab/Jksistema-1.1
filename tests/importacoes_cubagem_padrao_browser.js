"use strict";

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');

const fixture = {
    id: 'A', nome_lista: 'Cubagem por loja', store_id: 'store-A', dolar_hoje: 5,
    status: 'Pedido Aprovado', fornecedor_id: 'supplier-A',
    itens: [
        { SKU: '001', Quantidade: 2, 'Valor unidade': 10, 'M3 individual': 0.00003125,
            'Cubagem status': 'cadastro', 'Cubagem fonte': 'Packing List', 'Cubagem unidade': 'peca' },
        { SKU: '1', Quantidade: 3, 'Valor unidade': 10, M3: 999, 'cg_m3 individual': 888,
            'Cubagem status': 'pendente' },
        { SKU: 'H', Quantidade: 4, 'Valor unidade': 10, M3: 0.3, 'M3 individual': 0.2,
            'Cubagem status': 'historico', 'Cubagem fonte': 'Pedido aprovado' },
        { SKU: 'ZERO', Quantidade: 0, M3: 999, 'Cubagem status': 'pendente' },
    ],
};

function gate() {
    let resolve;
    const promise = new Promise(done => { resolve = done; });
    return { promise, resolve };
}

async function environment(browser, options = {}) {
    const context = await browser.newContext();
    const page = await context.newPage();
    const state = { catalog: 0, global: 0, errors: [], detailGate: gate(), detailRequested: false };
    page.on('pageerror', error => state.errors.push(error.message));
    const json = (route, value) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(value) }).catch(() => {});
    await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        const pathname = url.pathname;
        if (pathname === '/cadastro-unit.html') return route.fulfill({ status: 200, contentType: 'text/html', body:
            '<!doctype html><meta charset="utf-8"><body><div id="fields"></div><script src="/cadastro/form/00-core.js"></script><script src="/cadastro/editar-item-layout.js"></script></body>' });
        if (pathname.endsWith('.html')) return route.fulfill({ status: 200, contentType: 'text/html', body: fs.readFileSync(path.join(root, 'static', pathname.slice(1))) });
        if (pathname === '/auth.js') return route.fulfill({ status: 200, contentType: 'application/javascript', body:
            'window.verificarSessao=()=>true;window.obterAuthHeaders=()=>({Authorization:"Bearer synthetic"});' });
        if (pathname.startsWith('/medias_compras/') || pathname.startsWith('/cadastro/')) return route.fulfill({ status: 200, contentType: 'application/javascript', body:
            fs.readFileSync(path.join(root, 'static', pathname.slice(1))) });
        if (pathname === '/api/cadastro/produtos' || pathname === '/api/cadastro/produto') { state.global++; return json(route, [{ sku: '001', 'm3 individual': 999 }]); }
        if (pathname === '/api/cadastro/lojas/store-A/produtos') {
            state.catalog++;
            return json(route, [
                { store_id: 'store-X', sku: '001', 'm3 individual': 999 },
                { store_id: 'store-A', sku: '001', cubagem_sku: '1', 'm3 individual': 0.4 },
                { store_id: 'store-A', sku: 'A1', 'm3 individual': 7 },
            ]);
        }
        const detail = pathname.match(/^\/api\/cadastro\/lojas\/store-A\/produtos\/(.+)$/);
        if (detail) {
            state.detailRequested = true;
            if (options.holdDetail) await state.detailGate.promise;
            return json(route, { produto: { store_id: 'store-A', sku: detail[1] === '1' ? '001' : decodeURIComponent(detail[1]), ...(detail[1] === '1' ? { cubagem_sku: '1' } : {}), 'm3 individual': 20, nome: 'Cadastro atual' } });
        }
        if (pathname === '/api/medias-compras/listas-pedidos/A') return json(route, { lista: options.list || fixture });
        if (pathname === '/api/medias-compras/listas-pedidos') return json(route, { listas: [] });
        if (pathname === '/api/cadastro/fornecedores') return json(route, [{ id: 'supplier-A', nome_empresa: 'Fornecedor' }]);
        if (pathname === '/api/impostos/produtos') return json(route, { produtos: [] });
        if (pathname === '/api/medias-compras/concorrentes-links') return json(route, { concorrentes: {} });
        if (url.hostname === 'economia.awesomeapi.com.br') return json(route, { USDBRL: { bid: '5' } });
        if (pathname.startsWith('/api/')) return json(route, {});
        return route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
    });
    return { page, context, state };
}

(async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const lista = await environment(browser);
        await lista.page.goto('http://jk.test/importacoes_lista.html?lista_id=A');
        await lista.page.locator('tr[data-sku="001"]').waitFor();
        await lista.page.waitForFunction(() => !mapaM3IndividualCarregando);
        assert.strictEqual(await lista.page.locator('tr[data-sku="001"] [data-col-key="m3"]').textContent(), '0,0000625');
        assert.strictEqual(await lista.page.locator('tr[data-sku="1"] [data-col-key="m3"]').textContent(), 'Cubagem pendente');
        assert.strictEqual(await lista.page.locator('tr[data-sku="H"] [data-col-key="m3"]').textContent(), '0,3000');
        assert.strictEqual(await lista.page.locator('tr[data-sku="ZERO"] [data-col-key="m3"]').textContent(), '0,0000');
        assert.strictEqual(await lista.page.locator('tr[data-sku="ZERO"] [data-col-key="m3"]').evaluate(el => el.classList.contains('m3-alerta')), false);
        assert.match(await lista.page.locator('.totais-row [data-col-key="m3"]').textContent(), /0,3000625.*parcial/);
        assert.match(await lista.page.locator('#resumoTopo').textContent(), /M³ Total parcial/);
        assert.match(await lista.page.locator('#statusM3Complementar').textContent(), /Cubagem pendente.*total de m³ é parcial/);
        assert.strictEqual(lista.state.catalog, 0, 'Resposta autoritativa não recebe valores de outro cadastro');
        assert.strictEqual(lista.state.global, 0);
        assert.deepStrictEqual(lista.state.errors, []);
        await lista.context.close();

        const fallback = await environment(browser, { list: { ...fixture, status: 'Lista gerada', itens: [
            { SKU: '001', Quantidade: 2 }, { SKU: '1', Quantidade: 2 }, { SKU: 'A-1', Quantidade: 1 },
        ] } });
        await fallback.page.goto('http://jk.test/importacoes_lista.html?lista_id=A');
        await fallback.page.locator('tr[data-sku="001"]').waitFor();
        await fallback.page.waitForFunction(() => mapaM3IndividualCarregado && !mapaM3IndividualCarregando);
        assert.strictEqual(await fallback.page.locator('tr[data-sku="001"] [data-col-key="m3"]').textContent(), 'Cubagem pendente', '001 não herda SKU 1 nem loja X');
        assert.strictEqual(await fallback.page.locator('tr[data-sku="A-1"] [data-col-key="m3"]').textContent(), 'Cubagem pendente', 'A-1 não herda A1');
        assert.strictEqual(await fallback.page.locator('tr[data-sku="1"] [data-col-key="m3"]').textContent(), '0,8000');
        assert.strictEqual(fallback.state.catalog, 1);
        assert.strictEqual(fallback.state.global, 0);
        assert.deepStrictEqual(fallback.state.errors, []);
        await fallback.context.close();

        const historico = await environment(browser, { holdDetail: true });
        await historico.page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=H');
        await historico.page.waitForFunction(() => document.getElementById('vSku').textContent === 'H');
        assert.strictEqual(await historico.page.locator('#vM3').textContent(), '0,3000');
        assert.strictEqual(await historico.page.locator('[data-card-id="kv_m3"]').innerText(), 'M3\n0,3000');
        historico.state.detailGate.resolve();
        await historico.page.waitForFunction(() => document.getElementById('vTituloPt').textContent === 'Cadastro atual');
        assert.strictEqual(await historico.page.locator('#vM3').textContent(), '0,3000', 'Cadastro async divergente preserva o snapshot histórico');
        assert.strictEqual(historico.state.catalog, 0);
        assert.deepStrictEqual(historico.state.errors, []);
        await historico.context.close();

        const pendente = await environment(browser);
        await pendente.page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=1');
        await pendente.page.waitForFunction(() => document.getElementById('vTituloPt').textContent === 'Cadastro atual');
        assert.strictEqual(await pendente.page.locator('#vM3').textContent(), 'Cubagem pendente', 'CG, M3 residual e cadastro async não substituem o status pendente');
        assert.strictEqual(await pendente.page.locator('#vValorPorM3').textContent(), '-');
        assert.strictEqual(pendente.state.catalog, 0);
        assert.deepStrictEqual(pendente.state.errors, []);
        await pendente.context.close();

        const codigoOriginal = await environment(browser, { list: { ...fixture, status: 'Lista gerada', itens: [{ SKU: '1', Quantidade: 2 }] } });
        await codigoOriginal.page.goto('http://jk.test/importacoes_sku.html?lista_id=A&sku=1');
        await codigoOriginal.page.waitForFunction(() => document.getElementById('vTituloPt').textContent === 'Cadastro atual');
        assert.strictEqual(await codigoOriginal.page.locator('#vM3').textContent(), '0,8000', 'Cubagem usa o código original 1 mesmo quando o cadastro público retorna sku 001');
        assert.deepStrictEqual(codigoOriginal.state.errors, []);
        await codigoOriginal.context.close();

        const panorama = await environment(browser);
        await panorama.page.goto('http://jk.test/importacoes.html');
        const calc = await panorama.page.evaluate(itens => ({
            pendente: embObterM3TotalItem(itens[1]), historico: embObterM3TotalItem(itens[2]), zero: embObterM3TotalItem(itens[3]),
            resumo: formatarM3Resumo({ total_m3: 0.3, m3_completo: false, m3_pendente_itens: 1 }),
        }), fixture.itens);
        assert.deepStrictEqual(calc, { pendente: null, historico: 0.3, zero: 0, resumo: '0,30 m³ (parcial)' });
        await panorama.context.close();

        const cadastro = await environment(browser);
        await cadastro.page.goto('http://jk.test/cadastro-unit.html');
        const form = await cadastro.page.evaluate(() => {
            const tools = JKCadastroForm;
            const unidade = JKCadastroEditarLayout.criarCampo(tools, 'm3_unidade', 'par');
            const status = JKCadastroEditarLayout.criarCampo(tools, 'm3_status', 'cadastro');
            document.getElementById('fields').append(unidade, status);
            return { value: unidade.querySelector('select').value, label: unidade.querySelector('label').textContent,
                statusReadOnly: status.querySelector('input').readOnly, grupo: JKCadastroEditarLayout.obterGrupoCampo(tools, 'm3_origem'),
                rotuloM3: tools.formatarNomeCampo(tools.campoM3Individual), legado: tools.ehCampoCubagemLegado('cg_m³ individual'), canonico: tools.ehCampoCubagemLegado('m3 individual') };
        });
        assert.deepStrictEqual(form, { value: 'par', label: 'Unidade comercial da cubagem', statusReadOnly: true, grupo: 'cubagem', rotuloM3: 'M³ de transporte por unidade comercial', legado: true, canonico: false });
        assert.deepStrictEqual(cadastro.state.errors, []);
        await cadastro.context.close();
        console.log('Cubagem UI: fonte por loja e SKU exatos, pendente sem legado, total parcial, zero, decimal pequeno, histórico preservado e unidade comercial: OK');
    } finally { await browser.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
