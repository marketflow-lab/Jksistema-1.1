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
const token = (client, username, revision = 1) => `fixture.${Buffer.from(JSON.stringify({ client_id: client, sub: username, revision })).toString('base64url')}.fixture`;
const configurationKey = (client, username) => `jkPesquisaMercadoAvantEmail:v1:${encodeURIComponent(JSON.stringify([client, username]))}`;
const category = { key: 'MLB-TEST', nome: 'Categoria teste', url: 'https://www.mercadolivre.com.br/c/teste' };

async function main() {
    const commands = [];
    const errors = [];
    let job = null;
    const server = http.createServer(async (req, res) => {
        const url = new URL(req.url, 'http://127.0.0.1');
        const json = value => { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(value)); };
        if (url.pathname === '/pesquisa_mercado.html') { res.setHeader('Content-Type', 'text/html'); res.end(html); return; }
        if (url.pathname.startsWith('/pesquisa-mercado/')) {
            const file = path.basename(url.pathname);
            res.setHeader('Content-Type', file.endsWith('.css') ? 'text/css' : 'text/javascript');
            res.end(fs.readFileSync(path.join(root, 'static/pesquisa-mercado', file))); return;
        }
        if (url.pathname === '/test/command') {
            let body = '';
            for await (const chunk of req) body += chunk;
            const payload = JSON.parse(body || '{}');
            const action = url.searchParams.get('action');
            commands.push({ action, payload });
            if (action === 'categories') { json({ items: payload.parentCategory ? [] : [category], parentCategory: payload.parentCategory || null,
                complete: true, updatedAt: '2026-10-08T12:00:00.000Z', summary: { total: 1, rootCount: 1, subcategoryCount: 0, maxDepth: 1 }, childCounts: { [category.key]: 0 } }); return; }
            if (action === 'start') job = { id: 'test-job', status: 'running', revision: 1, scopeCategory: category, counters: {} };
            if (action === 'pause') job = { ...job, status: 'paused', revision: job.revision + 1 };
            if (action === 'resume') job = { ...job, status: 'running', revision: job.revision + 1 };
            if (action === 'cancel') job = { ...job, status: 'cancelled', revision: job.revision + 1 };
            json(action === 'refresh-names' ? { job, updated: 0, pending: 0 } : { job }); return;
        }
        if (url.pathname === '/api/pesquisa-mercado/jobs/latest') { json({ job }); return; }
        if (url.pathname.endsWith('/ranking')) { json({ items: [], total: 0, revision: job && job.revision || 0 }); return; }
        if (url.pathname.endsWith('/vendedores') || url.pathname.endsWith('/categorias')) { json({ items: [] }); return; }
        res.statusCode = 404; res.end();
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const origin = `http://127.0.0.1:${server.address().port}`;
    let browser;
    try {
        browser = await chromium.launch({ headless: true });
        const context = await browser.newContext({ storageState: { cookies: [], origins: [{ origin, localStorage: [
            { name: 'access_token', value: token('tenant-a', 'operator-a') },
            { name: 'user_data', value: JSON.stringify({ client_id: 'tenant-a', username: 'operator-a' }) }
        ] }] } });
        await context.addInitScript(() => {
            const command = action => async payload => (await fetch(`/test/command?action=${action}`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload)
            })).json();
            window.electronAPI = {
                startPesquisaMercadoJob: command('start'), getPesquisaMercadoJobStatus: command('status'),
                pausePesquisaMercadoJob: command('pause'), resumePesquisaMercadoJob: command('resume'),
                cancelPesquisaMercadoJob: command('cancel'), showPesquisaMercadoBrowser: command('browser'),
                getPesquisaMercadoCategories: command('categories'), refreshPesquisaMercadoNames: command('refresh-names'),
                onPesquisaMercadoProgress(callback) { window.__pmProgress = callback; return () => {}; }
            };
        });
        const page = await context.newPage();
        page.on('pageerror', error => errors.push(error.message));
        await page.goto(`${origin}/pesquisa_mercado.html`);
        await page.locator('#pm-mode-global').click();
        await page.locator('.pm-avant-login summary').click();
        const email = page.locator('#pm-global-avant-email');
        assert.strictEqual(await email.inputValue(), '', 'conta de Avant não tem default de outro usuário');
        await page.getByRole('button', { name: 'Selecionar Categoria teste e ver subcategorias', exact: true }).click();
        await page.waitForFunction(() => !document.getElementById('pm-global-start').disabled);
        await email.fill('operator-a@example.test');
        await email.press('Tab');
        assert.strictEqual(await page.evaluate(key => localStorage.getItem(key), configurationKey('tenant-a', 'operator-a')), 'operator-a@example.test');
        await page.locator('#pm-global-start').click();
        await page.waitForFunction(() => !document.getElementById('pm-global-pause').disabled);
        assert.strictEqual(commands.find(command => command.action === 'start').payload.avantLoginEmail, 'operator-a@example.test');
        assert.strictEqual(await email.isDisabled(), true, 'configuração não muda durante execução ativa');
        await page.locator('#pm-global-pause').click();
        await page.waitForFunction(() => !document.getElementById('pm-global-resume').disabled);
        assert.strictEqual(await email.isDisabled(), false);
        await page.locator('#pm-global-resume').click();
        await page.waitForFunction(() => !document.getElementById('pm-global-pause').disabled);
        assert.strictEqual(commands.find(command => command.action === 'resume').payload.avantLoginEmail, 'operator-a@example.test');
        await page.locator('#pm-global-pause').click();
        await page.waitForFunction(() => !document.getElementById('pm-global-resume').disabled);
        await page.evaluate(() => window.__jkPesquisaMercadoRefreshNames('test-job'));
        assert.strictEqual(commands.find(command => command.action === 'refresh-names').payload.avantLoginEmail, 'operator-a@example.test');
        await page.locator('#pm-global-browser').click();
        await page.waitForFunction(() => !document.getElementById('pm-global-browser').disabled);
        for (const command of commands.filter(command => !['start', 'resume', 'refresh-names'].includes(command.action))) {
            assert.strictEqual(Object.hasOwn(command.payload, 'avantLoginEmail'), false, 'email é restrito a comandos que podem precisar de login');
        }
        await page.reload();
        await page.locator('#pm-mode-global').click();
        await page.locator('.pm-avant-login summary').click();
        await page.waitForFunction(() => !document.getElementById('pm-global-resume').disabled);
        assert.strictEqual(await email.inputValue(), 'operator-a@example.test', 'configuração sobrevive à reabertura');
        await page.evaluate(value => {
            localStorage.setItem('access_token', value);
            dispatchEvent(new StorageEvent('storage', { key: 'access_token' }));
        }, token('tenant-a', 'operator-a', 2));
        assert.strictEqual(await email.inputValue(), 'operator-a@example.test', 'renovação de token da mesma identidade mantém a configuração');
        await page.evaluate(value => {
            localStorage.setItem('access_token', value);
            dispatchEvent(new StorageEvent('storage', { key: 'access_token' }));
        }, token('tenant-b', 'operator-a'));
        assert.strictEqual(await email.inputValue(), '', 'user_data antigo não reutiliza email com token de outro tenant');
        await page.evaluate(() => {
            localStorage.setItem('user_data', JSON.stringify({ client_id: 'tenant-b', username: 'operator-a' }));
            dispatchEvent(new StorageEvent('storage', { key: 'user_data' }));
        });
        assert.strictEqual(await email.inputValue(), '');
        await page.waitForFunction(() => !document.getElementById('pm-global-avant-email').disabled);
        await email.fill('operator-b@example.test');
        await email.press('Tab');
        assert.strictEqual(await page.evaluate(key => localStorage.getItem(key), configurationKey('tenant-b', 'operator-a')), 'operator-b@example.test');
        await page.evaluate(({ value, user }) => {
            localStorage.setItem('access_token', value);
            localStorage.setItem('user_data', JSON.stringify(user));
            dispatchEvent(new StorageEvent('storage', { key: 'access_token' }));
        }, { value: token('tenant-a', 'operator-a', 3), user: { client_id: 'tenant-a', username: 'operator-a' } });
        assert.strictEqual(await email.inputValue(), 'operator-a@example.test', 'retorno à conta recupera somente sua preferência');
        await page.evaluate(({ value, user }) => {
            localStorage.setItem('access_token', value);
            localStorage.setItem('user_data', JSON.stringify(user));
            dispatchEvent(new StorageEvent('storage', { key: 'access_token' }));
        }, { value: token('tenant-a', 'operator-b'), user: { client_id: 'tenant-a', username: 'operator-b' } });
        assert.strictEqual(await email.inputValue(), '', 'outro usuário dentro do mesmo tenant não herda a conta Avant');
        await page.evaluate(({ value, user }) => {
            localStorage.setItem('access_token', value);
            localStorage.setItem('user_data', JSON.stringify(user));
            dispatchEvent(new StorageEvent('storage', { key: 'access_token' }));
        }, { value: token('tenant-a', 'Operator-A'), user: { client_id: 'tenant-a', username: 'Operator-A' } });
        assert.strictEqual(await email.inputValue(), '', 'identidades com maiúsculas distintas não colidem na preferência');
        await page.evaluate(({ value, user }) => {
            localStorage.setItem('access_token', value);
            localStorage.setItem('user_data', JSON.stringify(user));
            dispatchEvent(new StorageEvent('storage', { key: 'access_token' }));
        }, { value: token('tenant-a', 'operator-a', 4), user: { client_id: 'tenant-a', username: 'operator-a' } });
        assert.strictEqual(await email.inputValue(), 'operator-a@example.test');
        await page.waitForFunction(() => !document.getElementById('pm-global-resume').disabled);
        await email.fill('invalid-address');
        const resumeCount = commands.filter(command => command.action === 'resume').length;
        await page.locator('#pm-global-resume').click();
        await page.waitForFunction(() => document.getElementById('pm-global-status-warning').textContent.includes('e-mail válido'));
        assert.strictEqual(commands.filter(command => command.action === 'resume').length, resumeCount, 'email inválido não chega ao main');
        await email.fill('');
        await email.press('Tab');
        assert.strictEqual(await page.evaluate(key => localStorage.getItem(key), configurationKey('tenant-a', 'operator-a')), null, 'limpar a configuração remove o valor persistido');
        await page.locator('#pm-global-resume').click();
        await page.waitForFunction(() => !document.getElementById('pm-global-pause').disabled);
        assert.strictEqual(Object.hasOwn(commands.filter(command => command.action === 'resume').at(-1).payload, 'avantLoginEmail'), false, 'configuração vazia mantém contrato legado');
        await page.locator('#pm-global-pause').click();
        await page.waitForFunction(() => !document.getElementById('pm-global-resume').disabled);
        await page.evaluate(() => {
            localStorage.setItem('access_token', 'opaque-token');
            localStorage.removeItem('user_data');
            dispatchEvent(new StorageEvent('storage', { key: 'access_token' }));
        });
        await page.waitForFunction(() => !document.getElementById('pm-global-avant-email').disabled);
        await email.fill('memory@example.test');
        await email.press('Tab');
        await page.evaluate(() => {
            localStorage.setItem('access_token', 'other-opaque-token');
            dispatchEvent(new StorageEvent('storage', { key: 'access_token' }));
        });
        assert.strictEqual(await email.inputValue(), '', 'sem identidade estável, troca da sessão limpa valor em memória');
        await page.evaluate(() => {
            localStorage.removeItem('access_token');
            dispatchEvent(new StorageEvent('storage', { key: 'access_token' }));
        });
        assert.strictEqual(await email.inputValue(), '');
        assert.strictEqual(await email.isDisabled(), true, 'logout limpa e desabilita configuração');
        assert.deepStrictEqual(errors, []);
        console.log('Pesquisa de Mercado Avant UI: configuração privada, renovação, isolamento de contas e comandos OK');
    } finally {
        if (browser) await browser.close();
        await new Promise(resolve => server.close(resolve));
    }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
