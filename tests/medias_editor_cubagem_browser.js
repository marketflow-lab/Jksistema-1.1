"use strict";

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const storeId = 'store-jk';
const knownVolumes = [0.0000625, 0.0123456789, 0.024, 0.077777777778, 0.05, 0.11379737755];
const quantities = [2, 50, 100, 80, 40, 60, 70, 75, 85, 90, 60, 50, 79];
const cbmTexts = ['0,0000625', '0.0123456789', '2.4e-2', '0.077777777778', '0.05', '0,11379737755'];
const initialVolume = 0.277983334228;

function fixture() {
    return {
        id: 'lista-1', nome_lista: 'Pedido sintético - total de cubagem', loja: 'JK Pecas', store_id: storeId,
        status: 'Lista gerada', created_at: '2026-08-26T10:00:00Z', updated_at: '2026-08-26T10:00:00Z',
        itens: quantities.map((quantity, index) => ({
            SKU: String(index + 1).padStart(3, '0'), Quantidade: quantity,
            'Título do produto em inglês': 'Produto sintético ' + (index + 1),
            'Valor unidade': index === 12 ? 630 / quantity : 5,
            'Valor total': index === 12 ? 630 : quantity * 5,
            'Estimed CBM': cbmTexts[index] || '',
            ...(index < knownVolumes.length ? { 'M3 individual': knownVolumes[index] / quantity, 'Cubagem status': 'cadastro' } : { 'Cubagem status': 'pendente' }),
        })),
    };
}

