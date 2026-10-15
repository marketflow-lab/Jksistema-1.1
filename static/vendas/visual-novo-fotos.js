(function (global) {
    'use strict';
    if (global.JKVendasNovaFotos) return;

    const SELECTOR = 'img[data-vendas-foto],img[data-vn-photo-sku]';
    const PHOTO_HEADER = 'X-JK-Vendas-Fotos';
    const METADATA_TTL_MS = 120000;
    const FAILURE_TTL_MS = 15000;
    const contexts = new Map();
    const roots = new WeakMap();
    const activeRoots = new Set();
    let sequence = 0;

    function sessionClient() {
        try {
            const user = JSON.parse(global.localStorage.getItem('user_data') || 'null');
            return String(user && user.client_id || '').trim();
        } catch (_error) { return ''; }
    }
    function images(root) {
        const result = [];
        if (root && root.matches && root.matches(SELECTOR)) result.push(root);
        if (root && root.querySelectorAll) result.push(...root.querySelectorAll(SELECTOR));
        return result;
    }
    function skuKey(value) { return String(value || '').trim().toLocaleLowerCase('pt-BR'); }
    function contextOf(snapshot) {
        const filters = snapshot && snapshot.filters || {};
        const clientId = String(snapshot && snapshot.clientId || '').trim();
        const inicio = String(filters.inicio || '').trim();
        const fim = String(filters.fim || '').trim();
        const loja = String(filters.loja || '').trim();
        const unidade = String(filters.unidade || '').trim();
        if (!clientId || clientId !== sessionClient() || snapshot.loading
            || !/^\d{4}-\d{2}-\d{2}$/.test(inicio) || !/^\d{4}-\d{2}-\d{2}$/.test(fim)
            || !loja) return null;
        const storeId = String(snapshot.storeId || '').trim();
        const allStores = /^(?:__todas|todas|todas as lojas)$/i.test(loja);
        return { clientId, inicio, fim, loja, unidade, storeId, allStores,
            key: JSON.stringify([clientId, inicio, fim, loja, unidade, storeId]) };
    }
    function markMissing(img, status) {
        const media = global.JKAuthenticatedMedia;
        if (media && typeof media.revogarImagem === 'function') media.revogarImagem(img);
        img.removeAttribute('data-jk-auth-src');
        img.removeAttribute('src');
        img.removeAttribute('data-vendas-foto-owner');
        img.removeAttribute('data-vendas-foto-resolved-store-id');
        img.removeAttribute('data-vendas-foto-ambiguo');
        img.removeAttribute('title');
        img.setAttribute('data-vendas-foto-estado', status || 'missing');
        img.hidden = true;
        img.classList && img.classList.remove('jk-auth-image-error');
        const frame = img.closest && img.closest('.vn-photo');
        frame && frame.classList.remove('vn-photo-ready');
    }
    function tenantPhotoUrl(reference, clientId) {
        const raw = String(reference || '').trim();
        if (!raw || raw.length > 8192 || /[\\\u0000-\u001f\u007f]/.test(raw)
            || !/^(?:\/api\/cadastro\/foto\/|https?:\/\/)/i.test(raw)) return '';
        // Validate the original path before URL parsing can collapse dot segments.
        const path = raw.replace(/^https?:\/\/[^/?#]*/i, '').split(/[?#]/, 1)[0];
        let parts, url;
        try {
            parts = path.split('/').map(decodeURIComponent);
            url = new global.URL(raw, global.location.origin);
        } catch (_error) { return ''; }
        if (url.origin !== global.location.origin || !/^https?:$/.test(url.protocol) || url.username || url.password
            || parts.length !== 6 || parts[0] !== '' || parts[1].toLowerCase() !== 'api'
            || parts[2].toLowerCase() !== 'cadastro' || parts[3].toLowerCase() !== 'foto'
            || parts[4] !== clientId
            || parts.slice(4).some(part => !part || part === '.' || part === '..'
                || /[\\/:%\u0000-\u001f\u007f]/.test(part))
            || !/\.(?:png|jpe?g|gif|webp|bmp)$/i.test(parts[5])) return '';
        return `/api/cadastro/foto/${encodeURIComponent(clientId)}/${encodeURIComponent(parts[5])}?tenant_only=true`;
    }
    function photoUrl(reference, clientId, storeId, scope) {
        if (scope === 'tenant') return tenantPhotoUrl(reference, clientId);
        const media = global.JKAuthenticatedMedia;
        if (!media || typeof media.normalizarUrlFotoCadastro !== 'function') return '';
        const raw = String(reference || '').trim();
        if (!raw || raw.length > 8192 || /[\\\u0000-\u001f\u007f]/.test(raw)) return '';
        const normalized = media.normalizarUrlFotoCadastro(raw);
        if (!normalized) return '';
        let url;
        try { url = new global.URL(normalized, global.location.origin); }
        catch (_error) { return ''; }
        if (!/^https?:$/.test(url.protocol) || url.username || url.password) return '';
        const protectedPath = /^\/api\/cadastro\/(?:foto\/|foto-arquivo\/)/i.test(url.pathname);
        if (!protectedPath) return /^(?:https?:)?\/\//i.test(normalized) ? url.href : '';
        if (url.origin !== global.location.origin) return '';
        let parts;
        try { parts = url.pathname.split('/').filter(Boolean).map(decodeURIComponent); }
        catch (_error) { return ''; }
        if (parts[2].toLowerCase() === 'foto') {
            if (parts[3] !== clientId) return '';
            parts = parts.slice(4);
        } else parts = parts.slice(3);
        if (parts.length !== 3 || parts[0].toLowerCase() !== 'lojas'
            || parts.some(part => !part || part === '.' || part === '..' || /[\\/:\u0000-\u001f\u007f]/.test(part))
            || !/\.(?:png|jpe?g|gif|webp|bmp)$/i.test(parts[2])) return '';
        if (!/^sid-[a-f0-9]{64}$/.test(parts[1]) && parts[1] !== storeId) return '';
        return `/api/cadastro/foto/${encodeURIComponent(clientId)}/${parts.map(encodeURIComponent).join('/')}?store_id=${encodeURIComponent(storeId)}`;
    }
    function evictUnused() {
        for (const [key, entry] of contexts) {
            if (contexts.size <= 3) break;
            if (entry.owners.size) continue;
            entry.controller && entry.controller.abort();
            contexts.delete(key);
        }
    }
    function expired(entry) {
        if (entry.pending) return false;
        const ttl = entry.failed ? FAILURE_TTL_MS : METADATA_TTL_MS;
        return Date.now() - entry.completedAt >= ttl;
    }
    function metadataFor(context) {
        const existing = contexts.get(context.key);
        if (existing && !expired(existing)) return existing;
        if (existing) contexts.delete(context.key);
        const controller = typeof global.AbortController === 'function' ? new global.AbortController() : null;
        const entry = { controller, owners: new Set(), pending: true, photos: new Map(), failed: false, completedAt: 0, promise: null };
        contexts.set(context.key, entry);
        entry.promise = (async () => {
            if (typeof global.obterAuthHeaders !== 'function') throw new Error('Autenticação indisponível.');
            const auth = global.obterAuthHeaders();
            if (!auth || !Object.keys(auth).length) throw new Error('Autenticação indisponível.');
            const params = new global.URLSearchParams({ data_inicio: context.inicio, data_fim: context.fim, loja: context.loja });
            if (context.unidade && context.unidade !== '__todos') params.set('unidade_negocio', context.unidade);
            const response = await global.fetch(`/api/vendas/resumo?${params}`, {
                headers: { ...auth, [PHOTO_HEADER]: '1' },
                ...(controller ? { signal: controller.signal } : {}),
            });
            if (!response.ok) throw new Error('Metadados de fotos indisponíveis.');
            const payload = await response.json();
            if (!payload || payload.photo_client_id !== context.clientId || !Array.isArray(payload.rows)
                || sessionClient() !== context.clientId) throw new Error('Escopo de fotos inválido.');
            const storeIds = new Set();
            const photos = new Map();
            payload.rows.forEach(row => {
                if (!row || typeof row !== 'object') return;
                const key = skuKey(row.sku), storeId = String(row.store_id || '').trim();
                const scope = row.photo_scope === 'tenant' ? 'tenant' : 'store';
                const ambiguous = row.photo_ambiguous === true;
                if (!key || (!storeId && scope !== 'tenant' && !ambiguous)) return;
                if (storeId) storeIds.add(storeId);
                if (photos.has(key)) { photos.set(key, null); return; }
                photos.set(key, { storeId, scope, ambiguous,
                    storeName: typeof row.photo_store_name === 'string' ? row.photo_store_name.trim() : '',
                    reference: String(row.foto || '').trim() });
            });
            if ((!context.allStores && storeIds.size > 1) || (context.storeId && storeIds.size && !storeIds.has(context.storeId))) {
                throw new Error('Loja das fotos inválida.');
            }
            // Only display metadata survives this response; financial fields are discarded.
            entry.photos = photos;
        })().catch(() => { entry.failed = true; entry.photos.clear(); }).finally(() => {
            entry.pending = false;
            entry.completedAt = Date.now();
            entry.controller = null;
            evictUnused();
        });
        return entry;
    }
    function current(root, state) {
        return roots.get(root) === state && sessionClient() === state.context.clientId;
    }
    function observeErrors(root, state) {
        if (typeof global.MutationObserver !== 'function') return;
        state.observer = new global.MutationObserver(mutations => {
            if (!current(root, state)) return;
            mutations.forEach(mutation => {
                const img = mutation.target;
                if (img.matches && img.matches(SELECTOR) && img.classList.contains('jk-auth-image-error')) markMissing(img);
            });
        });
        state.observer.observe(root, { attributes: true, subtree: true, attributeFilter: ['class'] });
    }
    function paint(root, state) {
        if (!current(root, state)) return;
        const media = global.JKAuthenticatedMedia;
        images(root).forEach(img => {
            const sku = img.getAttribute('data-vendas-foto') || img.getAttribute('data-vn-photo-sku');
            const record = state.entry.photos.get(skuKey(sku));
            const declaredStore = String(img.getAttribute('data-vendas-foto-store-id') || '').trim();
            const url = record && (!declaredStore || record.storeId === declaredStore)
                ? photoUrl(record.reference, state.context.clientId, record.storeId, record.scope) : '';
            if (!url || !media || typeof media.hidratar !== 'function') {
                markMissing(img);
                if (!url && record?.ambiguous) img.setAttribute('data-vendas-foto-ambiguo', 'true');
                return;
            }
            if (img.getAttribute('data-jk-auth-src') === url && img.getAttribute('data-vendas-foto-owner') === String(state.epoch)) return;
            markMissing(img, 'loading');
            img.hidden = false;
            img.setAttribute('data-vendas-foto-owner', String(state.epoch));
            img.setAttribute('data-vendas-foto-resolved-store-id', record.storeId);
            img.setAttribute('data-jk-auth-src', url);
            if (record.scope === 'store' && record.storeName) img.setAttribute('title', `Foto cadastrada em ${record.storeName}`);
            if (!state.listeners.has(img)) {
                const loaded = () => {
                    if (!current(root, state) || img.getAttribute('data-vendas-foto-owner') !== String(state.epoch)) return;
                    img.hidden = false;
                    img.setAttribute('data-vendas-foto-estado', 'ready');
                    const frame = img.closest && img.closest('.vn-photo');
                    frame && frame.classList.add('vn-photo-ready');
                };
                const failed = () => { if (current(root, state)) markMissing(img); };
                img.addEventListener('load', loaded);
                img.addEventListener('error', failed);
                state.listeners.set(img, { loaded, failed });
            }
            media.hidratar(img);
        });
    }
    function release(root) {
        const state = root && roots.get(root);
        if (state) {
            roots.delete(root);
            activeRoots.delete(root);
            state.observer && state.observer.disconnect();
            state.listeners.forEach((listeners, img) => {
                img.removeEventListener('load', listeners.loaded);
                img.removeEventListener('error', listeners.failed);
            });
            state.entry.owners.delete(root);
            if (!state.entry.owners.size && state.entry.pending) {
                state.entry.controller && state.entry.controller.abort();
                contexts.delete(state.context.key);
            }
        }
        const media = global.JKAuthenticatedMedia;
        if (root && media && typeof media.liberar === 'function') media.liberar(root);
        images(root).forEach(img => markMissing(img, 'idle'));
        evictUnused();
    }
    function hydrate(root, snapshot) {
        const context = contextOf(snapshot);
        if (!root) return Promise.resolve();
        if (!images(root).length) { release(root); return Promise.resolve(); }
        if (!context) { release(root); return Promise.resolve(); }
        let state = roots.get(root);
        if (state && expired(state.entry)) { release(root); state = null; }
        if (!state || state.context.key !== context.key) {
            release(root);
            const entry = metadataFor(context);
            state = { context, entry, epoch: ++sequence, listeners: new Map(), observer: null };
            roots.set(root, state);
            activeRoots.add(root);
            entry.owners.add(root);
            observeErrors(root, state);
        }
        const owned = state;
        return state.entry.promise.then(() => paint(root, owned));
    }
    global.JKVendasNovaFotos = Object.freeze({ hydrate, release });
    global.addEventListener && global.addEventListener('beforeunload', () => {
        Array.from(activeRoots).forEach(release);
        contexts.forEach(entry => entry.controller && entry.controller.abort());
        contexts.clear();
    });
})(window);
