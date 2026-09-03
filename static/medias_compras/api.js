(function installJKMediasApi(global) {
    'use strict';

    const app = global.JKMedias;
    app.defineModule('api', ['core', 'state'], ({ modules }) => {
        const { definirStatusInicial, parseJsonLocalStorage } = modules.core;
        const requisicoesMaisRecentes = new Map();

        if (typeof global.obterAuthHeaders !== 'function') {
            global.obterAuthHeaders = function obterAuthHeadersFallback(extra) {
                const token = localStorage.getItem('access_token') || '';
                const userData = parseJsonLocalStorage('user_data', null);
                const clientId = userData && userData.client_id ? userData.client_id : '';
                const headers = token ? { Authorization: 'Bearer ' + token } : (clientId ? { 'X-Client-ID': clientId } : {});
                if (extra && typeof extra === 'object') Object.assign(headers, extra);
                return headers;
            };
        }

        const sessaoOk = (typeof global.verificarSessao === 'function')
            ? global.verificarSessao()
            : !!(localStorage.getItem('access_token') || localStorage.getItem('user_data'));
        if (!sessaoOk) global.location.href = '/frontend_index.html';

        const permissions = parseJsonLocalStorage('permissions', {});
        const permissionsConhecidas = permissions && typeof permissions === 'object' && Object.keys(permissions).length > 0;
        if (permissionsConhecidas && !(permissions.full === true || permissions.medias_compras === true)) {
            global.alert('Acesso nao autorizado para Medias e Pedidos.');
            global.location.href = 'dashboard.html';
        }

        global.addEventListener('error', (event) => {
            definirStatusInicial('Erro ao iniciar a tela: ' + (event.message || 'verifique o console.'));
        });
        global.addEventListener('unhandledrejection', (event) => {
            const motivo = event && event.reason ? (event.reason.message || String(event.reason)) : 'erro inesperado';
            definirStatusInicial('Erro ao carregar dados: ' + motivo);
        });

        function request(input, init) {
            return global.fetch(input, init);
        }

        function cancelarRequisicao(chave) {
            const registro = requisicoesMaisRecentes.get(String(chave || ''));
            if (!registro) return;
            registro.controller.abort();
            requisicoesMaisRecentes.delete(registro.chave);
        }

        async function requestLatest(chave, input, init) {
            const chaveNormalizada = String(chave || '').trim();
            if (!chaveNormalizada || typeof global.AbortController !== 'function') {
                return request(input, init);
            }

            cancelarRequisicao(chaveNormalizada);
            const controller = new global.AbortController();
            const registro = { chave: chaveNormalizada, controller };
            requisicoesMaisRecentes.set(chaveNormalizada, registro);

            try {
                const response = await request(input, { ...(init || {}), signal: controller.signal });
                if (requisicoesMaisRecentes.get(chaveNormalizada) !== registro) {
                    const erro = new Error('Resposta substituida por uma requisicao mais recente.');
                    erro.name = 'AbortError';
                    throw erro;
                }
                return response;
            } finally {
                if (requisicoesMaisRecentes.get(chaveNormalizada) === registro) {
                    requisicoesMaisRecentes.delete(chaveNormalizada);
                }
            }
        }

        return {
            request,
            requestLatest,
            cancelarRequisicao,
            authHeaders: (extra) => global.obterAuthHeaders(extra),
        };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