async function setup(browser) {
    const context = await browser.newContext({ viewport: { width: 1500, height: 1050 } });
    const page = await context.newPage();
    const state = { lista: fixture(), errors: [], writes: [] };
    page.on('pageerror', error => state.errors.push(error.message));
    await page.addInitScript(() => { window.confirm = () => true; });
    const json = (route, body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    await page.route('**/*', async route => {
        const request = route.request();
        const url = new URL(request.url());
        const pathname = url.pathname;
        if (pathname === '/medias_compras.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: fs.readFileSync(path.join(root, 'static', 'medias_compras.html'), 'utf8') });
        if (pathname.startsWith('/medias_compras/')) return route.fulfill({ status: 200, contentType: pathname.endsWith('.css') ? 'text/css' : 'application/javascript', body: fs.readFileSync(path.join(root, 'static', pathname.slice(1))) });
        if (pathname === '/auth.js') return route.fulfill({ status: 200, contentType: 'application/javascript', body: 'window.verificarSessao=()=>true; window.obterAuthHeaders=()=>({});' });
        if (pathname === '/api/lojas') return json(route, [{ nome: 'JK Pecas', store_id: storeId }]);
        if (pathname === '/api/cadastro/fornecedores') return json(route, []);
        if (pathname === '/api/medias-compras/preferencias-skus-ocultos') return json(route, { skus_ocultos: [] });
        if (pathname === '/api/medias-compras/visao') return json(route, { colunas_meses: [], itens: [] });
        if (pathname === '/api/cadastro/produto') return json(route, { produto: { sku: url.searchParams.get('sku'), titulo: 'Produto adicionado', 'm3 individual': '0.000005' } });
        if (pathname === '/api/medias-compras/listas-pedidos') return json(route, { listas: [{ ...state.lista, itens: undefined, total_itens: state.lista.itens.length }] });
        if (pathname === '/api/medias-compras/listas-pedidos/lista-1/adicionar-sku') {
            const payload = request.postDataJSON();
            state.writes.push({ method: request.method(), pathname, payload });
            state.lista.itens.push({ SKU: payload.sku, Quantidade: payload.quantidade,
                'Valor unidade': payload.valor_unitario, 'Valor total': payload.quantidade * payload.valor_unitario,
                'Estimed CBM': 0.000005 * payload.quantidade, 'M3 individual': 0.000005, 'Cubagem status': 'cadastro' });
            return json(route, { lista: state.lista, acao: 'adicionado' });
        }
        if (pathname === '/api/medias-compras/listas-pedidos/lista-1') {
            if (request.method() === 'PUT') {
                const payload = request.postDataJSON();
                state.writes.push({ method: request.method(), pathname, payload });
                Object.assign(state.lista, payload);
            }
            return json(route, { lista: state.lista });
        }
        return route.fulfill({ status: 200, contentType: 'application/javascript', body: '' });
    });
    await page.goto('http://jk.test/medias_compras.html');
    await page.locator('#lojaBotoes button').filter({ hasText: 'JK Pecas' }).click();
    await page.locator('#btnAbaListasPedidos').click();
    await page.locator('.lista-item').first().click();
    await page.locator('#editorListaPedido:not(.hidden)').waitFor();
    return { page, context, state };
}

async function checkFooter(page, volume, pending) {
    const footer = page.locator('#tfootCbm');
    const data = await footer.evaluate(element => ({ text: element.textContent.trim(), total: Number(element.dataset.totalM3), pending: element.dataset.pendingCount, partial: element.dataset.partial, title: element.title, label: element.getAttribute('aria-label') }));
    assert(Math.abs(data.total - volume) < 5e-13, `Total m³ correto: esperado ${volume}; recebido ${data.total}`);
    assert.strictEqual(data.pending, String(pending));
    assert.strictEqual(data.partial, String(pending > 0));
    const expected = volume.toLocaleString('pt-BR', { maximumFractionDigits: 12 }) + ' m³' + (pending ? ' (parcial)' : '');
    assert.strictEqual(data.text, expected);
    if (pending) {
        assert((data.title + ' ' + data.label).includes(String(pending)), 'Contagem de pendências acessível no footer');
    }
}

(async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const { page, state, context } = await setup(browser);
        assert.strictEqual(await page.locator('#tblListaPedidoItens tbody tr[data-lista-pedido-row]').count(), 13);
        assert.strictEqual(await page.locator('#tfootQtd').textContent(), '841');
        assert.match(await page.locator('#tfootVtot').textContent(), /4[.,]440[.,]00/);
        await checkFooter(page, initialVolume, 7);
        assert.strictEqual(state.writes.length, 0, 'Render e soma não persistem nem alteram dados');
        if (process.env.JK_CBM_SCREENSHOT) await page.locator('#tblListaPedidoItens tfoot').screenshot({ path: process.env.JK_CBM_SCREENSHOT });

        const cbmPending = page.locator('input[data-kind="cbm"][data-idx="6"]');
        await cbmPending.fill('0,00000125');
        await checkFooter(page, initialVolume + 0.00000125, 6);
        await cbmPending.evaluate(element => { element.value = '0.0000025'; element.dispatchEvent(new Event('change', { bubbles: true })); });
        await checkFooter(page, initialVolume + 0.0000025, 6);
        await cbmPending.fill('1e-7');
        await checkFooter(page, initialVolume + 0.0000001, 6);

        const quantity = page.locator('input[data-kind="qtd"][data-idx="0"]');
        await quantity.fill('4');
        assert.strictEqual(await page.locator('input[data-kind="cbm"][data-idx="0"]').inputValue(), '0,000125');
        await checkFooter(page, initialVolume + 0.0000625 + 0.0000001, 6);
        await quantity.fill('0');
        assert.strictEqual(await page.locator('input[data-kind="cbm"][data-idx="0"]').inputValue(), '0');
        await checkFooter(page, initialVolume - 0.0000625 + 0.0000001, 6);
        await page.locator('input[data-kind="qtd"][data-idx="7"]').fill('0');
        await checkFooter(page, initialVolume - 0.0000625 + 0.0000001, 5);
        assert.strictEqual(state.writes.length, 0, 'Edições de CBM/quantidade refazem soma somente em memória');

        // Reabre a fixture para testar exclusão e inclusão sem depender de edições ainda não salvas.
        await page.evaluate(() => {
            cancelarAberturaListaPedido();
            return abrirListaPedidoPorId('lista-1');
        });
        await checkFooter(page, initialVolume, 7);
        await page.locator('button[data-kind="excluir-item"][data-idx="0"]').click();
        assert.strictEqual(await page.locator('#tblListaPedidoItens tbody tr[data-lista-pedido-row]').count(), 12);
        await checkFooter(page, initialVolume - 0.0000625, 7);
        await page.locator('#btnSalvarListaPedido').click();
        await page.locator('#statusListaPedido', { hasText: 'Lista salva com sucesso.' }).waitFor();
        assert.strictEqual(state.lista.itens.length, 12);
        assert.strictEqual(state.lista.created_at, '2026-08-26T10:00:00Z');
        assert.strictEqual(state.lista.updated_at, '2026-08-26T10:00:00Z');

        await page.locator('#btnAdicionarSkuPedido').click();
        await page.locator('#addSkuInput').fill('NOVO');
        await page.locator('#addSkuQtdInput').fill('4');
        await page.locator('#addSkuValorInput').fill('5');
        await page.locator('#btnConfirmarAdicionarSku').click();
        await page.locator('#modalAdicionarSkuPedido').waitFor({ state: 'hidden' });
        assert.strictEqual(await page.locator('#tblListaPedidoItens tbody tr[data-lista-pedido-row]').count(), 13);
        await checkFooter(page, initialVolume - 0.0000625 + 0.00002, 7);
        assert.strictEqual(state.writes.length, 2, 'Só salvar exclusão e adicionar SKU atingem as APIs sintéticas');

        // Lista completa usa o mesmo total sem o rótulo parcial; quantidade zero dispensa cubagem.
        state.lista.itens = [{ SKU: 'A', Quantidade: 1, 'Estimed CBM': '0,0000001' }, { SKU: 'ZERO', Quantidade: 0, 'Estimed CBM': '' }];
        await page.evaluate(() => abrirListaPedidoPorId('lista-1'));
        await checkFooter(page, 0.0000001, 0);
        assert.deepStrictEqual(state.errors, []);
        await context.close();
        console.log('Médias editor: soma CBM total por linha, 13 linhas/841 unidades/USD 4440, parcial, vírgula/ponto/científico, precisão, quantidade zero, input/change, exclusão e inclusão: OK');
    } finally { await browser.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
