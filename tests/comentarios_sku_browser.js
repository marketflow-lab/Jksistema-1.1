'use strict';

const assert = require('node:assert/strict');
const path = require('node:path');
const { chromium } = require('playwright');

const root = path.join(__dirname, '..', 'static');
const origin = 'http://jk.test';
const lojas = [{ nome: 'Loja A', store_id: 'a' }, { nome: 'Loja B', store_id: 'b' }];
const nota = 'Conferir reposição.\n<img src=x onerror=alert(1)>';
const ativosVendas = new Set([
    '/vendas/runtime.js', '/vendas/periodo-cache.js', '/vendas/sync.js',
    '/vendas/visual-novo-bridge.js', '/vendas/visual-novo.js',
    '/vendas/styles.css', '/vendas/visual-novo.css',
]);

async function main() {
    const channel = process.env.JK_TEST_CHROMIUM_CHANNEL;
    const browser = await chromium.launch({ headless: true, ...(channel ? { channel } : {}) });
    try {
        const context = await browser.newContext({ viewport: { width: 1320, height: 800 } });
        context.setDefaultTimeout(6000);
        const page = await context.newPage();
        const abaVendas = await context.newPage();
        const erros = [];
        const escritos = [];
        const comentarios = { a: {}, b: {}, geral: {} };
        let falharProximo = true;
        for (const aba of [page, abaVendas]) aba.on('pageerror', erro => erros.push(erro.message));
        await context.addInitScript(() => {
            localStorage.setItem('access_token', 'token-teste');
            localStorage.setItem('user_data', JSON.stringify({ client_id: 'teste', username: 'operador' }));
        });
        await context.route('**/*', async route => {
            const request = route.request();
            const url = new URL(request.url());
            if (url.origin !== origin) return route.abort();
            const json = (data, status = 200) => route.fulfill({
                contentType: 'application/json', status, body: JSON.stringify(data),
            });
            const pathname = url.pathname;
            if (pathname === '/auth.js') return route.fulfill({
                contentType: 'application/javascript',
                body: 'window.verificarSessao=()=>true; window.obterAuthHeaders=(extra)=>({Authorization:"Bearer token-teste",...extra});',
            });
            if (pathname === '/api/lojas') return json(lojas);
            if (pathname === '/api/medias-compras/preferencias-skus-ocultos') return json({ skus_ocultos: [] });
            if (pathname === '/api/medias-compras/visao') return json({
                colunas_meses: [{ key: '2026-11', label: '11/2026' }],
                itens: [
                    { sku: '001', titulo_anuncio: 'Produto com observação', saldo_atual_estoque: 5, posicao_estoque: 5 },
                    { sku: '002', titulo_anuncio: 'Produto sem observação', saldo_atual_estoque: 2, posicao_estoque: 2 },
                ],
            });
            if (pathname === '/api/comentarios-sku') {
                assert.equal(request.headers().authorization, 'Bearer token-teste');
                const escopo = url.searchParams.get('store_id')
                    || lojas.find(loja => loja.nome === url.searchParams.get('loja'))?.store_id || 'geral';
                if (request.method() === 'GET') return json({ comentarios: comentarios[escopo] });
                if (falharProximo) { falharProximo = false; return json({}, 503); }
                const { sku, comentario } = request.postDataJSON();
                const chave = sku === '1' ? '001' : sku;
                if (comentario) comentarios[escopo][chave] = comentario;
                else delete comentarios[escopo][chave];
                escritos.push({ escopo, sku, comentario });
                return json({ sku: chave, comentario });
            }
            if (pathname === '/vendas_sku.html') return route.fulfill({ contentType: 'text/html', body: '<h1>Detalhes</h1>' });
            if (pathname === '/medias_compras.html' || pathname === '/vendas.html'
                || pathname.startsWith('/medias_compras/') || ativosVendas.has(pathname)
                || /^\/comentarios-sku\.(css|js)$/.test(pathname)) {
                return route.fulfill({
                    path: path.join(root, pathname.slice(1)),
                    contentType: pathname.endsWith('.html') ? 'text/html; charset=utf-8'
                        : pathname.endsWith('.css') ? 'text/css' : 'application/javascript',
                });
            }
            if (pathname.startsWith('/api/')) return json({});
            return route.fulfill({ contentType: 'application/javascript', body: '' });
        });

        const menu = page.locator('.jk-sku-comment-menu');
        const dialogo = page.getByRole('dialog', { name: /Comentário do SKU/ });
        const texto = dialogo.getByRole('textbox', { name: 'Observação' });
        const medias = page.locator('#tblResultado tbody tr').filter({ hasText: 'Produto com observação' });
        const celula = medias.locator('td').first();
        const lojaMedias = nome => page.locator('#lojaBotoes .loja-btn').filter({ hasText: nome });
        async function abrirMenu(linha, label) {
            await linha.locator('td').nth(2).click({ button: 'right' });
            await menu.waitFor({ state: 'visible' });
            assert.equal(await menu.textContent(), label);
            await menu.click();
            await dialogo.waitFor();
        }
        async function lerNaCelula(aba, alvo, esperado) {
            const balao = aba.getByRole('tooltip');
            await alvo.locator('xpath=self::*[contains(@class, "jk-sku-has-comment")]').waitFor();
            await alvo.hover();
            await balao.waitFor();
            assert.equal(await balao.textContent(), esperado);
            assert.equal(await balao.locator('img').count(), 0, 'O texto da observação não pode criar HTML');
        }

        const vendas = abaVendas.locator('#tBody tr[data-sku="1"]');
        async function abrirVendas(aba) {
            await aba.goto(origin + '/vendas.html');
            await aba.evaluate(lista => {
                lojaSelecionada = 'Loja A';
                lojasDisponiveis = lista;
                renderTabelaAgrupada([{ sku: '1', produto: 'Mesmo SKU em Vendas', pedidos: 1, itens: 1, valor: 20 }]);
            }, lojas);
        }
        await Promise.all([
            abaVendas.waitForResponse(resposta => resposta.url().endsWith('/api/comentarios-sku?loja=Loja+A')),
            abrirVendas(abaVendas),
        ]);
        await vendas.waitFor();
        assert.equal(await vendas.locator('.jk-sku-has-comment').count(), 0);

        await page.goto(origin + '/medias_compras.html');
        await lojaMedias('Loja A').click();
        await abrirMenu(medias, 'Adicionar comentário');
        await texto.fill(nota);
        await dialogo.getByRole('button', { name: 'Salvar' }).click();
        await dialogo.getByRole('status').getByText(/Não foi possível salvar/).waitFor();
        assert.equal(await texto.inputValue(), nota, 'Erro ao salvar mantém o que foi escrito');
        await dialogo.getByRole('button', { name: 'Salvar' }).click();
        await dialogo.waitFor({ state: 'hidden' });
        assert.deepEqual(escritos, [{ escopo: 'a', sku: '001', comentario: nota }]);
        await lerNaCelula(page, celula, nota);
        await lerNaCelula(abaVendas, vendas.locator('td').first(), nota);

        await lojaMedias('Loja B').click();
        await abrirMenu(medias, 'Adicionar comentário');
        assert.equal(await texto.inputValue(), '', 'A nota da Loja A não é usada na Loja B');
        await dialogo.getByRole('button', { name: 'Cancelar' }).click();
        await page.reload();
        await lojaMedias('Loja A').click();
        await lerNaCelula(page, celula, nota);
        await celula.press('Shift+F10');
        assert.equal(await menu.textContent(), 'Editar comentário');
        await menu.press('Escape');

        await Promise.all([abaVendas.waitForURL('**/vendas_sku.html?*'), vendas.locator('td').first().click()]);
        assert.equal(new URL(abaVendas.url()).searchParams.get('sku'), '1', 'Clique esquerdo ainda abre o SKU');

        await abrirVendas(page);
        await page.locator('#btnVersaoNovaVendas').click();
        await page.locator('[data-vn-tab="products"]').click();
        const nova = page.locator('#vnProductTableBody tr');
        await lerNaCelula(page, nova.locator('.vn-sku'), nota);
        await abrirMenu(nova, 'Editar comentário');
        await dialogo.getByRole('button', { name: 'Remover comentário' }).click();
        await dialogo.waitFor({ state: 'hidden' });
        assert.equal(escritos.at(-1).comentario, '');
        assert.equal(await nova.locator('.jk-sku-has-comment').count(), 0);
        assert.deepEqual(erros, []);
        console.log('Comentários por SKU em Médias e Vendas: OK');
    } finally {
        await browser.close();
    }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
