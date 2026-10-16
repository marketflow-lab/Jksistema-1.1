(function instalarCacheMidiaJK(global) {
    'use strict';
    if (global.JKAuthenticatedMediaCache) return;
    const MAX_ENTRADAS = 128;
    const MAX_BYTES = 32 * 1024 * 1024;
    const MAX_FOTO = 4 * 1024 * 1024;
    const TTL = 60000;
    const CONCORRENCIA = 4;

    function criar({ aoNegarAcesso, aoRevogarChave } = {}) {
        const blobs = new Map();
        const pendentes = new Map();
        const fila = [];
        let bytes = 0;
        let downloads = 0;

        function abortado() {
            const error = new Error('Carregamento de imagem cancelado.');
            error.name = 'AbortError';
            return error;
        }

        function removerBlob(chave) {
            const item = blobs.get(chave);
            if (item) bytes -= item.blob.size;
            blobs.delete(chave);
        }

        function expirar() {
            const agora = global.Date.now();
            for (const [chave, item] of blobs) {
                if (item.expira <= agora) removerBlob(chave);
            }
        }

        function reter(chave, blob, expira) {
            if (!Number.isFinite(blob.size) || blob.size < 0 || blob.size > MAX_FOTO) return;
            expirar();
            removerBlob(chave);
            blobs.set(chave, { blob, expira });
            bytes += blob.size;
            while (blobs.size > MAX_ENTRADAS || bytes > MAX_BYTES) {
                removerBlob(blobs.keys().next().value);
            }
        }

        function cancelar(item) {
            if (item.concluido || item.cancelado || item.consumidores) return;
            item.cancelado = true;
            if (pendentes.get(item.chave) === item) pendentes.delete(item.chave);
            if (item.controller) item.controller.abort();
            item.rejeitar(abortado());
        }

        function invalidar() {
            blobs.clear();
            bytes = 0;
            for (const item of pendentes.values()) {
                item.reter = false;
                cancelar(item);
            }
            pendentes.clear();
        }

        async function baixar(item) {
            try {
                const response = await global.fetch(item.fonte, {
                    headers: item.headers,
                    ...(item.controller ? { signal: item.controller.signal } : {}),
                });
                if (item.cancelado) throw abortado();
                if (response.status === 401 || response.status === 403) {
                    invalidar();
                    if (typeof aoNegarAcesso === 'function') aoNegarAcesso();
                }
                if (response.status === 404) {
                    removerBlob(item.chave);
                    item.reter = false;
                    if (typeof aoRevogarChave === 'function') aoRevogarChave(item.chave);
                }
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                const blob = await response.blob();
                if (item.cancelado) throw abortado();
                if (!blob || (blob.type && !String(blob.type).toLowerCase().startsWith('image/'))) {
                    throw new Error('Resposta de foto invalida.');
                }
                item.expira = global.Date.now() + TTL;
                if (item.reter && item.consumidores && pendentes.get(item.chave) === item) reter(item.chave, blob, item.expira);
                item.concluido = true;
                item.resolver(blob);
            } catch (error) {
                item.rejeitar(error);
            } finally {
                item.concluido = true;
                if (pendentes.get(item.chave) === item) pendentes.delete(item.chave);
                downloads -= 1;
                drenar();
            }
        }

        function drenar() {
            while (downloads < CONCORRENCIA && fila.length) {
                const item = fila.shift();
                if (item.cancelado || !item.consumidores) continue;
                downloads += 1;
                baixar(item);
            }
        }

        function adquirir(chave, fonte, headers) {
            expirar();
            const salvo = blobs.get(chave);
            if (salvo) {
                blobs.delete(chave);
                blobs.set(chave, salvo);
                return { promise: Promise.resolve(salvo.blob), valido: () => global.Date.now() < salvo.expira, venceEm: () => salvo.expira, liberar() {} };
            }
            let item = pendentes.get(chave);
            if (!item) {
                item = {
                    chave, fonte, headers: { ...headers }, consumidores: 0,
                    reter: true, cancelado: false, concluido: false,
                    controller: typeof global.AbortController === 'function' ? new global.AbortController() : null,
                };
                item.promise = new Promise((resolve, reject) => { item.resolver = resolve; item.rejeitar = reject; });
                item.promise.catch(() => {});
                pendentes.set(chave, item);
                fila.push(item);
            }
            item.consumidores += 1;
            let liberado = false;
            drenar();
            return {
                promise: item.promise,
                valido: () => !item.cancelado && (!item.concluido || global.Date.now() < item.expira),
                venceEm: () => item.expira,
                liberar() {
                    if (liberado) return;
                    liberado = true;
                    item.consumidores -= 1;
                    cancelar(item);
                },
            };
        }
        return Object.freeze({ adquirir, invalidar });
    }
    global.JKAuthenticatedMediaCache = Object.freeze({ criar });
})(window);
