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
            return decorrido < 5000 ? 1000 : decorrido < 10000 ? 2000 : decorrido < 30000 ? 5000 : decorrido < 120000 ? 10000 : 15000;
        }

        async function consultarResultado(url, headers, signal, prazo) {
            const sessao = identidadeSessao(headers);
            exigirSessao(sessao);
            if (signal?.aborted) throw new DOMException('Consulta cancelada.', 'AbortError');
            const controller = new global.AbortController();
            let timer, rejeitarLimite;
            const cancelar = () => {
                rejeitarLimite(new DOMException('Consulta cancelada.', 'AbortError'));
                controller.abort();
            };
            try {
                const limite = new Promise((_, reject) => {
                    rejeitarLimite = reject;
                    timer = global.setTimeout(() => {
                        const erro = new Error('A atualização continua pendente.');
                        erro.name = 'TimeoutError';
                        reject(erro);
                        controller.abort();
                    }, Math.max(1, prazo));
                });
                signal?.addEventListener('abort', cancelar, { once: true });
                if (signal?.aborted) cancelar();
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

        function textoEstadoResultado(meta, pendente = false, acompanhando = false) {
            const data = meta?.gerado_em ? new Date(meta.gerado_em) : null;
            const horario = data && Number.isFinite(data.getTime()) ? data.toLocaleString('pt-BR') : '';
            const anterior = horario ? 'Dados de ' + horario + '. ' : '';
            if (pendente && acompanhando) return anterior + 'O cálculo está demorando. Continuamos acompanhando o resultado...';
            if (meta?.erro) return anterior + 'A atualização não terminou. Tente novamente.';
            if (pendente) return anterior + 'Atualização pendente. Tente novamente para acompanhar.';
            if (meta?.stale || meta?.atualizando || meta?.estado === 'waiting') return anterior + 'Atualizando dados...';
            return horario ? 'Dados atualizados em ' + horario + '.' : '';
        }

        async function acompanharResultado(url, { headers, signal, ativa = () => true, receber = () => {}, indisponivel = () => {}, estado = () => {}, limiteMs = 30000, avisoAposMs = limiteMs, limiteRequisicaoMs = limiteMs, recuperarFontes = false, sessao = identidadeSessao(headers) } = {}) {
            const inicio = Date.now();
            const separador = url.includes('?') ? '&' : '?';
            const consulta = url + separador + 'cache_mode=stale';
            let ultimaVersao;
            let ultimoMeta = null;
            let ultimoPayload = null;
            const emitirEstado = (final = false) => {
                const demorando = Date.now() - inicio >= avisoAposMs;
                estado(ultimoMeta, final || demorando, !final && demorando);
            };
            const pendente = () => {
                emitirEstado(true);
                return { atual: false, pendente: true, dados: ultimoPayload, meta: ultimoMeta };
            };
            const timerAviso = avisoAposMs < limiteMs ? global.setTimeout(() => {
                if (ativa() && !signal?.aborted && sessao === identidadeSessao()) emitirEstado();
            }, avisoAposMs) : null;
            try {
                while (ativa() && !signal?.aborted) {
                    exigirSessao(sessao);
                    let resposta;
                    let esperaMinima = 0;
                    try {
                        resposta = await consultarResultado(consulta, headers, signal, Math.min(limiteRequisicaoMs, limiteMs - (Date.now() - inicio)));
                    } catch (erro) {
                        if (erro.name !== 'TimeoutError') throw erro;
                        if (!ativa() || signal?.aborted) return { atual: false, cancelada: true };
                        if (Date.now() - inicio >= limiteMs) return pendente();
                    }
                    if (resposta) {
                        const { resp, data } = resposta;
                        exigirSessao(sessao);
                        if (!ativa() || signal?.aborted) return { atual: false, cancelada: true };
                        if (!resp.ok) {
                            const detail = data?.detail;
                            const transitorio = (resp.status === 503 && temporarios.has(detail?.code)) ||
                                (recuperarFontes && resp.status === 409 && detail?.code === 'sources_changing');
                            if (!transitorio) {
                                const erro = new Error(typeof detail === 'string' ? detail : detail?.message || 'Falha ao atualizar os dados.');
                                erro.status = resp.status;
                                throw erro;
                            }
                            if (detail?.code === 'sources_changing') esperaMinima = 5000;
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
                            const fontesMudando = recuperarFontes && ultimoMeta.erro?.code === 'sources_changing';
                            if (fontesMudando) {
                                esperaMinima = Math.max(5000, Math.min(15000, Number(ultimoMeta.retry_after_ms) || 0));
                                ultimoMeta = { ...ultimoMeta, erro: null, estado: 'waiting', atualizando: true };
                            }
                            if (atual || (ultimoMeta.erro && !ultimoMeta.atualizando)) {
                                estado(ultimoMeta, false, false);
                                return { atual, dados: atual ? copiarResultado(data) : ultimoPayload, meta: ultimoMeta };
                            }
                        }
                    }
                    const decorrido = Date.now() - inicio;
                    if (decorrido >= limiteMs) return pendente();
                    emitirEstado();
                    await aguardar(Math.min(Math.max(intervaloConsulta(decorrido), esperaMinima), limiteMs - decorrido), signal);
                }
                return { atual: false, cancelada: true };
            } finally {
                global.clearTimeout(timerAviso);
            }
        }

        return { acompanharResultado, textoEstadoResultado, copiarResultado, intervaloConsulta, consultarResultado, identidadeSessao, exigirSessao, aoMudarSessao };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
