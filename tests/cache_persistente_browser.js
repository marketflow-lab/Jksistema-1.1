'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const temporaryRoot = path.resolve(process.env.JK_TEST_TMP || os.tmpdir());
const profile = fs.mkdtempSync(path.join(temporaryRoot, 'jk-cache-persistente-'));
const origin = 'http://127.0.0.1:8127';
let context;

async function launch(options = {}) {
    context = await chromium.launchPersistentContext(profile, { headless: true });
    if (options.noStorage) await context.addInitScript(() => {
        Object.defineProperty(window, 'indexedDB', { value: null });
    });
    const errors = [];
    await context.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.pathname === '/cache-persistente.js') return route.fulfill({ contentType: 'application/javascript',
            body: fs.readFileSync(path.join(root, 'static/cache-persistente.js'), 'utf8') });
        if (url.pathname === '/auth/session.js') return route.fulfill({ contentType: 'application/javascript',
            body: fs.readFileSync(path.join(root, 'static/auth/session.js'), 'utf8') });
        if (url.pathname.startsWith('/api/')) return route.fulfill({ status: Number(url.searchParams.get('status') || 200),
            headers: url.searchParams.get('status') === '401' ? { 'WWW-Authenticate': 'Bearer' } : {},
            contentType: 'application/json', body: '{"success":true}' });
        const auth = options.withAuth ? '<script>window.navegarComTransicao=()=>{window.fixtureRedirected=true;};</script><script src="/auth/session.js"></script>' : '';
        return route.fulfill({ contentType: 'text/html', body: '<!doctype html>' + auth + '<script src="/cache-persistente.js"></script><p>Cache test</p>' });
    });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(origin);
    return { page, errors };
}

