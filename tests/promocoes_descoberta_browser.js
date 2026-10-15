'use strict';

const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');

(async () => {
    const browser = await chromium.launch({ headless: true });
    try {
        const page = await browser.newPage({ viewport: { width: 1306, height: 850 } });
        const errors = [];
        const unexpected = [];
        const discoveryCalls = [];
        let revision = 0;
        let manualPartial = false;
        page.on('pageerror', error => errors.push(error.message));
        await page.route('**/*', route => { unexpected.push(route.request().url()); return route.abort(); });
        await page.route('http://promo.test/**', route => {
            const url = new URL(route.request().url());
            const pathname = url.pathname;
            if (['/auth.js', '/ia-sidebar.js', '/frontend_promo/init.js'].includes(pathname)) {
                return route.fulfill({ contentType: 'text/javascript', body: '' });
            }
            if (pathname === '/frontend_promo.html') return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(path.join(root, 'static/frontend_promo.html')) });
            if (/^\/frontend_promo\/[\w-]+\.(?:js|css)$/.test(pathname) || pathname === '/table_columns.js') {
                return route.fulfill({ contentType: pathname.endsWith('.css') ? 'text/css' : 'text/javascript', body: fs.readFileSync(path.join(root, 'static', pathname.slice(1))) });
            }
            if (pathname === '/api/mercadolivre/promocoes') return route.fulfill({ json: { success: true,
                discovery_available: true, seller_id: 'seller-test', site_id: 'MLB', campaigns: [
                    { id: 'REFERENCE', type: 'SELLER_CAMPAIGN', name: 'Promoção referência' },
                    { id: 'BASE', type: 'SMART', name: 'Campanha existente', eligible_count: 4 },
                ] } });
            if (pathname.startsWith('/api/mercadolivre/promocoes/descoberta/')) {
                discoveryCalls.push({ pathname, query: url.search, headers: route.request().headers() });
                revision++;
                return route.fulfill({ json: { success: true, job_id: 'job-test', loja: 'Loja Teste',
                    client_id: 'client-test', seller_id: 'seller-test', site_id: 'MLB', revision,
                    status: manualPartial ? 'partial' : revision === 1 ? 'running' : revision === 2 ? 'partial' : 'completed',
                    complete: revision > 2 && !manualPartial, resumable: revision === 2 && !manualPartial,
                    progress: { processed: Math.min(revision, 3), total: 3, failed: revision === 2 ? 1 : 0, pages: 1 },
                    campaigns: revision === 1 ? [] : [{ id: 'NEW', type: 'PRICE_MATCHING',
                        name: revision === 2 ? 'Campanha complementar <img src=x onerror=alert(1)>' : 'Campanha de competitividade (exemplo)', eligible_count: 2 }],
                } });
            }
            if (pathname.startsWith('/api/')) return route.fulfill({ json: { success: true, campaigns: [], counts: {} } });
            unexpected.push(pathname); return route.abort();
        });
        await page.goto('http://promo.test/frontend_promo.html');
        await page.clock.install();
        await page.evaluate(async () => {
            localStorage.setItem('user_data', JSON.stringify({ client_id: 'client-test' }));
            localStorage.setItem('access_token', 'token-test');
            document.getElementById('apiLojaSelect').innerHTML = '<option>Loja Teste</option>';
            await carregarPromocoesApi();
        });
        await page.locator('#apiPromoDiscoveryStatus').filter({ hasText: 'Buscando outras promoções' }).waitFor();
        assert.strictEqual(await page.locator('#apiPromoBSelect input[type=checkbox]').count(), 1);
        await page.locator('#apiPromoBSelect input[value=BASE]').uncheck();
        await page.clock.fastForward(3000);
        await page.locator('#apiPromoBSelect input[value=NEW]').waitFor();
        assert.strictEqual(await page.locator('#apiPromoBSelect input:checked').count(), 0);
        assert.strictEqual(await page.locator('#apiPromoBSelect img').count(), 0, 'nome da API permanece texto, sem execução de HTML');
        assert((await page.locator('#apiPromoDiscoveryStatus').textContent()).includes('parcial'));
        await page.clock.fastForward(3000);
        await page.locator('#apiPromoDiscoveryStatus').filter({ hasText: 'concluída' }).waitFor();
        assert.strictEqual(await page.locator('#apiPromoASelect').inputValue(), 'REFERENCE');
        assert.strictEqual(await page.locator('#apiPromoDiscoveryStatus').getAttribute('aria-live'), 'polite');
        assert.strictEqual(discoveryCalls.length, 3);
        assert(discoveryCalls.every(call => call.headers['x-client-id'] === 'client-test' && call.headers.authorization === 'Bearer token-test'));
        assert(discoveryCalls.filter(call => call.pathname.endsWith('/start')).every(call => call.query.includes('force_refresh=false')));
        manualPartial = true;
        await page.evaluate(() => carregarPromocoesApi());
        await page.locator('#apiPromoDiscoveryStatus button').filter({ hasText: 'Atualizar consulta' }).waitFor();
        assert((await page.locator('#apiPromoDiscoveryStatus').textContent()).includes('parcial'));
        manualPartial = false;
        await page.locator('#apiPromoDiscoveryStatus button').click();
        await page.locator('#apiPromoDiscoveryStatus').filter({ hasText: 'concluída' }).waitFor();
        assert(discoveryCalls.at(-1).query.includes('force_refresh=true'));
        assert.strictEqual(await page.locator('#apiPromoBSelect input:checked').count(), 0);
        assert.strictEqual(await page.locator('#apiPromoASelect').inputValue(), 'REFERENCE');
        const statusBounds = await page.locator('#apiPromoDiscoveryStatus').boundingBox();
        const cardsBounds = await page.locator('#apiPromoBSelect').boundingBox();
        assert.strictEqual(statusBounds.x, cardsBounds.x);
        assert.strictEqual(statusBounds.width, cardsBounds.width);
        fs.mkdirSync(path.join(root, 'test-results'), { recursive: true });
        await page.screenshot({ path: path.join(root, 'test-results/promo-descoberta-preview.png'), fullPage: false });
        await page.evaluate(() => { document.getElementById('apiLojaSelect').value = ''; carregarPromocoesApi(); });
        assert.strictEqual(await page.locator('#apiPromoDiscoveryStatus').isVisible(), false);
        assert.deepStrictEqual(errors, []);
        assert.deepStrictEqual(unexpected, []);
        console.log('promocoes descoberta browser checks passed');
    } finally {
        await browser.close();
    }
})().catch(error => { console.error(error); process.exitCode = 1; });
