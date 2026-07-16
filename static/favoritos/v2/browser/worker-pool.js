(function (global) {
    'use strict';

    function erroCancelamentoFila() {
        const err = new Error('Fila de Favoritos cancelada.');
        err.name = 'AbortError';
        err.canceladoFavoritos = true;
        return err;
    }

    function dormir(ms) {
        return new Promise(resolve => setTimeout(resolve, Math.max(0, Number(ms) || 0)));
    }

    function snapshotEstado(estado) {
        return {
            total: estado.total,
            active: estado.active,
            queued: Math.max(0, estado.total - estado.dispatched),
            completed: estado.completed,
            succeeded: estado.succeeded,
            failed: estado.failed,
            retrying: estado.retrying,
            canceled: estado.canceled,
            fatal: estado.fatal
        };
    }

    async function executarFila(opcoes = {}) {
        const itens = Array.isArray(opcoes.items) ? opcoes.items.slice() : [];
        const concorrencia = Math.max(1, Math.min(4, Number(opcoes.concurrency) || 4, itens.length || 1));
        const workerIds = Array.isArray(opcoes.workerIds) && opcoes.workerIds.length
            ? opcoes.workerIds.slice(0, concorrencia)
            : Array.from({ length: concorrencia }, (_item, index) => `w${index + 1}`);
        const processar = opcoes.processItem;
        if (typeof processar !== 'function') throw new TypeError('processItem deve ser uma funcao.');
        const tentativasExtras = Math.max(0, Math.min(1, Number(opcoes.retryCount) || 0));
        const esperarRetry = typeof opcoes.wait === 'function' ? opcoes.wait : dormir;
        const aguardarLiberacao = typeof opcoes.waitUntilRunnable === 'function'
            ? opcoes.waitUntilRunnable
            : async () => {};
        const isFatal = typeof opcoes.isFatal === 'function' ? opcoes.isFatal : () => false;
        const onState = typeof opcoes.onState === 'function' ? opcoes.onState : () => {};
        const onFatal = typeof opcoes.onFatal === 'function' ? opcoes.onFatal : async () => {};
        const signal = opcoes.signal || null;
        const resultados = new Array(itens.length);
        const estado = {
            total: itens.length,
            active: 0,
            dispatched: 0,
            completed: 0,
            succeeded: 0,
            failed: 0,
            retrying: 0,
            canceled: false,
            fatal: false
        };
        let proximoIndice = 0;
        let erroFatal = null;

        const emitir = (evento, extra = {}) => onState({
            event: evento,
            ...snapshotEstado(estado),
            ...extra
        });
        const verificarCancelamento = () => {
            if (signal && signal.aborted) {
                estado.canceled = true;
                throw erroCancelamentoFila();
            }
        };
        const retirarProximo = () => {
            if (estado.fatal || estado.canceled || proximoIndice >= itens.length) return null;
            const index = proximoIndice;
            proximoIndice += 1;
            estado.dispatched = proximoIndice;
            return { index, item: itens[index] };
        };

        async function executarWorker(workerId) {
            while (!estado.fatal && !estado.canceled) {
                verificarCancelamento();
                await aguardarLiberacao({ workerId, state: snapshotEstado(estado) });
                verificarCancelamento();
                const tarefa = retirarProximo();
                if (!tarefa) return;
                const taskStartedAt = Date.now();
                estado.active += 1;
                emitir('started', { workerId, index: tarefa.index, item: tarefa.item, attempt: 1, taskStartedAt, taskElapsedMs: 0 });
                let concluida = false;
                try {
                    for (let attempt = 1; attempt <= tentativasExtras + 1; attempt += 1) {
                        verificarCancelamento();
                        if (attempt > 1) {
                            estado.retrying += 1;
                            emitir('retrying', { workerId, index: tarefa.index, item: tarefa.item, attempt, taskStartedAt, taskElapsedMs: Date.now() - taskStartedAt });
                        }
                        try {
                            const value = await processar(tarefa.item, {
                                workerId,
                                index: tarefa.index,
                                attempt,
                                taskStartedAt,
                                state: snapshotEstado(estado)
                            });
                            resultados[tarefa.index] = {
                                success: true,
                                value,
                                workerId,
                                attempt,
                                taskStartedAt,
                                taskElapsedMs: Date.now() - taskStartedAt
                            };
                            estado.succeeded += 1;
                            estado.completed += 1;
                            concluida = true;
                            emitir('completed', { workerId, index: tarefa.index, item: tarefa.item, attempt, value, taskStartedAt, taskElapsedMs: Date.now() - taskStartedAt, timings: value && value.timings || null });
                            break;
                        } catch (err) {
                            if (attempt > 1) estado.retrying = Math.max(0, estado.retrying - 1);
                            if ((signal && signal.aborted) || (err && (err.name === 'AbortError' || err.canceladoFavoritos))) {
                                estado.canceled = true;
                                throw err;
                            }
                            if (isFatal(err, { workerId, index: tarefa.index, item: tarefa.item, attempt })) {
                                estado.fatal = true;
                                erroFatal = err;
                                resultados[tarefa.index] = {
                                    success: false,
                                    fatal: true,
                                    error: err,
                                    workerId,
                                    attempt,
                                    taskStartedAt,
                                    taskElapsedMs: Date.now() - taskStartedAt
                                };
                                estado.failed += 1;
                                estado.completed += 1;
                                concluida = true;
                                emitir('fatal', { workerId, index: tarefa.index, item: tarefa.item, attempt, error: err, taskStartedAt, taskElapsedMs: Date.now() - taskStartedAt });
                                await onFatal(err, { workerId, index: tarefa.index, item: tarefa.item, attempt, state: snapshotEstado(estado) });
                                break;
                            }
                            if (attempt <= tentativasExtras) {
                                emitir('retry-wait', { workerId, index: tarefa.index, item: tarefa.item, attempt: attempt + 1, error: err, taskStartedAt, taskElapsedMs: Date.now() - taskStartedAt });
                                await esperarRetry(Number(opcoes.retryDelayMs) || 1000);
                                continue;
                            }
                            resultados[tarefa.index] = {
                                success: false,
                                fatal: false,
                                error: err,
                                workerId,
                                attempt,
                                taskStartedAt,
                                taskElapsedMs: Date.now() - taskStartedAt
                            };
                            estado.failed += 1;
                            estado.completed += 1;
                            concluida = true;
                            emitir('failed', { workerId, index: tarefa.index, item: tarefa.item, attempt, error: err, taskStartedAt, taskElapsedMs: Date.now() - taskStartedAt });
                        } finally {
                            if (attempt > 1 && estado.retrying > 0 && (!resultados[tarefa.index] || resultados[tarefa.index].attempt === attempt)) {
                                estado.retrying = Math.max(0, estado.retrying - 1);
                            }
                        }
                    }
                } finally {
                    estado.active = Math.max(0, estado.active - 1);
                    if (!concluida && estado.canceled) {
                        resultados[tarefa.index] = resultados[tarefa.index] || {
                            success: false,
                            canceled: true,
                            error: erroCancelamentoFila(),
                            workerId,
                            taskStartedAt,
                            taskElapsedMs: Date.now() - taskStartedAt
                        };
                    }
                    emitir('idle', { workerId, index: tarefa.index, item: tarefa.item, taskStartedAt, taskElapsedMs: Date.now() - taskStartedAt });
                }
            }
        }

        emitir('pool-started', { workerIds: workerIds.slice() });
        const saidas = await Promise.allSettled(workerIds.map(executarWorker));
        const rejeitada = saidas.find(saida => saida.status === 'rejected');
        if (erroFatal) {
            erroFatal.favoritosPoolFatal = true;
            erroFatal.favoritosPoolResult = { results: resultados, state: snapshotEstado(estado) };
            throw erroFatal;
        }
        if (rejeitada) throw rejeitada.reason;
        emitir('pool-completed', { workerIds: workerIds.slice() });
        return { results: resultados, state: snapshotEstado(estado), workerIds };
    }

    const namespace = global.FavoritosV2 = global.FavoritosV2 || {};
    namespace.browser = namespace.browser || {};
    namespace.browser.workerPool = {
        runQueue: executarFila,
        snapshotState: snapshotEstado
    };

    if (typeof module !== 'undefined' && module.exports) {
        module.exports = namespace.browser.workerPool;
    }
})(typeof window !== 'undefined' ? window : globalThis);
