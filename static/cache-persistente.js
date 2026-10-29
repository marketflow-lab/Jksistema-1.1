(function (global) {
    'use strict';
    if (global.JKCachePersistente) return;

    const DATABASE = 'jk-ui-snapshots-v1';
    const REVISION_KEY = 'jk.ui.cache.revisao.v1:';
    const REVOCATION_KEY = 'jk.ui.cache.revogacao.v1:';
    const DATA_REVISION_PREFIX = 'data:';
    const LEGACY_REVOCATION_PREFIX = 'legacy:';
    const SCHEMA = 1;
    const MAX_ENTRY_BYTES = 12 * 1024 * 1024;
    const MAX_BYTES = 128 * 1024 * 1024;
    const MAX_ENTRIES = 240;
    const MAX_SCOPE_BYTES = 64 * 1024 * 1024;
    const MAX_SCOPE_ENTRIES = 120;
    const STORAGE_TIMEOUT_MS = 800;
    const contexts = new WeakMap();
    const mutationReceipts = new WeakMap();
    let databasePromise;
    let generation = 0;
    let deniedSession = '';
    let lastSession;
    let storageUnavailable = false;

    function canonical(value) {
        if (Array.isArray(value)) return value.map(canonical);
        if (value && typeof value === 'object') {
            return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
        }
        return value;
    }

    function readIdentity() {
        try {
            const user = JSON.parse(global.localStorage.getItem('user_data') || 'null');
            const client = String(user?.client_id || '').trim();
            const username = String(user?.username || user?.user_id || '').trim();
            if (!client || !username) return null;
            const permissions = JSON.parse(global.localStorage.getItem('permissions') || 'null');
            const authority = {};
            ['role', 'roles', 'is_admin', 'is_superadmin', 'permissions', 'store_id',
                'store_ids', 'allowed_stores', 'allowed_store_ids'].forEach(key => {
                if (user[key] !== undefined) authority[key] = user[key];
            });
            const scope = JSON.stringify(canonical([SCHEMA, client, username, permissions, authority]));
            // A renewed token keeps the durable key; pending requests keep their own session fence.
            const session = JSON.stringify([scope, global.localStorage.getItem('access_token') || '']);
            return { scope, session };
        } catch (_error) { return null; }
    }

    function identity() {
        const current = readIdentity();
        const session = current?.session || '';
        if (lastSession !== session) {
            if (lastSession !== undefined) generation++;
            deniedSession = '';
            lastSession = session;
        }
        return current;
    }

    function revisions(prefix = REVISION_KEY) {
        try {
            return Object.fromEntries(['*', 'vendas', 'importacoes'].map(tag =>
                [tag, global.localStorage.getItem(prefix + tag) || '']));
        } catch (_error) { return null; }
    }

    function contexto() {
        const current = identity();
        const context = Object.freeze({});
        contexts.set(context, { ...current, generation, revisions: revisions(), revocations: revisions(REVOCATION_KEY) });
        return context;
    }

    function vigente(context) {
        const captured = contexts.get(context);
        const current = identity();
        return Boolean(!storageUnavailable && current && captured?.scope && captured.revisions && captured.revocations
            && captured.generation === generation
            && current.session === captured.session && deniedSession !== current.session);
    }

    function revisionMap(value) {
        return Boolean(value && Object.getPrototypeOf(value) === Object.prototype
            && Object.entries(value).every(([tag, revision]) => ['*', 'vendas', 'importacoes'].includes(tag)
                && typeof revision === 'string'));
    }

    function sameRevisions(before, after, tags) {
        return Boolean(before && after) && ['*', ...tags].every(tag => (before[tag] || '') === (after[tag] || ''));
    }

    function dataOnlyChanges(before, after, tags) {
        return Boolean(before && after) && ['*', ...tags].every(tag => (before[tag] || '') === (after[tag] || '')
            || String(after[tag] || '').startsWith(DATA_REVISION_PREFIX));
    }

    function legacyRevisionPromoted(revision, revocation) {
        if (typeof revocation !== 'string' || !revocation.startsWith(LEGACY_REVOCATION_PREFIX)) return false;
        try {
            const marker = JSON.parse(revocation.slice(LEGACY_REVOCATION_PREFIX.length));
            return Array.isArray(marker) && marker.length === 2 && marker[0] === revision
                && typeof marker[1] === 'string' && marker[1].startsWith(DATA_REVISION_PREFIX);
        } catch (_error) { return false; }
    }

    function dadosVigentes(context, tags = ['vendas', 'importacoes']) {
        const captured = contexts.get(context);
        return !storageUnavailable && vigente(context)
            && sameRevisions(captured.revisions, revisions(), tags)
            && sameRevisions(captured.revocations, revisions(REVOCATION_KEY), tags);
    }

    function confirmarMutacao(previous, response) {
        const receipt = mutationReceipts.get(response);
        const captured = contexts.get(previous);
        const tags = ['vendas', 'importacoes'];
        if (!receipt || storageUnavailable || !vigente(previous) || captured.session !== receipt.session
            || !sameRevisions(captured.revisions, receipt.before, tags)
            || !sameRevisions(receipt.after, revisions(), tags)
            || !sameRevisions(captured.revocations, receipt.revocationsBefore, tags)
            || !sameRevisions(receipt.revocationsAfter, revisions(REVOCATION_KEY), tags)) return null;
        return contexto();
    }

    function emit(name, detail) {
        try { global.dispatchEvent(new global.CustomEvent(name, { detail })); } catch (_error) {}
    }

    function openDatabase() {
        if (!global.indexedDB) return Promise.resolve(null);
        if (databasePromise) return databasePromise;
        const attempt = new Promise(resolve => {
            let settled = false;
            const finish = database => {
                if (settled) { database?.close(); return; }
                settled = true;
                global.clearTimeout(timer);
                resolve(database);
            };
            const timer = global.setTimeout(() => finish(null), STORAGE_TIMEOUT_MS);
            try {
                const request = global.indexedDB.open(DATABASE, SCHEMA);
                request.onupgradeneeded = () => {
                    const database = request.result;
                    if (!database.objectStoreNames.contains('snapshots')) database.createObjectStore('snapshots', { keyPath: 'id' });
                    if (!database.objectStoreNames.contains('indices')) database.createObjectStore('indices', { keyPath: 'id' });
                };
                request.onsuccess = () => {
                    const database = request.result;
                    database.onversionchange = () => { database.close(); databasePromise = undefined; };
                    finish(database);
                };
                request.onerror = request.onblocked = () => finish(null);
            } catch (_error) { finish(null); }
        });
        const pending = attempt.then(database => {
            if (!database && databasePromise === pending) databasePromise = undefined;
            return database;
        });
        databasePromise = pending;
        return pending;
    }

    async function scopeKey(context) {
        if (!vigente(context) || !global.crypto?.subtle) return null;
        try {
            const bytes = new global.TextEncoder().encode(contexts.get(context).scope);
            const hash = await global.crypto.subtle.digest('SHA-256', bytes);
            return vigente(context) ? Array.from(new Uint8Array(hash), byte => byte.toString(16).padStart(2, '0')).join('') : null;
        } catch (_error) { return null; }
    }

    function transaction(database, mode, action, fallback) {
        if (!database) return Promise.resolve(fallback);
        return new Promise(resolve => {
            let result = fallback;
            let settled = false;
            let tx;
            const finish = value => {
                if (settled) return;
                settled = true;
                global.clearTimeout(timer);
                resolve(value);
            };
            const timer = global.setTimeout(() => {
                try { tx?.abort(); } catch (_error) {}
                finish(fallback);
            }, STORAGE_TIMEOUT_MS);
            try {
                tx = database.transaction(['snapshots', 'indices'], mode);
                tx.oncomplete = () => finish(result);
                tx.onerror = tx.onabort = () => finish(fallback);
                action(tx, value => { result = value; });
            } catch (_error) {
                try { tx?.abort(); } catch (_abortError) {}
                finish(fallback);
            }
        });
    }

    async function ler(chave, options = {}) {
        if (storageUnavailable) return null;
        const context = options.contexto || contexto();
        const scope = await scopeKey(context);
        if (!scope || typeof chave !== 'string' || !chave || chave.length > 4096) return null;
        const database = await openDatabase();
        const entry = await transaction(database, 'readonly', (tx, done) => {
            const request = tx.objectStore('snapshots').get(scope + ':' + chave);
            request.onsuccess = () => done(request.result);
        }, null);
        if (!entry || entry.schema !== SCHEMA || entry.scope !== scope
            || !Array.isArray(entry.tags) || !entry.tags.length
            || entry.tags.some(tag => !['vendas', 'importacoes'].includes(tag))
            || !revisionMap(entry.revisions)
            || (entry.revocations !== undefined && !revisionMap(entry.revocations))
            || !Number.isFinite(entry.salvoEm) || entry.salvoEm > Date.now() + 60000
            || !dadosVigentes(context, entry.tags)
            || !sameRevisions(entry.revocations || {}, revisions(REVOCATION_KEY), entry.tags)) return null;
        const desatualizado = !sameRevisions(entry.revisions || {}, revisions(), entry.tags);
        // Legacy entries have no proof distinguishing old data from a manual/auth revocation.
        if (desatualizado && (options.permitirDesatualizado !== true || !entry.revocations
            || !dataOnlyChanges(entry.revisions, revisions(), entry.tags))) return null;
        return { dados: entry.dados, salvoEm: entry.salvoEm, desatualizado };
    }

    async function salvar(chave, dados, options = {}) {
        if (storageUnavailable) return false;
        const context = options.contexto || contexto();
        const tags = [...new Set((options.tags || []).filter(tag => ['vendas', 'importacoes'].includes(tag)))];
        const scope = await scopeKey(context);
        if (!scope || !tags.length || typeof chave !== 'string' || !chave || chave.length > 4096) return false;
        try {
            const serialized = JSON.stringify(dados);
            if (serialized === undefined) return false;
            const bytes = new global.TextEncoder().encode(serialized).byteLength;
            if (bytes > MAX_ENTRY_BYTES) return false;
            const database = await openDatabase();
            const captured = contexts.get(context);
            if (!dadosVigentes(context, tags)) return false;
            const entry = { id: scope + ':' + chave, scope, schema: SCHEMA, tags,
                revisions: captured.revisions, revocations: captured.revocations,
                salvoEm: Date.now(), bytes, dados: JSON.parse(serialized) };
            const saved = await transaction(database, 'readwrite', (tx, done) => {
                if (!dadosVigentes(context, tags)) return;
                tx.objectStore('snapshots').put(entry);
                const index = tx.objectStore('indices');
                const { dados: _data, ...metadata } = entry;
                index.put(metadata);
                // Retention scans only small metadata records, never every cached payload.
                const request = index.getAll();
                request.onsuccess = () => {
                    const rows = request.result.sort((a, b) => a.salvoEm - b.salvoEm);
                    let count = rows.length;
                    let total = rows.reduce((sum, row) => sum + row.bytes, 0);
                    let scoped = rows.filter(row => row.scope === scope).length;
                    let scopedBytes = rows.reduce((sum, row) => sum + (row.scope === scope ? row.bytes : 0), 0);
                    for (const row of rows) {
                        if (count <= MAX_ENTRIES && total <= MAX_BYTES
                            && (row.scope !== scope || (scoped <= MAX_SCOPE_ENTRIES && scopedBytes <= MAX_SCOPE_BYTES))) continue;
                        tx.objectStore('snapshots').delete(row.id);
                        index.delete(row.id);
                        count--; total -= row.bytes;
                        if (row.scope === scope) { scoped--; scopedBytes -= row.bytes; }
                    }
                    done(true);
                };
            }, false);
            return saved && dadosVigentes(context, tags);
        } catch (_error) { return false; }
    }

    async function invalidate(tags, origin = 'manual', receipt = null) {
        const selected = Array.isArray(tags) ? tags.filter(tag => ['vendas', 'importacoes'].includes(tag)) : ['*'];
        if (!selected.length) return true;
        const revoked = !['sync', 'mutation'].includes(origin);
        const nonce = (revoked ? '' : DATA_REVISION_PREFIX)
            + (global.crypto?.randomUUID?.() || Date.now().toString(36) + Math.random().toString(36));
        const before = revisions(), revocations = revisions(REVOCATION_KEY);
        if (receipt) {
            receipt.before = before;
            receipt.revocationsBefore = revocations;
            receipt.after = { ...before, ...Object.fromEntries(selected.map(tag => [tag, nonce])) };
        }
        let saved = true;
        let compatibilityRevocation = false;
        // Independent keys prevent concurrent updates of different families from rolling each other back.
        try {
            // Publish authorization fences first, so a partial write cannot permit a stale read.
            if (revoked) selected.forEach(tag => global.localStorage.setItem(REVOCATION_KEY + tag, nonce));
            else {
                if (!before || !revocations) throw new Error('Cache revisions unavailable');
                // An older tab cannot identify data-only invalidations. Retain its unknown fence
                // before overwriting freshness, or a later sync could revive manually revoked data.
                for (const tag of new Set(['*', ...selected])) {
                    if (before[tag] && !before[tag].startsWith(DATA_REVISION_PREFIX) && before[tag] !== revocations[tag]
                        && !legacyRevisionPromoted(before[tag], revocations[tag])) {
                        // Every promoter gets a unique fence: a delayed writer must never restore
                        // a value that authorized an entry revoked by another tab in the meantime.
                        const fence = LEGACY_REVOCATION_PREFIX + JSON.stringify([before[tag], nonce]);
                        global.localStorage.setItem(REVOCATION_KEY + tag, fence);
                        compatibilityRevocation = true;
                    }
                }
            }
            selected.forEach(tag => global.localStorage.setItem(REVISION_KEY + tag, nonce));
        }
        catch (_error) {
            saved = false;
            storageUnavailable = true;
            generation++;
            const database = await openDatabase();
            await transaction(database, 'readwrite', (tx, done) => {
                tx.objectStore('snapshots').clear(); tx.objectStore('indices').clear(); done(true);
            }, false);
        }
        if (receipt) receipt.revocationsAfter = revisions(REVOCATION_KEY);
        emit('jk:cache-persistente-invalidado', { tags: selected, origem: origin,
            revogado: revoked || compatibilityRevocation || !saved });
        return saved;
    }

    async function sessionDenied(response) {
        if (response.status !== 401) return false;
        try {
            const payload = await response.clone().json().catch(() => null);
            if (typeof global.respostaIndicaSessaoExpirada === 'function') {
                return global.respostaIndicaSessaoExpirada(response, payload);
            }
            return /bearer/i.test(response.headers?.get('WWW-Authenticate') || '')
                || /sess[aã]o expirada|token de autentica[cç][aã]o|login novamente/i.test(String(payload?.detail || ''));
        } catch (_error) { return false; }
    }

    function families(pathname, method) {
        if (/^\/api\/medias-compras(?:\/|$)/.test(pathname)) return ['importacoes'];
        if (/^\/api\/(?:vendas|notas-entrada|unidades-negocios)(?:\/|$)/.test(pathname)) return ['vendas', 'importacoes'];
        if (/^\/api\/cadastro\/foto(?:\/|$)/.test(pathname) && ['GET', 'HEAD'].includes(method)) return [];
        if (/^\/api\/(?:cadastro|lojas|impostos|impostos-ncm|ncm)(?:\/|$)/.test(pathname)) return ['vendas', 'importacoes'];
        return [];
    }

    if (typeof global.fetch === 'function') {
        const originalFetch = global.fetch.bind(global);
        global.fetch = async function fetchComInvalidacao(input, init) {
            const context = contexto();
            let tags = [];
            let method = 'GET';
            try {
                method = String(init?.method || input?.method || 'GET').toUpperCase();
                const source = typeof input === 'string' || input instanceof global.URL ? input : input.url;
                const url = new global.URL(source, global.location.href);
                if (url.origin === global.location.origin) tags = families(url.pathname, method);
            } catch (_error) {}
            const response = await originalFetch(input, init);
            if (tags.length && await sessionDenied(response)) {
                const captured = contexts.get(context);
                const current = identity();
                // The existing auth interceptor may already have removed the expired session.
                if (captured?.scope && ((!current && global.__jkSessionRedirecting) || current?.session === captured.session)) {
                    deniedSession = captured.session;
                    generation++;
                    await invalidate(['vendas', 'importacoes'], 'auth');
                    emit('jk:cache-persistente-auth-negado', { status: response.status });
                }
            } else if (tags.length && response.ok && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
                const captured = contexts.get(context);
                const receipt = { before: captured.revisions, session: captured.session };
                if (await invalidate(tags, 'mutation', receipt)) mutationReceipts.set(response, receipt);
            }
            return response;
        };
    }

    global.addEventListener?.('storage', event => {
        if (!event.key || ['access_token', 'user_data', 'permissions'].includes(event.key)) { generation++; deniedSession = ''; }
        if (event.key?.startsWith(REVISION_KEY) || event.key?.startsWith(REVOCATION_KEY)) {
            emit('jk:cache-persistente-invalidado', { tags: ['vendas', 'importacoes'], origem: 'storage',
                revogado: event.key.startsWith(REVOCATION_KEY) || !event.newValue?.startsWith(DATA_REVISION_PREFIX) });
        }
    });
    ['jk-auth-changed', 'jk-tenant-changed'].forEach(name => global.addEventListener?.(name, () => { generation++; deniedSession = ''; }));
    ['jk:vendas-sync-finished', 'jk:cadastro-operation-finished', 'jk-photo-policy-changed'].forEach(name => {
        global.addEventListener?.(name, () => { void invalidate(['vendas', 'importacoes'], 'sync'); });
    });
    global.addEventListener?.('jk:machine-sync-updated', event => {
        const scopes = event.detail?.received_scopes;
        if (!Array.isArray(scopes) || scopes.some(scope => /^(?:vendas|cadastro|lojas_integracoes|medias_compras|importacoes|notas_entrada)/.test(scope))) {
            void invalidate(['vendas', 'importacoes'], 'sync');
        }
    });
    global.JKCachePersistente = Object.freeze({ contexto, vigente, dadosVigentes, confirmarMutacao, ler, salvar,
        invalidar: tags => invalidate(tags) });
})(window);
