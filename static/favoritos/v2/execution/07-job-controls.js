// Extracted from 07-execucao-render-layout.js lines 2217-2393.
        async function enviarComandoFavoritosJobAtual(acao) {
            if (!mlFavoritosJobIdAtual) return null;
            const status = await fetchFavoritosJob(`/api/favoritos/jobs/${encodeURIComponent(mlFavoritosJobIdAtual)}/${acao}`, {
                method: 'POST'
            });
            receberStatusFavoritosJob(status);
            return status;
        }

        async function cancelarFavoritosJobAtualServidor() {
            const jobId = String(mlFavoritosJobIdAtual || '').trim();
            if (!jobId) return null;
            return fetchFavoritosJob(`/api/favoritos/jobs/${encodeURIComponent(jobId)}/cancel`, {
                method: 'POST'
            });
        }

        async function pausarFavoritosJobAtual() {
            if (!mlFavoritosEmExecucao) return;
            if (!mlFavoritosJobIdAtual) {
                mlFavoritosPausado = true;
                const api = obterElectronApiFavoritosExecucao();
                if (window.__JK_FAVORITOS_WORKERS_POOL_ACTIVE && api && typeof api.pauseFavoritosWorkersPool === 'function') {
                    await api.pauseFavoritosWorkersPool().catch(() => null);
                } else if (api && typeof api.pauseFavoritosWorkerBrowser === 'function') {
                    await api.pauseFavoritosWorkerBrowser().catch(() => null);
                }
                mlFavoritosJobUltimoStatus = { status: 'paused', mensagem: 'Favoritos pausado. Clique em retomar para continuar.' };
                atualizarFiltroAzulFavoritos();
                atualizarContadorSkuSidebarSelecionados();
                window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Favoritos pausado. A coleta continua a partir da proxima etapa ao retomar.', {
                    larga: true
                });
                return;
            }
            window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Pausando favoritos ao final da etapa atual...');
            try {
                await enviarComandoFavoritosJobAtual('pause');
            } catch (err) {
                window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Nao foi possivel pausar: ${err && err.message ? err.message : err}`, {
                    erro: true,
                    tempoMs: 4500
                });
            }
        }

        async function retomarFavoritosJobAtual() {
            if (!mlFavoritosEmExecucao) return;
            if (!mlFavoritosJobIdAtual) {
                mlFavoritosPausado = false;
                const api = obterElectronApiFavoritosExecucao();
                if (window.__JK_FAVORITOS_WORKERS_POOL_ACTIVE && api && typeof api.resumeFavoritosWorkersPool === 'function') {
                    await api.resumeFavoritosWorkersPool().catch(() => null);
                } else if (api && typeof api.resumeFavoritosWorkerBrowser === 'function') {
                    await api.resumeFavoritosWorkerBrowser().catch(() => null);
                }
                mlFavoritosJobUltimoStatus = { status: 'running', mensagem: 'Favoritos retomado.' };
                atualizarFiltroAzulFavoritos();
                atualizarContadorSkuSidebarSelecionados();
                window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Favoritos retomado.');
                return;
            }
            window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Retomando favoritos...');
            try {
                await enviarComandoFavoritosJobAtual('resume');
            } catch (err) {
                window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Nao foi possivel retomar: ${err && err.message ? err.message : err}`, {
                    erro: true,
                    tempoMs: 4500
                });
            }
        }

        async function prepararNavegadorFavoritosBackground(urlInicial = ML_DEFAULT_URL) {
            const api = obterElectronApiFavoritosExecucao();
            try {
                const urlWorker = String(urlInicial || ML_DEFAULT_URL || '').trim() || ML_DEFAULT_URL;
                window.__JK_FAVORITOS_WORKER_BROWSER_ACTIVE = true;
                emitirEstadoWorkerFavoritosExecucao('jk-favoritos-worker-enable', {
                    active: true,
                    cancelRequested: false,
                    startedAt: Number(mlFavoritosExecucaoIniciadaEmMs) || Date.now(),
                    status: 'running',
                    message: 'Favoritos rodando em segundo plano.'
                });
                if (typeof forcarProxyNavegadorFavoritosWorker === 'function') {
                    forcarProxyNavegadorFavoritosWorker(urlWorker);
                }
                if (!api) return;
                if (typeof api.startFavoritosWorkerBrowser === 'function') {
                    await api.startFavoritosWorkerBrowser(urlWorker);
                    if (typeof forcarProxyNavegadorFavoritosWorker === 'function') {
                        forcarProxyNavegadorFavoritosWorker(urlWorker);
                    }
                    return;
                }
                if (typeof api.startFavoritosJobBrowserBackground === 'function') {
                    await api.startFavoritosJobBrowserBackground(urlWorker);
                    return;
                }
            } catch (err) {
                console.warn('Nao foi possivel manter navegador ML em background:', err);
            }
        }

        function pararNavegadorFavoritosBackground(opcoes = {}) {
            const api = obterElectronApiFavoritosExecucao();
            const status = String(opcoes.status || 'stopped').toLowerCase();
            const message = Object.prototype.hasOwnProperty.call(opcoes, 'message')
                ? String(opcoes.message || '')
                : 'Favoritos finalizado.';
            const reason = String(opcoes.reason || 'favoritos-background-stop');
            const finishedAt = Number(opcoes.finishedAt) || 0;
            try {
                window.__JK_FAVORITOS_WORKER_BROWSER_ACTIVE = false;
                emitirEstadoWorkerFavoritosExecucao('jk-favoritos-worker-done', {
                    active: false,
                    status,
                    message,
                    finishedAt
                });
                if (!api) return;
                if (window.__JK_FAVORITOS_WORKERS_POOL_ACTIVE && typeof api.stopFavoritosWorkersPool === 'function') {
                    window.__JK_FAVORITOS_WORKERS_POOL_ACTIVE = false;
                    api.stopFavoritosWorkersPool({
                        destroy: true,
                        reason,
                        status,
                        message,
                        finishedAt
                    }).catch(() => {});
                    return;
                }
                if (typeof api.stopFavoritosWorkerBrowser === 'function') {
                    api.stopFavoritosWorkerBrowser({
                        destroy: true,
                        reason,
                        status,
                        message,
                        finishedAt
                    }).catch(() => {});
                    return;
                }
                if (typeof api.stopFavoritosJobBrowserBackground === 'function') {
                    api.stopFavoritosJobBrowserBackground().catch(() => {});
                    return;
                }
                if (typeof api.hideEmbeddedMlBrowser === 'function') {
                    api.hideEmbeddedMlBrowser({ destroy: true, reason }).catch(() => {});
                }
            } catch (_err) {}
        }

        window.addEventListener('message', (event) => {
            const origemConhecida = event && (
                event.source === window
                || event.source === window.parent
                || event.source === window.top
            );
            const origemCompativel = !event.origin
                || event.origin === 'null'
                || event.origin === window.location.origin;
            if (!origemConhecida || !origemCompativel) return;
            const data = event && event.data ? event.data : {};
            if (!data || data.channel !== 'jk-favoritos-worker-action') return;
            const action = String(data.action || data.payload && data.payload.action || '').toLowerCase();
            if (!action) return;
            if (action === 'pause') {
                pausarFavoritosJobAtual();
            } else if (action === 'resume') {
                retomarFavoritosJobAtual();
            } else if (action === 'cancel') {
                if (typeof window.FavoritosV2.searchRanking.publicApi.control.cancelarFavoritosEmExecucao === 'function') window.FavoritosV2.searchRanking.publicApi.control.cancelarFavoritosEmExecucao();
                else mlFavoritosCancelado = true;
            }
        });
