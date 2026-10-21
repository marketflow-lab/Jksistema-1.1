(function (global) {
    'use strict';

    if (global.JKCadastroListaCache) return;
    const KEY = 'jk.cadastro.lista.v1';
    const TTL_MS = 5 * 60 * 1000;
    const SUMMARY_FIELDS = new Set([
        'store_id', 'loja_sync', 'sku', 'sku_normalizado', 'scope_source', 'row_version', 'updated_at_utc',
        'foto', 'produto_bling', 'nome', 'produto', 'titulo_ml', 'ncm', 'ncm_validade', 'monofasico',
        'monofasico_confianca', 'cest', 'categoria', 'custo', 'preco', 'descricao', 'imposto', 'updated_at',
        'mlb_ids', 'titulos_anuncios_mlb', 'custos_frete_mlb',
    ]);
    let identidade = '';
    let cliente = '';
    let revisao = 0;

    function invalidar() {
        revisao += 1;
        try { global.sessionStorage.removeItem(KEY); } catch (_error) {}
    }

    async function preparar(clientId) {
        try {
            const token = global.localStorage.getItem('access_token') || '';
            const user = global.localStorage.getItem('user_data') || '';
            const bytes = new global.TextEncoder().encode(JSON.stringify([clientId, token, user]));
            const digest = await global.crypto.subtle.digest('SHA-256', bytes);
            identidade = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('');
            cliente = String(clientId || '');
            return Boolean(cliente);
        } catch (_error) {
            identidade = '';
            cliente = '';
            invalidar();
            return false;
        }
    }

    function idsLojas(lojas) {
        return lojas.map(loja => String(loja.store_id || '')).sort();
    }

    function lerEnvelope(clientId) {
        try {
            const value = JSON.parse(global.sessionStorage.getItem(KEY) || 'null');
            if (!identidade || cliente !== String(clientId || '') || !value || value.schema !== KEY
                || value.identidade !== identidade || value.client_id !== cliente
                || !Number.isFinite(value.atualizadoEm) || value.atualizadoEm > Date.now()
                || Date.now() - value.atualizadoEm >= TTL_MS || !Array.isArray(value.produtos)
                || !Array.isArray(value.lojas) || typeof value.store_id !== 'string'
                || value.produtos.some(item => !item || typeof item !== 'object' || Array.isArray(item)
                    || !String(item.sku || '').trim() || !value.lojas.includes(item.store_id)
                    || (value.store_id && item.store_id !== value.store_id))) return null;
            return value;
        } catch (_error) { return null; }
    }

    function escrever(value) {
        try {
            global.sessionStorage.setItem(KEY, JSON.stringify(value));
            return true;
        } catch (_error) {
            invalidar();
            return false;
        }
    }

    function ler(clientId, storeId, lojas) {
        const value = lerEnvelope(clientId);
        if (!value || value.store_id !== String(storeId || '')
            || JSON.stringify(value.lojas) !== JSON.stringify(idsLojas(lojas))) return null;
        return value;
    }

    function salvar(clientId, storeId, lojas, dados) {
        if (!identidade || cliente !== String(clientId || '')) return false;
        return escrever({ ...dados, schema: KEY, identidade, client_id: cliente,
            store_id: String(storeId || ''), lojas: idsLojas(lojas) });
    }

    function salvarVisual(clientId, storeId, visual) {
        const value = lerEnvelope(clientId);
        if (!value || value.store_id !== String(storeId || '')) return;
        escrever({ ...value, visual });
    }

    function atualizarProduto(clientId, storeId, skuAnterior, produto) {
        const value = lerEnvelope(clientId);
        if (!value) return;
        if (!produto || typeof produto !== 'object' || !String(produto.sku || '').trim()
            || String(produto.store_id || '') !== String(storeId || '')) {
            invalidar();
            return;
        }
        if (value.store_id && value.store_id !== storeId) return;
        const indice = value.produtos.findIndex(item => item.store_id === storeId
            && (item.sku === skuAnterior || (produto.sku_normalizado && item.sku_normalizado === produto.sku_normalizado)));
        if (indice < 0) { invalidar(); return; }
        // Keep the same summary projection as the list endpoint; editor-only fields are not table columns.
        value.produtos[indice] = Object.fromEntries(Object.entries(produto).filter(([campo]) => SUMMARY_FIELDS.has(campo)));
        escrever(value);
    }

    global.addEventListener?.('jk:machine-sync-updated', event => {
        const scopes = event.detail?.received_scopes;
        if (Array.isArray(scopes) && scopes.some(scope => ['cadastro', 'lojas_integracoes'].includes(scope))) invalidar();
    });
    global.addEventListener?.('storage', event => {
        if (!event.key || ['access_token', 'user_data'].includes(event.key)) {
            identidade = '';
            cliente = '';
            invalidar();
        }
    });
    global.JKCadastroListaCache = Object.freeze({ preparar, ler, salvar, salvarVisual, atualizarProduto,
        invalidar, geracao: () => revisao });
})(window);
