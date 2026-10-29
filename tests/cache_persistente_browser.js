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

async function testStaleSafety(env) {
    const key = 'importacoes:stale-safety';
    assert.deepStrictEqual(await env.page.evaluate(async key => {
        await JKCachePersistente.salvar(key, { version: 1 }, { tags: ['importacoes'] });
        const oldContext = JKCachePersistente.contexto();
        let event;
        addEventListener('jk:cache-persistente-invalidado', e => { event = e.detail; }, { once: true });
        dispatchEvent(new CustomEvent('jk:vendas-sync-finished'));
        return {
            strict: await JKCachePersistente.ler(key),
            stale: (await JKCachePersistente.ler(key, { permitirDesatualizado: true }))?.desatualizado,
            value: (await JKCachePersistente.ler(key, { permitirDesatualizado: true }))?.dados.version,
            oldContext: await JKCachePersistente.ler(key, { contexto: oldContext, permitirDesatualizado: true }),
            oldWrite: await JKCachePersistente.salvar(key, { version: 99 }, { contexto: oldContext, tags: ['importacoes'] }),
            revoked: event.revogado,
        };
    }, key), { strict: null, stale: true, value: 1, oldContext: null, oldWrite: false, revoked: false },
    'Somente opt-in com contexto atual pode ler dados anteriores a sync; resposta antiga continua cercada');

    assert.strictEqual(await env.page.evaluate(async key => {
        const user = localStorage.getItem('user_data'), permissions = localStorage.getItem('permissions');
        const initial = JSON.parse(user);
        for (const changed of [{ ...initial, username: 'other' }, { ...initial, client_id: 'other-client' },
            { ...initial, allowed_store_ids: ['other-store'] }]) {
            localStorage.setItem('user_data', JSON.stringify(changed));
            if (await JKCachePersistente.ler(key, { permitirDesatualizado: true })) return false;
        }
        localStorage.setItem('user_data', user);
        localStorage.setItem('permissions', JSON.stringify({ importacoes: false }));
        const denied = await JKCachePersistente.ler(key, { permitirDesatualizado: true });
        localStorage.setItem('permissions', permissions);
        const previous = JKCachePersistente.contexto();
        localStorage.setItem('access_token', 'synthetic-stale-renewed');
        return denied === null && (await JKCachePersistente.ler(key, { permitirDesatualizado: true }))?.dados.version === 1
            && (await JKCachePersistente.ler(key, { contexto: previous, permitirDesatualizado: true })) === null;
    }, key), true, 'Opt-in conserva isolamento de usuário, cliente, lojas, permissões e fence da sessão');

    assert.deepStrictEqual(env.errors, []);
    await context.close();
    env = await launch();
    assert.strictEqual((await env.page.evaluate(key => JKCachePersistente.ler(key, { permitirDesatualizado: true }), key)).dados.version, 1,
        'Cópia desatualizada autorizada sobrevive ao reinício real');
    assert.strictEqual(await env.page.evaluate(async key => {
        await fetch('/api/medias-compras/listas-pedidos/A', { method: 'PUT' });
        return (await JKCachePersistente.ler(key)) === null
            && (await JKCachePersistente.ler(key, { permitirDesatualizado: true }))?.desatualizado === true;
    }, key), true, 'Mutação de dados preserva somente a leitura opt-in');

    // A legacy entry cannot prove whether a prior invalidation was data-only or a revocation.
    await env.page.evaluate(async () => {
        await JKCachePersistente.salvar('legacy', { version: 2 }, { tags: ['importacoes'] });
        await JKCachePersistente.salvar('corrupt-revocations', { version: 2 }, { tags: ['importacoes'] });
        await new Promise((resolve, reject) => {
            const request = indexedDB.open('jk-ui-snapshots-v1');
            request.onsuccess = () => {
                const database = request.result, tx = database.transaction('snapshots', 'readwrite');
                const store = tx.objectStore('snapshots'), rows = store.getAll();
                rows.onsuccess = () => {
                    const entry = rows.result.find(row => row.id.endsWith(':legacy'));
                    delete entry.revocations; store.put(entry);
                    const corrupt = rows.result.find(row => row.id.endsWith(':corrupt-revocations'));
                    corrupt.revocations = ['invalid']; store.put(corrupt);
                };
                tx.oncomplete = () => { database.close(); resolve(); };
                tx.onerror = () => reject(tx.error);
            };
            request.onerror = () => reject(request.error);
        });
    });
    assert.strictEqual((await env.page.evaluate(() => JKCachePersistente.ler('legacy'))).dados.version, 2);
    assert.strictEqual(await env.page.evaluate(() => JKCachePersistente.ler('corrupt-revocations', { permitirDesatualizado: true })), null,
        'Metadados de revogação corrompidos não autorizam reutilização');
    await env.page.evaluate(() => dispatchEvent(new CustomEvent('jk:vendas-sync-finished')));
    assert.strictEqual(await env.page.evaluate(() => JKCachePersistente.ler('legacy', { permitirDesatualizado: true })), null,
        'Entrada legada desatualizada não revive sem prova de autorização');

    const second = await context.newPage();
    await second.goto(origin);
    await env.page.evaluate(() => {
        window.revocationObserved = false;
        addEventListener('jk:cache-persistente-invalidado', e => {
            if (e.detail.origem === 'storage' && e.detail.revogado) window.revocationObserved = true;
        });
    });
    await second.evaluate(() => JKCachePersistente.invalidar(['importacoes']));
    await env.page.waitForFunction(() => window.revocationObserved);
    assert.strictEqual(await env.page.evaluate(key => JKCachePersistente.ler(key, { permitirDesatualizado: true }), key), null,
        'Revogação manual em outra página remove também a leitura stale');
    await second.close();
    await context.close();
    env = await launch();
    await env.page.evaluate(() => dispatchEvent(new CustomEvent('jk:vendas-sync-finished')));
    assert.strictEqual(await env.page.evaluate(key => JKCachePersistente.ler(key, { permitirDesatualizado: true }), key), null,
        'Reinício e nova invalidação de dados não desfazem a revogação persistida');

    const legacyTab = await context.newPage();
    await legacyTab.goto(origin);
    await env.page.evaluate(async key => {
        await JKCachePersistente.salvar(key, { version: 3 }, { tags: ['importacoes'] });
        window.revocationObserved = false;
        addEventListener('jk:cache-persistente-invalidado', e => {
            if (e.detail.origem === 'storage' && e.detail.revogado) window.revocationObserved = true;
        });
    }, key);
    await legacyTab.evaluate(() => localStorage.setItem('jk.ui.cache.revisao.v1:importacoes', 'synthetic-v1-manual-revocation'));
    await env.page.waitForFunction(() => window.revocationObserved);
    assert.strictEqual(await env.page.evaluate(key => JKCachePersistente.ler(key, { permitirDesatualizado: true }), key), null,
        'Revisão de uma aba antiga não comprova invalidação somente de dados');
    assert.strictEqual(await env.page.evaluate(async key => {
        let revoked = false;
        addEventListener('jk:cache-persistente-invalidado', e => { revoked = e.detail.revogado; }, { once: true });
        dispatchEvent(new CustomEvent('jk:vendas-sync-finished'));
        return revoked && (await JKCachePersistente.ler(key, { permitirDesatualizado: true })) === null;
    }, key), true, 'Novo sync conserva a revogação desconhecida da aba antiga antes de mudar o frescor');
    await legacyTab.close();
    await context.close();
    env = await launch();
    assert.strictEqual(await env.page.evaluate(key => JKCachePersistente.ler(key, { permitirDesatualizado: true }), key), null,
        'Revogação compatível com v1 permanece após reinício');
    assert.strictEqual(await env.page.evaluate(async key => {
        const saved = await JKCachePersistente.salvar(key, { version: 4 }, { tags: ['importacoes'] });
        dispatchEvent(new CustomEvent('jk:vendas-sync-finished'));
        return saved && (await JKCachePersistente.ler(key, { permitirDesatualizado: true }))?.dados.version === 4;
    }, key), true, 'Nova resposta autorizada restabelece a cópia depois da revogação antiga');

    assert.strictEqual(await env.page.evaluate(async () => {
        localStorage.setItem('jk.ui.cache.revisao.v1:importacoes', 'synthetic-v1-before-put');
        const before = JKCachePersistente.contexto();
        const response = await fetch('/api/medias-compras/listas-pedidos/A', { method: 'PUT' });
        return JKCachePersistente.vigente(JKCachePersistente.confirmarMutacao(before, response));
    }), true, 'Primeira edição após upgrade confirma sua própria promoção da revisão legada');
    let releaseMutation, mutationStarted;
    const mutationGate = new Promise(resolve => { releaseMutation = resolve; });
    const started = new Promise(resolve => { mutationStarted = resolve; });
    await env.page.route('**/api/medias-compras/listas-pedidos/pending', async route => {
        mutationStarted();
        await mutationGate;
        await route.fulfill({ contentType: 'application/json', body: '{"success":true}' });
    });
    await env.page.evaluate(() => {
        const before = JKCachePersistente.contexto();
        window.pendingMutation = fetch('/api/medias-compras/listas-pedidos/pending', { method: 'PUT' })
            .then(response => JKCachePersistente.confirmarMutacao(before, response) !== null);
    });
    await started;
    await env.page.evaluate(() => localStorage.setItem('jk.ui.cache.revisao.v1:importacoes', 'synthetic-v1-during-put'));
    releaseMutation();
    assert.strictEqual(await env.page.evaluate(() => window.pendingMutation), false,
        'Edição pendente não absorve revogação v1 concorrente como se fosse sua própria promoção');

    // Delay A's writes to reproduce: A captures U/R0; B promotes, saves, revokes;
    // A then commits its old decision. Retaining U as the fence would revive B's entry.
    const promotionTab = await context.newPage();
    await promotionTab.goto(origin);
    await env.page.evaluate(() => {
        localStorage.setItem('jk.ui.cache.revisao.v1:importacoes', 'synthetic-v1-promotion-race');
        window.delayedPromotionWrites = [];
        window.originalStorageSet = Storage.prototype.setItem;
        Storage.prototype.setItem = function (name, value) {
            if (['jk.ui.cache.revogacao.v1:importacoes', 'jk.ui.cache.revisao.v1:importacoes'].includes(name)) {
                delayedPromotionWrites.push([name, value]); return;
            }
            return originalStorageSet.call(this, name, value);
        };
        dispatchEvent(new CustomEvent('jk:vendas-sync-finished'));
    });
    await promotionTab.evaluate(async key => {
        dispatchEvent(new CustomEvent('jk:vendas-sync-finished'));
        if (!await JKCachePersistente.salvar(key, { version: 'concurrent' }, { tags: ['importacoes'] })) throw new Error('Fixture save failed');
        await JKCachePersistente.invalidar(['importacoes']);
    }, key);
    assert.strictEqual(await env.page.evaluate(key => JKCachePersistente.ler(key, { permitirDesatualizado: true }), key), null);
    await env.page.evaluate(() => {
        Storage.prototype.setItem = originalStorageSet;
        if (delayedPromotionWrites.length !== 2) throw new Error('Expected promotion and freshness writes');
        delayedPromotionWrites.forEach(([name, value]) => localStorage.setItem(name, value));
    });
    assert.strictEqual(await env.page.evaluate(key => JKCachePersistente.ler(key, { permitirDesatualizado: true }), key), null,
        'Promoção atrasada não pode reutilizar fence antiga e reviver a cópia revogada de outra aba');
    await promotionTab.close();
    assert.strictEqual(await env.page.evaluate(async key => {
        localStorage.setItem('jk.ui.cache.revisao.v1:*', 'synthetic-v1-wildcard');
        dispatchEvent(new CustomEvent('jk:vendas-sync-finished'));
        const fence = localStorage.getItem('jk.ui.cache.revogacao.v1:*');
        await JKCachePersistente.salvar(key, { version: 'wildcard' }, { tags: ['importacoes'] });
        let revoked = true;
        addEventListener('jk:cache-persistente-invalidado', event => { revoked = event.detail.revogado; }, { once: true });
        dispatchEvent(new CustomEvent('jk:vendas-sync-finished'));
        return !revoked && localStorage.getItem('jk.ui.cache.revogacao.v1:*') === fence
            && (await JKCachePersistente.ler(key, { permitirDesatualizado: true }))?.dados.version === 'wildcard';
    }, key), true, 'Wildcard legado já promovido não revoga uma nova cópia em cada sincronização');

    assert.strictEqual(await env.page.evaluate(async key => {
        await JKCachePersistente.salvar(key, { version: 3 }, { tags: ['importacoes'] });
        const original = crypto.subtle.digest.bind(crypto.subtle);
        let release, started;
        const gate = new Promise(resolve => { release = resolve; });
        const ready = new Promise(resolve => { started = resolve; });
        crypto.subtle.digest = async (...args) => { started(); await gate; return original(...args); };
        try {
            const reading = JKCachePersistente.ler(key, { permitirDesatualizado: true });
            await ready;
            await JKCachePersistente.invalidar(['importacoes']);
            release();
            return (await reading) === null;
        } finally { crypto.subtle.digest = original; }
    }, key), true, 'Revogação durante leitura pendente impede publicação da cópia');

    assert.strictEqual(await env.page.evaluate(async key => {
        await JKCachePersistente.salvar(key, { version: 4 }, { tags: ['importacoes'] });
        const original = Storage.prototype.getItem;
        Storage.prototype.getItem = function (name) {
            if (name.startsWith('jk.ui.cache.revogacao.v1:')) throw new Error('Synthetic storage failure');
            return original.call(this, name);
        };
        try {
            return !JKCachePersistente.vigente(JKCachePersistente.contexto())
                && (await JKCachePersistente.ler(key, { permitirDesatualizado: true })) === null
                && !(await JKCachePersistente.salvar(key, { version: 99 }, { tags: ['importacoes'] }));
        } finally { Storage.prototype.getItem = original; }
    }, key), true, 'Erro ao consultar revogação falha fechado em leitura e escrita');
    assert.strictEqual(await env.page.evaluate(async key => {
        const original = Storage.prototype.setItem;
        Storage.prototype.setItem = function (name, value) {
            if (name.startsWith('jk.ui.cache.revogacao.v1:')) throw new Error('Synthetic quota failure');
            return original.call(this, name, value);
        };
        try {
            const result = await JKCachePersistente.invalidar(['importacoes']);
            return result === false && !JKCachePersistente.vigente(JKCachePersistente.contexto())
                && (await JKCachePersistente.ler(key, { permitirDesatualizado: true })) === null;
        } finally { Storage.prototype.setItem = original; }
    }, key), true, 'Falha para persistir revogação desabilita reutilização e limpa snapshots');
    await context.close();
    env = await launch();
    assert.strictEqual(await env.page.evaluate(key => JKCachePersistente.ler(key, { permitirDesatualizado: true }), key), null,
        'Fallback de armazenamento não revive a entrada no reinício');
    return env;
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
        assert.strictEqual(saved.desatualizado, false);
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

        env = await testStaleSafety(env);
        await env.page.evaluate(() => JKCachePersistente.salvar('auth-revoked', { total: 24 }, { tags: ['importacoes'] }));

        assert.strictEqual(await env.page.evaluate(async () => {
            let denied = false;
            addEventListener('jk:cache-persistente-auth-negado', () => { denied = true; }, { once: true });
            const captured = JKCachePersistente.contexto();
            const response = await fetch('/api/vendas?status=401');
            return response.status === 401 && denied && !JKCachePersistente.vigente(captured)
                && (await JKCachePersistente.ler('auth-revoked', { permitirDesatualizado: true })) === null;
        }), true, 'Autorização recusada bloqueia a sessão e avisa consumidores');

        await context.close();
        env = await launch();
        await env.page.evaluate(() => localStorage.setItem('access_token', 'synthetic-after-auth-denial'));
        assert.strictEqual(await env.page.evaluate(() => JKCachePersistente.ler('auth-revoked', { permitirDesatualizado: true })), null,
            'Revogação por autenticação persiste após reinício e renovação do token');

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