(async () => {
    try {
        let env = await launch();
        await env.page.evaluate(() => {
            localStorage.setItem('user_data', JSON.stringify({ username: 'tester', client_id: 'client-a' }));
            localStorage.setItem('permissions', JSON.stringify({ vendas: true, importacoes: true }));
            localStorage.setItem('access_token', 'synthetic-session-one');
        });
        assert.strictEqual(await env.page.evaluate(async () => {
            const contexto = JKCachePersistente.contexto();
            return JKCachePersistente.salvar('importacoes:lista:A', { itens: [{ SKU: '001', Quantidade: 2, M3: 0 }] },
                { contexto, tags: ['importacoes'] });
        }), true);
        assert.strictEqual(await env.page.evaluate(() => JKCachePersistente.salvar('vendas:periodo:loja-A:2026-10',
            { total: 10 }, { tags: ['vendas'] })), true);
        const saved = await env.page.evaluate(() => JKCachePersistente.ler('importacoes:lista:A'));
        assert.deepStrictEqual(saved.dados.itens, [{ SKU: '001', Quantidade: 2, M3: 0 }]);
        assert(Number.isFinite(saved.salvoEm));
        await context.close();

        // A new Chromium process reuses only the disk profile, not a storageState export.
        env = await launch();
        assert.deepStrictEqual(await env.page.evaluate(() => JKCachePersistente.ler('importacoes:lista:A')), saved,
            'O snapshot deve sobreviver ao encerramento real do navegador');
        assert.strictEqual(await env.page.evaluate(async () => {
            const beforeRenewal = JKCachePersistente.contexto();
            localStorage.setItem('access_token', 'synthetic-session-two');
            return !JKCachePersistente.vigente(beforeRenewal)
                && Boolean(await JKCachePersistente.ler('importacoes:lista:A'))
                && !(await JKCachePersistente.salvar('late-session', {}, { contexto: beforeRenewal, tags: ['importacoes'] }));
        }), true, 'Token renovado deve reutilizar snapshot e rejeitar resposta da sessão anterior');

        assert.strictEqual(await env.page.evaluate(async () => {
            const user = localStorage.getItem('user_data');
            localStorage.setItem('user_data', JSON.stringify({ username: 'other', client_id: 'client-a' }));
            const otherUser = await JKCachePersistente.ler('importacoes:lista:A');
            localStorage.setItem('user_data', JSON.stringify({ username: 'tester', client_id: 'client-b' }));
            const otherTenant = await JKCachePersistente.ler('importacoes:lista:A');
            localStorage.setItem('user_data', user);
            const perms = localStorage.getItem('permissions');
            localStorage.setItem('permissions', JSON.stringify({ vendas: false, importacoes: true }));
            const otherAuthority = await JKCachePersistente.ler('importacoes:lista:A');
            localStorage.setItem('permissions', perms);
            return otherUser === null && otherTenant === null && otherAuthority === null;
        }), true, 'Usuário, cliente e permissões fazem parte do escopo durável');
        assert.strictEqual(await env.page.evaluate(() => JKCachePersistente.ler('importacoes:lista:B')), null);
        assert.strictEqual(await env.page.evaluate(() => {
            const captured = JKCachePersistente.contexto();
            const user = localStorage.getItem('user_data');
            localStorage.setItem('user_data', JSON.stringify({ username: 'other', client_id: 'client-a' }));
            const different = !JKCachePersistente.vigente(captured);
            localStorage.setItem('user_data', user);
            return different && !JKCachePersistente.vigente(captured);
        }), true, 'Voltar à mesma conta não pode reativar uma requisição da sessão anterior');

        await env.page.evaluate(() => JKCachePersistente.salvar('corrupt', { total: 3 }, { tags: ['importacoes'] }));
        await env.page.evaluate(() => new Promise((resolve, reject) => {
            const request = indexedDB.open('jk-ui-snapshots-v1');
            request.onerror = () => reject(request.error);
            request.onsuccess = () => {
                const database = request.result;
                const tx = database.transaction('snapshots', 'readwrite');
                const store = tx.objectStore('snapshots');
                const rows = store.getAll();
                rows.onsuccess = () => {
                    const entry = rows.result.find(row => row.id.endsWith(':corrupt'));
                    entry.schema = 999;
                    store.put(entry);
                };
                tx.oncomplete = () => { database.close(); resolve(); };
                tx.onerror = () => reject(tx.error);
            };
        }));
        assert.strictEqual(await env.page.evaluate(() => JKCachePersistente.ler('corrupt')), null,
            'Formato persistido inválido deve cair para a leitura normal');

        assert.strictEqual(await env.page.evaluate(async () => {
            const beforeMutation = JKCachePersistente.contexto();
            const response = await fetch('/api/medias-compras/listas-pedidos/A', { method: 'PUT' });
            const old = await JKCachePersistente.ler('importacoes:lista:A');
            const late = await JKCachePersistente.salvar('importacoes:lista:A', { obsolete: true },
                { contexto: beforeMutation, tags: ['importacoes'] });
            const unaffected = await JKCachePersistente.ler('vendas:periodo:loja-A:2026-10');
            return old === null && !late && unaffected.dados.total === 10
                && JKCachePersistente.vigente(beforeMutation)
                && !JKCachePersistente.dadosVigentes(beforeMutation, ['importacoes'])
                && JKCachePersistente.vigente(JKCachePersistente.confirmarMutacao(beforeMutation, response));
        }), true, 'Mutação confirmada invalida a família e impede reentrada de resposta anterior');
        assert.strictEqual(await env.page.evaluate(async () => {
            const before = JKCachePersistente.contexto();
            const response = await fetch('/api/medias-compras/listas-pedidos/A', { method: 'PATCH' });
            await JKCachePersistente.invalidar(['vendas']);
            return JKCachePersistente.confirmarMutacao(before, response) === null;
        }), true, 'O recibo de uma edição não incorpora outra alteração concorrente');

        await env.page.evaluate(() => JKCachePersistente.salvar('importacoes:lista:A', { total: 22 }, { tags: ['importacoes'] }));
        assert.strictEqual(await env.page.evaluate(async () => {
            await fetch('/api/medias-compras/listas-pedidos/A?status=500', { method: 'PUT' });
            await fetch('/api/ia/chat', { method: 'POST' });
            return (await JKCachePersistente.ler('importacoes:lista:A')).dados.total;
        }), 22, 'Mutação recusada e POST de consulta preservam a cópia válida');

        const second = await context.newPage();
        await second.goto(origin);
        await second.evaluate(() => fetch('/api/cadastro/produtos/001', { method: 'PUT' }));
        assert.strictEqual(await env.page.evaluate(() => JKCachePersistente.ler('importacoes:lista:A')), null,
            'Alteração de Cadastro em outra página invalida o resultado');
        await second.close();

        await env.page.evaluate(() => JKCachePersistente.salvar('importacoes:lista:A', { total: 23 }, { tags: ['importacoes'] }));
        assert.strictEqual(await env.page.evaluate(async () => {
            const original = IDBObjectStore.prototype.put;
            IDBObjectStore.prototype.put = () => { throw new DOMException('Synthetic quota', 'QuotaExceededError'); };
            try { return await JKCachePersistente.salvar('importacoes:lista:A', { total: 99 }, { tags: ['importacoes'] }); }
            finally { IDBObjectStore.prototype.put = original; }
        }), false);
        assert.strictEqual((await env.page.evaluate(() => JKCachePersistente.ler('importacoes:lista:A'))).dados.total, 23,
            'Falha de gravação deve preservar a última cópia válida');

        assert.strictEqual(await env.page.evaluate(async () => {
            const captured = JKCachePersistente.contexto();
            await fetch('/api/vendas/grafico?status=403');
            return JKCachePersistente.vigente(captured)
                && (await JKCachePersistente.ler('importacoes:lista:A')).dados.total === 23;
        }), true, 'Um complemento sem permissão não pode invalidar a tela principal autorizada');
        await env.page.evaluate(() => fetch(new URL('/api/cadastro/foto/upload', location.href), { method: 'POST' }));
        assert.strictEqual(await env.page.evaluate(() => JKCachePersistente.ler('importacoes:lista:A')), null,
            'Upload canônico de foto também invalida snapshots');
        await env.page.evaluate(() => JKCachePersistente.salvar('importacoes:lista:A', { total: 24 }, { tags: ['importacoes'] }));

        assert.strictEqual(await env.page.evaluate(async () => {
            let denied = false;
            addEventListener('jk:cache-persistente-auth-negado', () => { denied = true; }, { once: true });
            const captured = JKCachePersistente.contexto();
            const response = await fetch('/api/vendas?status=401');
            return response.status === 401 && denied && !JKCachePersistente.vigente(captured)
                && (await JKCachePersistente.ler('importacoes:lista:A')) === null;
        }), true, 'Autorização recusada bloqueia a sessão e avisa consumidores');

        await env.page.evaluate(() => {
            localStorage.removeItem('user_data');
            localStorage.removeItem('access_token');
        });
        assert.strictEqual(await env.page.evaluate(() => JKCachePersistente.ler('importacoes:lista:A')), null);
        assert.deepStrictEqual(env.errors, []);
        await context.close();

        env = await launch({ noStorage: true });
        await env.page.evaluate(() => {
            localStorage.setItem('user_data', JSON.stringify({ username: 'tester', client_id: 'client-a' }));
            localStorage.setItem('access_token', 'synthetic-session-three');
        });
        assert.strictEqual(await env.page.evaluate(() => JKCachePersistente.ler('anything')), null);
        assert.strictEqual(await env.page.evaluate(() => JKCachePersistente.salvar('anything', {}, { tags: ['vendas'] })), false);
        assert.strictEqual(await env.page.evaluate(async () => (await fetch('/api/vendas')).ok), true);
        assert.deepStrictEqual(env.errors, []);
        await context.close();

        env = await launch({ withAuth: true });
        await env.page.evaluate(() => {
            localStorage.setItem('user_data', JSON.stringify({ username: 'tester', client_id: 'client-a' }));
            localStorage.setItem('access_token', 'synthetic-auth-interceptor');
        });
        assert.strictEqual(await env.page.evaluate(async () => {
            let cleared = false;
            addEventListener('jk:cache-persistente-auth-negado', () => { cleared = true; }, { once: true });
            const response = await fetch('/api/vendas?status=401');
            return response.status === 401 && fixtureRedirected && cleared
                && localStorage.getItem('user_data') === null;
        }), true, 'O auth real pode remover a sessão antes de o helper receber a resposta');
        assert.deepStrictEqual(env.errors, []);
        console.log('Cache persistente: reinício real, renovação de token, isolamento, invalidação entre páginas, corrida, quota e fallback: OK');
    } finally {
        await context?.close().catch(() => {});
        const resolved = path.resolve(profile);
        assert.strictEqual(path.dirname(resolved), temporaryRoot);
        assert(path.basename(resolved).startsWith('jk-cache-persistente-'));
        fs.rmSync(resolved, { recursive: true, force: true });
    }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
