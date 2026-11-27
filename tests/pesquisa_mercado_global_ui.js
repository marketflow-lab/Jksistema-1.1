'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'pesquisa_mercado.html'), 'utf8')
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')
    .replace('</body>', '<script src="/pesquisa-mercado/core.js"></script><script src="/pesquisa-mercado/global-ui.js"></script></body>');

async function checkDetailConsultations(page, job) {
    const now = Date.parse('2026-11-27T12:00:00Z');
    await page.clock.setFixedTime(now);
    const loading = { key: 1, adId: 'MLB1000000001', title: 'Selim <script>untrusted()</script>',
        startedAt: now - 55000, phase: 'missing_fields', missingFields: ['data_criacao', 'media_mensal'],
        retryAt: now + 65000, attempt: 1 };
    const failed = { key: 2, adId: 'MLB1000000002', title: 'Pedal de bicicleta', startedAt: now - 65000,
        phase: 'failed', missingFields: ['vendas'], error: 'O painel do anúncio não respondeu.',
        retryAt: now + 55000, attempt: 1 };
    const progress = async consultations => {
        job.detailConsultations = consultations;
        await page.evaluate(value => window.__pmProgress({ job: value }), job);
    };
    await progress([loading, failed]);
    const list = page.locator('#pm-global-detail-consultations');
    const first = list.locator('li').filter({ hasText: loading.adId });
    const second = list.locator('li').filter({ hasText: failed.adId });
    assert.equal(await list.locator('li').count(), 2);
    assert.match(await first.textContent(), /Selim <script>untrusted\(\)<\/script>/);
    assert.equal(await first.locator('script').count(), 0);
    assert.match(await first.textContent(), /data de cria[çc][ãa]o/i);
    assert.match(await first.textContent(), /mensal|mensais/i);
    assert.ok((await second.textContent()).includes(failed.error));
    assert.match(await second.textContent(), /vendas/i);
    assert.equal(await list.locator('button').count(), 0, 'nenhuma fase, incluindo falha, oferece reconsulta manual');
    assert.match(await first.textContent(), /55 s desde o início/);
    assert.match(await first.textContent(), /Reconsulta automática em 1 min 05 s/);
    assert.match(await second.textContent(), /Reconsulta automática em 55 s/);

    const originalRow = await first.elementHandle();
    await page.clock.setFixedTime(now + 65000);
    await page.waitForFunction(() => {
        const text = document.querySelector('#pm-global-detail-consultations li')?.textContent || '';
        return text.includes('2 min 00 s desde o início') && text.includes('Aguardando reconsulta automática');
    }, undefined, { timeout: 4000 });
    const verifying = { ...loading, phase: 'confirming', missingFields: [], attempt: 2, retryAt: null };
    await progress([verifying, failed]);
    assert.ok(await first.evaluate((row, original) => row === original, originalRow), 'a nova tentativa atualiza a mesma linha do anúncio');
    await originalRow.dispose();
    const verification = await first.textContent();
    assert.match(verification, /Tentativa 2/);
    assert.match(verification, /confirmando/i);
    assert.match(verification, /2 min 00 s desde o início/, 'o relógio total continua após a reconsulta');
    assert.doesNotMatch(verification, /reconsulta automática|média mensal|data de cria[çc][ãa]o/i);
    assert.ok((await second.textContent()).includes(failed.error));
    await progress([]);
    assert.equal(await list.isVisible(), false);
    assert.equal(await list.locator('li').count(), 0);
    await progress([verifying]);
}

