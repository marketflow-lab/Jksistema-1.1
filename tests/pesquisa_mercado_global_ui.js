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
    const items = Array.from({ length: 105 }, (_, index) => ({
        id: `MLB${1000000000 + index}`, titulo: index === 0 ? '<script>untrusted()</script>' : `Produto ${index}`,
        url: `https://produto.mercadolivre.com.br/MLB-${1000000000 + index}-produto`,
        imagem: '', preco: 12.5, vendedor: index < 100 ? 'Loja A' : 'Loja B', seller_key: index < 100 ? 'id:1' : 'id:2',
        categoria: 'Ferramentas', vendas: 105 - index, data_criacao: '2026-09-01', media_vendas_mensal: 105 - index,
        rank: index + 1, status: 'analisado'
    }));
    items.push({ id: 'MLB2000000000', titulo: 'Pendente Avant', vendedor: 'Loja A', seller_key: 'id:1',
        categoria: 'Ferramentas', rank: null, vendas: null, media_vendas_mensal: null, erro: 'Avant sem dados',
        url: 'javascript:untrusted()', imagem: 'https://evil.example/image.png' });
    items.push({ id: 'MLB2000000001', titulo: 'Catálogo divergente', vendedor: 'Loja B', seller_key: 'id:2',
        categoria: 'Ferramentas', rank: null, erro: 'Vendedor divergente' });
    let job = { id: 'job-test', status: 'running', revision: 1, counters: { categories: 1, pages: 2, sellers: 2, ads: 107, pending: 2 }, currentCategory: 'Ferramentas',
        scopeCategory: { key: 'MLB-FERRAMENTAS', nome: 'Ferramentas', url: 'https://lista.mercadolivre.com.br/ferramentas' },
        message: 'Coletando Loja A: 33 anuncio(s).', progress: { phase: 'collecting',
            detail: 'Coletando dados do Avant Pro de Loja A: 33 anúncios coletados.',
            warning: 'Alguns anúncios ficaram sem dados suficientes do Avant Pro.' } };
    const commands = [];
    const requests = [];
    const server = http.createServer((req, res) => {
        const url = new URL(req.url, 'http://127.0.0.1');
        const json = value => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(value)); };
        if (url.pathname === '/pesquisa_mercado.html') { res.setHeader('Content-Type', 'text/html'); res.end(html); return; }
        if (url.pathname === '/dashboard.html') { res.end('<!doctype html><title>Dashboard</title>'); return; }
        if (url.pathname.startsWith('/pesquisa-mercado/')) {
            const file = path.basename(url.pathname);
            res.setHeader('Content-Type', file.endsWith('.css') ? 'text/css' : 'text/javascript');
            res.end(fs.readFileSync(path.join(root, 'static/pesquisa-mercado', file))); return;
        }
        if (url.pathname === '/test/command') {
            const action = url.searchParams.get('action');
            commands.push(action);
            if (action === 'pause') job = { ...job, status: 'paused', revision: job.revision + 1 };
            if (action === 'resume') job = { ...job, status: 'running', revision: job.revision + 1 };
            if (action === 'cancel') job = { ...job, status: 'cancelled', revision: job.revision + 1 };
            json({ job }); return;
        }
        if (url.pathname === '/api/pesquisa-mercado/jobs/latest') { json({ job }); return; }
        if (url.pathname.endsWith('/vendedores')) {
            json({ items: [{ seller_key: 'id:1', vendedor: 'Loja A', ads: 101 }, { seller_key: 'id:2', vendedor: 'Loja B', ads: 6 }] }); return;
        }
        if (url.pathname.endsWith('/categorias')) { json({ items: [{ categoria: 'Ferramentas', status: 'incomplete' }] }); return; }
        if (url.pathname.endsWith('/ranking')) {
            requests.push(url.searchParams);
            const seller = url.searchParams.get('seller_key');
            const query = (url.searchParams.get('q') || '').toLowerCase();
            const category = url.searchParams.get('category');
            const filtered = items.filter(item => (!seller || item.seller_key === seller) && (!category || item.categoria === category)
                && `${item.id} ${item.titulo} ${item.vendedor}`.toLowerCase().includes(query));
            const offset = Number(url.searchParams.get('offset'));
            const limit = Number(url.searchParams.get('limit'));
            json({ items: filtered.slice(offset, offset + limit), total: filtered.length, revision: job.revision }); return;
        }
        res.statusCode = 404; res.end();
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    let browser;
    try {
        browser = await chromium.launch({ headless: true });
        const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
        await context.addInitScript(() => {
            localStorage.setItem('access_token', 'fixture-token');
            const command = action => async payload => {
                window.__lastCommandPayload = payload;
                return (await fetch(`/test/command?action=${action}`)).json();
            };
            window.electronAPI = {
                startPesquisaMercadoJob: command('start'), getPesquisaMercadoJobStatus: command('status'),
                pausePesquisaMercadoJob: command('pause'), resumePesquisaMercadoJob: command('resume'),
                cancelPesquisaMercadoJob: command('cancel'), showPesquisaMercadoBrowser: command('browser'),
                onPesquisaMercadoProgress(callback) { window.__pmProgress = callback; return () => { window.__pmUnsubscribed = true; }; }
            };
        });
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(`${origin}/pesquisa_mercado.html`);
        await page.locator('#pm-mode-global').click();
        await page.waitForFunction(() => document.querySelectorAll('#pm-global-body tr').length === 100);
        assert.strictEqual(await page.locator('#pm-global-status').textContent(), 'O sistema está pesquisando');
        assert.match(await page.locator('#pm-global-status-detail').textContent(), /Loja A: 33/);
        assert.match(await page.locator('#pm-global-status-warning').textContent(), /Avant Pro.*A pesquisa continua/);
        assert.strictEqual(await page.locator('#pm-global-status-panel').getAttribute('data-state'), 'running');
        assert.strictEqual(await page.locator('#pm-global-start').textContent(), 'pesquisar em todo ML');
        assert.strictEqual(await page.locator('#pm-global-start').isDisabled(), true, 'uma execução ativa bloqueia nova pesquisa');
        assert.match(await page.locator('#pm-global-scope').textContent(), /Ferramentas/, 'escopo persistido fica visível na aba da pesquisa');
        assert.strictEqual(await page.locator('#pm-global-taxonomy-refresh').isDisabled(), true, 'execução ativa impede navegação do seletor');
        assert.strictEqual(await page.locator('#pm-global-body script').count(), 0, 'títulos externos devem ser texto escapado');
        assert.match(await page.locator('#pm-global-body').textContent(), /<script>untrusted\(\)<\/script>/);
        assert.match(await page.locator('#pm-global-category option').last().textContent(), /incompleta/);
        await page.locator('#pm-global-next').click();
        await page.waitForFunction(() => document.querySelectorAll('#pm-global-body tr').length === 7);
        const invalid = page.locator('#pm-global-body tr').filter({ hasText: 'Pendente Avant' });
        assert.strictEqual(await invalid.locator('td').first().textContent(), '', 'pendente não recebe posição numérica');
        assert.strictEqual(await invalid.locator('a,img').count(), 0, 'links e imagens de hosts externos devem ser recusados');
        assert.match(await invalid.textContent(), /Avant sem dados/);
        await page.locator('#pm-global-tab-vendedor').click();
        assert.strictEqual(await page.locator('#pm-global-sellers').isVisible(), true);
        await page.locator('#pm-global-seller-search').fill('Loja B');
        assert.strictEqual(await page.locator('.pm-global-seller').count(), 1, 'busca filtra a lista de vendedores');
        await page.locator('.pm-global-seller').click();
        await page.waitForFunction(() => document.querySelectorAll('#pm-global-body tr').length === 6);
        assert.strictEqual(await page.locator('#pm-global-tab-vendedor').getAttribute('aria-selected'), 'true');
        items.push({ ...items[100], id: 'MLB3000000000', titulo: 'Novo produto', rank: 106 });
        job = { ...job, revision: 2, counters: { ...job.counters, ads: 108 }, currentCategory: { key: 'MLB1', nome: 'Ferramentas', url: 'https://lista.mercadolivre.com.br/ferramentas' } };
        await page.evaluate(value => window.__pmProgress({ job: value }), job);
        await page.waitForFunction(() => document.querySelectorAll('#pm-global-body tr').length === 7);
        assert.strictEqual(await page.locator('#pm-global-category-current').textContent(), 'Categoria atual: Ferramentas', 'nome da categoria atual vem do runner');
        assert.strictEqual(await page.locator('#pm-global-tab-vendedor').getAttribute('aria-selected'), 'true', 'progresso preserva a aba ativa');
        await page.locator('#pm-global-tab-geral').click();
        assert.match(await page.locator('#pm-global-count').textContent(), /108/, 'progresso atualiza também ranking geral em cache');
        await page.locator('#pm-global-search').fill('Novo produto');
        await page.waitForFunction(() => document.querySelectorAll('#pm-global-body tr').length === 1);
        assert.match(await page.locator('#pm-global-body').textContent(), /Novo produto/);
        await page.locator('#pm-global-category').selectOption('Ferramentas');
        await page.waitForTimeout(350);
        assert.ok(requests.every(params => params.get('limit') === '100'));
        assert.ok(requests.some(params => params.get('category') === 'Ferramentas' && params.get('q') === 'Novo produto'));
        await page.locator('#pm-global-pause').click();
        await page.waitForFunction(() => !document.getElementById('pm-global-resume').disabled);
        assert.strictEqual(await page.locator('#pm-global-status').textContent(), 'Pesquisa pausada');
        assert.match(await page.locator('#pm-global-status-detail').textContent(), /Retomar/);
        assert.doesNotMatch(await page.locator('#pm-global-status-warning').textContent(), /A pesquisa continua/);
        assert.strictEqual(await page.evaluate(() => window.__lastCommandPayload.jobId), job.id);
        await page.locator('#pm-global-resume').click();
        await page.waitForFunction(() => !document.getElementById('pm-global-pause').disabled);
        await page.locator('#pm-global-browser').click();
        await page.waitForFunction(() => !document.getElementById('pm-global-browser').disabled);
        const cancels = commands.filter(action => action === 'cancel').length;
        await page.goto(`${origin}/dashboard.html`);
        assert.strictEqual(commands.filter(action => action === 'cancel').length, cancels, 'sair do módulo preserva pesquisa em segundo plano');
        await page.goto(`${origin}/pesquisa_mercado.html`);
        await page.locator('#pm-mode-global').click();
        await page.waitForFunction(() => document.querySelectorAll('#pm-global-body tr').length === 100);
        assert.strictEqual(await page.locator('#pm-global-start').isDisabled(), true, 'reabrir recupera a execução persistida');
        await page.evaluate(() => window.__pmProgress({ job: { id: 'other-user-job', status: 'completed', revision: 999, counters: { ads: 9999 } } }));
        assert.match(await page.locator('#pm-global-ads-count').textContent(), /108/, 'evento de outra execução não altera sessão autenticada');
        assert.strictEqual(await page.locator('#pm-global-start').isDisabled(), true);
        job = { ...job, status: 'failed', revision: job.revision + 1 };
        await page.evaluate(value => window.__pmProgress({ job: value }), job);
        assert.strictEqual(await page.locator('#pm-global-resume').isDisabled(), false, 'falha com progresso preservado permite retomar');
        assert.strictEqual(await page.locator('#pm-global-browser').isDisabled(), false, 'pesquisa com falha permite inspecionar navegador');
        await page.locator('#pm-global-resume').click();
        await page.waitForFunction(() => !document.getElementById('pm-global-pause').disabled);
        await page.locator('#pm-global-cancel').click();
        await page.waitForFunction(() => document.getElementById('pm-global-cancel').disabled
            && document.getElementById('pm-global-status').textContent === 'Pesquisa cancelada'
            && !document.getElementById('pm-global-taxonomy-refresh').disabled);
        assert.strictEqual(await page.locator('#pm-global-start').isDisabled(), true, 'nova pesquisa exige selecionar uma categoria');
        assert.strictEqual(await page.locator('#pm-global-browser').isDisabled(), true, 'execução encerrada não oferece navegador ativo');
        assert.strictEqual(await page.locator('#pm-btn-analisar').isDisabled(), false, 'controles manuais são independentes');
        assert.strictEqual(await page.locator('#pm-global-status').textContent(), 'Pesquisa cancelada');
        for (const [status, title] of [['waiting_login', 'Aguardando login'],
            ['completed', 'Pesquisa concluída'], ['completed_partial', 'Pesquisa concluída com pendências'],
            ['failed', 'Pesquisa interrompida por uma falha']]) {
            job = { ...job, status, revision: job.revision + 1 };
            await page.evaluate(value => window.__pmProgress({ job: value }), job);
            assert.strictEqual(await page.locator('#pm-global-status').textContent(), title);
            assert.doesNotMatch(await page.locator('#pm-global-status-detail').textContent(), /33 anúncios coletados/, 'estado encerrado não mostra atividade de coleta antiga');
        }
        job = { ...job, status: 'running', revision: job.revision + 1, progress: undefined,
            message: 'Alguns anuncios ficaram sem dados suficientes do Avant.' };
        await page.evaluate(value => window.__pmProgress({ job: value }), job);
        assert.strictEqual(await page.locator('#pm-global-status').textContent(), 'O sistema está pesquisando', 'execução legada com aviso continua explícita');
        assert.match(await page.locator('#pm-global-status-warning').textContent(), /Avant/);
        assert.doesNotMatch(await page.locator('#pm-global-status-detail').textContent(), /dados suficientes/);
        job = { ...job, revision: job.revision + 1, message: 'Descobrindo vendedores em Ferramentas.',
            progress: { phase: 'discovery', detail: '<script>untrusted()</script>', warning: '<img src=x onerror=untrusted()>' } };
        await page.evaluate(value => window.__pmProgress({ job: value }), job);
        assert.strictEqual(await page.locator('#pm-global-status-panel script, #pm-global-status-panel img').count(), 0, 'status externo usa texto, sem executar HTML');
        assert.match(await page.locator('#pm-global-status-detail').textContent(), /<script>/);
        for (const width of [390, 1440]) {
            await page.setViewportSize({ width, height: 900 });
            assert.strictEqual(await page.locator('#pm-global-status-panel').evaluate(element => element.scrollWidth > element.clientWidth), false, 'status cabe na tela sem cortar texto');
        }
        await page.emulateMedia({ reducedMotion: 'reduce' });
        assert.strictEqual(await page.locator('.pm-global-status-indicator').evaluate(element => getComputedStyle(element).animationName), 'none');
        assert.deepStrictEqual(errors, []);
        await page.setViewportSize({ width: 640, height: 900 });
        assert.strictEqual(await page.locator('#pm-global-start').isVisible(), true);
        await page.evaluate(() => {
            localStorage.removeItem('access_token');
            dispatchEvent(new StorageEvent('storage', { key: 'access_token' }));
        });
        assert.strictEqual(await page.locator('#pm-global-body tr').count(), 0, 'logout limpa os anúncios da tela');
        assert.strictEqual(await page.locator('#pm-global-ads-count').textContent(), '0', 'logout limpa dados da sessão anterior');
        assert.strictEqual(await page.locator('#pm-global-status-detail').textContent(), '', 'logout limpa a etapa da sessão anterior');
        assert.strictEqual(await page.locator('#pm-global-status-warning').textContent(), '', 'logout limpa o aviso da sessão anterior');
        assert.strictEqual(await page.locator('#pm-global-start').isDisabled(), true);
        await page.evaluate(() => window.__pmProgress({ job: { id: 'job-test', status: 'running', revision: 999 } }));
        assert.strictEqual(await page.locator('#pm-global-body tr').count(), 0, 'evento antigo após logout não repõe dados');
        console.log('Pesquisa de Mercado global UI: paginação, abas, filtros, progresso e controles OK');
    } finally {
        if (browser) await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
