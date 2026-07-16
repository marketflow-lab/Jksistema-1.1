(function () {
    'use strict';

    const raiz = window.FavoritosV2 = window.FavoritosV2 || {};
    const ui = raiz.ui = raiz.ui || {};
    const statusModal = ui.statusModal = ui.statusModal || {};
    if (statusModal.__loaded) {
        window.mostrarBalaoFavoritosStatus = statusModal.mostrarBalaoFavoritosStatus || window.mostrarBalaoFavoritosStatus;
        window.esconderBalaoFavoritosStatus = statusModal.esconderBalaoFavoritosStatus || window.esconderBalaoFavoritosStatus;
        window.posicionarBalaoFavoritosStatus = statusModal.posicionarBalaoFavoritosStatus || window.posicionarBalaoFavoritosStatus;
        window.garantirCamadaBalaoFavoritosStatus = statusModal.garantirCamadaBalaoFavoritosStatus || window.garantirCamadaBalaoFavoritosStatus;
        window.atualizarStatusFavoritosNoNavegadorMl = statusModal.atualizarStatusFavoritosNoNavegadorMl || window.atualizarStatusFavoritosNoNavegadorMl;
        window.limparStatusTerminalFavoritos = statusModal.limparStatusTerminalFavoritos || window.limparStatusTerminalFavoritos;
        window.favoritosMarcarBotaoAcaoBalaoClicado = statusModal.marcarBotaoAcaoBalaoClicado || window.favoritosMarcarBotaoAcaoBalaoClicado;
        window.favoritosResolverAcaoBalao = statusModal.resolverAcaoBalao || window.favoritosResolverAcaoBalao;
        return;
    }
    statusModal.__loaded = true;
    let fallbackBalloonTimer = null;
    let fallbackBalloonLayerEl = null;
    let ultimoEstadoPosicaoBalao = '';
    const statusOverlayNavegadorMl = {
        webview: null,
        ativo: false,
        texto: '',
        visivel: false,
        timer: null,
        pendente: null
    };

    function elementoPorId(id) {
        return typeof document !== 'undefined' ? document.getElementById(id) : null;
    }

    function obterMlFavoritosStatusEl() {
        return typeof mlFavoritosStatusEl !== 'undefined' ? mlFavoritosStatusEl : elementoPorId('ml-favoritos-status');
    }

    function obterMlWorkModalLiveStatusEl() {
        return typeof mlWorkModalLiveStatusEl !== 'undefined' ? mlWorkModalLiveStatusEl : elementoPorId('ml-work-modal-live-status');
    }

    function obterMlFavoritosBalloonEl() {
        return typeof mlFavoritosBalloonEl !== 'undefined' ? mlFavoritosBalloonEl : elementoPorId('ml-favoritos-status-balloon');
    }

    function obterMlFavoritosBalloonTextEl() {
        return typeof mlFavoritosBalloonTextEl !== 'undefined' ? mlFavoritosBalloonTextEl : elementoPorId('ml-favoritos-status-balloon-text');
    }

    function obterMlFavoritosBalloonActionsEl() {
        return typeof mlFavoritosBalloonActionsEl !== 'undefined' ? mlFavoritosBalloonActionsEl : elementoPorId('ml-favoritos-status-balloon-actions');
    }

    function obterMlFavoritosBalloonOriginalParentEl() {
        return typeof mlFavoritosBalloonOriginalParentEl !== 'undefined' ? mlFavoritosBalloonOriginalParentEl : null;
    }

    function obterMlBrowserFrameWrapEl() {
        return typeof mlBrowserFrameWrapEl !== 'undefined' ? mlBrowserFrameWrapEl : null;
    }

    function obterMlWebviewEl() {
        return typeof mlWebviewEl !== 'undefined' ? mlWebviewEl : null;
    }

    function favoritosEmExecucao() {
        return typeof mlFavoritosEmExecucao !== 'undefined' && !!mlFavoritosEmExecucao;
    }

    function cancelamentoFavoritosAtivo() {
        return typeof mlFavoritosCancelado !== 'undefined' && !!mlFavoritosCancelado;
    }

    function notificarShellFavoritosWorkerStatus(mensagem, opcoes = {}) {
        if (!favoritosEmExecucao()) return;
        try {
            if (!window.top || window.top === window || typeof window.top.postMessage !== 'function') return;
            const pausado = typeof mlFavoritosPausado !== 'undefined' && !!mlFavoritosPausado;
            const segundoPlano = typeof mlFavoritosExecucaoEmSegundoPlano !== 'undefined' && !!mlFavoritosExecucaoEmSegundoPlano;
            const inicioExecucao = typeof mlFavoritosExecucaoIniciadaEmMs !== 'undefined'
                ? Number(mlFavoritosExecucaoIniciadaEmMs) || 0
                : 0;
            const workerAtivo = !!window.__JK_FAVORITOS_WORKER_BROWSER_ACTIVE;
            if (!segundoPlano && !workerAtivo) return;
            window.top.postMessage({
                channel: 'jk-favoritos-worker-progress',
                payload: {
                    active: true,
                    paused: pausado,
                    background: segundoPlano,
                    startedAt: inicioExecucao,
                    status: pausado ? 'paused' : 'running',
                    message: String(mensagem || '').trim() || 'Favoritos rodando em segundo plano.',
                    error: !!(opcoes && opcoes.erro)
                }
            }, '*');
        } catch (_err) {}
    }

    function notificarShellFavoritosWorkerTerminal(status = 'canceled') {
        try {
            if (!window.top || window.top === window || typeof window.top.postMessage !== 'function') return;
            window.top.postMessage({
                channel: 'jk-favoritos-worker-done',
                payload: {
                    active: false,
                    paused: false,
                    background: false,
                    status: String(status || 'canceled').toLowerCase(),
                    message: ''
                }
            }, '*');
        } catch (_err) {}
    }

    function obterTimerBalao() {
        return typeof mlFavoritosBalloonTimer !== 'undefined' ? mlFavoritosBalloonTimer : fallbackBalloonTimer;
    }

    function definirTimerBalao(timer) {
        if (typeof mlFavoritosBalloonTimer !== 'undefined') {
            mlFavoritosBalloonTimer = timer;
        } else {
            fallbackBalloonTimer = timer;
        }
    }

    function limparTimerBalao() {
        const timer = obterTimerBalao();
        if (timer) clearTimeout(timer);
        definirTimerBalao(null);
    }

    function obterCamadaBalaoAtual() {
        return typeof mlFavoritosBalloonLayerEl !== 'undefined' ? mlFavoritosBalloonLayerEl : fallbackBalloonLayerEl;
    }

    function definirCamadaBalao(layer) {
        if (typeof mlFavoritosBalloonLayerEl !== 'undefined') {
            mlFavoritosBalloonLayerEl = layer;
        } else {
            fallbackBalloonLayerEl = layer;
        }
    }

    function resultadosMlAbertos() {
        return typeof balaoResultadosMlAberto === 'function' && balaoResultadosMlAberto();
    }

    function ocultarNavegadorTemporariamente() {
        if (typeof ocultarNavegadorMlShellTemporariamente === 'function') {
            ocultarNavegadorMlShellTemporariamente();
        }
    }

    function restaurarNavegadorSeVisivel() {
        if (typeof restaurarNavegadorMlShellSeVisivel === 'function') {
            restaurarNavegadorMlShellSeVisivel();
        }
    }

    function agendarPosicaoNavegador() {
        if (typeof agendarAtualizacaoPosicaoNavegadorMlShell === 'function') {
            agendarAtualizacaoPosicaoNavegadorMlShell();
        }
    }

    function navegadorMlNoShellElectron() {
        return typeof usarNavegadorMlNoShellElectron === 'function' && usarNavegadorMlNoShellElectron();
    }

    function esperarProximoPaint(callback) {
        const concluir = typeof callback === 'function' ? callback : () => {};
        if (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
            window.requestAnimationFrame(() => setTimeout(concluir, 0));
        } else {
            setTimeout(concluir, 0);
        }
    }

    function marcarBotaoAcaoBalaoClicado(botao, opcoes = {}) {
        if (!botao || botao.dataset.favoritosActionClicked === '1') return false;
        botao.dataset.favoritosActionClicked = '1';
        botao.classList.add('is-processing');
        botao.setAttribute('aria-busy', 'true');
        botao.disabled = true;
        const bloquearTodos = opcoes.bloquearTodos !== false;
        const acoesEl = obterMlFavoritosBalloonActionsEl();
        if (bloquearTodos && acoesEl) {
            acoesEl.querySelectorAll('button').forEach((item) => {
                if (item === botao) return;
                item.disabled = true;
                item.classList.add('is-disabled-by-action');
            });
        }
        return true;
    }

    function resolverAcaoBalao(resolve, valor, botao, opcoes = {}) {
        marcarBotaoAcaoBalaoClicado(botao, opcoes);
        if (opcoes.esconder) {
            esconderBalaoFavoritosStatus();
        }
        esperarProximoPaint(() => {
            if (typeof resolve === 'function') resolve(valor);
        });
    }

    function mostrarBalaoFavoritosStatus(mensagem, opcoes = {}) {
        if (cancelamentoFavoritosAtivo() && opcoes.permitirAposCancelamento !== true) {
            limparStatusTerminalFavoritos({ status: 'canceled' });
            return;
        }
        const textoStatus = mensagem || '';
        notificarShellFavoritosWorkerStatus(textoStatus, opcoes);
        const statusEl = obterMlFavoritosStatusEl();
        const liveStatusEl = obterMlWorkModalLiveStatusEl();
        const balaoEl = obterMlFavoritosBalloonEl();
        const textoEl = obterMlFavoritosBalloonTextEl();
        const acoesEl = obterMlFavoritosBalloonActionsEl();
        if (statusEl) statusEl.textContent = textoStatus;
        if (liveStatusEl) {
            const deveMostrarNoModal = !!(textoStatus && resultadosMlAbertos());
            liveStatusEl.textContent = textoStatus;
            liveStatusEl.classList.toggle('hidden', !deveMostrarNoModal);
        }
        if (!balaoEl || !textoEl) return;
        const deveOcultarNavegador = !!(opcoes.ocultarNavegador || (opcoes.manterAcoes && !opcoes.manterNavegadorVisivel));
        if (deveOcultarNavegador) {
            ocultarNavegadorTemporariamente();
        } else {
            restaurarNavegadorSeVisivel();
        }
        limparTimerBalao();
        textoEl.textContent = mensagem || '';
        balaoEl.classList.toggle('is-error', !!opcoes.erro);
        balaoEl.classList.toggle('is-wide', !!opcoes.larga);
        if (!opcoes.manterAcoes && acoesEl) {
            acoesEl.innerHTML = '';
        }
        balaoEl.classList.remove('is-comparison');
        const titulo = balaoEl.querySelector('.ml-favoritos-balloon-title');
        if (titulo) titulo.textContent = opcoes.titulo || 'Fazendo Favorito! Aguarde...';
        balaoEl.classList.remove('hidden');
        posicionarBalaoFavoritosStatus();
        if (opcoes.tempoMs) {
            definirTimerBalao(setTimeout(() => {
                balaoEl.classList.add('hidden');
                definirTimerBalao(null);
                posicionarBalaoFavoritosStatus();
                restaurarNavegadorSeVisivel();
            }, opcoes.tempoMs));
        }
    }

    function esconderBalaoFavoritosStatus(opcoes = {}) {
        const statusEl = obterMlFavoritosStatusEl();
        const liveStatusEl = obterMlWorkModalLiveStatusEl();
        const balaoEl = obterMlFavoritosBalloonEl();
        const textoEl = obterMlFavoritosBalloonTextEl();
        const acoesEl = obterMlFavoritosBalloonActionsEl();
        limparTimerBalao();
        if (statusEl) statusEl.textContent = '';
        if (liveStatusEl) {
            liveStatusEl.textContent = '';
            liveStatusEl.classList.add('hidden');
        }
        if (balaoEl) balaoEl.classList.add('hidden');
        if (balaoEl) balaoEl.classList.remove('is-wide');
        if (balaoEl) balaoEl.classList.remove('is-comparison');
        if (textoEl) textoEl.textContent = '';
        if (acoesEl) acoesEl.innerHTML = '';
        posicionarBalaoFavoritosStatus();
        if (opcoes.restaurarNavegador !== false) restaurarNavegadorSeVisivel();
    }

    function limparStatusTerminalFavoritos(opcoes = {}) {
        esconderBalaoFavoritosStatus({ restaurarNavegador: false });
        const frameWrapEl = obterMlBrowserFrameWrapEl();
        if (frameWrapEl) {
            frameWrapEl.classList.remove('has-status-overlay');
            frameWrapEl.style.setProperty('--ml-favoritos-status-overlay-height', '0px');
        }
        const camadaBalao = obterCamadaBalaoAtual();
        if (camadaBalao) {
            camadaBalao.classList.add('hidden');
            camadaBalao.setAttribute('aria-hidden', 'true');
        }
        atualizarStatusFavoritosNoNavegadorMl('', false, { imediato: true, forcar: true });
        notificarShellFavoritosWorkerTerminal(opcoes.status || 'canceled');
    }

    function garantirCamadaBalaoFavoritosStatus() {
        const balaoEl = obterMlFavoritosBalloonEl();
        if (!balaoEl) return null;
        let camadaBalao = obterCamadaBalaoAtual();
        if (camadaBalao && document.contains(camadaBalao)) {
            return camadaBalao;
        }
        camadaBalao = elementoPorId('ml-favoritos-balloon-layer');
        if (!camadaBalao) {
            camadaBalao = document.createElement('div');
            camadaBalao.id = 'ml-favoritos-balloon-layer';
            camadaBalao.className = 'ml-favoritos-balloon-layer hidden';
            camadaBalao.setAttribute('aria-hidden', 'true');
        }
        definirCamadaBalao(camadaBalao);
        return camadaBalao;
    }

    function posicionarBalaoFavoritosStatus() {
        const balaoEl = obterMlFavoritosBalloonEl();
        if (!balaoEl) return;
        const textoEl = obterMlFavoritosBalloonTextEl();
        const acoesEl = obterMlFavoritosBalloonActionsEl();
        const frameWrapEl = obterMlBrowserFrameWrapEl();
        const sobreNavegador = resultadosMlAbertos();
        const balaoVisivel = !balaoEl.classList.contains('hidden');
        const statusVisivel = balaoVisivel || (favoritosEmExecucao() && !cancelamentoFavoritosAtivo());
        const destino = document.body || obterMlFavoritosBalloonOriginalParentEl();
        const camadaBalao = garantirCamadaBalaoFavoritosStatus();
        const chavePosicao = [
            sobreNavegador ? '1' : '0',
            balaoVisivel ? '1' : '0',
            statusVisivel ? '1' : '0',
            camadaBalao && camadaBalao.parentElement ? '1' : '0',
            balaoEl.parentElement === camadaBalao ? '1' : '0'
        ].join(':');
        balaoEl.classList.toggle('is-over-browser', !!sobreNavegador);
        if (frameWrapEl) {
            frameWrapEl.classList.toggle('has-status-overlay', !!(sobreNavegador && statusVisivel));
            frameWrapEl.style.setProperty(
                '--ml-favoritos-status-overlay-height',
                sobreNavegador && statusVisivel ? '64px' : '0px'
            );
        }
        if (camadaBalao && chavePosicao !== ultimoEstadoPosicaoBalao) {
            if (destino && camadaBalao.parentElement !== destino) {
                destino.appendChild(camadaBalao);
            }
            if (balaoEl.parentElement !== camadaBalao) {
                camadaBalao.appendChild(balaoEl);
            }
        } else if (!camadaBalao && destino && balaoEl.parentElement !== destino) {
            destino.appendChild(balaoEl);
        }
        if (camadaBalao) {
            camadaBalao.classList.toggle('hidden', !balaoVisivel);
            camadaBalao.classList.toggle('is-over-browser', !!sobreNavegador);
            camadaBalao.setAttribute('aria-hidden', balaoVisivel ? 'false' : 'true');
        }
        ultimoEstadoPosicaoBalao = chavePosicao;
        const temAcoes = !!(acoesEl && acoesEl.children.length);
        const textoStatus = textoEl ? textoEl.textContent : '';
        if (temAcoes) {
            atualizarStatusFavoritosNoNavegadorMl('', false, { somenteSeVisivel: true });
        } else {
            atualizarStatusFavoritosNoNavegadorMl(textoStatus, !!(
                sobreNavegador
                && favoritosEmExecucao()
                && !cancelamentoFavoritosAtivo()
                && statusVisivel
            ));
        }
        agendarPosicaoNavegador();
    }

    function resetarEstadoOverlaySeWebviewMudou(webview) {
        if (statusOverlayNavegadorMl.webview === webview) return;
        if (statusOverlayNavegadorMl.timer) {
            clearTimeout(statusOverlayNavegadorMl.timer);
        }
        statusOverlayNavegadorMl.webview = webview;
        statusOverlayNavegadorMl.ativo = false;
        statusOverlayNavegadorMl.texto = '';
        statusOverlayNavegadorMl.visivel = false;
        statusOverlayNavegadorMl.timer = null;
        statusOverlayNavegadorMl.pendente = null;
    }

    function executarAtualizacaoStatusFavoritosNoNavegadorMl(webview, texto, ativoFinal) {
        const script = ativoFinal ? `
            (() => {
                const STYLE_ID = 'jk-favoritos-status-overlay-style';
                const STATUS_ID = 'jk-favoritos-status-overlay';
                if (!document.getElementById(STYLE_ID)) {
                    const style = document.createElement('style');
                    style.id = STYLE_ID;
                    style.textContent = \`
                        #\${STATUS_ID} {
                            position: fixed;
                            left: 50%;
                            top: 50%;
                            transform: translate(-50%, -50%);
                            z-index: 2147483647;
                            width: min(620px, calc(100vw - 48px));
                            box-sizing: border-box;
                            padding: 14px 16px;
                            border: 1px solid rgba(96, 165, 250, .82);
                            border-radius: 10px;
                            background: rgba(15, 23, 42, .96);
                            color: #fff;
                            box-shadow: 0 18px 48px rgba(15, 23, 42, .34), 0 0 34px rgba(14, 165, 233, .28);
                            pointer-events: none;
                            font-family: Inter, Arial, sans-serif;
                        }
                        #\${STATUS_ID} strong {
                            display: block;
                            font-size: 13px;
                            line-height: 1.25;
                            margin-bottom: 6px;
                        }
                        #\${STATUS_ID} span {
                            display: block;
                            font-size: 12px;
                            line-height: 1.4;
                        }
                    \`;
                    document.documentElement.appendChild(style);
                }
                let box = document.getElementById(STATUS_ID);
                if (!box) {
                    box = document.createElement('div');
                    box.id = STATUS_ID;
                    document.documentElement.appendChild(box);
                }
                box.innerHTML = '';
                const title = document.createElement('strong');
                title.textContent = 'Fazendo Favorito! Aguarde...';
                const text = document.createElement('span');
                text.textContent = ${JSON.stringify(texto)};
                box.appendChild(title);
                box.appendChild(text);
                return true;
            })();
        ` : `
            (() => {
                document.getElementById('jk-favoritos-status-overlay')?.remove();
                document.getElementById('jk-favoritos-status-overlay-style')?.remove();
                return true;
            })();
        `;
        statusOverlayNavegadorMl.ativo = ativoFinal;
        statusOverlayNavegadorMl.texto = texto;
        statusOverlayNavegadorMl.visivel = ativoFinal;
        try {
            const resultado = webview.executeJavaScript(script);
            if (resultado && typeof resultado.catch === 'function') resultado.catch(() => {});
        } catch (_err) {}
    }

    function atualizarStatusFavoritosNoNavegadorMl(mensagem, ativo, opcoes = {}) {
        const webview = obterMlWebviewEl();
        if (!navegadorMlNoShellElectron() || !webview || typeof webview.executeJavaScript !== 'function') return;
        const texto = String(mensagem || '').trim();
        const ativoFinal = !!(ativo && texto);
        resetarEstadoOverlaySeWebviewMudou(webview);
        if (!ativoFinal && statusOverlayNavegadorMl.timer) {
            clearTimeout(statusOverlayNavegadorMl.timer);
            statusOverlayNavegadorMl.timer = null;
            statusOverlayNavegadorMl.pendente = null;
        }
        if (!ativoFinal && !statusOverlayNavegadorMl.visivel && opcoes.forcar !== true) return;
        if (
            opcoes.forcar !== true
            &&
            statusOverlayNavegadorMl.ativo === ativoFinal
            && statusOverlayNavegadorMl.texto === texto
            && statusOverlayNavegadorMl.visivel === ativoFinal
        ) {
            return;
        }
        if (statusOverlayNavegadorMl.timer) {
            clearTimeout(statusOverlayNavegadorMl.timer);
            statusOverlayNavegadorMl.timer = null;
        }
        if (!ativoFinal || opcoes.imediato) {
            executarAtualizacaoStatusFavoritosNoNavegadorMl(webview, texto, ativoFinal);
            return;
        }
        statusOverlayNavegadorMl.pendente = { webview, texto, ativoFinal };
        statusOverlayNavegadorMl.timer = setTimeout(() => {
            const pendente = statusOverlayNavegadorMl.pendente;
            statusOverlayNavegadorMl.timer = null;
            statusOverlayNavegadorMl.pendente = null;
            if (!pendente || pendente.webview !== obterMlWebviewEl()) return;
            executarAtualizacaoStatusFavoritosNoNavegadorMl(pendente.webview, pendente.texto, pendente.ativoFinal);
        }, 90);
    }

    Object.assign(statusModal, {
        mostrarBalaoFavoritosStatus,
        esconderBalaoFavoritosStatus,
        posicionarBalaoFavoritosStatus,
        garantirCamadaBalaoFavoritosStatus,
        atualizarStatusFavoritosNoNavegadorMl,
        limparStatusTerminalFavoritos,
        marcarBotaoAcaoBalaoClicado,
        resolverAcaoBalao
    });

    window.mostrarBalaoFavoritosStatus = mostrarBalaoFavoritosStatus;
    window.esconderBalaoFavoritosStatus = esconderBalaoFavoritosStatus;
    window.posicionarBalaoFavoritosStatus = posicionarBalaoFavoritosStatus;
    window.garantirCamadaBalaoFavoritosStatus = garantirCamadaBalaoFavoritosStatus;
    window.atualizarStatusFavoritosNoNavegadorMl = atualizarStatusFavoritosNoNavegadorMl;
    window.limparStatusTerminalFavoritos = limparStatusTerminalFavoritos;
    window.favoritosMarcarBotaoAcaoBalaoClicado = marcarBotaoAcaoBalaoClicado;
    window.favoritosResolverAcaoBalao = resolverAcaoBalao;
})();