async function main() {
    const items = Array.from({ length: 105 }, (_, index) => ({
        id: `MLB${1000000000 + index}`, titulo: index === 0 ? '<script>untrusted()</script>' : `Produto ${index}`,
        url: `https://produto.mercadolivre.com.br/MLB-${1000000000 + index}-produto`,
        imagem: '', preco: 12.5, vendedor: index < 100 ? 'Loja A' : 'Loja B', seller_key: index < 100 ? 'id:1' : 'id:2',
        categoria: 'Ferramentas', vendas: 105 - index, data_criacao: '2026-09-01', media_vendas_mensal: 105 - index,
        rank: index + 1, status: 'analisado'
    }));
    Object.assign(items[0], { vendas_exibicao: '188', vendas_rotulo: 'Vendas estimadas', vendas_limite_inferior: '+100', data_criacao: 'há 2 anos',
        media_vendas_mensal_exibicao: '5/Mês', media_mensal_rotulo: 'Vendas mensais' });
    Object.assign(items[1], { vendas: null, vendas_limite_inferior: '+100', media_vendas_mensal_exibicao: '4/Mês', media_mensal_rotulo: 'Ritmo atual' });
    const catalogueTooltip = 'Vendas de catalogo sem identificacao especifica do vendedor. A origem dos dados anteriores deste anuncio precisa ser confirmada em uma nova coleta completa.';
    const exhausted = 'O Avant Pro não confirmou os dados após 3 tentativas.';
    items[101] = { ...items[101], catalogo: true, rank: null, status: 'pending', erro: `Vendas de catalogo sem identificacao especifica do vendedor. ${exhausted}` };
    items[102] = { ...items[102], catalogo: true, rank: null, status: 'catalogo_sem_evidencia_vendedor' };
    items[103] = { ...items[103], catalogo: true, rank: null, status: 'pending',
        erro: 'Vendas de catálogo sem identificação específica do vendedor. ' + catalogueTooltip };
    items[104] = { ...items[104], catalogo: true, erro: catalogueTooltip };
    items.push({ id: 'MLB2000000000', titulo: 'Pendente Avant', vendedor: 'Loja A', seller_key: 'id:1',
        categoria: 'Ferramentas', rank: null, vendas: null, media_vendas_mensal: null, erro: 'Avant sem dados',
        url: 'javascript:untrusted()', imagem: 'https://evil.example/image.png' });
    items.push({ id: 'MLB2000000001', titulo: 'Catálogo divergente', vendedor: 'Loja B', seller_key: 'id:2',
        categoria: 'Ferramentas', rank: null, erro: 'Vendedor divergente' });
    let job = { id: 'job-test', status: 'running', revision: 1, counters: { categories: 1, pages: 2, sellers: 2, ads: 107, pending: 4 }, currentCategory: 'Ferramentas',
        scopeCategory: { key: 'MLB-FERRAMENTAS', nome: 'Ferramentas', url: 'https://lista.mercadolivre.com.br/ferramentas' },
        message: 'Coletando Loja A: 33 anuncio(s).', progress: { phase: 'collecting',
            detail: 'Coletando dados do Avant Pro de Loja A: 33 anúncios coletados.',
            warning: 'Alguns anúncios ficaram sem dados suficientes do Avant Pro.' } };
    const commands = [];
    const requests = [];
    let chrome = false;
    const origin = 'http://jk.test';
    function respond(route) {
        const url = new URL(route.request().url());
        if (url.origin !== origin) return route.abort();
        const json = value => route.fulfill({ json: value });
        if (url.pathname === '/pesquisa_mercado.html') return route.fulfill({ contentType: 'text/html', body: html });
        if (url.pathname === '/dashboard.html') return route.fulfill({ contentType: 'text/html', body: '<!doctype html><title>Dashboard</title>' });
        if (url.pathname.startsWith('/pesquisa-mercado/')) {
            const file = path.basename(url.pathname);
            return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : 'text/javascript',
                body: fs.readFileSync(path.join(root, 'static/pesquisa-mercado', file)) });
        }
        if (url.pathname === '/test/command') {
            const action = url.searchParams.get('action');
            commands.push(action);
            if (action === 'pause') job = { ...job, status: 'paused', revision: job.revision + 1 };
            if (action === 'resume') job = { ...job, status: 'running', revision: job.revision + 1 };
            if (action === 'recover-incomplete') job = { ...job, status: 'running', revision: job.revision + 1,
                checkpoint: { ...job.checkpoint, recovery: { categoryKeys: ['MLB1', 'MLB2'], index: 0 } } };
            if (action === 'cancel') job = { ...job, status: 'cancelled', revision: job.revision + 1 };
            if (action === 'status' && chrome) return json({ job, browser: 'chrome' });
            if (action === 'browser' && chrome) {
                job = { ...job, revision: job.revision + 1 };
                const { id, status, revision, marketplaceAccess } = job;
                return json({ job: { id, status, revision, marketplaceAccess } });
            }
            return json({ job });
        }
        if (url.pathname === '/api/pesquisa-mercado/jobs/latest') return json({ job });
        if (url.pathname.endsWith('/vendedores')) {
            return json({ items: [{ seller_key: 'id:1', vendedor: 'Loja A', ads: 101 }, { seller_key: 'id:2', vendedor: 'Loja B', ads: 6 }] });
        }
        if (url.pathname.endsWith('/categorias')) return json({ items: [{ categoria: 'Ferramentas', status: 'incomplete' }] });
        if (url.pathname.endsWith('/ranking')) {
            requests.push(url.searchParams);
            const seller = url.searchParams.get('seller_key');
            const query = (url.searchParams.get('q') || '').toLowerCase();
            const category = url.searchParams.get('category');
            const filtered = items.filter(item => (!seller || item.seller_key === seller) && (!category || item.categoria === category)
                && `${item.id} ${item.titulo} ${item.vendedor}`.toLowerCase().includes(query));
            const offset = Number(url.searchParams.get('offset'));
            const limit = Number(url.searchParams.get('limit'));
            return json({ items: filtered.slice(offset, offset + limit), total: filtered.length, revision: job.revision });
        }
        return route.fulfill({ status: 404 });
    }
    let browser;
    try {
        browser = await chromium.launch({ headless: true });
        const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
        await context.route('**/*', respond);
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
                recoverPesquisaMercadoIncomplete: command('recover-incomplete'),
                onPesquisaMercadoProgress(callback) { window.__pmProgress = callback; return () => { window.__pmUnsubscribed = true; }; }
            };
        });
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(`${origin}/pesquisa_mercado.html`);
        await page.locator('#pm-mode-global').click();
        await page.waitForFunction(() => document.querySelectorAll('#pm-global-body tr').length === 100);
        const ranking = page.locator('#pm-global-body tr');
        assert.match(await ranking.first().locator('.sales-cell').innerText(), /188.*Vendas estimadas.*\+100/s);
        assert.match(await ranking.first().innerText(), /há 2 anos.*5\/Mês/s);
        assert.strictEqual(await ranking.nth(1).locator('.sales-cell').innerText(), '+100');
        assert.match(await ranking.nth(1).locator('.monthly-cell').innerText(), /4\/Mês.*Ritmo atual/s);
        assert.strictEqual(await ranking.nth(2).locator('.monthly-cell').innerText(), '103', 'legado mantém a formatação numérica');
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
        const exhaustedRow = ranking.filter({ hasText: 'Produto 101' });
        assert.strictEqual(await exhaustedRow.locator('.status-chip').innerText(), exhausted);
        assert.strictEqual(await exhaustedRow.locator('.status-chip').getAttribute('title'), items[101].erro);
        assert.strictEqual(await exhaustedRow.locator('td').first().innerText(), '', 'o anúncio esgotado continua pendente');
        for (const title of ['Produto 102', 'Produto 103']) {
            const row = page.locator('#pm-global-body tr').filter({ hasText: title });
            const chip = row.locator('.status-chip');
            assert.strictEqual(await chip.textContent(), 'Anuncio de catálogo', 'falta de comprovação de catálogo usa um status curto');
            await chip.hover();
            assert.strictEqual(await chip.getAttribute('title'), catalogueTooltip, 'hover mantém a explicação exata sem duplicar o texto anterior');
            assert.doesNotMatch(await row.innerText(), /identifica[cç][aã]o|origem dos dados/i, 'a explicação longa não amplia a linha da tabela');
            assert.strictEqual(await row.locator('td').first().textContent(), '', 'catálogo sem comprovação continua sem posição no ranking');
        }
        const verifiedCatalogue = page.locator('#pm-global-body tr').filter({ hasText: 'Produto 104' }).locator('.status-chip');
        assert.strictEqual(await verifiedCatalogue.textContent(), 'Analisado', 'catálogo validado mantém o estado analisado');
        assert.strictEqual(await verifiedCatalogue.getAttribute('title'), null, 'erro legado não sobrepõe um catálogo validado');
        const otherFailure = page.locator('#pm-global-body tr').filter({ hasText: 'Catálogo divergente' }).locator('.status-chip');
        assert.strictEqual(await otherFailure.textContent(), 'Vendedor divergente', 'outras falhas não recebem o rótulo de falta de comprovação de catálogo');
        assert.strictEqual(await otherFailure.getAttribute('title'), null);
        await page.locator('#pm-global-tab-vendedor').click();
        assert.strictEqual(await page.locator('#pm-global-sellers').isVisible(), true);
        await page.locator('#pm-global-seller-search').fill('Loja B');
        assert.strictEqual(await page.locator('.pm-global-seller').count(), 1, 'busca filtra a lista de vendedores');
        await page.locator('.pm-global-seller').click();
        await page.waitForFunction(() => document.querySelectorAll('#pm-global-body tr').length === 6);
        const sellerCatalogue = page.locator('#pm-global-body tr').filter({ hasText: 'Produto 103' }).locator('.status-chip');
        assert.strictEqual(await sellerCatalogue.textContent(), 'Anuncio de catálogo', 'ranking por vendedor compartilha o status curto');
        assert.strictEqual(await sellerCatalogue.getAttribute('title'), catalogueTooltip);
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
        const filteredRank = page.waitForResponse(response => {
            const { pathname, searchParams } = new URL(response.url());
            return pathname.endsWith('/ranking') && searchParams.get('category') === 'Ferramentas'
                && searchParams.get('q') === 'Novo produto';
        });
        await page.locator('#pm-global-category').selectOption('Ferramentas');
        await filteredRank;
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
        const url403 = 'https://www.mercadolivre.com.br/categorias';
        const detailPanel = page.locator('#pm-global-status-detail');
        chrome = true;
        job = { ...job, revision: job.revision + 1, status: 'paused', progress: undefined,
            message: 'Feche a janela do Chrome dedicada ao login e clique em Retomar.',
            marketplaceAccess: { status: 403, stage: 'category', url: url403 } };
        await page.reload();
        await page.locator('#pm-mode-global').click();
        await page.waitForFunction(() => document.querySelector('#pm-global-status-detail').textContent.includes('HTTP 403'));
        const pausedText = await detailPanel.textContent();
        assert.match(pausedText, /HTTP 403 no índice de categorias/);
        assert.match(pausedText, /Feche a janela do Chrome.*Retomar/);
        assert.ok(pausedText.includes(url403));
        await page.locator('#pm-global-browser').click();
        await page.waitForFunction(() => !document.querySelector('#pm-global-browser').disabled);
        assert.equal(await detailPanel.textContent(), pausedText, 'a resposta compacta de Mostrar navegador mantém o endereço e o motivo');
        job = { ...job, revision: job.revision + 1, marketplaceAccess: { status: 429 },
            message: 'Mercado Livre retornou HTTP 429 na categoria. Pesquisa pausada para respeitar o limite de consultas.' };
        await page.evaluate(value => window.__pmProgress({ job: value }), job);
        assert.match(await detailPanel.textContent(), /HTTP 429/);
        assert.doesNotMatch(await detailPanel.textContent(), /403|mercadolivre\.com/);
        // Older checkpoints may identify the HTTP response without carrying a public address.
        job = { ...job, revision: job.revision + 1, marketplaceAccess: { status: 403, stage: 'category' },
            message: 'Mercado Livre retornou HTTP 403 na categoria.' };
        await page.evaluate(value => window.__pmProgress({ job: value }), job);
        assert.match(await detailPanel.textContent(), /HTTP 403 na categoria/);
        assert.doesNotMatch(await detailPanel.textContent(), /mercadolivre\.com|índice de categorias/);
        for (const [reason, title, expectedDetail] of [
            ['ml_login_required', 'Acesso ao Mercado Livre pendente', /solicitação do Mercado Livre/],
            ['ml_verification_required', 'Verificação do Mercado Livre pendente', /verificação de segurança/],
            ['avant_login_required', 'Login do Avant Pro pendente', /Entre no Avant Pro/]
        ]) {
            job = { ...job, status: 'waiting_login', revision: job.revision + 1, message: reason,
                progress: reason === 'ml_login_required' ? undefined : { phase: 'waiting_login', detail: reason } };
            await page.evaluate(value => window.__pmProgress({ job: value }), job);
            assert.strictEqual(await page.locator('#pm-global-status').textContent(), title);
            const detail = await page.locator('#pm-global-status-detail').textContent();
            assert.match(detail, expectedDetail);
            assert.doesNotMatch(detail, /HTTP 403/);
            assert.match(detail, /Retomar/);
            assert.strictEqual(await page.locator('#pm-global-browser').isDisabled(), false);
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
        job = { ...job, revision: job.revision + 1, status: 'completed_partial', counters: { ...job.counters, pending: 0 },
            progress: { phase: 'completed_partial', detail: '', warning: '' },
            checkpoint: { officialTaxonomy: { ancestry: [{ nome: 'Esportes e Fitness' }, { nome: 'Ciclismo' }, { nome: 'Peças' }] },
                incompleteCategories: [{ key: 'MLB1', nome: 'Freios', code: 'category_access_denied', motivo: 'private raw details',
                    stage: 'navigation', dependency_key: 'MLB2', dependency_name: 'Peças', discovery_outcome: 'timeout' }, 'Categoria legada'] } };
        await page.evaluate(value => window.__pmProgress({ job: value }), job);
        assert.match(await page.locator('#pm-global-scope').textContent(), /Esportes e Fitness → Ciclismo → Peças/);
        assert.strictEqual(await page.locator('#pm-global-incomplete-count').textContent(), '2', 'falhas de categorias independem dos anúncios pendentes');
        assert.strictEqual(await page.locator('#pm-global-pending-count').textContent(), '0');
        assert.match(await page.locator('#pm-global-incomplete-list').textContent(), /bloqueou.*Motivo anterior desconhecido/s);
        assert.match(await page.locator('#pm-global-incomplete-list').textContent(), /categoria anterior Peças \(MLB2\).*busca alternativa atingiu o tempo limite/s);
        assert.match(await page.locator('#pm-global-status-warning').textContent(), /Freios.*categoria anterior Peças \(MLB2\)/);
        assert.doesNotMatch(await page.locator('#pm-global-incomplete-list').textContent(), /private raw details/);
        assert.strictEqual(await page.locator('#pm-global-browser').isEnabled(), true, 'pesquisa parcial permite diagnóstico');
        assert.strictEqual(await page.locator('#pm-global-resume').isDisabled(), true, 'recuperação usa ação explícita');
        const recoveryRevision = job.revision;
        await page.locator('#pm-global-recover-incomplete').click();
        await page.waitForFunction(() => document.querySelector('#pm-global-recovery-progress').textContent.includes('0 de 2'));
        assert.deepStrictEqual(await page.evaluate(() => ({ jobId: window.__lastCommandPayload.jobId, expectedRevision: window.__lastCommandPayload.expectedRevision })),
            { jobId: job.id, expectedRevision: recoveryRevision }, 'recuperação envia a revisão da pesquisa exibida');
        assert.strictEqual(commands.filter(action => action === 'recover-incomplete').length, 1);
        await page.evaluate(value => window.__pmProgress({ job: { ...value, checkpoint: undefined, scopePath: value.checkpoint.officialTaxonomy.ancestry,
            incompleteCategories: value.checkpoint.incompleteCategories, incompleteCount: 2, recovery: { index: 1, total: 2 }, status: 'failed', revision: value.revision + 2 } }), job);
        assert.strictEqual(await page.locator('#pm-global-recover-incomplete').isVisible(), true, 'falha também permite recuperar as categorias incompletas');
        assert.strictEqual(await page.locator('#pm-global-incomplete-count').textContent(), '2', 'eventos compactos atualizam incompletas sem checkpoint');
        assert.match(await page.locator('#pm-global-recovery-progress').textContent(), /1 de 2/);
        job = { ...job, revision: job.revision + 3, status: 'completed_partial', incompleteCategories: [], incompleteCount: 0, salesRecoveryPending: true,
            checkpoint: { incompleteCategories: [], salesRecoveryPending: true } };
        await page.evaluate(value => window.__pmProgress({ job: value }), job);
        assert.strictEqual(await page.locator('#pm-global-incomplete-count').textContent(), '0');
        assert.strictEqual(await page.locator('#pm-global-recover-incomplete').textContent(), 'Tentar dados pendentes novamente');
        assert.strictEqual(await page.locator('#pm-global-recover-incomplete').isEnabled(), true, 'métricas pendentes permitem recuperação mesmo sem categoria incompleta');
        const salesRecoveryRevision = job.revision;
        await page.locator('#pm-global-recover-incomplete').click();
        await page.waitForFunction(() => document.querySelector('#pm-global-recover-incomplete').hidden);
        assert.deepStrictEqual(await page.evaluate(() => ({ jobId: window.__lastCommandPayload.jobId, expectedRevision: window.__lastCommandPayload.expectedRevision })),
            { jobId: job.id, expectedRevision: salesRecoveryRevision });
        assert.strictEqual(commands.filter(action => action === 'recover-incomplete').length, 2);
        job = { ...job, status: 'completed', revision: job.revision + 2, legacyAvantMetricsPending: true, salesRecoveryPending: false, checkpoint: {} };
        await page.evaluate(value => window.__pmProgress({ job: value }), job);
        assert.strictEqual(await page.locator('#pm-global-recover-incomplete').textContent(), 'Reconsultar métricas do Avant');
        const legacyRevision = job.revision;
        await page.locator('#pm-global-recover-incomplete').click();
        await page.waitForFunction(() => document.querySelector('#pm-global-recover-incomplete').hidden);
        assert.deepStrictEqual(await page.evaluate(() => [window.__lastCommandPayload.jobId, window.__lastCommandPayload.expectedRevision]), [job.id, legacyRevision]);
        await checkDetailConsultations(page, job);
        await page.evaluate(() => {
            localStorage.removeItem('access_token');
            dispatchEvent(new StorageEvent('storage', { key: 'access_token' }));
        });
        await page.evaluate(previous => window.__pmProgress({ job: previous }), job);
        assert.equal(await page.locator('#pm-global-detail-consultations li').count(), 0, 'um evento antigo não restaura as consultas após o logout');
        assert.strictEqual(await page.locator('#pm-global-body tr').count(), 0, 'logout limpa os anúncios da tela');
        assert.strictEqual(await page.locator('#pm-global-ads-count').textContent(), '0', 'logout limpa dados da sessão anterior');
        assert.strictEqual(await page.locator('#pm-global-status-detail').textContent(), '', 'logout limpa a etapa da sessão anterior');
        assert.strictEqual(await page.locator('#pm-global-status-warning').textContent(), '', 'logout limpa o aviso da sessão anterior');
        assert.strictEqual(await page.locator('#pm-global-incomplete-count').textContent(), '0', 'logout limpa categorias incompletas');
        assert.strictEqual(await page.locator('#pm-global-incomplete').isVisible(), false);
        assert.strictEqual(await page.locator('#pm-global-start').isDisabled(), true);
        await page.evaluate(() => window.__pmProgress({ job: { id: 'job-test', status: 'running', revision: 999 } }));
        assert.strictEqual(await page.locator('#pm-global-body tr').count(), 0, 'evento antigo após logout não repõe dados');
        console.log('Pesquisa de Mercado global UI: paginação, abas, filtros, progresso e controles OK');
    } finally {
        if (browser) await browser.close();
    }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
