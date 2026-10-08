(function (root, factory) {
    'use strict';
    if (typeof module === 'object' && module.exports) module.exports = factory;
    else root.createPesquisaMercadoShellBridge = factory;
})(typeof window !== 'undefined' ? window : null, function createPesquisaMercadoShellBridge(window, context) {
    'use strict';
    const allowed = Object.freeze({
        start: 'startPesquisaMercadoJob', status: 'getPesquisaMercadoJobStatus', pause: 'pausePesquisaMercadoJob',
        resume: 'resumePesquisaMercadoJob', cancel: 'cancelPesquisaMercadoJob', browser: 'showPesquisaMercadoBrowser',
        categories: 'getPesquisaMercadoCategories', 'refresh-names': 'refreshPesquisaMercadoNames'
    });
    const subscribers = new Set();
    const appOrigin = new URL(context.appUrl).origin;
    let unsubscribe = null;
    function validAvantEmail(value) {
        return typeof value === 'string' && value.length <= 254 && value === value.trim()
            && /^(?=.{1,64}@)[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$/.test(value)
            && !value.startsWith('.') && !value.includes('..') && !value.includes('.@');
    }
    function categoryChoice(value) {
        if (!value || typeof value !== 'object' || Array.isArray(value)
            || Object.keys(value).some(key => !['key', 'nome', 'url'].includes(key))) return null;
        if (typeof value.key !== 'string' || !value.key.trim() || value.key.length > 2048
            || typeof value.nome !== 'string' || !value.nome.trim() || value.nome.length > 500
            || typeof value.url !== 'string' || value.url.length > 4096) return null;
        try {
            const url = new URL(value.url);
            if (url.protocol !== 'https:' || !/(^|\.)mercadolivre\.com\.br$/i.test(url.hostname)) return null;
            return { key: value.key.trim(), nome: value.nome.trim(), url: url.href };
        } catch (_error) { return null; }
    }
    function trustedTab(source, origin) {
        if (origin !== appOrigin) return null;
        const found = context.findTabBySource(source);
        if (!found || !found.tab) return null;
        try {
            const url = new URL(found.tab.url || found.tab.webview.src, context.appUrl);
            return url.origin === appOrigin && /^\/pesquisa_mercado\.html$/i.test(url.pathname) ? found.tab : null;
        } catch (_error) { return null; }
    }
    function reply(source, payload) {
        try { source.postMessage(payload, appOrigin); } catch (_error) {}
    }
    function subscribe(source) {
        subscribers.add(source);
        const api = context.getApi();
        if (unsubscribe || !api || typeof api.onPesquisaMercadoProgress !== 'function') return;
        unsubscribe = api.onPesquisaMercadoProgress(payload => {
            for (const target of subscribers) {
                if (!trustedTab(target, appOrigin)) { subscribers.delete(target); continue; }
                reply(target, { channel: 'jk-pesquisa-mercado-progress', payload });
            }
        });
    }
    async function onMessage(event) {
        const data = event && event.data;
        if (!data || data.channel !== 'jk-pesquisa-mercado-command') return;
        if (!trustedTab(event.source, event.origin)) return;
        const requestId = typeof data.requestId === 'string' ? data.requestId : '';
        if (!/^pm-\d+-\d+$/.test(requestId) || requestId.length > 80) return;
        const respond = result => reply(event.source, { channel: 'jk-pesquisa-mercado-result', requestId, result });
        if (data.action === 'subscribe') { subscribe(event.source); respond({ success: true }); return; }
        if (data.action === 'unsubscribe') { subscribers.delete(event.source); respond({ success: true }); return; }
        const method = Object.prototype.hasOwnProperty.call(allowed, data.action) ? allowed[data.action] : null;
        const api = context.getApi();
        if (!method || !api || typeof api[method] !== 'function') {
            reply(event.source, { channel: 'jk-pesquisa-mercado-result', requestId, error: 'Comando da Pesquisa de Mercado indisponível.' });
            return;
        }
        const input = data.payload;
        const fields = { start: ['authToken', 'category', 'avantLoginEmail'], categories: ['authToken', 'parentCategory', 'forceRefresh'],
            status: ['authToken'], pause: ['authToken', 'jobId'], resume: ['authToken', 'jobId', 'avantLoginEmail'],
            cancel: ['authToken', 'jobId'], 'refresh-names': ['authToken', 'jobId', 'avantLoginEmail'], browser: ['authToken', 'jobId', 'purpose'] };
        if (!input || typeof input !== 'object' || Array.isArray(input)
            || Object.keys(input).some(key => !fields[data.action].includes(key))) {
            reply(event.source, { channel: 'jk-pesquisa-mercado-result', requestId, error: 'Argumentos do comando da pesquisa inválidos.' });
            return;
        }
        if (!input || typeof input.authToken !== 'string' || !input.authToken || input.authToken.length > 8192) {
            reply(event.source, { channel: 'jk-pesquisa-mercado-result', requestId, error: 'Entre no JK Sistema para consultar a pesquisa.' });
            return;
        }
        const payload = { authToken: input.authToken };
        if (Object.prototype.hasOwnProperty.call(input, 'avantLoginEmail')) {
            if (!validAvantEmail(input.avantLoginEmail)) {
                reply(event.source, { channel: 'jk-pesquisa-mercado-result', requestId, error: 'E-mail do Avant Pro inválido.' });
                return;
            }
            payload.avantLoginEmail = input.avantLoginEmail;
        }
        if (data.action === 'categories' && Object.prototype.hasOwnProperty.call(input, 'forceRefresh')) {
            if (typeof input.forceRefresh !== 'boolean') {
                reply(event.source, { channel: 'jk-pesquisa-mercado-result', requestId, error: 'Atualização das categorias inválida.' });
                return;
            }
            payload.forceRefresh = input.forceRefresh;
        }
        if (data.action === 'start' || data.action === 'categories' && input.parentCategory != null) {
            const field = data.action === 'start' ? 'category' : 'parentCategory';
            const category = categoryChoice(input[field]);
            if (!category) {
                reply(event.source, { channel: 'jk-pesquisa-mercado-result', requestId, error: 'Escolha uma categoria válida do Mercado Livre.' });
                return;
            }
            payload[field] = category;
        }
        if (data.action === 'browser' && input.purpose != null) {
            if (!['categories', 'names'].includes(input.purpose)) {
                reply(event.source, { channel: 'jk-pesquisa-mercado-result', requestId, error: 'Finalidade do navegador inválida.' });
                return;
            }
            payload.purpose = input.purpose;
        }
        if (['pause', 'resume', 'cancel', 'browser', 'refresh-names'].includes(data.action) && input.jobId != null && payload.purpose !== 'categories') {
            if (typeof input.jobId !== 'string' || !input.jobId || input.jobId.length > 128) {
                reply(event.source, { channel: 'jk-pesquisa-mercado-result', requestId, error: 'Pesquisa inválida.' });
                return;
            }
            payload.jobId = input.jobId;
        }
        if ((['resume', 'cancel', 'refresh-names'].includes(data.action) || data.action === 'browser' && payload.purpose === 'names') && !payload.jobId) {
            reply(event.source, { channel: 'jk-pesquisa-mercado-result', requestId, error: 'Pesquisa inválida.' });
            return;
        }
        try { respond(await api[method](payload)); }
        catch (error) { reply(event.source, { channel: 'jk-pesquisa-mercado-result', requestId, error: error.message || 'Falha no comando da pesquisa.' }); }
    }
    window.addEventListener('message', onMessage);
    return () => {
        window.removeEventListener('message', onMessage);
        if (typeof unsubscribe === 'function') unsubscribe();
        subscribers.clear();
    };
});
