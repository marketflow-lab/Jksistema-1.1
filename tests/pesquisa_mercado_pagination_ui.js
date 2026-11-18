'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const http = require('http');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'pesquisa_mercado.html'), 'utf8')
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')
    .replace('</body>', '<script src="/pesquisa-mercado/global-ui.js"></script></body>');

async function main() {
    const items = Array.from({ length: 2400 }, (_, index) => ({
        id: `MLB${1000000000 + index}`, titulo: `Produto ${String(index + 1).padStart(4, '0')}`,
        vendedor: index < 1500 ? 'Loja A' : 'Loja B', seller_key: index < 1500 ? 'id:1' : 'id:2',
        categoria: index % 2 ? 'Freios' : 'Ferramentas', preco: 25, vendas: 2400 - index,
        media_vendas_mensal: 2400 - index, rank: index + 1, status: 'analisado'
    }));
    let job = { id: 'pagination-fixture', status: 'running', revision: 1,
        counters: { categories: 2, pages: 24, sellers: 2, ads: items.length, pending: 0 } };
    const rankingRequests = [];
    const server = http.createServer((req, res) => {
        const url = new URL(req.url, 'http://127.0.0.1');
        const json = value => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(value)); };
        if (url.pathname === '/pesquisa_mercado.html') {
            res.setHeader('Content-Type', 'text/html'); res.end(html); return;
        }
        if (url.pathname.startsWith('/pesquisa-mercado/')) {
            const name = path.basename(url.pathname);
            res.setHeader('Content-Type', name.endsWith('.css') ? 'text/css' : 'text/javascript');
            res.end(fs.readFileSync(path.join(root, 'static/pesquisa-mercado', name))); return;
        }
        if (url.pathname === '/api/pesquisa-mercado/jobs/latest') { json({ job }); return; }
        if (url.pathname.endsWith('/vendedores')) {
            json({ items: [{ seller_key: 'id:1', vendedor: 'Loja A', ads: 1500 },
                { seller_key: 'id:2', vendedor: 'Loja B', ads: 900 }] }); return;
        }
        if (url.pathname.endsWith('/categorias')) {
            json({ items: [{ categoria: 'Ferramentas' }, { categoria: 'Freios' }] }); return;
        }
        if (url.pathname.endsWith('/ranking')) {
            const query = url.searchParams.get('q') || '';
            const category = url.searchParams.get('category') || '';
            const seller = url.searchParams.get('seller_key') || '';
            const offset = Number(url.searchParams.get('offset'));
            const limit = Number(url.searchParams.get('limit'));
            rankingRequests.push({ query, category, seller, offset, limit, token: req.headers.authorization });
            const filtered = items.filter(item => (!seller || item.seller_key === seller)
                && (!category || item.categoria === category) && (!query || item.titulo.includes(query)));
            json({ items: filtered.slice(offset, offset + limit).map(item => ({ ...item, titulo: `${item.titulo} [rev ${job.revision}]` })),
                total: filtered.length, revision: job.revision }); return;
        }
        res.statusCode = 404; res.end();
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    let browser;
    try {
        browser = await chromium.launch({ headless: true });
        const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
        await context.addInitScript(() => {
            localStorage.setItem('access_token', 'pagination-fixture-token');
            window.electronAPI = {
                getPesquisaMercadoJobStatus: async () => (await fetch('/api/pesquisa-mercado/jobs/latest')).json(),
                onPesquisaMercadoProgress(callback) { window.__pmProgress = callback; return () => {}; }
            };
        });
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(`http://127.0.0.1:${server.address().port}/pesquisa_mercado.html`);
        await page.locator('#pm-mode-global').click();
        const current = () => page.locator('#pm-global-page-numbers button[aria-current="page"]');
        const editor = () => page.getByRole('textbox', { name: 'Página desejada', exact: true });
        const pageNumber = value => page.locator(`#pm-global-page-numbers button[data-page="${value}"]`);
        const waitPage = async (number, firstRank) => page.waitForFunction(({ number, firstRank }) =>
            document.querySelector('#pm-global-page-numbers button[aria-current="page"]')?.textContent.trim() === String(number)
            && document.querySelector('#pm-global-body tr td')?.textContent.trim() === String(firstRank), { number, firstRank });
        const enterPage = async number => {
            await current().dblclick();
            await editor().fill(String(number));
            await editor().press('Enter');
        };

        await waitPage(1, 1);
        assert.strictEqual(await page.locator('#pm-global-first').isDisabled(), true);
        assert.strictEqual(await page.locator('#pm-global-previous').isDisabled(), true);
        assert.strictEqual(await pageNumber(24).isVisible(), true, 'a última página pode ser selecionada pelo número');
        await page.evaluate(() => { window.__currentPageButton = document.querySelector('#pm-global-page-numbers button[aria-current="page"]'); });
        const beforeCurrentClick = rankingRequests.length;
        await current().click();
        assert.strictEqual(await page.evaluate(() => window.__currentPageButton === document.querySelector('#pm-global-page-numbers button[aria-current="page"]')), true,
            'o primeiro clique no número atual preserva o elemento para o duplo clique');
        assert.strictEqual(rankingRequests.length, beforeCurrentClick, 'clicar na página já aberta não consulta novamente a API');

        await pageNumber(3).click();
        await waitPage(3, 201);
        assert.ok(rankingRequests.some(request => !request.seller && request.offset === 200), 'o número selecionado usa o deslocamento correspondente');
        await enterPage(7);
        await waitPage(7, 601);
        assert.strictEqual(await current().evaluate(element => element === document.activeElement), true, 'confirmar a edição devolve o foco à página atual');
        await current().dblclick();
        assert.deepStrictEqual(await editor().evaluate(element => [element.selectionStart, element.selectionEnd]), [0, 1], 'o número atual vem selecionado para substituição');
        await editor().fill('18');
        await editor().press('Escape');
        await waitPage(7, 601);
        await current().press('F2');
        await editor().fill('18');
        await page.locator('#pm-global-count').click();
        assert.strictEqual(await editor().count(), 0, 'sair do campo cancela a edição');
        await waitPage(7, 601);

        await current().press('Enter');
        for (const invalid of ['0', '25', '2.5', '']) {
            const beforeInvalid = rankingRequests.length;
            await editor().fill(invalid);
            await editor().press('Enter');
            assert.strictEqual(await editor().getAttribute('aria-invalid'), 'true');
            assert.strictEqual(await page.locator('#pm-global-page-error').isVisible(), true);
            assert.strictEqual(rankingRequests.length, beforeInvalid, 'uma página inválida não consulta resultados');
        }
        await editor().fill('18');
        await editor().press('Enter');
        await waitPage(18, 1701);
        await current().dblclick();
        await pageNumber(19).click();
        await waitPage(19, 1801);
        await page.locator('#pm-global-last').click();
        await waitPage(24, 2301);
        assert.strictEqual(await page.locator('#pm-global-next').isDisabled(), true);
        assert.strictEqual(await page.locator('#pm-global-last').isDisabled(), true);
        await page.locator('#pm-global-previous').click();
        await waitPage(23, 2201);
        await page.locator('#pm-global-first').click();
        await waitPage(1, 1);
        await page.locator('#pm-global-next').click();
        await waitPage(2, 101);

        await enterPage(7);
        await waitPage(7, 601);
        await current().dblclick();
        await editor().fill('19');
        await page.evaluate(() => { window.__pageEditor = document.querySelector('.pm-global-page-editor'); });
        job = { ...job, revision: 2 };
        await page.evaluate(value => window.__pmProgress({ job: value }), job);
        await page.waitForFunction(() => document.querySelector('#pm-global-body')?.textContent.includes('[rev 2]'));
        assert.strictEqual(await editor().inputValue(), '19', 'o progresso da pesquisa preserva o número que está sendo digitado');
        assert.strictEqual(await page.evaluate(() => window.__pageEditor === document.querySelector('.pm-global-page-editor')), true,
            'atualizar os anúncios preserva o campo de edição ativo');

        await page.locator('#pm-global-tab-vendedor').click();
        await waitPage(1, 1);
        assert.strictEqual(await editor().count(), 0, 'trocar a aba cancela a edição anterior');
        await enterPage(3);
        await waitPage(3, 201);
        await page.locator('#pm-global-tab-geral').click();
        await waitPage(7, 601);
        await page.locator('#pm-global-tab-vendedor').click();
        await waitPage(3, 201);
        await current().dblclick();
        await page.locator('.pm-global-seller').filter({ hasText: 'Loja B' }).click();
        await waitPage(1, 1501);
        assert.strictEqual(await editor().count(), 0, 'trocar o vendedor cancela a edição e abre sua primeira página');
        await page.locator('#pm-global-last').click();
        await waitPage(9, 2301);
        assert.ok(rankingRequests.some(request => request.seller === 'id:2' && request.offset === 800), 'o salto final respeita o total do vendedor');
        await page.locator('#pm-global-tab-geral').click();
        await waitPage(7, 601);

        await current().dblclick();
        await editor().fill('22');
        await page.locator('#pm-global-search').fill('Produto 0001');
        await waitPage(1, 1);
        assert.strictEqual(await editor().count(), 0, 'o filtro descarta a edição da página anterior');
        assert.strictEqual(await page.locator('#pm-global-next').isDisabled(), true);
        assert.strictEqual(await page.locator('#pm-global-last').isDisabled(), true);
        assert.strictEqual(await page.locator('#pm-global-body tr').count(), 1);
        await page.locator('#pm-global-search').fill('Nenhum produto');
        await page.waitForFunction(() => document.querySelectorAll('#pm-global-body tr').length === 0);
        assert.strictEqual(await pageNumber(1).count(), 1, 'uma busca vazia mantém uma única página');
        for (const action of ['first', 'previous', 'next', 'last']) assert.strictEqual(await page.locator(`#pm-global-${action}`).isDisabled(), true);
        await page.locator('#pm-global-search').fill('');
        await waitPage(1, 1);
        await page.locator('#pm-global-category').selectOption('Freios');
        await waitPage(1, 2);
        await page.locator('#pm-global-last').click();
        await waitPage(12, 2202);
        assert.ok(rankingRequests.some(request => !request.seller && request.category === 'Freios' && request.offset === 1100),
            'a última página segue os resultados da categoria selecionada');

        await page.locator('#pm-global-category').selectOption('');
        await waitPage(1, 1);
        await page.locator('#pm-global-last').click();
        await waitPage(24, 2301);
        await current().dblclick();
        await editor().fill('17');
        await editor().evaluate(element => {
            window.__growingPageEditor = element;
            element.setSelectionRange(1, 2);
        });
        items.push(...Array.from({ length: 100 }, (_, index) => ({
            ...items[0], id: `MLB${1000002400 + index}`, titulo: `Produto ${2401 + index}`,
            vendedor: 'Loja B', seller_key: 'id:2', rank: 2401 + index
        })));
        job = { ...job, revision: 3, counters: { ...job.counters, ads: items.length } };
        await page.evaluate(value => window.__pmProgress({ job: value }), job);
        await page.waitForFunction(() => document.querySelector('#pm-global-body')?.textContent.includes('[rev 3]'));
        assert.strictEqual(await editor().inputValue(), '17', 'novos resultados preservam o número em edição');
        assert.deepStrictEqual(await editor().evaluate(element => ({
            same: element === window.__growingPageEditor,
            focused: element === document.activeElement,
            selection: [element.selectionStart, element.selectionEnd]
        })), { same: true, focused: true, selection: [1, 2] }, 'o aumento do total preserva campo, foco e seleção');
        assert.strictEqual(await pageNumber(25).isVisible(), true, 'a nova última página aparece mesmo durante a edição');
        await page.locator('#pm-global-count').click();
        await waitPage(24, 2301);
        assert.strictEqual(await pageNumber(25).isVisible(), true, 'cancelar por perda de foco mantém a última página atualizada');
        await page.locator('#pm-global-last').click();
        await waitPage(25, 2401);

        items.splice(250);
        const beforeShrink = rankingRequests.length;
        job = { ...job, revision: 4, counters: { ...job.counters, ads: items.length } };
        await page.evaluate(value => window.__pmProgress({ job: value }), job);
        await waitPage(3, 201);
        assert.strictEqual(await page.locator('#pm-global-body tr').count(), 50,
            'a redução do total recupera os anúncios da última página válida');
        assert.strictEqual(await page.locator('#pm-global-last').isDisabled(), true);
        assert.ok(rankingRequests.slice(beforeShrink).some(request => !request.seller && request.offset === 200),
            'a página que deixou de existir provoca uma consulta ao deslocamento corrigido');

        assert.ok(rankingRequests.every(request => request.limit === 100), 'todos os saltos preservam páginas de 100 anúncios');
        assert.ok(rankingRequests.every(request => request.token === 'Bearer pagination-fixture-token'), 'a paginação mantém a sessão autenticada');
        assert.deepStrictEqual(errors, []);
        console.log('Pesquisa de Mercado: paginação numerada, edição inline, progresso, filtros e rankings independentes OK');
    } finally {
        if (browser) await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
}

main().catch(error => { console.error(error); process.exitCode = 1; });
