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
const tools = { key: 'MLB-FERRAMENTAS', nome: 'Ferramentas', url: 'https://www.mercadolivre.com.br/c/ferramentas' };
const electric = { key: 'MLB-ELETRICAS', nome: 'Ferramentas Elétricas', url: 'https://lista.mercadolivre.com.br/ferramentas/eletricas/' };
const manual = { key: 'MLB-MANUAIS', nome: 'Ferramentas Manuais', url: 'https://lista.mercadolivre.com.br/ferramentas/manuais/' };
const drills = { key: 'MLB-BROCAS', nome: 'Brocas', url: 'https://lista.mercadolivre.com.br/ferramentas/brocas/' };
const electronics = { key: 'MLB-ELETRONICOS', nome: 'Eletrônicos', url: 'https://www.mercadolivre.com.br/c/eletronicos' };

async function main() {
    let job = null;
    let mode = 'normal';
    let releaseTaxonomy = null;
    let legacyStart = false;
    let items = [{ id: 'MLB1000000000', titulo: 'Produto de teste', vendedor: 'Loja Teste', seller_key: 'id:1',
        categoria: 'Ferramentas', vendas: 30, media_vendas_mensal: 10, rank: 1 }];
    const commands = [];
    const errors = [];
    let scenarios = 0;
    const scenario = async operation => { await operation(); scenarios += 1; };
    const server = http.createServer(async (req, res) => {
        const url = new URL(req.url, 'http://127.0.0.1');
        const json = value => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(value)); };
        if (url.pathname === '/pesquisa_mercado.html') { res.setHeader('Content-Type', 'text/html'); res.end(html); return; }
        if (url.pathname === '/test/shell') {
            res.setHeader('Content-Type', 'text/html');
            res.end(`<!doctype html><iframe id="pm-frame" src="/pesquisa_mercado.html" style="width:1440px;height:1000px"></iframe><script>
                window.addEventListener('message', event => {
                    const data = event.data || {};
                    if (data.channel !== 'jk-pesquisa-mercado-command') return;
                    if (data.action === 'categories') { window.categoryRequest = { source: event.source, requestId: data.requestId }; return; }
                    event.source.postMessage({ channel: 'jk-pesquisa-mercado-result', requestId: data.requestId,
                        result: data.action === 'status' ? { job: null } : { success: true } }, location.origin);
                });
            </script>`);
            return;
        }
        if (url.pathname.startsWith('/pesquisa-mercado/')) {
            const file = path.basename(url.pathname);
            res.setHeader('Content-Type', file.endsWith('.css') ? 'text/css' : 'text/javascript');
            res.end(fs.readFileSync(path.join(root, 'static/pesquisa-mercado', file))); return;
        }
        if (url.pathname === '/test/command') {
            let raw = '';
            for await (const chunk of req) raw += chunk;
            const payload = JSON.parse(raw || '{}');
            const action = url.searchParams.get('action');
            commands.push({ action, payload });
            if (action === 'categories') {
                if (mode === 'slow') await new Promise(resolve => { releaseTaxonomy = resolve; });
                if (mode === 'error') { json({ success: false, message: 'Falha temporária ao carregar categorias.' }); return; }
                if (mode === 'redirect-error') { json({ ipcError: "Error invoking remote method 'pesquisa-mercado:categories': Error: O ML abriu outra categoria." }); return; }
                if (mode === 'foreign-error') { json({ ipcError: "Error invoking remote method 'other-module:categories': Error: Mensagem de outra origem." }); return; }
                if (mode === 'login') { json({ authRequired: true, reason: 'Faça login no Mercado Livre e recarregue a lista.' }); return; }
                const parent = payload.parentCategory || null;
                const children = !parent ? [tools, electronics] : parent.key === tools.key ? [electric, manual] : parent.key === electric.key ? [drills] : [];
                json({ parentCategory: parent, items: children }); return;
            }
            if (action === 'start') job = legacyStart ? { id: 'legacy-job', status: 'running', revision: 1, counters: { ads: 1 }, checkpoint: {} }
                : { id: 'scoped-job', status: 'running', revision: 1, scopeCategory: payload.category,
                    checkpoint: { scope: { category: payload.category } }, counters: { categories: 0, pages: 1, sellers: 1, ads: 1, pending: 0 } };
            if (action === 'browser') { json({ success: true }); return; }
            json({ job: action === 'status' && payload.authToken === 'fixture-new-session' ? null : job }); return;
        }
        if (url.pathname.endsWith('/vendedores')) { json({ items: [{ seller_key: 'id:1', vendedor: 'Loja Teste', ads: items.length }] }); return; }
        if (url.pathname.endsWith('/categorias')) { json({ items: [{ categoria: 'Ferramentas', status: 'completed' }] }); return; }
        if (url.pathname.endsWith('/ranking')) { json({ items, total: items.length, revision: job && job.revision || 0 }); return; }
        res.statusCode = 404; res.end();
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    let browser;
    try {
        browser = await chromium.launch({ headless: true });
        const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
        page.on('pageerror', error => errors.push(error.message));
        await page.addInitScript(() => {
            localStorage.setItem('access_token', 'fixture-original-session');
            const command = action => async payload => {
                const result = await (await fetch(`/test/command?action=${action}`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
                })).json();
                if (result.ipcError) throw new Error(result.ipcError);
                return result;
            };
            window.electronAPI = {
                startPesquisaMercadoJob: command('start'), getPesquisaMercadoJobStatus: command('status'),
                getPesquisaMercadoCategories: command('categories'), showPesquisaMercadoBrowser: command('browser'),
                onPesquisaMercadoProgress(callback) { window.__pmProgress = callback; return () => {}; }
            };
            window.addEventListener('jk-pesquisa-mercado-mode-change', event => { window.__pmMode = event.detail.mode; });
        });
        await page.goto(`http://127.0.0.1:${server.address().port}/pesquisa_mercado.html`);
        await scenario(async () => {
            assert.strictEqual(await page.locator('#pm-panel-manual').isVisible(), true);
            assert.strictEqual(await page.locator('#pm-panel-global').isVisible(), false);
            assert.strictEqual(commands.filter(command => command.action === 'categories').length, 0, 'aba manual não navega a taxonomia');
            await page.locator('#pm-vendedor').fill('VENDEDOR_TESTE');
            await page.locator('#pm-mode-manual').press('ArrowRight');
            await page.waitForFunction(() => document.querySelectorAll('.pm-taxonomy-item').length === 2);
            assert.strictEqual(await page.evaluate(() => window.__pmMode), 'global');
            assert.strictEqual(await page.locator('#pm-panel-manual').isVisible(), false);
            assert.strictEqual(await page.locator('#pm-global-start').isDisabled(), true);
            assert.match(await page.locator('#pm-global-taxonomy-message').textContent(), /Escolha uma categoria ou subcategoria/);
            await page.locator('#pm-global-start').dispatchEvent('click');
            assert.strictEqual(commands.filter(command => command.action === 'start').length, 0, 'seleção obrigatória bloqueia início');
        });
        await scenario(async () => {
            await page.getByRole('button', { name: 'Selecionar Ferramentas e ver subcategorias', exact: true }).click();
            await page.waitForFunction(() => !document.getElementById('pm-global-start').disabled);
            mode = 'redirect-error';
            await page.getByRole('button', { name: 'Selecionar Ferramentas Elétricas e ver subcategorias', exact: true }).click();
            await page.waitForFunction(() => document.querySelector('#pm-global-taxonomy-message').textContent === 'O ML abriu outra categoria.');
            assert.doesNotMatch(await page.locator('#pm-global-taxonomy-message').textContent(), /remote method|pesquisa-mercado:categories/);
            assert.strictEqual(await page.locator('#pm-global-start').isDisabled(), true, 'falha na subcategoria impede início acidental no pai');
            assert.strictEqual(await page.locator('#pm-global-taxonomy-breadcrumb button').count(), 2, 'falha preserva caminho anterior para retry');
            assert.match(await page.locator('#pm-global-taxonomy-selection').textContent(), /Selecionada: Ferramentas\./);
            assert.strictEqual(await page.locator('.pm-taxonomy-item').count(), 2);
            const starts = commands.filter(command => command.action === 'start').length;
            await page.locator('#pm-global-start').dispatchEvent('click');
            assert.strictEqual(commands.filter(command => command.action === 'start').length, starts, 'click programático também é bloqueado após erro');
            assert.strictEqual(await page.locator('#pm-global-taxonomy-message').textContent(), 'O ML abriu outra categoria.');
            mode = 'foreign-error';
            await page.getByRole('button', { name: 'Selecionar Ferramentas Elétricas e ver subcategorias', exact: true }).click();
            await page.waitForFunction(() => document.querySelector('#pm-global-taxonomy-message').textContent.includes('other-module:categories'));
            assert.strictEqual(await page.locator('#pm-global-taxonomy-message').textContent(), "Error invoking remote method 'other-module:categories': Error: Mensagem de outra origem.",
                'limpeza de mensagens fica restrita aos comandos deste módulo');
            mode = 'normal';
            await page.getByRole('button', { name: 'Selecionar Ferramentas Elétricas e ver subcategorias', exact: true }).click();
            await page.waitForFunction(() => !document.getElementById('pm-global-start').disabled);
            assert.match(await page.locator('#pm-global-taxonomy-selection').textContent(), /Ferramentas › Ferramentas Elétricas/);
            assert.strictEqual(await page.locator('#pm-global-taxonomy-message').evaluate(element => element.classList.contains('error')), false, 'retry recompõe o estado sem erro');
            assert.strictEqual(await page.locator('.pm-taxonomy-item').count(), 1);
            await page.locator('#pm-global-taxonomy-breadcrumb').getByRole('button', { name: 'Todas as categorias', exact: true }).click();
            await page.waitForFunction(() => document.querySelectorAll('.pm-taxonomy-item').length === 2);
        });
        await scenario(async () => {
            await page.getByRole('button', { name: 'Selecionar Ferramentas e ver subcategorias', exact: true }).click();
            await page.waitForFunction(() => document.querySelector('#pm-global-taxonomy-selection').textContent.includes('Selecionada: Ferramentas.'));
            assert.strictEqual(await page.locator('#pm-global-start').isDisabled(), false, 'categoria com filhos também é pesquisável');
            assert.strictEqual(await page.locator('.pm-taxonomy-item').count(), 2);
            await page.getByRole('button', { name: 'Selecionar Ferramentas Elétricas e ver subcategorias', exact: true }).click();
            await page.waitForFunction(() => document.querySelectorAll('.pm-taxonomy-item').length === 1);
            await page.getByRole('button', { name: 'Selecionar Brocas e ver subcategorias', exact: true }).click();
            await page.waitForFunction(() => document.querySelector('#pm-global-taxonomy-message').textContent.includes('não possui outras'));
            assert.strictEqual(await page.locator('.pm-taxonomy-item').count(), 0);
            assert.strictEqual(await page.locator('#pm-global-start').isDisabled(), false, 'folha sem filhos é pesquisável');
            assert.deepStrictEqual(commands.filter(command => command.action === 'categories').at(-1).payload.parentCategory, drills);
            assert.strictEqual(commands.filter(command => command.action === 'start').length, 0, 'escolher categoria não inicia pesquisa');
            await page.locator('#pm-global-taxonomy-breadcrumb').getByRole('button', { name: 'Ferramentas', exact: true }).click();
            await page.waitForFunction(() => document.querySelectorAll('.pm-taxonomy-item').length === 2);
            assert.strictEqual(await page.locator('#pm-global-taxonomy-breadcrumb button').count(), 2, 'breadcrumb volta ao nível escolhido');
            await page.locator('#pm-global-taxonomy-breadcrumb').getByRole('button', { name: 'Todas as categorias', exact: true }).click();
            await page.waitForFunction(() => document.querySelector('#pm-global-taxonomy-selection').textContent === 'Nenhuma categoria selecionada.');
            assert.strictEqual(await page.locator('#pm-global-start').isDisabled(), true, 'voltar às raízes remove a seleção');
        });
        await scenario(async () => {
            await page.getByRole('button', { name: 'Selecionar Ferramentas e ver subcategorias', exact: true }).click();
            await page.getByRole('button', { name: 'Selecionar Ferramentas Elétricas e ver subcategorias', exact: true }).click();
            await page.getByRole('button', { name: 'Selecionar Brocas e ver subcategorias', exact: true }).click();
            await page.waitForFunction(() => !document.getElementById('pm-global-start').disabled);
            await page.locator('#pm-global-start').click();
            await page.waitForFunction(() => document.querySelectorAll('#pm-global-body tr').length === 1);
            assert.deepStrictEqual(commands.find(command => command.action === 'start').payload.category, drills);
            assert.match(await page.locator('#pm-global-scope').textContent(), /Brocas/);
            assert.strictEqual(await page.locator('#pm-global-taxonomy-refresh').isDisabled(), true, 'execução ativa bloqueia navegação de categorias');
            assert.strictEqual(await page.locator('#pm-global-start').isDisabled(), true);
            const before = commands.filter(command => command.action === 'categories').length;
            await page.locator('#pm-global-tab-vendedor').click();
            await page.locator('#pm-mode-manual').click();
            assert.strictEqual(await page.locator('#pm-vendedor').inputValue(), 'VENDEDOR_TESTE');
            await page.locator('#pm-mode-global').click();
            assert.strictEqual(await page.locator('#pm-global-tab-vendedor').getAttribute('aria-selected'), 'true', 'alternar modo preserva a aba do ranking');
            assert.strictEqual(commands.filter(command => command.action === 'categories').length, before);
            assert.strictEqual(commands.filter(command => command.action === 'cancel').length, 0, 'mudar aba preserva pesquisa em segundo plano');
        });
        await scenario(async () => {
            job = { ...job, status: 'completed', revision: 2 };
            await page.evaluate(job => window.__pmProgress({ job }), job);
            await page.waitForFunction(() => !document.getElementById('pm-global-taxonomy-refresh').disabled);
            mode = 'slow';
            await page.locator('#pm-global-taxonomy-breadcrumb').getByRole('button', { name: 'Ferramentas', exact: true }).click();
            await page.waitForFunction(() => document.getElementById('pm-global-taxonomy-list').getAttribute('aria-busy') === 'true');
            assert.strictEqual(await page.locator('#pm-global-start').isDisabled(), true);
            assert.strictEqual(await page.locator('#pm-global-tab-geral').isDisabled(), false, 'carregar árvore não bloqueia ranking');
            items = items.concat({ ...items[0], id: 'MLB2000000000', titulo: 'Segundo produto', rank: 2 });
            job = { ...job, revision: 3, counters: { ...job.counters, ads: 2 } };
            await page.evaluate(job => window.__pmProgress({ job }), job);
            await page.waitForFunction(() => document.querySelectorAll('#pm-global-body tr').length === 2);
            assert.strictEqual(await page.locator('#pm-global-tab-vendedor').getAttribute('aria-selected'), 'true');
            mode = 'normal';
            releaseTaxonomy();
            await page.waitForFunction(() => document.querySelectorAll('.pm-taxonomy-item').length === 2);
            assert.match(await page.locator('#pm-global-taxonomy-selection').textContent(), /Selecionada: Ferramentas\./);
            assert.match(await page.locator('#pm-global-scope').textContent(), /Brocas/, 'seleção futura não muda escopo do resultado salvo');
        });
        await scenario(async () => {
            mode = 'error';
            await page.locator('#pm-global-taxonomy-refresh').click();
            await page.waitForFunction(() => document.querySelector('#pm-global-taxonomy-message').textContent.includes('Falha temporária'));
            assert.strictEqual(await page.locator('#pm-global-taxonomy-refresh').isDisabled(), false, 'erro deixa disponível a nova tentativa');
            assert.strictEqual(await page.locator('#pm-global-body tr').count(), 2, 'falha no seletor preserva resultados');
            assert.strictEqual(await page.locator('#pm-global-taxonomy-message').evaluate(element => element.classList.contains('error')), true);
        });
        await scenario(async () => {
            mode = 'login';
            await page.locator('#pm-global-taxonomy-refresh').click();
            await page.waitForFunction(() => !document.getElementById('pm-global-taxonomy-browser').hidden);
            assert.strictEqual(await page.locator('#pm-global-start').isDisabled(), true);
            const before = commands.filter(command => command.action === 'categories').length;
            await page.locator('#pm-global-taxonomy-browser').click();
            await page.waitForFunction(() => document.querySelector('#pm-global-taxonomy-message').textContent.includes('Conclua o login'));
            assert.deepStrictEqual(commands.find(command => command.action === 'browser').payload, { authToken: 'fixture-original-session', purpose: 'categories' });
            assert.strictEqual(commands.filter(command => command.action === 'categories').length, before, 'abrir login não inicia navegação adicional');
            mode = 'normal';
            await page.locator('#pm-global-taxonomy-refresh').click();
            await page.waitForFunction(() => document.getElementById('pm-global-taxonomy-browser').hidden && !document.getElementById('pm-global-start').disabled);
            assert.strictEqual(await page.locator('#pm-global-start').isDisabled(), false);
        });
        await scenario(async () => {
            await page.evaluate(() => {
                localStorage.setItem('access_token', 'fixture-new-session');
                dispatchEvent(new StorageEvent('storage', { key: 'access_token' }));
            });
            await page.waitForFunction(() => document.querySelectorAll('.pm-taxonomy-item').length === 0);
            assert.strictEqual(await page.locator('#pm-global-taxonomy-breadcrumb button').count(), 1);
            assert.strictEqual(await page.locator('#pm-global-taxonomy-selection').textContent(), 'Nenhuma categoria selecionada.');
            assert.strictEqual(await page.locator('#pm-global-start').isDisabled(), true);
            assert.strictEqual(await page.locator('#pm-global-body tr').count(), 0);
            assert.strictEqual(await page.locator('#pm-global-scope').textContent(), 'Nenhuma pesquisa iniciada.');
            await page.locator('#pm-global-taxonomy-refresh').click();
            await page.waitForFunction(() => document.querySelectorAll('.pm-taxonomy-item').length === 2);
            assert.strictEqual(commands.filter(command => command.action === 'categories').at(-1).payload.authToken, 'fixture-new-session');
        });
        await scenario(async () => {
            legacyStart = true;
            await page.getByRole('button', { name: 'Selecionar Ferramentas e ver subcategorias', exact: true }).click();
            await page.waitForFunction(() => !document.getElementById('pm-global-start').disabled);
            await page.locator('#pm-global-start').click();
            await page.waitForFunction(() => document.getElementById('pm-global-cancel').disabled === false);
            assert.strictEqual(commands.filter(command => command.action === 'start').at(-1).payload.category.nome, 'Ferramentas');
            assert.strictEqual(await page.locator('#pm-global-scope').textContent(), 'Escopo da pesquisa: execução anterior.',
                'job sem escopo não pode receber categoria escolhida como escopo inventado');
        });
        await scenario(async () => {
            const bridgePage = await browser.newPage({ viewport: { width: 1500, height: 1100 } });
            await bridgePage.addInitScript(() => localStorage.setItem('access_token', 'fixture-shell-session'));
            await bridgePage.clock.install();
            await bridgePage.goto(`http://127.0.0.1:${server.address().port}/test/shell`);
            const frame = bridgePage.frameLocator('#pm-frame');
            await frame.locator('#pm-mode-global').click();
            await bridgePage.waitForFunction(() => !!window.categoryRequest);
            await bridgePage.clock.fastForward(50000);
            assert.match(await frame.locator('#pm-global-taxonomy-message').textContent(), /Carregando categorias/, 'comando de categorias pode demorar mais de 45 segundos');
            await bridgePage.evaluate(items => {
                const request = window.categoryRequest;
                window.categoryRequest = null;
                request.source.postMessage({ channel: 'jk-pesquisa-mercado-result', requestId: request.requestId,
                    result: { items, parentCategory: null } }, location.origin);
            }, [tools, electronics]);
            await frame.locator('.pm-taxonomy-item').last().waitFor();
            assert.strictEqual(await frame.locator('.pm-taxonomy-item').count(), 2, 'resposta tardia dentro do prazo atualiza a árvore');
            await frame.locator('#pm-global-taxonomy-refresh').click();
            await bridgePage.waitForFunction(() => !!window.categoryRequest);
            await bridgePage.clock.fastForward(180001);
            await frame.locator('#pm-global-taxonomy-message').filter({ hasText: 'não respondeu' }).waitFor();
            assert.strictEqual(await frame.locator('#pm-global-taxonomy-refresh').isDisabled(), false, 'timeout limpa loading e permite retry');
            await bridgePage.evaluate(items => {
                const request = window.categoryRequest;
                request.source.postMessage({ channel: 'jk-pesquisa-mercado-result', requestId: request.requestId,
                    result: { items, parentCategory: null } }, location.origin);
            }, [electronics]);
            await bridgePage.waitForTimeout(50);
            assert.strictEqual(await frame.locator('.pm-taxonomy-item').count(), 2, 'resposta após timeout não repõe request expirado');
            await bridgePage.close();
        });
        assert.deepStrictEqual(errors, []);
        console.log(`Pesquisa de Mercado categorias UI: ${scenarios} cenários de abas, hierarquia, escopo, erro, login e sessão OK`);
    } finally {
        if (browser) await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
