(function installJKMediasResultados(global) {
    'use strict';

    const app = global.JKMedias;
    app.defineModule('resultados', ['core', 'state', 'api'], ({ modules }) => {
        const temporarios = new Set(['stores_snapshot_initializing', 'stores_snapshot_unavailable', 'stores_snapshot_credentials_unavailable', 'cache_capacity']);
        const copiarResultado = value => JSON.parse(JSON.stringify(value));

        function identidadeSessao(headers = modules.api.authHeaders?.() || {}) {
            return JSON.stringify([
                Object.keys(headers).sort().map(key => [key.toLowerCase(), String(headers[key])]),
                ...['access_token', 'user_data', 'permissions'].map(key => global.localStorage?.getItem(key) || ''),
            ]);
        }

        function exigirSessao(identidade) {
            if (identidade === identidadeSessao()) return;
            const erro = new Error('A sessão mudou. Reabra a tela para carregar os dados atuais.');
            erro.name = 'SessionChangedError';
            erro.status = 401;
            throw erro;
        }

        function aoMudarSessao(callback) {
            global.addEventListener('storage', event => {
                if (!event.key || ['access_token', 'user_data', 'permissions'].includes(event.key)) callback();
            });
            global.addEventListener('jk-auth-changed', callback);
        }

        function aguardar(ms, signal) {
            return new Promise((resolve, reject) => {
                const cancelar = () => { global.clearTimeout(timer); reject(new DOMException('Consulta cancelada.', 'AbortError')); };
                const timer = global.setTimeout(() => { signal?.removeEventListener('abort', cancelar); resolve(); }, ms);
                signal?.addEventListener('abort', cancelar, { once: true });
                if (signal?.aborted) cancelar();
            });
        }

        function intervaloConsulta(decorrido) {
            return decorrido < 5000 ? 1000 : decorrido < 10000 ? 2000 : 5000;
        }

        async function consultarResultado(url, headers, signal, prazo) {
            const sessao = identidadeSessao(headers);
            exigirSessao(sessao);
            const controller = new global.AbortController();
            const cancelar = () => controller.abort();
            signal?.addEventListener('abort', cancelar, { once: true });
            if (signal?.aborted) cancelar();
            let timer;
            try {
                const limite = new Promise((_, reject) => {
                    timer = global.setTimeout(() => {
                        controller.abort();
                        const erro = new Error('A atualização continua pendente.');
                        erro.name = 'TimeoutError';
                        reject(erro);
                    }, Math.max(1, prazo));
                });
                const resposta = await Promise.race([limite, (async () => {
                    const resp = await modules.api.request(url, { method: 'GET', headers, signal: controller.signal });
                    return { resp, data: await resp.json() };
                })()]);
                exigirSessao(sessao);
                if (resposta.resp.ok && resposta.data?._cache?.politica_fotos) {
                    global.JKAuthenticatedMedia?.atualizarPoliticaRaiz?.(global.document, resposta.data._cache.politica_fotos);
                }
                return resposta;
            } finally {
                global.clearTimeout(timer);
                signal?.removeEventListener('abort', cancelar);
            }
        }

        function textoEstadoResultado(meta, pendente = false) {
            const data = meta?.gerado_em ? new Date(meta.gerado_em) : null;
            const horario = data && Number.isFinite(data.getTime()) ? data.toLocaleString('pt-BR') : '';
            const anterior = horario ? 'Dados de ' + horario + '. ' : '';
            if (meta?.erro) return anterior + 'A atualização não terminou. Tente novamente.';
            if (pendente) return anterior + 'Atualização pendente. Tente novamente para acompanhar.';
            if (meta?.stale || meta?.atualizando || meta?.estado === 'waiting') return anterior + 'Atualizando dados...';
            return horario ? 'Dados atualizados em ' + horario + '.' : '';
        }

        async function acompanharResultado(url, { headers, signal, ativa = () => true, receber = () => {}, indisponivel = () => {}, estado = () => {}, limiteMs = 30000, sessao = identidadeSessao(headers) } = {}) {
            const inicio = Date.now();
            const separador = url.includes('?') ? '&' : '?';
            const consulta = url + separador + 'cache_mode=stale';
            let ultimaVersao;
            let ultimoMeta = null;
            let ultimoPayload = null;
            while (ativa() && !signal?.aborted) {
                exigirSessao(sessao);
                let resposta;
                try {
                    resposta = await consultarResultado(consulta, headers, signal, limiteMs - (Date.now() - inicio));
                } catch (erro) {
                    if (erro.name !== 'TimeoutError') throw erro;
                    if (ativa() && !signal?.aborted) estado(ultimoMeta, true);
                    return { atual: false, pendente: true, dados: ultimoPayload, meta: ultimoMeta };
                }
                const { resp, data } = resposta;
                exigirSessao(sessao);
                if (!ativa() || signal?.aborted) return { atual: false, cancelada: true };
                if (!resp.ok) {
                    const detail = data?.detail;
                    const transitorio = resp.status === 503 && temporarios.has(detail?.code);
                    if (!transitorio) {
                        const erro = new Error(typeof detail === 'string' ? detail : detail?.message || 'Falha ao atualizar os dados.');
                        erro.status = resp.status;
                        throw erro;
                    }
                    ultimoMeta = { estado: 'waiting', stale: !!ultimoPayload, atualizando: true };
                } else {
                    ultimoMeta = data._cache || { estado: 'ready', stale: false, atualizando: false };
                    const completo = resp.status !== 202 && data.dados !== null;
                    if (completo) {
                        const versao = ultimoMeta.versao;
                        ultimoPayload = data;
                        if (versao == null || versao !== ultimaVersao) {
                            ultimaVersao = versao;
                            receber(copiarResultado(data), ultimoMeta);
                        }
                    } else {
                        const autoridadeAnterior = ultimoPayload?._cache?.autoridade;
                        if (ultimoMeta.pendente_fontes || (autoridadeAnterior && ultimoMeta.autoridade && autoridadeAnterior !== ultimoMeta.autoridade)) {
                            ultimoPayload = null;
                            ultimaVersao = undefined;
                        }
                        indisponivel(ultimoMeta);
                    }
                    const atual = completo && ultimoMeta.completo !== false && !ultimoMeta.stale && !ultimoMeta.atualizando && ultimoMeta.estado === 'ready';
                    estado(ultimoMeta, false);
                    if (atual) return { atual: true, dados: copiarResultado(data), meta: ultimoMeta };
                    if (ultimoMeta.erro && !ultimoMeta.atualizando) return { atual: false, dados: ultimoPayload, meta: ultimoMeta };
                }
                const decorrido = Date.now() - inicio;
                if (decorrido >= limiteMs) {
                    estado(ultimoMeta, true);
                    return { atual: false, pendente: true, dados: ultimoPayload, meta: ultimoMeta };
                }
                estado(ultimoMeta, false);
                await aguardar(Math.min(intervaloConsulta(decorrido), limiteMs - decorrido), signal);
            }
            return { atual: false, cancelada: true };
        }

        return { acompanharResultado, textoEstadoResultado, copiarResultado, intervaloConsulta, consultarResultado, identidadeSessao, exigirSessao, aoMudarSessao };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
