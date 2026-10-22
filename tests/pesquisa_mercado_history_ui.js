'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const http = require('http');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'static/pesquisa_mercado.html'), 'utf8')
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '')
    .replace('</body>', '<script src="/pesquisa-mercado/history-ui.js"></script></body>');

async function main() {
    const scopes = Array.from({ length: 102 }, (_, index) => ({ key: `MLB${1000 + index}`, nome: `Escopo ${index}`, url: `https://lista.mercadolivre.com.br/_CatId_MLB${1000 + index}` }));
    const oldA = { id: 'old-A', status: 'completed_partial', revision: 4, scope_category: { ...scopes[0], nome: 'Freios' },
        created_at: '2026-10-05T10:00:00Z', updated_at: '2026-10-05T11:00:00Z', counters: { sellers: 2, ads: 105 } };
    const oldB = { ...oldA, id: 'old-B', status: 'cancelled', scope_category: { ...scopes[1], nome: 'Suspensão' }, created_at: '2026-10-06T10:00:00Z' };
    const jobs = [oldA, oldB, ...Array.from({ length: 24 }, (_, index) => ({ ...oldA, id: `other-${index}`, status: index === 0 ? 'running' : oldA.status }))];
    const ad = (index, title) => ({ id: `MLB${1000000000 + index}`, ad_key: `MLB${1000000000 + index}`, seller_key: 'id:1',
        titulo: title || `Produto ${index}`, vendedor: 'Loja A', preco: 19.9, vendas: 100 + index, media_vendas_mensal: 10,
        rank: index + 1, status: 'analisado', url: `https://produto.mercadolivre.com.br/MLB-${1000000000 + index}-produto` });
    const historyAds = Array.from({ length: 105 }, (_, index) => ad(index));
    historyAds[104] = { ...ad(104, 'Sem Avant'), rank: null, vendas: null, media_vendas_mensal: null, url: 'javascript:untrusted()' };
    const consolidatedAds = [{ ...ad(0, '<script>untrusted()</script>'), observations: 9, research_count: 2, source_job_id: oldB.id,
        observed_at: '2026-10-06T13:00:00Z', date_approximate: false },
    { ...ad(1, 'Pendente atual'), rank: null, media_vendas_mensal: null, observations: 1, research_count: 1, source_job_id: oldA.id,
        observed_at: '2026-10-05T10:00:00Z', date_approximate: true, url: 'https://evil.example/item' }];
    const requests = [];
    let revision = 7, failNextPage = false, unauthorized = false;
    const server = http.createServer((req, res) => {
        const url = new URL(req.url, 'http://127.0.0.1');
        const json = (value, status = 200, delay = 0) => setTimeout(() => { res.statusCode = status; res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(value)); }, delay);
        if (url.pathname === '/pesquisa_mercado.html') { res.setHeader('Content-Type', 'text/html'); res.end(html); return; }
        if (url.pathname.startsWith('/pesquisa-mercado/')) {
            const name = path.basename(url.pathname);
            res.setHeader('Content-Type', name.endsWith('.css') ? 'text/css' : 'text/javascript');
            res.end(fs.readFileSync(path.join(root, 'static/pesquisa-mercado', name))); return;
        }
        if (!url.pathname.startsWith('/api/pesquisa-mercado')) { res.statusCode = 404; res.end(); return; }
        requests.push({ path: url.pathname, params: new URLSearchParams(url.searchParams), method: req.method, token: req.headers.authorization });
        if (unauthorized) { json({ detail: 'Unauthorized' }, 403); return; }
        const suffix = url.pathname.slice('/api/pesquisa-mercado'.length);
        const offset = Number(url.searchParams.get('offset') || 0), limit = Number(url.searchParams.get('limit') || 100);
        const q = url.searchParams.get('q') || '';
        const requestedRevision = url.searchParams.get('revision');
        if (suffix === '/jobs' && failNextPage && offset > 0) { failNextPage = false; revision += 1; json({ detail: { code: 'research_revision_changed', owner_revision: revision } }, 409); return; }
        if ((suffix === '/jobs' || suffix.startsWith('/consolidado/')) && requestedRevision != null && Number(requestedRevision) !== revision) { json({ detail: { code: 'research_revision_changed', owner_revision: revision } }, 409); return; }
        if (suffix === '/jobs') {
            const items = q === 'fast' ? [oldB] : q === 'slow' ? [oldA] : q === 'empty' ? [] : jobs.filter(job => !url.searchParams.get('status') || job.status === url.searchParams.get('status'));
            json({ items: items.slice(offset, offset + limit), total: items.length, owner_revision: revision }, 200, q === 'slow' ? 400 : 0); return;
        }
        if (suffix === '/consolidado/categorias') { json({ items: scopes.slice(offset, offset + limit), total: scopes.length, owner_revision: revision }); return; }
        const detail = suffix.match(/^\/jobs\/(old-[AB])\/resumo$/);
        if (detail) { json({ job: detail[1] === oldA.id ? oldA : oldB }, 200, detail[1] === oldA.id ? 300 : 0); return; }
        const diagnostic = suffix.match(/^\/jobs\/(old-[AB])\/diagnostico$/);
        if (diagnostic) { json(diagnostic[1] === oldA.id ? {
            scopePath: [{ ...scopes[0], nome: 'Ciclismo' }, { ...scopes[0], nome: 'Freios' }],
            incompleteCategories: oldA.salesRecoveryPending ? [] : [{ key: 'MLB1', nome: 'Pastilhas', code: 'category_access_denied', motivo: 'private raw details' }, { key: 'MLB2', nome: 'Legado', code: 'legacy_unknown' }],
            salesRecoveryPending: oldA.salesRecoveryPending === true,
            recovery: oldA.status === 'running' ? { generation: 1, index: 0, total: 2 } : null
        } : { scopePath: [], incompleteCategories: [], recovery: null }); return; }
        if (/^\/jobs\/[^/]+\/vendedores$/.test(suffix)) { json({ items: [{ seller_key: 'id:1', vendedor: 'Loja A' }, { seller_key: 'id:2', vendedor: 'Loja B' }] }); return; }
        if (/^\/jobs\/[^/]+\/categorias$/.test(suffix)) { json({ items: [{ categoria: 'Freios', status: 'incomplete' }] }); return; }
        if (/^\/jobs\/[^/]+\/ranking$/.test(suffix)) {
            const items = historyAds.filter(item => (!q || item.titulo.includes(q)) && (!url.searchParams.get('seller_key') || item.seller_key === url.searchParams.get('seller_key')));
            json({ items: items.slice(offset, offset + limit), total: items.length, revision: 4 }); return;
        }
        const evidence = url.searchParams.get('evidence');
        const items = consolidatedAds.filter(item => (!evidence || (evidence === 'rankable' ? item.rank != null : item.rank == null)) && (!q || item.titulo.includes(q)));
        if (suffix === '/consolidado/resumo') { json({ researches: 2, observations: 3, unique_ads: items.length, sellers: 1,
            rankable: items.filter(item => item.rank != null).length, pending: items.filter(item => item.rank == null).length,
            first_research_at: oldA.created_at, last_research_at: oldB.created_at, owner_revision: revision }); return; }
        if (suffix === '/consolidado/ranking') { json({ items: items.slice(offset, offset + limit), total: items.length, owner_revision: revision }); return; }
        if (suffix === '/consolidado/origens') { json({ items: [oldB, oldA].map(job => ({ source_job_id: job.id, scope_category: job.scope_category,
            status: job.status, observed_at: job.updated_at, date_approximate: job.id === oldA.id, vendas: 100, media_vendas_mensal: 10 })), total: 2, owner_revision: revision }); return; }
        json({ detail: 'Not found' }, 404);
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    let browser;
    try {
        browser = await chromium.launch({ headless: true });
        const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
        await context.addInitScript(() => {
            localStorage.setItem('access_token', 'fixture-A');
            window.__commands = [];
            window.electronAPI = new Proxy({}, { get(_target, key) { return () => { window.__commands.push(key); throw new Error('History must not issue operational commands'); }; } });
            // Ignore abort in this fixture so generation/session guards face late successful responses.
            const fetch = window.fetch.bind(window);
            window.fetch = (url, options = {}) => fetch(url, { ...options, signal: undefined });
            window.__jkPesquisaMercadoChangeMode = mode => {
                for (const value of ['manual', 'global', 'history', 'consolidated']) {
                    const tab = document.getElementById(`pm-mode-${value}`), panel = document.getElementById(`pm-panel-${value}`);
                    tab.setAttribute('aria-selected', String(value === mode)); panel.hidden = value !== mode;
                }
                dispatchEvent(new CustomEvent('jk-pesquisa-mercado-mode-change', { detail: { mode } }));
            };
        });
        const page = await context.newPage();
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(`${origin}/pesquisa_mercado.html`);
        assert.strictEqual(requests.length, 0, 'history does not collect or eagerly load all saved research on startup');
        const mode = value => page.evaluate(value => window.__jkPesquisaMercadoChangeMode(value), value);
        await mode('history');
        await page.waitForFunction(() => document.querySelectorAll('#pm-history-list .pm-saved-card').length === 25);
        assert.match(await page.locator('#pm-history-list').textContent(), /Concluída parcialmente|Cancelada/);
        assert.strictEqual(await page.locator('#pm-history-scope option').count(), 101);
        await page.locator('#pm-history-scope-more').click();
        await page.waitForFunction(() => document.querySelectorAll('#pm-history-scope option').length === 103);
        assert.strictEqual(await page.locator('#pm-history-scope-more').isVisible(), false, 'metadata scopes are paginated');
        await page.locator('#pm-history-scope').selectOption('MLB1101');
        await page.waitForTimeout(290);
        await page.locator('#pm-history-refresh').click();
        await page.waitForFunction(() => !window.__jkPesquisaMercadoHistory.state.history.busy);
        assert.strictEqual(await page.locator('#pm-history-scope').inputValue(), 'MLB1101', 'refresh preserves a scope selected from a later metadata page');
        await page.locator('#pm-history-scope').selectOption('');
        await page.waitForTimeout(290);
        failNextPage = true;
        await page.locator('#pm-history-next').click();
        await page.waitForFunction(() => window.__jkPesquisaMercadoHistory.state.history.revision === 8 && !window.__jkPesquisaMercadoHistory.state.history.busy);
        assert.strictEqual(await page.evaluate(() => window.__jkPesquisaMercadoHistory.state.history.offset), 0, '409 restarts snapshot at first page');
        await page.locator('#pm-history-next').click();
        await page.waitForFunction(() => document.querySelectorAll('#pm-history-list .pm-saved-card').length === 1);
        await page.locator('#pm-history-previous').click();
        await page.waitForFunction(() => document.querySelectorAll('#pm-history-list .pm-saved-card').length === 25);
        await page.locator('#pm-history-search').fill('slow');
        await page.waitForTimeout(290);
        await page.locator('#pm-history-search').fill('fast');
        await page.waitForFunction(() => document.querySelectorAll('#pm-history-list .pm-saved-card').length === 1 && document.getElementById('pm-history-list').textContent.includes('Suspensão'));
        await page.waitForTimeout(450);
        assert.match(await page.locator('#pm-history-list').textContent(), /Suspensão/, 'late previous filter response cannot replace current results');
        await page.locator('#pm-history-search').fill('slow');
        await page.waitForTimeout(290);
        await mode('consolidated');
        await page.waitForTimeout(450);
        await mode('history');
        await page.waitForFunction(() => document.querySelectorAll('#pm-history-list .pm-saved-card').length === 1 && document.getElementById('pm-history-list').textContent.includes('Freios'));
        assert.match(await page.locator('#pm-history-list').textContent(), /Freios/, 'returning to a panel reloads a query whose previous response was invalidated');
        await page.evaluate(() => { window.__jkPesquisaMercadoHistory.openDetail('old-A'); });
        await page.waitForTimeout(25);
        await page.evaluate(() => { window.__jkPesquisaMercadoHistory.openDetail('old-B'); });
        await page.waitForFunction(() => document.getElementById('pm-history-detail-heading').textContent.includes('Suspensão') && document.querySelectorAll('#pm-history-detail-rows tr').length === 100);
        await page.waitForTimeout(350);
        assert.match(await page.locator('#pm-history-detail-heading').textContent(), /Suspensão/, 'late selection A cannot overwrite selected research B');
        await page.locator('#pm-history-detail-next').click();
        await page.waitForFunction(() => document.querySelectorAll('#pm-history-detail-rows tr').length === 5);
        const pending = page.locator('#pm-history-detail-rows tr').filter({ hasText: 'Sem Avant' });
        assert.strictEqual(await pending.locator('td').first().textContent(), '');
        assert.strictEqual(await pending.locator('a').count(), 0);
        await page.locator('#pm-history-tab-geral').focus();
        await page.keyboard.press('ArrowRight');
        await page.waitForFunction(() => document.getElementById('pm-history-tab-vendedor').getAttribute('aria-selected') === 'true');
        assert.strictEqual(await page.locator('#pm-history-detail-seller').isVisible(), true);
        await page.locator('#pm-history-detail-seller').selectOption('id:2');
        await page.waitForFunction(() => document.getElementById('pm-history-detail-message').textContent.includes('Nenhum anúncio'));
        await page.evaluate(() => dispatchEvent(new CustomEvent('jk-pesquisa-mercado-progress', { detail: { job: { id: 'active-new', revision: 99 } } })));
        assert.match(await page.locator('#pm-history-detail-heading').textContent(), /Suspensão/, 'operational progress does not replace historical view');
        await page.locator('#pm-history-tab-geral').click();
        await page.waitForFunction(() => document.querySelectorAll('#pm-history-detail-rows tr').length === 100);
        await page.locator('#pm-history-detail-category').selectOption('Freios');
        await page.waitForTimeout(290);
        await page.locator('#pm-history-detail-next').click();
        await page.waitForFunction(() => document.querySelectorAll('#pm-history-detail-rows tr').length === 5);
        await mode('consolidated');
        await page.waitForFunction(() => document.querySelectorAll('#pm-consolidated-rows tr').length === 2);
        jobs[2].status = 'paused';
        oldB.status = 'paused'; oldB.counters = { ...oldB.counters, ads: 106 };
        revision += 1;
        await mode('history');
        await page.waitForFunction(() => window.__jkPesquisaMercadoHistory.state.history.revision === 9 && !window.__jkPesquisaMercadoHistory.state.history.busy);
        await page.waitForFunction(() => document.getElementById('pm-history-detail-heading').textContent.includes('Pausada') && !window.__jkPesquisaMercadoHistory.state.history.detailBusy);
        assert.match(await page.locator('#pm-history-detail-heading').textContent(), /106 anúncios/, 'selected research summary refreshes on return');
        assert.strictEqual(await page.evaluate(() => window.__jkPesquisaMercadoHistory.state.history.detailOffset), 100, 'return preserves historical ranking page');
        assert.strictEqual(await page.locator('#pm-history-detail-category').inputValue(), 'Freios', 'return preserves historical scope filter');
        assert.strictEqual(await page.locator('#pm-history-tab-geral').getAttribute('aria-selected'), 'true');
        assert.strictEqual(await page.locator('#pm-history-detail-rows tr').count(), 5, 'selected ranking refreshes while preserving its page');
        // Clear the earlier text filter to expose the active fixture alongside the selected old research.
        await page.locator('#pm-history-search').fill('');
        await page.waitForFunction(() => document.querySelector('[data-consult-job="other-0"]') && document.querySelector('[data-consult-job="other-0"]').closest('article').textContent.includes('Pausada'));
        assert.strictEqual(await page.evaluate(() => window.__jkPesquisaMercadoHistory.state.history.detailId), 'old-B', 'return refresh preserves selected old research');
        assert.match(await page.locator('#pm-history-detail-heading').textContent(), /Suspensão/);
        const beforeRepeatedMode = requests.length;
        await mode('history');
        await page.waitForTimeout(30);
        assert.strictEqual(requests.length, beforeRepeatedMode, 'selecting the current mode does not duplicate read requests');
        await mode('consolidated');
        await page.waitForFunction(() => window.__jkPesquisaMercadoHistory.state.consolidated.revision === 9 && !window.__jkPesquisaMercadoHistory.state.consolidated.busy);
        assert.strictEqual(await page.locator('#pm-consolidated-rows script').count(), 0);
        assert.match(await page.locator('#pm-consolidated-rows').textContent(), /<script>untrusted\(\)<\/script>/);
        assert.match(await page.locator('#pm-consolidated-rows [data-origin-index="0"]').textContent(), /Ver 2 pesquisa/, 'distinct research count is not an alias observation count');
        const latestPending = page.locator('#pm-consolidated-rows tr').filter({ hasText: 'Pendente atual' });
        assert.strictEqual(await latestPending.locator('td').first().textContent(), '');
        assert.strictEqual(await latestPending.locator('a').count(), 0);
        assert.match(await latestPending.textContent(), /Data aproximada/);
        await page.locator('#pm-consolidated-rows [data-origin-index="0"]').click();
        await page.waitForFunction(() => document.querySelectorAll('#pm-consolidated-origin-list .pm-saved-card').length === 2);
        assert.match(await page.locator('#pm-consolidated-origin-list').textContent(), /Concluída parcialmente|Cancelada/);
        await page.locator('#pm-consolidated-origin-list [data-consult-job="old-B"]').click();
        await page.waitForFunction(() => !document.getElementById('pm-panel-history').hidden && document.getElementById('pm-history-detail-heading').textContent.includes('Suspensão'));
        assert.deepStrictEqual(await page.evaluate(() => window.__commands), [], 'saved queries issue no operational command');
        assert.ok(requests.every(request => request.method === 'GET'));
        assert.ok(requests.filter(request => request.path.endsWith('/ranking')).every(request => request.params.get('limit') === '100'));
        assert.ok(requests.filter(request => request.path.endsWith('/jobs')).every(request => request.params.get('limit') === '25'));
        assert.ok(!requests.some(request => /\/jobs\/old-[AB]$/.test(request.path)), 'history must not download crawl checkpoints');

        await page.evaluate(() => window.__jkPesquisaMercadoHistory.openDetail('old-A'));
        await page.waitForFunction(() => window.__jkPesquisaMercadoHistory.state.history.detailId === 'old-A' && !window.__jkPesquisaMercadoHistory.state.history.detailBusy);
        assert.match(await page.locator('#pm-history-detail-heading').textContent(), /Ciclismo → Freios/);
        assert.match(await page.locator('#pm-history-incomplete-summary').textContent(), /2 categorias incompletas/);
        assert.match(await page.locator('#pm-history-incomplete-list').textContent(), /bloqueou.*Motivo anterior desconhecido/s);
        assert.doesNotMatch(await page.locator('#pm-history-incomplete-list').textContent(), /private raw details/);
        await page.evaluate(() => {
            window.__recoveryCalls = [];
            window.__jkPesquisaMercadoRecoverIncomplete = async (jobId, expectedRevision) => {
                window.__recoveryCalls.push({ jobId, expectedRevision });
                await new Promise(resolve => { window.__releaseRecovery = resolve; });
                return { job: { id: jobId, status: 'running', revision: expectedRevision + 1 } };
            };
            window.__diagnosticCalls = [];
            window.__jkPesquisaMercadoDiagnosticBrowser = async jobId => { window.__diagnosticCalls.push(jobId); };
        });
        await page.locator('#pm-history-diagnostic-browser').click();
        assert.deepStrictEqual(await page.evaluate(() => window.__diagnosticCalls), ['old-A'], 'diagnóstico usa a pesquisa selecionada');
        await page.locator('#pm-history-recover-incomplete').click();
        await page.locator('#pm-history-recover-incomplete').dispatchEvent('click');
        assert.deepStrictEqual(await page.evaluate(() => window.__recoveryCalls), [{ jobId: 'old-A', expectedRevision: 4 }], 'recuperação explícita usa revisão histórica e evita duplicidade');
        assert.strictEqual(await page.locator('[data-delete-job="old-A"]').isDisabled(), true, 'exclusão aguarda recuperação em curso');
        oldA.status = 'running';
        await page.evaluate(() => window.__releaseRecovery());
        await page.waitForFunction(() => !window.__jkPesquisaMercadoHistory.state.history.recoverBusy && document.querySelector('#pm-history-recovery-progress').textContent.includes('0 de 2'));
        assert.strictEqual(await page.locator('#pm-history-recover-incomplete').isVisible(), false);
        oldA.status = 'completed_partial'; oldA.salesRecoveryPending = true;
        await page.evaluate(() => window.__jkPesquisaMercadoHistory.openDetail('old-A'));
        await page.waitForFunction(() => !window.__jkPesquisaMercadoHistory.state.history.detailBusy && document.querySelector('#pm-history-recover-incomplete').textContent === 'Tentar dados pendentes novamente');
        assert.strictEqual(await page.locator('#pm-history-recover-incomplete').isEnabled(), true, 'histórico permite recuperar apenas métricas pendentes');
        assert.strictEqual(await page.locator('#pm-history-incomplete').isVisible(), false);
        await page.locator('#pm-history-recover-incomplete').click();
        assert.strictEqual(await page.evaluate(() => window.__recoveryCalls.length), 2);
        oldA.status = 'running';
        await page.evaluate(() => window.__releaseRecovery());
        await page.waitForFunction(() => !window.__jkPesquisaMercadoHistory.state.history.recoverBusy);

        // An in-flight request from a previous user must not repopulate any saved results.
        await page.locator('#pm-history-search').fill('slow');
        await page.waitForTimeout(290);
        await page.evaluate(() => { localStorage.removeItem('access_token'); dispatchEvent(new StorageEvent('storage', { key: 'access_token' })); });
        assert.strictEqual(await page.locator('#pm-history-list .pm-saved-card').count(), 0);
        assert.strictEqual(await page.locator('#pm-history-detail-rows tr').count(), 0);
        await page.waitForTimeout(450);
        assert.strictEqual(await page.locator('#pm-history-list .pm-saved-card').count(), 0, 'late response after logout stays cleared');
        await page.evaluate(() => { localStorage.setItem('access_token', 'fixture-B'); dispatchEvent(new StorageEvent('storage', { key: 'access_token' })); });
        await page.waitForFunction(() => document.querySelectorAll('#pm-history-list .pm-saved-card').length === 25);
        assert.strictEqual(requests.at(-1).token, 'Bearer fixture-B');
        assert.strictEqual(await page.evaluate(() => window.__jkPesquisaMercadoHistory.state.history.detailId), '', 'session changes clear selected research');
        await page.locator('#pm-history-search').fill('empty');
        await page.waitForFunction(() => document.getElementById('pm-history-message').textContent.includes('Nenhuma pesquisa'));
        unauthorized = true;
        await page.locator('#pm-history-refresh').click();
        await page.waitForFunction(() => document.getElementById('pm-history-message').textContent.includes('renovada'));
        unauthorized = false;
        await page.locator('#pm-history-search').fill('');
        await page.waitForFunction(() => document.querySelectorAll('#pm-history-list .pm-saved-card').length === 25);
        await page.setViewportSize({ width: 390, height: 900 });
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), '390px history fits viewport');
        await mode('consolidated');
        await page.waitForFunction(() => document.querySelectorAll('#pm-consolidated-rows tr').length === 2);
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), '390px consolidated table scroll stays contained');
        await page.locator('#pm-consolidated-evidence').selectOption('pending');
        await page.waitForFunction(() => document.querySelectorAll('#pm-consolidated-rows tr').length === 1);
        assert.match(await page.locator('#pm-consolidated-rows').textContent(), /Pendente atual/);
        await mode('history');
        await page.locator('#pm-history-search').fill('slow');
        await page.waitForTimeout(290);
        await page.evaluate(() => window.__jkPesquisaMercadoHistory.dispose());
        const beforeDisposed = await page.locator('#pm-history-list').textContent();
        await page.waitForTimeout(450);
        assert.strictEqual(await page.locator('#pm-history-list').textContent(), beforeDisposed, 'disposed module rejects a late successful response');
        assert.deepStrictEqual(errors, []);
        console.log('Pesquisa de Mercado history UI: snapshots, read-only history, ranking, origins, races, logout and responsive layout OK');
    } finally {
        if (browser) await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
