'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const http = require('http');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'pesquisa_mercado.html'), 'utf8')
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')
    .replace('</body>', '<script src="/pesquisa-mercado/core.js"></script><script src="/pesquisa-mercado/global-ui.js"></script><script src="/pesquisa-mercado/history-ui.js"></script></body>');
async function main() {
    const scope = { key: 'MLB123', nome: 'Ferramentas', url: 'https://www.mercadolivre.com.br/c/ferramentas' };
    const makeJob = (id, status) => ({ id, status, revision: 1, counters: { categories: 1, pages: 1, sellers: 1, ads: 1, pending: 0 }, scope_category: scope, scopeCategory: scope, created_at: '2026-10-06T12:00:00Z', updated_at: '2026-10-06T13:00:00Z' });
    const jobs = { active: makeJob('active', 'paused'), saved: makeJob('saved', 'completed') };
    const names = { active: '67912443', saved: 'Ir para a loja oficial' };
    const commands = [], errors = [];
    let revision = 1;
    const ad = id => ({ id: 'MLB12345678', titulo: 'Produto', vendedor: names[id], seller_id: '67912443', seller_key: 'id:67912443', preco: 44.65, vendas: 50000, media_vendas_mensal: 1772.76, data_criacao: '2024-05-31', rank: 1, rankable: true, url: 'https://produto.mercadolivre.com.br/MLB-12345678-produto', observed_at: '2026-10-06T13:00:00Z', observations: 1 });
    const server = http.createServer((req, res) => {
        const u = new URL(req.url, 'http://127.0.0.1');
        const json = value => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(value)); };
        if (u.pathname === '/pesquisa_mercado.html') { res.setHeader('Content-Type', 'text/html'); res.end(html); return; }
        if (u.pathname.startsWith('/pesquisa-mercado/')) {
            const file = path.join(root, 'static/pesquisa-mercado', path.basename(u.pathname));
            res.setHeader('Content-Type', u.pathname.endsWith('.css') ? 'text/css' : 'text/javascript'); res.end(fs.readFileSync(file)); return;
        }
        if (u.pathname === '/test/refresh') {
            const id = u.searchParams.get('job'); commands.push(id); names[id] = id === 'active' ? 'Loja confirmada' : 'Loja oficial recuperada'; jobs[id].revision++; revision++;
            json({ job: jobs[id], updated: 1, pending: 0, requiresLogin: false, message: '1 nome atualizado.' }); return;
        }
        const suffix = u.pathname.replace('/api/pesquisa-mercado', '');
        if (suffix === '/jobs') { json({ items: [jobs.saved], total: 1, owner_revision: revision }); return; }
        if (suffix === '/jobs/latest') { json({ job: jobs.active }); return; }
        if (suffix === '/consolidado/resumo') { json({ researches: 2, sellers: 1, unique_ads: 1, rankable: 1, pending: 0, owner_revision: revision }); return; }
        if (suffix === '/consolidado/ranking') { json({ items: [ad('saved')], total: 1, owner_revision: revision }); return; }
        if (suffix.startsWith('/consolidado/')) { json({ items: [], total: 0, owner_revision: revision }); return; }
        const match = suffix.match(/^\/jobs\/(active|saved)(?:\/(.*))?$/);
        if (match) {
            const id = match[1], part = match[2];
            if (part === 'vendedores') json({ items: [{ seller_key: 'id:67912443', id: '67912443', vendedor: names[id], nome: names[id], ads: 1 }] });
            else if (part === 'categorias') json({ items: [{ categoria: 'Ferramentas' }] });
            else if (part === 'ranking') json({ items: [ad(id)], total: 1 });
            else json({ job: jobs[id] }); return;
        }
        json({ items: [], total: 0, owner_revision: revision });
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const browser = await chromium.launch({ headless: true });
    try {
        const page = await browser.newPage();
        page.on('pageerror', error => errors.push(error.message));
        await page.addInitScript(() => {
            localStorage.setItem('access_token', 'fixture-token');
            const originalInterval = window.setInterval.bind(window);
            window.setInterval = (callback, ms) => { if (ms === 15000) window.__namesPoll = callback; return originalInterval(callback, ms); };
            window.__statusCalls = 0;
            window.electronAPI = {
                getPesquisaMercadoJobStatus: async () => { window.__statusCalls++; return (await fetch('/api/pesquisa-mercado/jobs/latest')).json(); },
                getPesquisaMercadoCategories: async () => ({ items: [], parentCategory: null }),
                refreshPesquisaMercadoNames: async payload => { if (window.__holdNames) await new Promise(resolve => { window.__releaseNames = resolve; }); return (await fetch('/test/refresh?job=' + encodeURIComponent(payload.jobId))).json(); },
                onPesquisaMercadoProgress: callback => { window.__namesProgress = callback; return () => {}; }
            };
        });
        await page.goto(`http://127.0.0.1:${server.address().port}/pesquisa_mercado.html`);
        await page.click('#pm-mode-global');
        await page.waitForFunction(() => document.querySelector('#pm-global-body').textContent.includes('Nome pendente'));
        assert.match(await page.locator('#pm-global-body').textContent(), /Nome pendente.*67912443/);
        await page.click('#pm-global-tab-vendedor');
        await page.waitForFunction(() => document.querySelector('#pm-global-seller-list').textContent.includes('Nome pendente'));
        await page.click('#pm-global-seller-list button');
        assert.strictEqual(await page.evaluate(() => window.PesquisaMercadoCore.nomeVendedorParaExibicao({ vendedor: 'Ver Auto Peças', seller_key: 'id:1' })), 'Ver Auto Peças');
        await page.evaluate(() => { window.__holdNames = true; });
        await page.click('#pm-global-refresh-names');
        await page.waitForFunction(() => !!window.__releaseNames);
        const statusBeforeRecovery = await page.evaluate(() => window.__statusCalls);
        await page.evaluate(() => window.__namesPoll());
        assert.strictEqual(await page.evaluate(() => window.__statusCalls), statusBeforeRecovery, 'status nao entra na fila durante recuperacao');
        await page.evaluate(() => { window.__holdNames = false; window.__releaseNames(); });
        await page.waitForFunction(() => document.querySelector('#pm-global-seller-label').textContent === 'Loja confirmada');
        assert.match(await page.locator('#pm-global-body').textContent(), /Loja confirmada/);
        assert.match(await page.locator('#pm-global-body').textContent(), /50\.000/);
        await page.click('#pm-mode-history');
        await page.locator('[data-consult-job="saved"]').click();
        await page.waitForFunction(() => document.querySelector('#pm-history-detail-rows').textContent.includes('Nome pendente'));
        assert.strictEqual(await page.locator('#pm-history-refresh-names').isDisabled(), false, 'recuperacao permitida em pesquisa encerrada');
        await page.click('#pm-history-refresh-names');
        await page.waitForFunction(() => document.querySelector('#pm-history-detail-rows').textContent.includes('Loja oficial recuperada'));
        assert.match(await page.locator('#pm-history-detail-rows').textContent(), /50\.000/);
        await page.click('#pm-mode-global');
        assert.match(await page.locator('#pm-global-seller-label').textContent(), /Loja confirmada/, 'recuperar historico nao troca pesquisa ativa');
        await page.evaluate(() => window.__namesProgress({ job: { id: 'active', status: 'running', revision: 20, counters: { sellers: 1, ads: 1 } } }));
        assert.strictEqual(await page.locator('#pm-global-refresh-names').isDisabled(), true, 'coleta ativa protege navegador');
        assert.deepStrictEqual(commands, ['active', 'saved']);
        assert.deepStrictEqual(errors, []);
        console.log('Pesquisa nomes UI: IDs pendentes, recuperação atual/histórica, métricas e pesquisa ativa preservadas OK');
    } finally { await browser.close(); await new Promise(resolve => server.close(resolve)); }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
