        function mostrarBalaoFavoritosStatus(mensagem, opcoes = {}) {
            return window.FavoritosV2?.ui?.statusModal?.mostrarBalaoFavoritosStatus?.(mensagem, opcoes);
        }

        function esconderBalaoFavoritosStatus(opcoes = {}) {
            return window.FavoritosV2?.ui?.statusModal?.esconderBalaoFavoritosStatus?.(opcoes);
        }

        function resolverAcaoBalaoFavoritos(resolve, valor, botao, opcoes = {}) {
            if (typeof window.favoritosResolverAcaoBalao === 'function') {
                window.favoritosResolverAcaoBalao(resolve, valor, botao, opcoes);
                return;
            }
            if (opcoes.esconder) esconderBalaoFavoritosStatus();
            setTimeout(() => resolve(valor), 0);
        }

        function erroLoginMercadoLivreFavoritos(mensagem) {
            const erro = new Error(mensagem || 'O Mercado Livre pediu login/verificacao no navegador interno. Abra o navegador interno, conclua o acesso e tente Fazer favoritos novamente.');
            erro.loginMercadoLivreNecessario = true;
            return erro;
        }

        function erroEhLoginMercadoLivreFavoritos(err) {
            if (err && err.loginMercadoLivreNecessario) return true;
            const texto = String(err && err.message || err || '').toLowerCase();
            return texto.indexOf('mercado livre pediu login') >= 0
                || texto.indexOf('pediu login/verificacao') >= 0
                || texto.indexOf('account-verification') >= 0
                || texto.indexOf('acesse sua conta') >= 0;
        }

        function erroLoginAvantProFavoritos(mensagem) {
            const erro = new Error(mensagem || 'Avant Pro nao retornou dados coletaveis. Confirme manualmente no navegador interno se o Avant Pro esta pronto e tente Fazer favoritos novamente.');
            erro.loginAvantProNecessario = true;
            return erro;
        }

        function erroEhLoginAvantProFavoritos(err) {
            if (err && err.loginAvantProNecessario) return true;
            const texto = String(err && err.message || err || '').toLowerCase();
            return (texto.indexOf('avant pro') >= 0 || texto.indexOf('avantpro') >= 0)
                && (texto.indexOf('login') >= 0
                    || texto.indexOf('vincul') >= 0
                    || texto.indexOf('conexao') >= 0
                    || texto.indexOf('conect') >= 0
                    || texto.indexOf('account') >= 0);
        }

        function erroColetaMercadoLivreFavoritos(mensagem) {
            const erro = new Error(mensagem || 'Mercado Livre/Avant Pro nao retornou dados coletaveis. Abra o navegador interno, confira se a pagina carregou e tente Fazer favoritos novamente.');
            erro.coletaMercadoLivreFalhou = true;
            return erro;
        }

        function erroEhColetaMercadoLivreFavoritos(err) {
            if (err && err.coletaMercadoLivreFalhou) return true;
            const texto = String(err && err.message || err || '').toLowerCase();
            return texto.indexOf('mercado livre/avant pro nao retornou dados') >= 0
                || texto.indexOf('nao retornou dados coletaveis') >= 0;
        }

        function statusAvantProSemDadosColetaveis(status) {
            if (!status || status.needsAccountLink || status.accountActionRequired) return false;
            if (status.loadingScreen && Number(status.cardCount || 0) <= 0) return false;
            if (typeof statusAvantProTemDadosColetaveis === 'function' && statusAvantProTemDadosColetaveis(status)) return false;
            if (status.rows > 0 || status.dataTextNodes > 0 || status.bodyDataLabels > 1 || status.hasAvantData) return false;
            return !!(status.shellOnly || status.extensionDetected || status.widgets > 0 || status.actionButtons > 0 || status.toolsButtons > 0);
        }

        function statusAvantProLoginConcluidoSemDados(status) {
            if (!status) return false;
            const reloadAposLoginAvantRecomendado = !!status.reloadAposLoginAvantRecomendado;
            if (reloadAposLoginAvantRecomendado) return true;
            if (status.needsAccountLink && !status.loginAvantClicadoRecentemente) return false;
            return !!(
                status.avantShellProntoParaColeta
                || status.resumedAfterAvantLogin
                || status.extensionDetected
                || Number(status.infoButtons || 0) > 0
                || Number(status.toolsButtons || 0) > 0
                || Number(status.widgets || 0) > 0
                || status.bodyHasAvantInfo
            );
        }

        async function recarregarAposLoginAvantProFavoritosSePossivel(status = {}, opcoes = {}) {
            if (typeof recarregarNavegadorMlAposLoginAvantProFavoritos !== 'function') return status;
            return await recarregarNavegadorMlAposLoginAvantProFavoritos(status, opcoes).catch(() => status);
        }

        function statusAvantProPodeRetomarColeta(status) {
            if (!status) return false;
            if (typeof statusAvantProTemDadosColetaveis === 'function' && statusAvantProTemDadosColetaveis(status)) return true;
            if (typeof statusAvantProPedeLoginOuVinculo === 'function' && statusAvantProPedeLoginOuVinculo(status)) return false;
            const accountActionRequired = !!status.accountActionRequired;
            const infoButtons = Number(status.infoButtons || 0);
            const bodyHasAvantInfo = !!status.bodyHasAvantInfo;
            if (accountActionRequired && !statusAvantProLoginConcluidoSemDados(status)) return false;
            return !!(
                statusAvantProLoginConcluidoSemDados(status)
                || infoButtons > 0
                || bodyHasAvantInfo
                || status.avantShellProntoParaColeta
            );
        }

        function aguardarConexaoAvantProFavoritos(status = {}, opcoes = {}) {
            const estadoSegundoPlanoAnterior = mlFavoritosExecucaoEmSegundoPlano;
            const termo = String(opcoes.termo || '').trim();
            mlFavoritosExecucaoEmSegundoPlano = false;
            if (typeof mudarAba === 'function') {
                try { mudarAba('navegador'); } catch (_err) {}
            }
            abrirBalaoResultadosMl({
                titulo: 'Conectar Avant Pro',
                subtitulo: termo ? `Conclua a conexao do Avant Pro para continuar: ${termo}` : 'Conclua a conexao do Avant Pro para continuar.',
                mostrarFavoritos: true,
                browserCompleto: true,
                forcarExibicao: true
            });
            if (typeof forcarNavegadorMlShellVisivel === 'function') {
                setTimeout(() => forcarNavegadorMlShellVisivel(), 80);
                setTimeout(() => forcarNavegadorMlShellVisivel(), 420);
                setTimeout(() => forcarNavegadorMlShellVisivel(), 950);
            }
            if (typeof agendarAtualizacaoPosicaoNavegadorMlShell === 'function') {
                setTimeout(() => agendarAtualizacaoPosicaoNavegadorMlShell(), 100);
            }

            return new Promise((resolve, reject) => {
                let concluido = false;
                const limpar = () => {
                    concluido = true;
                };
                const restaurarSegundoPlano = () => {
                    mlFavoritosExecucaoEmSegundoPlano = estadoSegundoPlanoAnterior;
                };
                const podeRetomarNestaConexao = (statusAtual) => {
                    if (opcoes.exigirEntradaAvantAntesPesquisa !== true) return true;
                    if (typeof statusAvantProPedeEntradaAntesPesquisa !== 'function') return true;
                    return !statusAvantProPedeEntradaAntesPesquisa(statusAtual);
                };
                const concluirRetomada = async (statusAtual = {}) => {
                    const retomada = await recarregarAposLoginAvantProFavoritosSePossivel(statusAtual, {
                        termo,
                        acaoUsuario: true
                    });
                    limpar();
                    esconderBalaoFavoritosStatus();
                    restaurarSegundoPlano();
                    resolve(retomada || statusAtual);
                };
                const cancelar = () => {
                    limpar();
                    mlFavoritosCancelado = true;
                    try {
                        if (mlFavoritosAbortController) mlFavoritosAbortController.abort();
                    } catch (_err) {}
                    const erro = new Error('Favoritos cancelado pelo usuario.');
                    erro.canceladoFavoritos = true;
                    restaurarSegundoPlano();
                    reject(erro);
                };
                const verificarAgora = async () => {
                    if (concluido) return;
                    verificarCancelamentoFavoritos();
                    mostrarBalaoFavoritosStatus('Verificando se o Avant Pro ja liberou os dados...', {
                        manterAcoes: true,
                        manterNavegadorVisivel: true,
                        larga: true,
                        titulo: 'Conectar Avant Pro'
                    });
                    await acionarControlesAvantProNoWebview({
                        acaoUsuario: true,
                        forceClick: true,
                        permitirFerramentas: true,
                        maxClicks: 12
                    }).catch(() => 0);
                    const novoStatus = await aguardarAvantProNoWebview({
                        recarregarSeAusente: false,
                        autoLoginAvant: false,
                        timeoutMs: 9000,
                        pollMs: 350,
                        acaoUsuario: true
                    }).catch(() => null);
                    if (statusAvantProPodeRetomarColeta(novoStatus) && podeRetomarNestaConexao(novoStatus)) {
                        await concluirRetomada(novoStatus);
                        return;
                    }
                    if (typeof statusAvantProTemDadosColetaveis === 'function'
                        ? statusAvantProTemDadosColetaveis(novoStatus)
                        : !!(novoStatus && (novoStatus.hasAvantData || novoStatus.rows > 0 || novoStatus.dataTextNodes > 0 || novoStatus.bodyDataLabels > 1))) {
                        limpar();
                        esconderBalaoFavoritosStatus();
                        restaurarSegundoPlano();
                        resolve(novoStatus);
                        return;
                    }
                    mostrarControles(novoStatus || status);
                };
                const abrirLogin = async () => {
                    if (concluido) return;
                    const statusAntesLogin = await diagnosticarAvantProNoWebview().catch(() => null);
                    if (statusAvantProPodeRetomarColeta(statusAntesLogin) && podeRetomarNestaConexao(statusAntesLogin)) {
                        await concluirRetomada(statusAntesLogin);
                        return;
                    }
                    mostrarBalaoFavoritosStatus('Abrindo login do Avant Pro no navegador interno...', {
                        manterAcoes: true,
                        manterNavegadorVisivel: true,
                        larga: true,
                        titulo: 'Login Avant Pro'
                    });
                    const resultado = await abrirLoginAvantProNoWebview().catch(() => null);
                    const mensagem = resultado && resultado.success
                        ? 'Conclua o login no navegador interno. Depois clique em Tentar novamente.'
                        : 'Nao consegui acionar o login automaticamente. Use o navegador interno para entrar no Avant Pro e clique em Tentar novamente.';
                    mostrarBalaoFavoritosStatus(mensagem, {
                        manterAcoes: true,
                        manterNavegadorVisivel: true,
                        larga: true,
                        erro: !(resultado && resultado.success),
                        titulo: 'Login Avant Pro'
                    });
                    mostrarControles(status);
                };
                const mostrarControles = (statusAtual = {}) => {
                    if (concluido) return;
                    if (!mlFavoritosBalloonActionsEl) {
                        mostrarBalaoFavoritosStatus('Avant Pro esta aguardando login ou vinculacao. Conclua no navegador interno para continuar.', {
                            erro: true,
                            larga: true,
                            titulo: 'Conectar Avant Pro'
                        });
                        return;
                    }
                    mlFavoritosBalloonActionsEl.innerHTML = '';

                    const conectar = document.createElement('button');
                    conectar.type = 'button';
                    conectar.textContent = 'Conectar Avant Pro';
                    conectar.addEventListener('click', () => {
                        conectar.disabled = true;
                        abrirLogin().finally(() => { conectar.disabled = false; });
                    });

                    const tentar = document.createElement('button');
                    tentar.type = 'button';
                    tentar.textContent = 'Tentar novamente';
                    tentar.addEventListener('click', () => {
                        tentar.disabled = true;
                        verificarAgora().finally(() => { tentar.disabled = false; });
                    });

                    const cancelarBotao = document.createElement('button');
                    cancelarBotao.type = 'button';
                    cancelarBotao.textContent = 'Cancelar favoritos';
                    cancelarBotao.addEventListener('click', cancelar);

                    mlFavoritosBalloonActionsEl.appendChild(conectar);
                    mlFavoritosBalloonActionsEl.appendChild(tentar);
                    mlFavoritosBalloonActionsEl.appendChild(cancelarBotao);
                    const msg = statusAtual && statusAtual.message
                        ? statusAtual.message
                        : 'Avant Pro esta aguardando login ou vinculacao da conta.';
                    mostrarBalaoFavoritosStatus(`${msg} Conclua no navegador interno e clique em Tentar novamente para continuar o Favoritos.`, {
                        manterAcoes: true,
                        manterNavegadorVisivel: true,
                        erro: true,
                        larga: true,
                        titulo: 'Conectar Avant Pro'
                    });
                };

                mostrarControles(status);
            });
        }

        function perguntarQuantidadePesquisasFavoritos() {
            if (!mlFavoritosBalloonEl || !mlFavoritosBalloonActionsEl) {
                const resposta = window.prompt('Quantas pesquisas deseja fazer por SKU? Digite 1, 2 ou 3.', '3');
                if (resposta === null) return Promise.resolve(null);
                const quantidade = Number(String(resposta).trim());
                if (![1, 2, 3].includes(quantidade)) {
                    alert('Informe apenas 1, 2 ou 3 pesquisas.');
                    return Promise.resolve(null);
                }
                return Promise.resolve(quantidade);
            }

            if (mlFavoritosPerguntaResolver) {
                mlFavoritosPerguntaResolver(null);
                mlFavoritosPerguntaResolver = null;
            }

            mlFavoritosBalloonActionsEl.innerHTML = '';
            return new Promise(resolve => {
                mlFavoritosPerguntaResolver = resolve;
                [1, 2, 3].forEach(qtd => {
                    const botao = document.createElement('button');
                    botao.type = 'button';
                    botao.textContent = `${qtd} pesquisa${qtd > 1 ? 's' : ''}`;
                    botao.addEventListener('click', () => {
                        mlFavoritosPerguntaResolver = null;
                        resolve(qtd);
                    });
                    mlFavoritosBalloonActionsEl.appendChild(botao);
                });
                const cancelar = document.createElement('button');
                cancelar.type = 'button';
                cancelar.textContent = 'Cancelar';
                cancelar.addEventListener('click', () => {
                    mlFavoritosPerguntaResolver = null;
                    esconderBalaoFavoritosStatus();
                    resolve(null);
                });
                mlFavoritosBalloonActionsEl.appendChild(cancelar);
                mostrarBalaoFavoritosStatus('Escolha quantas pesquisas deseja fazer para os SKUs selecionados.', {
                    manterAcoes: true
                });
            });
        }

        function obterTermoInicialLoginAvantProFavoritos(selecionados = [], quantidade = 1) {
            const limite = Math.max(1, Math.min(3, Number(quantidade) || 1));
            const itens = Array.isArray(selecionados) ? selecionados : [];
            for (const item of itens) {
                if (!item) continue;
                try {
                    if (typeof montarPesquisasFavoritosSku === 'function') {
                        const info = montarPesquisasFavoritosSku(item, limite);
                        const termoInfo = info && Array.isArray(info.termos)
                            ? info.termos.map(pesquisa => pesquisa && pesquisa.termo).find(Boolean)
                            : '';
                        if (termoInfo) return normalizarTermoPesquisaFavoritos(termoInfo);
                    }
                } catch (_err) {}
                for (let numero = 1; numero <= limite; numero += 1) {
                    const chaves = [`pesquisa_${numero}`, `pesquisa${numero}`, `Pesquisa ${numero}`];
                    const termo = normalizarTermoPesquisaFavoritos(chaves.map(chave => item[chave]).find(Boolean) || '');
                    if (termo) return termo;
                }
            }
            return '';
        }

        function limparBotaoContinuarLoginAvantProFavoritos() {
            const existente = document.getElementById('ml-work-modal-continue-login-favoritos');
            if (existente && existente.parentElement) existente.parentElement.removeChild(existente);
        }

        function urlEmFluxoAutenticacaoMercadoLivreFavoritos(value) {
            const raw = String(value || '').trim();
            if (!raw) return false;
            try {
                const url = new URL(raw);
                const host = String(url.hostname || '').toLowerCase();
                const mercadoLivreHost = host === 'mercadolivre.com'
                    || host.endsWith('.mercadolivre.com')
                    || host === 'mercadolivre.com.br'
                    || host.endsWith('.mercadolivre.com.br')
                    || host === 'mercadolibre.com'
                    || host.endsWith('.mercadolibre.com');
                if (!mercadoLivreHost) return false;
                const pathname = String(url.pathname || '/').toLowerCase().replace(/\/{2,}/g, '/');
                const specificAuthRoute = /^\/gz\/account-verification(?:\/|$)|^\/jms\/[^/]+\/lgz(?:\/|$)|^\/password\/validation(?:\/|$)|^\/totp(?:\/|$)|^\/login\/challenges?(?:\/|$)/.test(pathname);
                const genericAuthHost = host === 'mercadolivre.com'
                    || host === 'mercadolivre.com.br'
                    || host === 'mercadolibre.com'
                    || /^(?:www|auth|accounts?|account)\./.test(host);
                const genericAuthRoute = /^\/login(?:\/|$)|^\/(?:captcha|recaptcha|security[-_/]?check|identity[-_/]?verification)(?:\/|$)/.test(pathname);
                let negativeTraffic = false;
                let explicitAuthParam = false;
                for (const [name, itemValueRaw] of url.searchParams.entries()) {
                    const key = String(name || '').toLowerCase();
                    const itemValue = String(itemValueRaw || '').toLowerCase();
                    if (key === 'logintype' && itemValue === 'negative_traffic') negativeTraffic = true;
                    if (['captcha', 'recaptcha', 'security_check', 'identity_verification'].includes(key) && itemValue) {
                        explicitAuthParam = true;
                    }
                }
                return specificAuthRoute
                    || negativeTraffic
                    || (genericAuthHost && (genericAuthRoute || explicitAuthParam));
            } catch (_err) {
                return false;
            }
        }

        async function obterEstadoAutenticacaoMercadoLivreFavoritos() {
            let urlAtual = '';
            let needsLogin = false;
            let leituraConfiavel = false;
            const podeExecutarDireto = !!(mlWebviewEl && typeof mlWebviewEl.executeJavaScript === 'function');
            if (mlWebviewEl && typeof mlWebviewEl.getURL === 'function') {
                try { urlAtual = String(mlWebviewEl.getURL() || '').trim(); } catch (_err) {}
                if (!podeExecutarDireto && /^https?:\/\//i.test(urlAtual)) leituraConfiavel = true;
            }
            if (podeExecutarDireto) {
                const estado = await mlWebviewEl.executeJavaScript(`
                    (function () {
                        var texto = String(document.body && (document.body.innerText || document.body.textContent) || '').toLowerCase();
                        try { texto = texto.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return {
                            url: String(location.href || ''),
                            needsLogin: texto.indexOf('digite seu e-mail') >= 0
                                || texto.indexOf('digite seu email') >= 0
                                || texto.indexOf('para iniciar sessao') >= 0
                                || texto.indexOf('codigo de verificacao') >= 0
                                || texto.indexOf('verifique sua identidade') >= 0
                        };
                    })();
                `, true).catch(() => null);
                if (estado && /^https?:\/\//i.test(String(estado.url || '').trim())) {
                    urlAtual = String(estado.url).trim();
                    leituraConfiavel = true;
                }
                needsLogin = !!(estado && estado.needsLogin);
            }
            return {
                url: urlAtual,
                indeterminado: !leituraConfiavel,
                pendente: needsLogin || urlEmFluxoAutenticacaoMercadoLivreFavoritos(urlAtual)
            };
        }

        async function validarLoginMercadoLivreAntesDeContinuarFavoritos(onContinuar, botao) {
            const estado = await obterEstadoAutenticacaoMercadoLivreFavoritos().catch(() => ({ url: '', pendente: false, indeterminado: true }));
            if (!estado || estado.indeterminado) {
                if (botao) botao.disabled = false;
                mostrarBalaoFavoritosStatus('A pagina de login ainda esta mudando e nao foi possivel confirmar seu estado. Aguarde alguns segundos e clique em Continuar favoritos novamente.', {
                    manterAcoes: true,
                    manterNavegadorVisivel: true,
                    larga: true,
                    titulo: 'Aguardando login'
                });
                return false;
            }
            if (estado && estado.pendente) {
                if (botao) botao.disabled = false;
                abrirBalaoResultadosMl({
                    titulo: 'Login Mercado Livre',
                    subtitulo: 'Conclua todas as etapas de acesso antes de continuar.',
                    mostrarFavoritos: true,
                    browserCompleto: true,
                    forcarExibicao: true
                });
                mostrarBalaoFavoritosStatus('Conclua o e-mail, senha e eventual codigo de verificacao do Mercado Livre. Aguarde a pagina da pesquisa voltar e so entao clique em Continuar favoritos.', {
                    manterAcoes: true,
                    manterNavegadorVisivel: true,
                    larga: true,
                    titulo: 'Login Mercado Livre'
                });
                return false;
            }
            if (typeof onContinuar === 'function') {
                const resultado = await onContinuar();
                return resultado !== false;
            }
            return true;
        }

        function obterElectronApiPersistenciaAvantProFavoritos() {
            try {
                const controller = window.FavoritosV2?.browser?.workerController;
                if (controller && typeof controller.electronApi === 'function') {
                    const apiController = controller.electronApi();
                    if (apiController) return apiController;
                }
            } catch (_err) {}
            try {
                if (window.electronAPI) return window.electronAPI;
            } catch (_err) {}
            try {
                if (window.top && window.top !== window && window.top.electronAPI) return window.top.electronAPI;
            } catch (_err) {}
            return null;
        }

        async function obterStatusPersistenciaAvantProFavoritos() {
            const api = obterElectronApiPersistenciaAvantProFavoritos();
            if (!api || typeof api.getAvantProStorageStatus !== 'function') {
                return {
                    disponivel: false,
                    currentUsable: false,
                    snapshotUsable: false,
                    usable: false
                };
            }
            const status = await api.getAvantProStorageStatus().catch(() => null);
            const currentUsable = !!(status && status.currentUsable);
            const snapshotUsable = !!(status && status.snapshotUsable);
            return {
                disponivel: !!(status && status.success !== false),
                currentUsable,
                snapshotUsable,
                usable: currentUsable || snapshotUsable,
                manifest: status && status.manifest || null
            };
        }

        async function obterEstadoAutenticacaoAvantProFavoritos() {
            const persistencia = await obterStatusPersistenciaAvantProFavoritos().catch(() => ({
                disponivel: false,
                currentUsable: false,
                snapshotUsable: false,
                usable: false
            }));
            const podeExecutarDireto = !!(mlWebviewEl && typeof mlWebviewEl.executeJavaScript === 'function');
            if (!podeExecutarDireto) {
                return {
                    indeterminado: true,
                    pendente: false,
                    detectado: false,
                    storageUsable: !!persistencia.usable,
                    currentStorageUsable: !!persistencia.currentUsable,
                    snapshotStorageUsable: !!persistencia.snapshotUsable
                };
            }
            const estado = await mlWebviewEl.executeJavaScript(`
                (function () {
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var texto = normalizar(document.body && (document.body.innerText || document.body.textContent) || '');
                    var marcadores = 0;
                    try { marcadores = document.querySelectorAll('[class*="avant" i], [id*="avant" i]').length; } catch (_err) {}
                    var recursos = [];
                    try {
                        recursos = performance.getEntriesByType('resource')
                            .map(function (entry) { return String(entry && entry.name || ''); })
                            .filter(function (name) { return /chrome-extension:\/\/jdefnfmbnchmnjkcknaadaddgjbgephh|avantpro/i.test(name); });
                    } catch (_err) {}
                    var pendente = /comece\\s+a\\s+usar\\s+o?\\s*avantpro|entre\\s+na\\s+sua\\s+conta\\s+para\\s+liberar\\s+os\\s+recursos\\s+da\\s+extensao|nao\\s+possui\\s+uma\\s+conta\\?\\s*crie\\s+uma\\s+aqui|fazer\\s+login\\s+no\\s+avant|entrar\\s+no\\s+avant|login\\s+avant\\s*pro/.test(texto);
                    var detectado = marcadores > 0 || recursos.length > 0 || /avant\\s*pro|avantpro/.test(texto);
                    return {
                        url: String(location.href || ''),
                        pendente: pendente,
                        detectado: detectado,
                        marcadores: marcadores,
                        recursos: recursos.length
                    };
                })();
            `, true).catch(() => null);
            return {
                url: String(estado && estado.url || ''),
                indeterminado: !(estado && estado.detectado),
                pendente: !!(estado && estado.pendente),
                detectado: !!(estado && estado.detectado),
                marcadores: Number(estado && estado.marcadores || 0),
                recursos: Number(estado && estado.recursos || 0),
                storageUsable: !!persistencia.usable,
                currentStorageUsable: !!persistencia.currentUsable,
                snapshotStorageUsable: !!persistencia.snapshotUsable
            };
        }

        async function validarLoginAvantProAntesDeContinuarFavoritos(onContinuar, botao) {
            mostrarBalaoFavoritosStatus('Validando login e sessao salva do Avant Pro...', {
                manterAcoes: true,
                manterNavegadorVisivel: true,
                larga: true,
                titulo: 'Validando Avant Pro'
            });
            const estado = await obterEstadoAutenticacaoAvantProFavoritos().catch(() => ({ indeterminado: true, pendente: false }));
            const storageUsable = !!(estado && estado.storageUsable);
            if (!estado || estado.pendente || (estado.indeterminado && !storageUsable)) {
                if (botao) botao.disabled = false;
                abrirBalaoResultadosMl({
                    titulo: 'Login Avant Pro',
                    subtitulo: 'Conclua o login antes de continuar.',
                    mostrarFavoritos: true,
                    browserCompleto: true,
                    forcarExibicao: true
                });
                mostrarBalaoFavoritosStatus(
                    estado && estado.pendente
                        ? 'O Avant Pro ainda mostra a tela de login. Entre na conta e clique em Continuar favoritos novamente.'
                        : 'Ainda nao encontrei login ativo nem uma sessao salva do Avant Pro. Aguarde a extensao carregar e clique em Continuar favoritos novamente.',
                    {
                        manterAcoes: true,
                        manterNavegadorVisivel: true,
                        larga: true,
                        titulo: 'Login Avant Pro'
                    }
                );
                return false;
            }
            const snapshot = estado.indeterminado && storageUsable
                ? { success: true, skipped: true, reason: 'persisted-session-reused' }
                : await registrarConfirmacaoUsuarioLoginAvantProFavoritos('validacao_dom_apos_login', estado);
            if (!snapshot || snapshot.success !== true) {
                console.warn('Login Avant Pro reconhecido; snapshot sera tentado novamente sem bloquear Favoritos.', {
                    reason: snapshot && snapshot.reason || '',
                    storageUsable
                });
            }
            if (typeof onContinuar === 'function') {
                const resultado = await onContinuar();
                return resultado !== false;
            }
            return true;
        }

        function mostrarBotaoContinuarLoginAvantProFavoritos(onContinuar) {
            const acoes = document.querySelector('.ml-work-modal-actions');
            const fechar = document.getElementById('ml-work-modal-close');
            if (!acoes) return null;
            let botao = document.getElementById('ml-work-modal-continue-login-favoritos');
            if (!botao) {
                botao = document.createElement('button');
                botao.id = 'ml-work-modal-continue-login-favoritos';
                botao.type = 'button';
                botao.className = 'ml-work-modal-control';
                botao.textContent = 'Continuar favoritos';
                botao.title = 'Clique depois de concluir o login do Mercado Livre e do Avant Pro';
                if (fechar && fechar.parentElement === acoes) {
                    acoes.insertBefore(botao, fechar);
                } else {
                    acoes.appendChild(botao);
                }
            }
            botao.classList.remove('hidden');
            botao.onclick = async () => {
                botao.disabled = true;
                try {
                    const concluiu = typeof onContinuar === 'function' ? await onContinuar(botao) : true;
                    if (concluiu === false) botao.disabled = false;
                } catch (err) {
                    botao.disabled = false;
                    console.warn('Falha ao validar login antes de continuar Favoritos:', err);
                }
            };
            return botao;
        }

        async function registrarConfirmacaoUsuarioLoginAvantProFavoritos(origem = 'prompt', estado = {}) {
            const api = obterElectronApiPersistenciaAvantProFavoritos();
            if (api && typeof api.saveAvantProStorageSnapshot === 'function') {
                const resultado = await api.saveAvantProStorageSnapshot('favoritos_usuario_confirmou_login_avant', {
                    source: 'favoritos',
                    origem,
                    confirmedAt: Date.now(),
                    liveAuthConfirmed: true,
                    validation: {
                        url: String(estado && estado.url || '').split('#')[0],
                        markers: Number(estado && estado.marcadores || 0),
                        resources: Number(estado && estado.recursos || 0)
                    }
                }).catch(() => null);
                if (!resultado || resultado.success !== true) return resultado || { success: false };
            }
            try {
                localStorage.setItem('jk_favoritos_avant_login_confirmado_usuario_at', String(Date.now()));
            } catch (_err) {}
            return { success: true };
        }

        async function perguntarLoginAvantProAntesFavoritos(opcoes = {}) {
            if (opcoes.avantLoginConfirmadoPeloUsuario === true || opcoes.pularPerguntaAvantLogin === true) {
                return true;
            }

            const termo = String(opcoes.termo || obterTermoInicialLoginAvantProFavoritos(opcoes.selecionados, opcoes.quantidade)).trim();
            if (!mlFavoritosBalloonEl || !mlFavoritosBalloonActionsEl) {
                return window.confirm('Antes de fazer favoritos, o Avant Pro ja esta logado?');
            }
            if (mlFavoritosPerguntaResolver) {
                mlFavoritosPerguntaResolver(null);
                mlFavoritosPerguntaResolver = null;
            }

            mlFavoritosBalloonActionsEl.innerHTML = '';
            return new Promise(resolve => {
                let concluido = false;
                const finalizar = (valor) => {
                    if (concluido) return;
                    concluido = true;
                    mlFavoritosPerguntaResolver = null;
                    limparBotaoContinuarLoginAvantProFavoritos();
                    if (valor === true) esconderBalaoFavoritosStatus();
                    resolve(valor);
                };
                const abrirTelaLogin = async () => {
                    if (concluido) return;
                    if (typeof mudarAba === 'function') {
                        try { mudarAba('navegador'); } catch (_err) {}
                    }
                    abrirBalaoResultadosMl({
                        titulo: 'Login Avant Pro',
                        subtitulo: '',
                        mostrarFavoritos: true,
                        browserCompleto: true,
                        forcarExibicao: true
                    });
                    if (typeof abrirMercadoLivreNoPrograma === 'function') {
                        const payload = termo
                            ? {
                                termoPesquisa: termo,
                                titulo: 'Login Avant Pro',
                                subtitulo: '',
                                mostrarFavoritos: true,
                                browserCompleto: true,
                                forcarExibicao: true,
                                aguardarPesquisaMs: 900
                            }
                            : {
                                titulo: 'Login Avant Pro',
                                subtitulo: '',
                                mostrarFavoritos: true,
                                browserCompleto: true,
                                forcarExibicao: true
                            };
                        await abrirMercadoLivreNoPrograma(payload).catch(() => null);
                    }
                    if (typeof forcarNavegadorMlShellVisivel === 'function') {
                        setTimeout(() => forcarNavegadorMlShellVisivel(), 80);
                        setTimeout(() => forcarNavegadorMlShellVisivel(), 450);
                    }
                    if (typeof agendarAtualizacaoPosicaoNavegadorMlShell === 'function') {
                        setTimeout(() => agendarAtualizacaoPosicaoNavegadorMlShell(), 120);
                    }
                    const estadoLoginMl = await obterEstadoAutenticacaoMercadoLivreFavoritos().catch(() => ({ pendente: false }));
                    const loginMercadoLivrePendente = !!(estadoLoginMl && estadoLoginMl.pendente);
                    if (loginMercadoLivrePendente) {
                        abrirBalaoResultadosMl({
                            titulo: 'Login Mercado Livre',
                            subtitulo: 'Conclua todas as etapas de acesso antes de continuar.',
                            mostrarFavoritos: true,
                            browserCompleto: true,
                            forcarExibicao: true
                        });
                    }
                    const validarEFinalizar = botao => validarLoginMercadoLivreAntesDeContinuarFavoritos(
                        () => validarLoginAvantProAntesDeContinuarFavoritos(() => finalizar(true), botao),
                        botao
                    );
                    mostrarBotaoContinuarLoginAvantProFavoritos(validarEFinalizar);
                    mlFavoritosBalloonActionsEl.innerHTML = '';

                    const continuar = document.createElement('button');
                    continuar.type = 'button';
                    continuar.textContent = 'Continuar favoritos';
                    continuar.addEventListener('click', async () => {
                        continuar.disabled = true;
                        const concluiu = await validarEFinalizar(continuar).catch(() => false);
                        if (!concluiu) continuar.disabled = false;
                    });

                    const abrirNovamente = document.createElement('button');
                    abrirNovamente.type = 'button';
                    abrirNovamente.textContent = 'Abrir busca novamente';
                    abrirNovamente.addEventListener('click', () => {
                        abrirNovamente.disabled = true;
                        abrirTelaLogin().finally(() => { abrirNovamente.disabled = false; });
                    });

                    const cancelar = document.createElement('button');
                    cancelar.type = 'button';
                    cancelar.textContent = 'Cancelar favoritos';
                    cancelar.addEventListener('click', () => {
                        esconderBalaoFavoritosStatus();
                        finalizar(false);
                    });

                    mlFavoritosBalloonActionsEl.appendChild(continuar);
                    mlFavoritosBalloonActionsEl.appendChild(abrirNovamente);
                    mlFavoritosBalloonActionsEl.appendChild(cancelar);
                    const mensagemLogin = loginMercadoLivrePendente
                        ? 'Conclua o login ou verificacao do Mercado Livre. Aguarde a pagina da pesquisa voltar e depois clique em Continuar favoritos.'
                        : 'Faça o login do Avant Pro no navegador interno. Quando terminar, clique em Continuar favoritos.';
                    mostrarBalaoFavoritosStatus(mensagemLogin, {
                        manterAcoes: true,
                        manterNavegadorVisivel: true,
                        larga: true,
                        titulo: loginMercadoLivrePendente ? 'Login Mercado Livre' : 'Login Avant Pro'
                    });
                };

                mlFavoritosPerguntaResolver = finalizar;

                const sim = document.createElement('button');
                sim.type = 'button';
                sim.textContent = 'Sim, continuar';
                sim.addEventListener('click', async () => {
                    sim.disabled = true;
                    const confirmado = await validarLoginAvantProAntesDeContinuarFavoritos(() => finalizar(true), sim).catch(() => false);
                    if (!confirmado && !concluido) {
                        sim.disabled = false;
                        await abrirTelaLogin().catch(() => null);
                    }
                });

                const nao = document.createElement('button');
                nao.type = 'button';
                nao.textContent = 'Nao, abrir login';
                nao.addEventListener('click', () => {
                    nao.disabled = true;
                    abrirTelaLogin().finally(() => { nao.disabled = false; });
                });

                const cancelar = document.createElement('button');
                cancelar.type = 'button';
                cancelar.textContent = 'Cancelar';
                cancelar.addEventListener('click', () => {
                    esconderBalaoFavoritosStatus();
                    finalizar(false);
                });

                mlFavoritosBalloonActionsEl.appendChild(sim);
                mlFavoritosBalloonActionsEl.appendChild(nao);
                mlFavoritosBalloonActionsEl.appendChild(cancelar);
                mostrarBalaoFavoritosStatus('Antes de fazer favoritos, o Avant Pro ja esta logado?', {
                    manterAcoes: true,
                    manterNavegadorVisivel: false,
                    larga: true,
                    titulo: 'Confirmar Avant Pro'
                });
            });
        }

        function nomePromocaoFavoritos(campanha) {
            const id = String(campanha && campanha.id || '').trim();
            const nome = String(campanha && (campanha.name || campanha.title || campanha.nome) || id || 'Promocao sem nome').trim();
            const status = String(campanha && campanha.status || '').trim();
            const quantidade = campanha && (campanha.eligible_count ?? campanha.items_count ?? campanha.item_count ?? campanha.total_items);
            const partes = [nome];
            if (status) partes.push(status);
            if (id) partes.push(id);
            if (quantidade !== null && quantidade !== undefined && quantidade !== '') partes.push(`${quantidade} item(ns)`);
            return partes.join(' - ');
        }

        function grupoPromocaoFavoritos(campanha) {
            const explicito = String(campanha && campanha.selection_group || '').trim().toLowerCase();
            if (explicito) return explicito;
            const tipo = String(campanha && (campanha.type || campanha.promotion_type) || '').trim().toUpperCase();
            const nome = String(campanha && (campanha.name || campanha.title) || '').normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
            if (['SELLER_CAMPAIGN', 'SELLER_COUPON_CAMPAIGN'].includes(tipo)) return 'usuario';
            if (['SMART', 'PRICE_MATCHING', 'PRICE_MATCHING_MELI_ALL', 'MARKETPLACE_CAMPAIGN', 'PRE_NEGOTIATED'].includes(tipo)) return 'mercado_livre';
            if (['aceler', 'tarifa', 'menos tarifa', 'reduzimos', 'aumente suas vendas'].some(chave => nome.includes(chave))) return 'mercado_livre';
            return 'outras';
        }

        function tituloGrupoPromocaoFavoritos(grupo) {
            if (grupo === 'usuario') return 'Promocoes criadas por voce';
            if (grupo === 'mercado_livre') return 'Promocoes do Mercado Livre';
            return 'Outras promocoes ativas';
        }

        async function perguntarSimNaoPromocaoFavoritos() {
            return false;
        }

        async function carregarPromocoesAtivasFavoritos(loja, opcoes = {}) {
            const query = new URLSearchParams({ loja: String(loja || '').trim() });
            const queryString = query.toString();
            const montarUrl = (rota) => `${rota}?${queryString}`;
            const urls = [
                montarUrl('/api/favoritos/ml/promocoes'),
                montarUrl('/api/mercadolivre/promocoes')
            ];
            try {
                const origemAtual = new URL(window.location.href);
                const ehBackendLocal = /^https?:$/i.test(origemAtual.protocol)
                    && /^(127\.0\.0\.1|localhost)$/i.test(origemAtual.hostname)
                    && String(origemAtual.port || '80') === '8001';
                if (!ehBackendLocal) {
                    urls.push(`http://127.0.0.1:8001${montarUrl('/api/favoritos/ml/promocoes')}`);
                    urls.push(`http://127.0.0.1:8001${montarUrl('/api/mercadolivre/promocoes')}`);
                }
            } catch (_err) {
                urls.push(`http://127.0.0.1:8001${montarUrl('/api/favoritos/ml/promocoes')}`);
                urls.push(`http://127.0.0.1:8001${montarUrl('/api/mercadolivre/promocoes')}`);
            }
            const urlsUnicas = Array.from(new Set(urls));
            const avisoLentidaoMs = Number(opcoes && opcoes.avisoLentidaoMs) > 0
                ? Number(opcoes.avisoLentidaoMs)
                : 6500;
            const onLento = typeof (opcoes && opcoes.onLento) === 'function' ? opcoes.onLento : null;
            let avisoTimer = null;
            if (onLento) {
                avisoTimer = setTimeout(() => {
                    try {
                        onLento();
                    } catch (_err) {}
                }, avisoLentidaoMs);
            }
            try {
                const erros = [];
                let erroBloqueante = '';
                for (const url of urlsUnicas) {
                    if (erroBloqueante) break;
                    try {
                        const response = await fetch(url, {
                            headers: obterAuthHeaders(),
                            cache: 'no-store'
                        });
                        const data = await response.json().catch(() => ({}));
                        if (!response.ok) {
                            const detalhe = data.detail || data.message || `HTTP ${response.status}`;
                            if ([401, 403].includes(Number(response.status))) {
                                erroBloqueante = detalhe;
                                break;
                            }
                            erros.push(`${url}: ${detalhe}`);
                            continue;
                        }
                        return Array.isArray(data.campaigns) ? data.campaigns : [];
                    } catch (err) {
                        const mensagem = err && err.message ? err.message : String(err);
                        erros.push(`${url}: ${mensagem}`);
                    }
                }
                if (erroBloqueante) {
                    throw new Error(erroBloqueante);
                }
                const detalheErro = erros.find(txt => !/Failed to fetch/i.test(txt)) || erros[0] || '';
                throw new Error(
                    detalheErro
                        ? `Nao foi possivel conectar ao backend para carregar promocoes. ${detalheErro}`
                        : 'Nao foi possivel conectar ao backend para carregar promocoes.'
                );
            } finally {
                if (avisoTimer) clearTimeout(avisoTimer);
            }
        }

        function perguntarSelecionarPromocaoFavoritos(campanhas) {
            const lista = Array.isArray(campanhas) ? campanhas.filter(campanha => campanha && campanha.id) : [];
            if (!lista.length) return Promise.resolve(null);
            if (!mlFavoritosBalloonEl || !mlFavoritosBalloonActionsEl) {
                const texto = lista.map((campanha, index) => `${index + 1}. ${nomePromocaoFavoritos(campanha)}`).join('\n');
                const resposta = window.prompt(`Escolha a promocao ativa:\n${texto}`, '1');
                if (resposta === null) return Promise.resolve(null);
                const idx = Number(String(resposta).trim()) - 1;
                return Promise.resolve(lista[idx] || null);
            }
            if (mlFavoritosPerguntaResolver) {
                mlFavoritosPerguntaResolver(null);
                mlFavoritosPerguntaResolver = null;
            }
            mlFavoritosBalloonActionsEl.innerHTML = '';
            return new Promise(resolve => {
                mlFavoritosPerguntaResolver = resolve;
                const wrap = document.createElement('div');
                wrap.className = 'ml-favoritos-promo-list';
                const grupos = ['usuario', 'mercado_livre', 'outras'];
                grupos.forEach(grupo => {
                    const itens = lista.filter(campanha => grupoPromocaoFavoritos(campanha) === grupo);
                    if (!itens.length) return;
                    const grupoEl = document.createElement('div');
                    grupoEl.className = 'ml-favoritos-promo-group';
                    const titulo = document.createElement('div');
                    titulo.className = 'ml-favoritos-promo-group-title';
                    titulo.textContent = tituloGrupoPromocaoFavoritos(grupo);
                    grupoEl.appendChild(titulo);
                    itens.forEach(campanha => {
                        const botao = document.createElement('button');
                        botao.type = 'button';
                        botao.className = 'ml-favoritos-promo-button';
                        botao.textContent = nomePromocaoFavoritos(campanha);
                        botao.addEventListener('click', () => {
                            mlFavoritosPerguntaResolver = null;
                            resolverAcaoBalaoFavoritos(resolve, campanha, botao);
                        });
                        grupoEl.appendChild(botao);
                    });
                    wrap.appendChild(grupoEl);
                });
                mlFavoritosBalloonActionsEl.appendChild(wrap);
                const cancelar = document.createElement('button');
                cancelar.type = 'button';
                cancelar.textContent = 'Cancelar';
                cancelar.addEventListener('click', () => {
                    mlFavoritosPerguntaResolver = null;
                    resolverAcaoBalaoFavoritos(resolve, null, cancelar, {
                        esconder: true
                    });
                });
                mlFavoritosBalloonActionsEl.appendChild(cancelar);
                mostrarBalaoFavoritosStatus('Selecione a promocao ativa que deseja usar.', {
                    manterAcoes: true,
                    larga: true
                });
            });
        }

        function perguntarContinuarSemPromocaoFavoritos() {
            if (!mlFavoritosBalloonEl || !mlFavoritosBalloonActionsEl) {
                return Promise.resolve(window.confirm('Nenhuma promocao ativa foi encontrada. Deseja continuar sem promocao?') ? { usar_promocao: false } : null);
            }
            if (mlFavoritosPerguntaResolver) {
                mlFavoritosPerguntaResolver(null);
                mlFavoritosPerguntaResolver = null;
            }
            mlFavoritosBalloonActionsEl.innerHTML = '';
            return new Promise(resolve => {
                mlFavoritosPerguntaResolver = resolve;
                const continuar = document.createElement('button');
                continuar.type = 'button';
                continuar.textContent = 'Continuar sem promocao';
                continuar.addEventListener('click', () => {
                    mlFavoritosPerguntaResolver = null;
                    resolverAcaoBalaoFavoritos(resolve, { usar_promocao: false }, continuar);
                });
                const cancelar = document.createElement('button');
                cancelar.type = 'button';
                cancelar.textContent = 'Cancelar';
                cancelar.addEventListener('click', () => {
                    mlFavoritosPerguntaResolver = null;
                    resolverAcaoBalaoFavoritos(resolve, null, cancelar, {
                        esconder: true
                    });
                });
                mlFavoritosBalloonActionsEl.appendChild(continuar);
                mlFavoritosBalloonActionsEl.appendChild(cancelar);
                mostrarBalaoFavoritosStatus('Nenhuma promocao ativa foi encontrada para essa loja.', {
                    manterAcoes: true
                });
            });
        }

        function perguntarModoDescontoPromocaoFavoritos(campanha) {
            if (!mlFavoritosBalloonEl || !mlFavoritosBalloonActionsEl) {
                const percentual = window.prompt('Informe a % fixa de desconto:', '21');
                if (percentual === null) return Promise.resolve(null);
                const numero = Number(String(percentual).replace(',', '.').trim());
                if (!Number.isFinite(numero) || numero <= 0) return Promise.resolve(null);
                return Promise.resolve({ modo: 'percentual_fixo', percentual: numero });
            }
            if (mlFavoritosPerguntaResolver) {
                mlFavoritosPerguntaResolver(null);
                mlFavoritosPerguntaResolver = null;
            }
            mlFavoritosBalloonActionsEl.innerHTML = '';
            return new Promise(resolve => {
                mlFavoritosPerguntaResolver = resolve;
                const linha = document.createElement('div');
                linha.className = 'ml-favoritos-promo-percent';
                const label = document.createElement('label');
                label.textContent = '% fixa';
                const input = document.createElement('input');
                input.type = 'number';
                input.min = '1';
                input.max = '99';
                input.step = '0.1';
                input.value = '21';
                linha.appendChild(label);
                linha.appendChild(input);
                mlFavoritosBalloonActionsEl.appendChild(linha);

                const fixa = document.createElement('button');
                fixa.type = 'button';
                fixa.textContent = 'Usar % fixa';
                fixa.addEventListener('click', () => {
                    const numero = Number(String(input.value || '').replace(',', '.').trim());
                    if (!Number.isFinite(numero) || numero <= 0) {
                        input.focus();
                        return;
                    }
                    mlFavoritosPerguntaResolver = null;
                    resolverAcaoBalaoFavoritos(resolve, { modo: 'percentual_fixo', percentual: numero }, fixa);
                });
                const cancelar = document.createElement('button');
                cancelar.type = 'button';
                cancelar.textContent = 'Cancelar';
                cancelar.addEventListener('click', () => {
                    mlFavoritosPerguntaResolver = null;
                    resolverAcaoBalaoFavoritos(resolve, null, cancelar, {
                        esconder: true
                    });
                });
                mlFavoritosBalloonActionsEl.appendChild(fixa);
                mlFavoritosBalloonActionsEl.appendChild(cancelar);
                mostrarBalaoFavoritosStatus(`Promocao selecionada: ${nomePromocaoFavoritos(campanha)}. Informe a porcentagem da campanha para calcular o preco cheio do anuncio.`, {
                    manterAcoes: true,
                    larga: true
                });
                setTimeout(() => input.focus(), 50);
            });
        }

        async function perguntarOpcoesPromocaoFavoritos(opcoes = {}) {
            const exigirPromocao = !!(opcoes && opcoes.exigirPromocao);
            const usarPromocao = exigirPromocao ? true : false;
            if (usarPromocao === null) return null;
            if (!usarPromocao) return { usar_promocao: false };
            const loja = favoritosLojaSelecionadaParaApi(
                (opcoes && opcoes.loja)
                || favMlLojaSelecionada
                || mlSkuLojaSelecionada
                || skuLojaSelecionada
                || ''
            );
            if (!loja) {
                mostrarBalaoFavoritosStatus('Escolha uma loja do Mercado Livre antes de selecionar promocao.', {
                    erro: true,
                    tempoMs: 4500
                });
                return null;
            }
            mostrarBalaoFavoritosStatus(`Carregando promocoes ativas da loja ${loja}...`);
            const chaveCache = skuNormalizarLoja(loja);
            let campanhas = [];
            try {
                campanhas = favMlPromocoesPorLojaCache.get(chaveCache) || [];
                if (!Array.isArray(campanhas) || !campanhas.length) {
                    campanhas = await carregarPromocoesAtivasFavoritos(loja, {
                        onLento: () => mostrarBalaoFavoritosStatus('Mercado Livre demorou para responder, tentando novamente...')
                    });
                    if (Array.isArray(campanhas) && campanhas.length) {
                        favMlPromocoesPorLojaCache.set(chaveCache, campanhas);
                    }
                }
            } catch (err) {
                mostrarBalaoFavoritosStatus(`Erro ao carregar promocoes: ${err && err.message ? err.message : err}`, {
                    erro: true
                });
                return null;
            }
            if (!campanhas.length) {
                if (exigirPromocao) {
                    mostrarBalaoFavoritosStatus('Nenhuma promocao ativa foi encontrada para escolher campanha e porcentagem.', {
                        erro: true,
                        tempoMs: 6500,
                        larga: true
                    });
                    return null;
                }
                return perguntarContinuarSemPromocaoFavoritos();
            }
            const campanha = await perguntarSelecionarPromocaoFavoritos(campanhas);
            if (!campanha) return null;
            const desconto = await perguntarModoDescontoPromocaoFavoritos(campanha);
            if (!desconto) return null;
            return {
                usar_promocao: true,
                campanha: {
                    id: String(campanha.id || '').trim(),
                    nome: String(campanha.name || campanha.title || campanha.id || '').trim(),
                    tipo: String(campanha.type || campanha.promotion_type || '').trim(),
                    status: String(campanha.status || '').trim()
                },
                desconto
            };
        }

        function resumoOpcoesPromocaoFavoritos(opcoes) {
            if (!opcoes || !opcoes.usar_promocao) return 'sem promocao';
            const nome = opcoes.campanha && (opcoes.campanha.nome || opcoes.campanha.id) || 'promocao selecionada';
            if (opcoes.desconto && opcoes.desconto.modo === 'percentual_fixo') {
                return `${nome}, % fixa ${opcoes.desconto.percentual}%`;
            }
            return `${nome}, sugestao do Mercado Livre`;
        }

        function criarErroFavoritosCancelado() {
            const erro = new Error('Processo de favoritos cancelado pelo usuario.');
            erro.canceladoFavoritos = true;
            return erro;
        }

        function aplicarEstadoWorkerFavoritos(status = {}) {
            const dados = status && typeof status === 'object' ? status : {};
            const statusTexto = String(dados.status || '').toLowerCase();
            const cancelado = dados.cancelRequested === true
                || statusTexto === 'cancel_requested'
                || statusTexto === 'canceling'
                || statusTexto === 'canceled'
                || statusTexto === 'cancelled';
            if (cancelado && mlFavoritosEmExecucao) {
                mlFavoritosCancelado = true;
                mlFavoritosPausado = false;
                if (mlFavoritosAbortController) {
                    try {
                        mlFavoritosAbortController.abort();
                    } catch (_err) {}
                }
            }
            return cancelado;
        }

        function verificarCancelamentoFavoritos() {
            if (mlFavoritosCancelado) {
                throw criarErroFavoritosCancelado();
            }
        }

        async function sincronizarEstadoWorkerFavoritos() {
            const api = obterElectronApiFavoritosExecucao();
            if (!api) return null;
            try {
                const status = window.__JK_FAVORITOS_WORKERS_POOL_ACTIVE && typeof api.getFavoritosWorkersPoolStatus === 'function'
                    ? await api.getFavoritosWorkersPoolStatus()
                    : (typeof api.getFavoritosWorkerBrowserStatus === 'function'
                        ? await api.getFavoritosWorkerBrowserStatus()
                        : null);
                if (!status) return null;
                aplicarEstadoWorkerFavoritos(status);
                return status;
            } catch (_err) {
                return null;
            }
        }

        async function aguardarControleFavoritos() {
            await sincronizarEstadoWorkerFavoritos();
            verificarCancelamentoFavoritos();
            while (mlFavoritosPausado && !mlFavoritosCancelado) {
                await sincronizarEstadoWorkerFavoritos();
                await new Promise(resolve => setTimeout(resolve, 180));
            }
            verificarCancelamentoFavoritos();
        }

        function sinalFavoritosAtual() {
            return mlFavoritosAbortController ? mlFavoritosAbortController.signal : undefined;
        }

        function executarComTimeoutFavoritos(tarefa, timeoutMs = 45000, sinalPai = undefined) {
            const limiteMs = Math.max(50, Number(timeoutMs) || 45000);
            const controller = typeof AbortController === 'function' ? new AbortController() : null;
            let timeoutId = null;
            let onAbortPai = null;
            const erroTimeout = new Error(`A etapa excedeu o limite de ${Math.ceil(limiteMs / 1000)}s.`);
            erroTimeout.name = 'TimeoutError';
            erroTimeout.favoritosTimeout = true;
            const abortarPeloPai = () => {
                if (controller && !controller.signal.aborted) controller.abort();
            };
            if (sinalPai && typeof sinalPai.addEventListener === 'function') {
                onAbortPai = abortarPeloPai;
                if (sinalPai.aborted) abortarPeloPai();
                else sinalPai.addEventListener('abort', onAbortPai, { once: true });
            }
            const execucao = Promise.resolve().then(() => tarefa(controller ? controller.signal : sinalPai));
            const prazo = new Promise((_resolve, reject) => {
                timeoutId = setTimeout(() => {
                    reject(erroTimeout);
                    if (controller && !controller.signal.aborted) controller.abort();
                }, limiteMs);
            });
            return Promise.race([execucao, prazo]).finally(() => {
                if (timeoutId) clearTimeout(timeoutId);
                if (sinalPai && onAbortPai && typeof sinalPai.removeEventListener === 'function') {
                    sinalPai.removeEventListener('abort', onAbortPai);
                }
            });
        }

        function cancelarFavoritosEmExecucao() {
            if (!mlFavoritosEmExecucao) return;
            mlFavoritosCancelado = true;
            mlFavoritosPausado = false;
            if (mlFavoritosAbortController) {
                try {
                    mlFavoritosAbortController.abort();
                } catch (_err) {}
            }
            if (mlFavoritosJobIdAtual && typeof cancelarFavoritosJobAtualServidor === 'function') {
                cancelarFavoritosJobAtualServidor().catch((err) => {
                    console.warn('Nao foi possivel cancelar job de favoritos no backend:', err);
                });
            }
            if (typeof pararPollingFavoritosJob === 'function') pararPollingFavoritosJob();
            if (mlFavoritosJobRenderRaf) {
                try {
                    if (typeof cancelAnimationFrame === 'function') cancelAnimationFrame(mlFavoritosJobRenderRaf);
                    else clearTimeout(mlFavoritosJobRenderRaf);
                } catch (_err) {}
                mlFavoritosJobRenderRaf = 0;
            }
            mlFavoritosJobRenderPendente = false;
            if (typeof limparStatusTerminalFavoritos === 'function') {
                limparStatusTerminalFavoritos({ status: 'canceled' });
            } else {
                esconderBalaoFavoritosStatus();
                atualizarStatusFavoritosNoNavegadorMl('', false, { imediato: true, forcar: true });
            }
            if (typeof pararNavegadorFavoritosBackground === 'function') {
                pararNavegadorFavoritosBackground({
                    status: 'canceled',
                    message: '',
                    reason: 'favoritos-cancelado-pelo-usuario'
                });
            }
            atualizarContadorSkuSidebarSelecionados();
            atualizarFiltroAzulFavoritos();
        }

        function inicializarSincronizacaoWorkerFavoritos() {
            const api = obterElectronApiFavoritosExecucao();
            if (!api || window.__favoritosCancelSyncReady) return;
            window.__favoritosCancelSyncReady = true;
            const ouvir = (nome, handler) => {
                if (typeof api[nome] !== 'function') return;
                try {
                    api[nome](handler);
                } catch (_err) {}
            };
            ouvir('onFavoritosWorkerProgress', (status) => aplicarEstadoWorkerFavoritos(status));
            ouvir('onFavoritosWorkerDone', (status) => aplicarEstadoWorkerFavoritos(status));
            ouvir('onFavoritosWorkerError', (status) => aplicarEstadoWorkerFavoritos(status));
        }

        function obterCadastroSkuFavoritos(sku, loja = '') {
            const chave = skuChaveSku(sku);
            if (!chave || !Array.isArray(skuDados)) return null;
            const candidatos = skuDados.filter(row => skuChaveSku(skuObterSku(row)) === chave);
            if (!candidatos.length) return null;
            const lojaAlvo = skuNormalizarLoja(loja || favoritosLojaSelecionadaParaApi());
            const temPesquisa = (row) => [1, 2, 3].some(numero => String(skuObterPesquisa(row, numero) || '').trim());
            const candidatoLoja = candidatos.find(row => lojaAlvo && skuNormalizarLoja(skuObterLoja(row)) === lojaAlvo) || null;
            return (candidatoLoja && temPesquisa(candidatoLoja))
                ? candidatoLoja
                : (candidatos.find(temPesquisa) || candidatoLoja || candidatos[0]);
        }

        function normalizarTermoPesquisaFavoritos(termo) {
            return String(termo || '').replace(/[.,/]+/g, ' ').replace(/\s+/g, ' ').trim();
        }

        function montarPesquisasFavoritosSku(item, quantidade) {
            const cadastro = obterCadastroSkuFavoritos(item.sku, item.loja);
            const descricaoCadastro = cadastro
                ? String(cadastro.descricao_ml || cadastro.descricao || cadastro['descrição'] || cadastro.description || '').trim()
                : '';
            const termos = [];
            for (let numero = 1; numero <= quantidade; numero += 1) {
                const termo = normalizarTermoPesquisaFavoritos(cadastro ? skuObterPesquisa(cadastro, numero) : '');
                if (!termo) continue;
                if (!termos.some(t => t.termo.toLowerCase() === termo.toLowerCase())) {
                    termos.push({ campo: numero, termo });
                }
            }
            return {
                sku: item.sku,
                loja: item.loja || (cadastro && skuObterLoja(cadastro)) || favoritosLojaSelecionadaParaApi(),
                titulo: (cadastro && skuObterProduto(cadastro)) || item.titulo || '',
                descricao: descricaoCadastro,
                cadastro,
                termos
            };
        }

        function validarAnunciosFavoritosPertencemAoTermo(termo, anuncios) {
            const tokens = normalizarTextoMl(termo)
                .split(' ')
                .filter(token => token.length >= 3)
                .slice(0, 6);
            if (!tokens.length || !Array.isArray(anuncios) || anuncios.length < 3) return true;
            const amostra = anuncios.slice(0, 14).map(anuncio => normalizarTextoMl([
                anuncio && anuncio.titulo,
                anuncio && anuncio.title,
                anuncio && anuncio.url
            ].filter(Boolean).join(' ')));
            const correspondentes = amostra.filter(texto => tokens.some(token => texto.includes(token))).length;
            if (correspondentes > 0 || amostra.length < 3) return true;
            throw erroColetaMercadoLivreFavoritos(`Os anuncios coletados nao correspondem a pesquisa "${termo}". Reabra a busca no navegador interno e tente novamente.`);
        }

        function filtrarAnunciosFavoritosComDadosAvant(anuncios) {
            return (Array.isArray(anuncios) ? anuncios : []).filter(anuncio => {
                const fonteVendas = normalizarFonte(anuncio && (anuncio.vendasFonte || anuncio.vendas_fonte || anuncio.fonte_vendas || ''));
                return !!(anuncio && (
                    fonteVendasConfiavel(fonteVendas)
                    || vendedorValido(anuncio.vendedor)
                    || anuncio.data_criacao
                    || anuncio.cacheAvant
                    || anuncio.origem_dados === 'avantpro_fast_dom'
                ));
            });
        }

        function preparacaoAvantPrePesquisaConfirmada(resultado) {
            return !!(resultado && (
                resultado.loginAvantConfirmado === true
                || resultado.confirmed === true
                || (resultado.entrada && resultado.entrada.confirmed === true)
            ));
        }

        async function buscarAnunciosFavoritosPorTermoAvant(termo, opcoes = {}) {
            if (!hasInternalBrowserApi && !usarNavegadorMlNoShellElectron()) return [];
            verificarCancelamentoFavoritos();
            if (
                opcoes.loginAvantAntesDaColeta === true
                && opcoes.avantLoginPrePesquisaConfirmado !== true
            ) {
                if (typeof prepararAvantProAntesDaPesquisaFavoritos !== 'function') {
                    throw erroLoginAvantProFavoritos(`Nao consegui preparar o Avant Pro antes da pesquisa de "${termo}". Reabra a tela de Favoritos e tente novamente.`);
                }
                mostrarBalaoFavoritosStatus(`Preparando Avant Pro antes da pesquisa de "${termo}"...`, {
                    manterNavegadorVisivel: true,
                    larga: true,
                    titulo: 'Preparando Avant Pro'
                });
                if (typeof abrirMercadoLivreNoPrograma === 'function') {
                    if (typeof mlUrlInput !== 'undefined' && mlUrlInput) {
                        mlUrlInput.value = typeof ML_DEFAULT_URL !== 'undefined'
                            ? ML_DEFAULT_URL
                            : 'https://www.mercadolivre.com.br/';
                    }
                    await abrirMercadoLivreNoPrograma({
                        titulo: opcoes.titulo || 'Fazendo Favorito! Aguarde...',
                        subtitulo: opcoes.subtitulo || `Preparando Avant Pro - ${termo}`,
                        mostrarFavoritos: true,
                        browserCompleto: true,
                        forcarExibicao: true
                    });
                }
                const preparacaoAvant = await prepararAvantProAntesDaPesquisaFavoritos({
                    termo,
                    aguardarConexao: true,
                    timeoutMs: Math.max(9000, Number(opcoes.timeoutAvantPrePesquisaMs) || 22000),
                    pollMs: opcoes.segundoPlano ? 650 : 350
                });
                if (!preparacaoAvantPrePesquisaConfirmada(preparacaoAvant)) {
                    throw erroLoginAvantProFavoritos(`Avant Pro nao confirmou o login antes da pesquisa de "${termo}". Confirme o e-mail em Ferramentas e aguarde o aviso de obrigado antes de pesquisar.`);
                }
                verificarCancelamentoFavoritos();
            }
            const urlPesquisaMl = construirUrlPesquisaMercadoLivre(termo);
            mlUrlInput.value = urlPesquisaMl;
            const abriu = await abrirMercadoLivreNoPrograma({
                termoPesquisa: termo,
                titulo: opcoes.titulo || 'Fazendo Favorito! Aguarde...',
                subtitulo: opcoes.subtitulo || `Pesquisa: ${termo}`,
                mostrarFavoritos: true,
                browserCompleto: true,
                aguardarPesquisaMs: opcoes.segundoPlano ? 1200 : 900
            });
            verificarCancelamentoFavoritos();
            if (!abriu) {
                throw erroColetaMercadoLivreFavoritos(`Nao consegui abrir o Mercado Livre no navegador interno para "${termo}". Confira se a pagina carregou e se o Avant Pro esta conectado.`);
            }
            mostrarBalaoFavoritosStatus(`Abrindo resultados de "${termo}"...`);
            const segundoPlano = typeof navegadorMlEmSegundoPlano === 'function' && navegadorMlEmSegundoPlano();
            const leituraPaginaAutorizada = typeof acaoUsuarioFavoritosPermiteLeituraPagina === 'function'
                ? acaoUsuarioFavoritosPermiteLeituraPagina(opcoes)
                : true;
            const pesquisaConfirmada = typeof aguardarPesquisaMercadoLivreAtual === 'function'
                ? await aguardarPesquisaMercadoLivreAtual(termo, {
                    url: urlPesquisaMl,
                    timeoutMs: segundoPlano ? 12000 : 9000,
                    pollMs: segundoPlano ? 420 : 260
                }).catch(() => null)
                : null;
            if (pesquisaConfirmada && pesquisaConfirmada.ok) {
                mostrarBalaoFavoritosStatus(`Pesquisa confirmada para "${termo}". Acionando Avant Pro para carregar dados...`, {
                    larga: true,
                    titulo: 'Pesquisa confirmada'
                });
            } else if (typeof garantirPesquisaMercadoLivreSubmetida === 'function') {
                await garantirPesquisaMercadoLivreSubmetida(termo, urlPesquisaMl).catch(() => null);
            }
            let avantStatusPreColeta = null;
            if (opcoes.loginAvantAntesDaColeta === true && typeof diagnosticarAvantProNoWebview === 'function') {
                avantStatusPreColeta = await diagnosticarAvantProNoWebview().catch(() => null);
            }
            let pronto = await aguardarPrimeirosDadosAvantOuCardsWebview({
                timeoutMs: segundoPlano ? 12000 : 9000,
                idleMs: segundoPlano ? 420 : 320,
                acaoUsuario: leituraPaginaAutorizada
            }).catch(() => null);
            if (pronto && pronto.needsLogin) {
                throw erroLoginMercadoLivreFavoritos();
            }
            if (pronto && pronto.needsAvantLogin && opcoes.exigirAvantPro !== true) {
                const mensagemAvant = segundoPlano
                    ? 'Avant Pro nao retornou dados coletaveis. Vou seguir com os anuncios visiveis do Mercado Livre sem travar a fila.'
                    : 'Avant Pro nao retornou dados coletaveis. Confirme manualmente o login no navegador interno.';
                mostrarBalaoFavoritosStatus(mensagemAvant, {
                    larga: true,
                    titulo: 'Avant Pro sem dados'
                });
                await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
                await aguardarPrimeirosDadosAvantOuCardsWebview({ timeoutMs: 900, idleMs: 160, acaoUsuario: leituraPaginaAutorizada }).catch(() => null);
            }
            if (pronto && pronto.noResults && !pronto.loadingScreen) {
                const erro = new Error('Mercado Livre nao encontrou resultados para esta pesquisa.');
                erro.semResultadosMl = true;
                throw erro;
            }
            if (
                pronto
                && pronto.loadingScreen
                && !pronto.hasCards
                && !pronto.hasAvantData
                && typeof aguardarResultadosMercadoLivreWebview === 'function'
            ) {
                mostrarBalaoFavoritosStatus(`Mercado Livre ainda esta carregando "${termo}". Aguardando os resultados antes do Avant Pro...`, {
                    larga: true,
                    titulo: 'Aguardando Mercado Livre'
                });
                const prontoMl = await aguardarResultadosMercadoLivreWebview({
                    timeoutMs: segundoPlano ? 45000 : 26000,
                    pollMs: segundoPlano ? 850 : 600,
                    reloadAfterMs: segundoPlano ? 15000 : 8500,
                    recarregarSeTravado: !mlFavoritosEmExecucao,
                    mensagemRecarregando: `Mercado Livre ficou preso no carregamento de "${termo}". Recarregando a pesquisa uma vez...`
                }).catch(() => null);
                if (prontoMl) {
                    pronto = {
                        ...pronto,
                        ...prontoMl,
                        hasCards: !!(prontoMl.hasCards || Number(prontoMl.cardCount || 0) > 0),
                        hasAvantData: !!(prontoMl.hasAvantData || pronto.hasAvantData)
                    };
                }
                if (pronto && pronto.needsLogin) {
                    throw erroLoginMercadoLivreFavoritos();
                }
                if (pronto && pronto.noResults && !pronto.loadingScreen) {
                    const erro = new Error('Mercado Livre nao encontrou resultados para esta pesquisa.');
                    erro.semResultadosMl = true;
                    throw erro;
                }
                if (pronto && pronto.loadingScreen && !pronto.hasCards && !pronto.hasAvantData) {
                    const statusAvantDuranteCarregamento = typeof diagnosticarAvantProNoWebview === 'function'
                        ? await diagnosticarAvantProNoWebview().catch(() => null)
                        : null;
                    const avantPedeContaDuranteCarregamento = !!(statusAvantDuranteCarregamento && (
                        typeof statusAvantProPedeLoginOuVinculo === 'function'
                            ? statusAvantProPedeLoginOuVinculo(statusAvantDuranteCarregamento)
                            : (statusAvantDuranteCarregamento.needsAccountLink || statusAvantDuranteCarregamento.accountActionRequired)
                    ));
                    const avantShellDuranteCarregamento = !!(statusAvantDuranteCarregamento && (
                        avantPedeContaDuranteCarregamento
                        || statusAvantDuranteCarregamento.extensionDetected
                        || statusAvantDuranteCarregamento.shellOnly
                        || Number(statusAvantDuranteCarregamento.widgets || 0) > 0
                        || Number(statusAvantDuranteCarregamento.toolsButtons || 0) > 0
                        || Number(statusAvantDuranteCarregamento.actionButtons || 0) > 0
                    ));
                    if (avantShellDuranteCarregamento) {
                        pronto = {
                            ...pronto,
                            loadingScreen: false,
                            aguardandoAvantMesmoSemCards: true,
                            avantStatusDuranteCarregamento: statusAvantDuranteCarregamento
                        };
                        mostrarBalaoFavoritosStatus(
                            avantPedeContaDuranteCarregamento
                                ? `Avant Pro nao retornou dados coletaveis para "${termo}". Confirme manualmente o login no navegador interno.`
                                : `Avant Pro carregou antes dos cards de "${termo}". Aguardando dados do Avant Pro...`,
                            {
                                larga: true,
                                titulo: avantPedeContaDuranteCarregamento ? 'Avant Pro sem dados' : 'Aguardando Avant Pro'
                            }
                        );
                        if (avantPedeContaDuranteCarregamento && !segundoPlano) {
                            await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
                            await aguardarPrimeirosDadosAvantOuCardsWebview({ timeoutMs: 1400, idleMs: 200, acaoUsuario: leituraPaginaAutorizada }).catch(() => null);
                        }
                    } else {
                        throw erroColetaMercadoLivreFavoritos(`Mercado Livre ficou carregando a busca "${termo}" e nao exibiu anuncios. Reabra a pesquisa no navegador interno e tente novamente.`);
                    }
                }
            }
            let avantStatus = avantStatusPreColeta || null;
            let statusFinal = null;
            let reloadedAfterAvantLogin = false;
            if (opcoes.exigirAvantPro === true && typeof garantirAvantProProntoParaFavoritos === 'function') {
                avantStatus = await garantirAvantProProntoParaFavoritos({
                        termo,
                        timeoutMs: pronto && pronto.aguardandoAvantMesmoSemCards
                            ? (segundoPlano ? 95000 : 65000)
                            : (segundoPlano ? 14000 : 9500),
                    pollMs: segundoPlano ? 650 : 350,
                    acaoUsuario: leituraPaginaAutorizada
                });
                statusFinal = avantStatus;
                reloadedAfterAvantLogin = !!(avantStatus && avantStatus.reloadedAfterAvantLogin);
            } else if (!avantStatus) {
                avantStatus = await diagnosticarAvantProNoWebview().catch(() => null);
                statusFinal = avantStatus;
            }
            if (avantStatus && (
                typeof statusAvantProPedeLoginOuVinculo === 'function'
                    ? statusAvantProPedeLoginOuVinculo(avantStatus)
                    : (avantStatus.needsAccountLink || avantStatus.accountActionRequired)
            )) {
                if (opcoes.exigirAvantPro === true) {
                    if (typeof mostrarAcaoConectarAvantPro === 'function') {
                        mostrarAcaoConectarAvantPro(avantStatus);
                    }
                    statusFinal = await aguardarConexaoAvantProFavoritos(avantStatus, { termo });
                    reloadedAfterAvantLogin = reloadedAfterAvantLogin || !!(statusFinal && statusFinal.reloadedAfterAvantLogin);
                    avantStatus = statusFinal || avantStatus;
                } else {
                    const mensagemAvant = segundoPlano
                        ? 'Avant Pro nao retornou dados coletaveis. Coletando os anuncios visiveis do Mercado Livre em background.'
                        : 'Avant Pro nao retornou dados coletaveis. Confirme manualmente o login no navegador interno.';
                    mostrarBalaoFavoritosStatus(mensagemAvant, {
                        larga: true,
                        titulo: 'Avant Pro sem dados'
                    });
                    const fechamentoAvant = await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
                    const resultadoLoginAvant = segundoPlano
                        ? fechamentoAvant
                        : ((fechamentoAvant && fechamentoAvant.closed)
                            ? fechamentoAvant
                            : await abrirLoginAvantProNoWebview().catch(() => null));
                    await aguardarPrimeirosDadosAvantOuCardsWebview({
                        timeoutMs: resultadoLoginAvant && resultadoLoginAvant.success ? 1200 : 700,
                        idleMs: 180,
                        acaoUsuario: leituraPaginaAutorizada
                    }).catch(() => null);
                    avantStatus = await diagnosticarAvantProNoWebview().catch(() => avantStatus);
                }
                if (!segundoPlano && avantStatus && (
                    typeof statusAvantProPedeLoginOuVinculo === 'function'
                        ? statusAvantProPedeLoginOuVinculo(avantStatus)
                        : (avantStatus.needsAccountLink || avantStatus.accountActionRequired)
                )) {
                    avantStatus = await aguardarConexaoAvantProFavoritos(avantStatus, { termo });
                    statusFinal = avantStatus;
                    reloadedAfterAvantLogin = reloadedAfterAvantLogin || !!(statusFinal && statusFinal.reloadedAfterAvantLogin);
                    await aguardarPrimeirosDadosAvantOuCardsWebview({
                        timeoutMs: segundoPlano ? 4500 : 1800,
                        idleMs: segundoPlano ? 320 : 180,
                        acaoUsuario: leituraPaginaAutorizada
                    }).catch(() => null);
                    avantStatus = await diagnosticarAvantProNoWebview().catch(() => avantStatus);
                }
            }
            if (opcoes.exigirAvantPro === true && typeof statusAvantProPedeLoginOuVinculo === 'function' && statusAvantProPedeLoginOuVinculo(avantStatus)) {
                if (typeof mostrarAcaoConectarAvantPro === 'function') {
                    mostrarAcaoConectarAvantPro(avantStatus);
                }
                throw erroLoginAvantProFavoritos(`Avant Pro nao retornou dados coletaveis para "${termo}". Confirme manualmente o login no navegador interno e tente Fazer favoritos novamente.`);
            }
            const deveIgnorarLoginAvantBackground = () => !!(segundoPlano && avantStatus && (
                avantStatus.needsAccountLink
                || avantStatus.accountActionRequired
                || avantStatus.avantLoginDialog
                || avantStatus.avantLoginEmailInputs > 0
            ));
            let ignorarLoginAvantBackground = deveIgnorarLoginAvantBackground();
            const resultadosMlVisiveis = !!(pronto && pronto.hasCards);
            const permitirReloadAvant = !mlFavoritosEmExecucao && opcoes.recarregarAvantSeAusente !== false;
            if (statusAvantProSemDadosColetaveis(avantStatus) && !resultadosMlVisiveis) {
                const mensagemAguardandoAvant = permitirReloadAvant
                    ? `Avant Pro ainda nao liberou dados para "${termo}". Aguardando e recarregando se necessario...`
                    : `Avant Pro ainda nao liberou dados para "${termo}". Vou continuar sem recarregar a pagina.`;
                mostrarBalaoFavoritosStatus(mensagemAguardandoAvant, {
                    larga: true,
                    titulo: 'Aguardando Avant Pro'
                });
                const statusAguardado = await aguardarAvantProNoWebview({
                    timeoutMs: permitirReloadAvant
                        ? (segundoPlano ? 95000 : 18000)
                        : (segundoPlano ? 22000 : 6500),
                    pollMs: segundoPlano ? 750 : 420,
                    recarregarSeAusente: permitirReloadAvant,
                    timeoutAposReloadMs: segundoPlano ? 45000 : 14000,
                    mensagemRecarregando: `Avant Pro ainda nao liberou dados para "${termo}". Recarregando Mercado Livre...`
                }).catch(() => null);
                if (statusAguardado) avantStatus = statusAguardado;
                verificarCancelamentoFavoritos();
                if (!segundoPlano && avantStatus && (
                    typeof statusAvantProPedeLoginOuVinculo === 'function'
                        ? statusAvantProPedeLoginOuVinculo(avantStatus)
                        : (avantStatus.needsAccountLink || avantStatus.accountActionRequired)
                )) {
                    avantStatus = await aguardarConexaoAvantProFavoritos(avantStatus, { termo });
                    await aguardarPrimeirosDadosAvantOuCardsWebview({
                        timeoutMs: segundoPlano ? 4500 : 1800,
                        idleMs: segundoPlano ? 320 : 180
                    }).catch(() => null);
                    avantStatus = await diagnosticarAvantProNoWebview().catch(() => avantStatus);
                }
            }
            ignorarLoginAvantBackground = deveIgnorarLoginAvantBackground();
            const avantProntoParaColeta = typeof statusAvantProTemDadosColetaveis === 'function'
                ? statusAvantProTemDadosColetaveis(avantStatus)
                : !!(avantStatus && (avantStatus.hasAvantData || avantStatus.rows > 0 || avantStatus.dataTextNodes > 0 || avantStatus.bodyDataLabels > 1));
            if (avantProntoParaColeta) {
                mostrarBalaoFavoritosStatus(`Avant Pro detectado. Coletando anuncios visiveis de "${termo}"...`);
            } else if (pronto && pronto.hasCards) {
                mostrarBalaoFavoritosStatus(`${pronto.cardCount || 0} anuncio(s) encontrados. Coletando dados visiveis...`);
            } else {
                mostrarBalaoFavoritosStatus(`Coletando resultados visiveis de "${termo}"...`);
            }
            verificarCancelamentoFavoritos();
            const maxAnunciosColeta = Math.max(20, Math.min(Number(opcoes.maxAnuncios) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80, 80));
            const maxCliquesAvantColeta = Math.min(
                100,
                Number(opcoes.maxCliquesAvant) || maxAnunciosColeta
            );

            let resultado = await extrairAnunciosWebviewVisivel({
                clicarAvant: false,
                ignorarLoginAvant: ignorarLoginAvantBackground,
                cliqueAvantForcadoDesativado: true,
                clicarCardsSemDados: false,
                permitirFerramentasAvant: false,
                permitirAutoLoginAvant: false,
                maxCliquesAvant: maxCliquesAvantColeta,
                fastLinks: false,
                timeoutMs: segundoPlano ? 9500 : 5500,
                aguardarAposCliqueAvant: avantProntoParaColeta ? 0 : 300,
                aguardarEstabilidadeAvant: avantProntoParaColeta
                    ? { minWaitMs: 0, stableMs: segundoPlano ? 420 : 240, maxWaitMs: segundoPlano ? 1400 : 700 }
                    : { minWaitMs: segundoPlano ? 350 : 160, stableMs: segundoPlano ? 520 : 320, maxWaitMs: segundoPlano ? 2200 : 1100 }
            }).catch(() => null);
            verificarCancelamentoFavoritos();
            const anunciosResultadoInicial = Array.isArray(resultado && resultado.anuncios) ? resultado.anuncios : [];
            if (resultado && !segundoPlano && (
                typeof statusAvantProPedeLoginOuVinculo === 'function'
                    ? statusAvantProPedeLoginOuVinculo(resultado)
                    : resultado.needsAvantLogin
            )) {
                if (opcoes.exigirAvantPro === true) {
                    if (typeof mostrarAcaoConectarAvantPro === 'function') {
                        mostrarAcaoConectarAvantPro(resultado);
                    }
                    statusFinal = await aguardarConexaoAvantProFavoritos(resultado, { termo });
                    reloadedAfterAvantLogin = reloadedAfterAvantLogin || !!(statusFinal && statusFinal.reloadedAfterAvantLogin);
                    const retryAposConexao = await extrairAnunciosWebviewVisivel({
                        clicarAvant: false,
                        ignorarLoginAvant: false,
                        cliqueAvantForcadoDesativado: true,
                        clicarCardsSemDados: false,
                        permitirFerramentasAvant: false,
                        permitirAutoLoginAvant: false,
                        fastLinks: false,
                        timeoutMs: segundoPlano ? 10000 : 6200,
                        aguardarAposCliqueAvant: 500,
                        aguardarEstabilidadeAvant: {
                            minWaitMs: segundoPlano ? 380 : 180,
                            stableMs: segundoPlano ? 520 : 320,
                            maxWaitMs: segundoPlano ? 2400 : 1200
                        }
                    }).catch(() => resultado);
                    if (retryAposConexao) resultado = retryAposConexao;
                } else {
                    mostrarBalaoFavoritosStatus('Avant Pro nao retornou dados coletaveis. Vou seguir apenas com os dados visiveis do Mercado Livre.', {
                        larga: true,
                        titulo: 'Avant Pro sem dados'
                    });
                    await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
                    await aguardarPrimeirosDadosAvantOuCardsWebview({ timeoutMs: 1100, idleMs: 180, acaoUsuario: leituraPaginaAutorizada }).catch(() => null);
                    const retryLoginAvant = await extrairAnunciosWebviewVisivel({
                        clicarAvant: false,
                        ignorarLoginAvant: segundoPlano,
                        clicarCardsSemDados: false,
                        permitirFerramentasAvant: false,
                        permitirAutoLoginAvant: false,
                        fastLinks: false,
                        timeoutMs: segundoPlano ? 10000 : 6200,
                        aguardarAposCliqueAvant: 500,
                        aguardarEstabilidadeAvant: {
                            minWaitMs: segundoPlano ? 380 : 180,
                            stableMs: segundoPlano ? 520 : 320,
                            maxWaitMs: segundoPlano ? 2400 : 1200
                        }
                    }).catch(() => null);
                    if (retryLoginAvant) resultado = retryLoginAvant;
                }
                verificarCancelamentoFavoritos();
                if (!segundoPlano && resultado && (
                    typeof statusAvantProPedeLoginOuVinculo === 'function'
                        ? statusAvantProPedeLoginOuVinculo(resultado)
                        : (resultado.needsAvantLogin || resultado.needsAccountLink || resultado.accountActionRequired)
                )) {
                    statusFinal = await aguardarConexaoAvantProFavoritos(resultado, { termo });
                    reloadedAfterAvantLogin = reloadedAfterAvantLogin || !!(statusFinal && statusFinal.reloadedAfterAvantLogin);
                    resultado = await extrairAnunciosWebviewVisivel({
                        clicarAvant: false,
                        cliqueAvantForcadoDesativado: true,
                        clicarCardsSemDados: false,
                        permitirFerramentasAvant: false,
                        permitirAutoLoginAvant: false,
                        fastLinks: false,
                        timeoutMs: segundoPlano ? 10000 : 6200,
                        aguardarAposCliqueAvant: 500,
                        aguardarEstabilidadeAvant: {
                            minWaitMs: segundoPlano ? 380 : 180,
                            stableMs: segundoPlano ? 520 : 320,
                            maxWaitMs: segundoPlano ? 2400 : 1200
                        }
                    }).catch(() => resultado);
                }
            }
            statusFinal = statusFinal || {};
            if (reloadedAfterAvantLogin || statusFinal.resumedAfterAvantLogin) {
                const retryRetomada = await extrairAnunciosWebviewVisivel({
                    clicarAvant: false,
                    ignorarLoginAvant: false,
                    cliqueAvantForcadoDesativado: true,
                    clicarCardsSemDados: false,
                    permitirFerramentasAvant: false,
                    permitirAutoLoginAvant: false,
                    fastLinks: false,
                    timeoutMs: segundoPlano ? 10000 : 6200,
                    aguardarAposCliqueAvant: 450,
                    aguardarEstabilidadeAvant: {
                        minWaitMs: segundoPlano ? 380 : 180,
                        stableMs: segundoPlano ? 520 : 320,
                        maxWaitMs: segundoPlano ? 2400 : 1200
                    }
                }).catch(() => null);
                if (retryRetomada) resultado = retryRetomada;
            }
            let anunciosAvant = (resultado && resultado.anuncios) || [];
            if (resultado && resultado.needsLogin) {
                throw erroLoginMercadoLivreFavoritos();
            }
            if (resultado && resultado.noResults && !anunciosAvant.length) {
                const erro = new Error('Mercado Livre nao encontrou resultados para esta pesquisa.');
                erro.semResultadosMl = true;
                throw erro;
            }

            if (!anunciosAvant.length) {
                await aguardarPrimeirosDadosAvantOuCardsWebview({
                    timeoutMs: segundoPlano ? 6500 : 1800,
                    idleMs: segundoPlano ? 360 : 220,
                    acaoUsuario: leituraPaginaAutorizada
                }).catch(() => null);
                verificarCancelamentoFavoritos();
                const retry = await extrairAnunciosWebviewVisivel({
                    clicarAvant: false,
                    ignorarLoginAvant: segundoPlano,
                    clicarCardsSemDados: false,
                    permitirFerramentasAvant: false,
                    permitirAutoLoginAvant: false,
                    fastLinks: false,
                    timeoutMs: segundoPlano ? 9500 : 5200,
                    aguardarAposCliqueAvant: 350,
                    aguardarEstabilidadeAvant: {
                        minWaitMs: segundoPlano ? 380 : 180,
                        stableMs: segundoPlano ? 520 : 320,
                        maxWaitMs: segundoPlano ? 2400 : 1000
                    }
                }).catch(() => null);
                verificarCancelamentoFavoritos();
                anunciosAvant = mesclarAnunciosAvant(anunciosAvant, (retry && retry.anuncios) || []);
                if (retry && (
                    typeof statusAvantProPedeLoginOuVinculo === 'function'
                        ? statusAvantProPedeLoginOuVinculo(retry)
                        : (retry.needsAvantLogin || retry.needsAccountLink || retry.accountActionRequired)
                )) {
                    if (opcoes.exigirAvantPro === true) {
                        if (typeof mostrarAcaoConectarAvantPro === 'function') {
                            mostrarAcaoConectarAvantPro(retry);
                        }
                        throw erroLoginAvantProFavoritos(`Avant Pro nao retornou dados coletaveis para "${termo}". Confirme manualmente o login no navegador interno e tente Fazer favoritos novamente.`);
                    }
                    if (!segundoPlano || !anunciosAvant.length) {
                        if (!segundoPlano) {
                            await aguardarConexaoAvantProFavoritos(retry, { termo });
                        }
                    }
                    const retryDepoisAvant = await extrairAnunciosWebviewVisivel({
                        clicarAvant: false,
                        ignorarLoginAvant: segundoPlano,
                        clicarCardsSemDados: false,
                        permitirFerramentasAvant: false,
                        permitirAutoLoginAvant: false,
                        fastLinks: false,
                        timeoutMs: segundoPlano ? 10000 : 6200,
                        aguardarAposCliqueAvant: 500,
                        aguardarEstabilidadeAvant: {
                            minWaitMs: segundoPlano ? 380 : 180,
                            stableMs: segundoPlano ? 520 : 320,
                            maxWaitMs: segundoPlano ? 2400 : 1200
                        }
                    }).catch(() => null);
                    anunciosAvant = mesclarAnunciosAvant(anunciosAvant, (retryDepoisAvant && retryDepoisAvant.anuncios) || []);
                }
                if (retry && retry.noResults && !anunciosAvant.length) {
                    const erro = new Error('Mercado Livre nao encontrou resultados para esta pesquisa.');
                    erro.semResultadosMl = true;
                    throw erro;
                }
            }

            if (!anunciosAvant.length && opcoes.exigirAvantPro !== true && typeof extrairCardsMercadoLivreBasicoWebview === 'function') {
                mostrarBalaoFavoritosStatus(`Lendo cards visiveis do Mercado Livre para "${termo}"...`, {
                    larga: true
                });
                const basico = await extrairCardsMercadoLivreBasicoWebview({
                    limite: Math.max(60, Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80)
                }).catch(() => null);
                anunciosAvant = mesclarAnunciosAvant(anunciosAvant, (basico && basico.anuncios) || []);
                verificarCancelamentoFavoritos();
            }

            if (opcoes.exigirAvantPro === true && typeof diagnosticarAvantProNoWebview === 'function' && typeof statusAvantProPedeLoginOuVinculo === 'function') {
                const statusAntesRolagem = await diagnosticarAvantProNoWebview().catch(() => null);
                if (statusAvantProPedeLoginOuVinculo(statusAntesRolagem)) {
                    if (typeof mostrarAcaoConectarAvantPro === 'function') {
                        mostrarAcaoConectarAvantPro(statusAntesRolagem);
                    }
                    throw erroLoginAvantProFavoritos(`Avant Pro nao retornou dados coletaveis para "${termo}". Confirme manualmente o login no navegador interno e tente Fazer favoritos novamente.`);
                }
            }

            const alvoColetaAvant = maxAnunciosColeta;
            const maxPosicoesRolagem = Number(opcoes.maxPosicoesRolagem) || (segundoPlano ? 28 : 24);
            if (typeof coletarDadosAvantComRolagem === 'function' && (anunciosAvant.length || resultadosMlVisiveis) && anunciosAvant.length < alvoColetaAvant) {
                mostrarBalaoFavoritosStatus(
                    anunciosAvant.length
                        ? `${anunciosAvant.length} anuncio(s) encontrados. Percorrendo a primeira pagina para completar a coleta...`
                        : `Percorrendo a primeira pagina de "${termo}" para coletar os anuncios visiveis...`,
                    { larga: true }
                );
                const anunciosRolagem = await coletarDadosAvantComRolagem({
                    maxAnuncios: maxAnunciosColeta,
                    maxPosicoes: maxPosicoesRolagem,
                    clicarCardsSemDados: false,
                    permitirFerramentasAvant: false,
                    permitirAutoLoginAvant: false,
                    permitirBasico: opcoes.exigirAvantPro !== true
                }).catch((err) => {
                    if (erroEhLoginAvantProFavoritos(err) || (err && err.loginAvantProNecessario)) {
                        throw err;
                    }
                    return [];
                });
                anunciosAvant = mesclarAnunciosAvant(anunciosAvant, anunciosRolagem);
                verificarCancelamentoFavoritos();
            }

            if (!anunciosAvant.length && opcoes.exigirAvantPro === true) {
                const diagnosticoFinal = typeof diagnosticarAvantProNoWebview === 'function'
                    ? await diagnosticarAvantProNoWebview().catch(() => null)
                    : null;
                const cardCount = Number((diagnosticoFinal && diagnosticoFinal.cardCount) || 0);
                const rows = Number((diagnosticoFinal && diagnosticoFinal.rows) || 0);
                const widgets = Number((diagnosticoFinal && diagnosticoFinal.widgets) || 0);
                const labels = Number((diagnosticoFinal && (diagnosticoFinal.bodyDataLabels || diagnosticoFinal.avantLabels)) || 0);
                throw erroColetaMercadoLivreFavoritos(
                    `Nao consegui transformar os resultados visiveis em anuncios para "${termo}". Diagnostico: cards=${cardCount}, linhasAvant=${rows}, widgetsAvant=${widgets}, rotulosAvant=${labels}.`
                );
            }

            validarAnunciosFavoritosPertencemAoTermo(termo, anunciosAvant);
            anunciosAvant = aplicarCacheAvantAosAnuncios(anunciosAvant, {
                termo,
                sku: opcoes.sku || termo
            });
            salvarCacheAvantDosAnuncios(anunciosAvant, {
                termo,
                sku: opcoes.sku || termo
            });
            const totalComDadosAvant = anunciosAvant.filter(item => {
                const fonte = normalizarFonte(item && (item.vendasFonte || item.vendas_fonte || ''));
                return fonteVendasConfiavel(fonte) || !!(item && (item.vendedor || item.data_criacao || item.cacheAvant));
            }).length;
            if (anunciosAvant.length) {
                mostrarBalaoFavoritosStatus(`${anunciosAvant.length} anuncio(s) encontrados. ${totalComDadosAvant} com dados Avant. Coletando mais resultados em segundo plano...`);
            }
            return anunciosAvant.map(item => {
                const fonteVendas = normalizarFonte(item && (item.vendasFonte || item.vendas_fonte || ''));
                const vendas = fonteVendasConfiavel(fonteVendas) ? parseNumeroVendas(item && item.vendas) : null;
                return {
                    ...item,
                    origem_dados: 'avantpro',
                    vendas,
                    vendasFonte: vendas !== null ? fonteVendas : '',
                    vendas_fonte: vendas !== null ? fonteVendas : ''
                };
            });
        }

        async function buscarAnunciosFavoritosPorTermoFluxoControlado(termo, opcoes = {}) {
            const termoPesquisa = String(termo || '').trim();
            if (!termoPesquisa) return [];
            if (typeof coletarPrimeiraPaginaFavoritosControlada !== 'function') return null;
            if (typeof abrirMercadoLivreNoPrograma !== 'function') return null;
            const limite = Number(opcoes.maxAnuncios) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80;
            const url = typeof construirUrlPesquisaMercadoLivre === 'function'
                ? construirUrlPesquisaMercadoLivre(termoPesquisa)
                : `https://lista.mercadolivre.com.br/${encodeURIComponent(termoPesquisa)}`;
            if (typeof mlUrlInput !== 'undefined' && mlUrlInput) {
                mlUrlInput.value = url;
            }
            mostrarBalaoFavoritosStatus(`Abrindo "${termoPesquisa}" e coletando a primeira pagina pelo fluxo novo...`, {
                manterNavegadorVisivel: true,
                larga: true,
                titulo: opcoes.titulo || 'Coleta da primeira pagina'
            });
            const abriu = await abrirMercadoLivreNoPrograma({
                termoPesquisa: termoPesquisa,
                titulo: opcoes.titulo || 'Fazendo Favorito! Aguarde...',
                subtitulo: opcoes.subtitulo || `Pesquisa: ${termoPesquisa}`,
                mostrarFavoritos: true,
                browserCompleto: true,
                forcarExibicao: true,
                apenasAbrirUrl: true,
                confirmarPesquisa: false,
                agendarPosicaoAntes: false,
                reposicionarDepois: false
            }).catch(() => false);
            if (!abriu) return null;
            await new Promise(resolve => setTimeout(resolve, Number(opcoes.aguardarPesquisaMs) || 1200));
            const resultado = await coletarPrimeiraPaginaFavoritosControlada({
                maxAnuncios: limite,
                tempoLimiteMs: Number(opcoes.tempoLimiteMs) || 180000,
                maxPassadas: Number(opcoes.maxPassadas) || 3,
                loteCliques: opcoes.loteCliques === undefined ? 6 : Number(opcoes.loteCliques),
                onProgress: (progresso) => {
                    if (typeof formatarProgressoColetaPrimeiraPaginaFavoritos === 'function') {
                        mostrarBalaoFavoritosStatus(formatarProgressoColetaPrimeiraPaginaFavoritos(opcoes.campo || 1, progresso), {
                            manterNavegadorVisivel: true,
                            larga: true,
                            titulo: 'Coleta da primeira pagina'
                        });
                    }
                }
            }).catch((err) => {
                console.warn('Coleta controlada pelo wrapper legado falhou:', err);
                return null;
            });
            let anuncios = Array.isArray(resultado && resultado.anuncios) ? resultado.anuncios : [];
            if (!anuncios.length && typeof extrairCardsMercadoLivreBasicoWebview === 'function') {
                const basico = await extrairCardsMercadoLivreBasicoWebview({ limite }).catch(() => null);
                anuncios = Array.isArray(basico && basico.anuncios) ? basico.anuncios : [];
            }
            if (!anuncios.length && typeof extrairBaseMercadoLivreEmergencialWebview === 'function') {
                const emergencia = await extrairBaseMercadoLivreEmergencialWebview({
                    limite,
                    timeoutMs: 12000
                }).catch(() => null);
                anuncios = Array.isArray(emergencia && emergencia.anuncios) ? emergencia.anuncios : [];
            }
            if (typeof aplicarCacheAvantAosAnuncios === 'function') {
                anuncios = aplicarCacheAvantAosAnuncios(anuncios, {
                    termo: termoPesquisa,
                    sku: opcoes.sku || termoPesquisa
                });
            }
            if (typeof salvarCacheAvantDosAnuncios === 'function') {
                salvarCacheAvantDosAnuncios(anuncios, {
                    termo: termoPesquisa,
                    sku: opcoes.sku || termoPesquisa
                });
            }
            return anuncios.slice(0, limite).map(item => ({
                ...item,
                origem_dados: item && item.origem_dados || 'primeira_pagina_controlada_wrapper'
            }));
        }

        async function buscarAnunciosFavoritosPorTermo(termo, opcoes = {}) {
            const anunciosFluxoNovo = await buscarAnunciosFavoritosPorTermoFluxoControlado(termo, opcoes);
            if (Array.isArray(anunciosFluxoNovo)) return anunciosFluxoNovo;

            if (!(hasInternalBrowserApi || usarNavegadorMlNoShellElectron())) {
                throw erroColetaMercadoLivreFavoritos('Navegador interno indisponivel para coletar dados pelo Avant Pro.');
            }

            const tentarColetaAvant = async (extras = {}) => {
                mostrarBalaoFavoritosStatus(`Abrindo "${termo}" e lendo vendas pelo Avant Pro...`);
                const anunciosAvant = await buscarAnunciosFavoritosPorTermoAvant(termo, {
                    ...opcoes,
                    ...extras,
                    exigirAvantPro: true
                });
                verificarCancelamentoFavoritos();
                return anunciosAvant;
            };

            try {
                const anunciosAvant = await tentarColetaAvant();
                if (anunciosAvant.length) return anunciosAvant;

                const statusFinalAvantWrapper = typeof diagnosticarAvantProNoWebview === 'function'
                    ? await diagnosticarAvantProNoWebview().catch(() => null)
                    : null;
                const precisaConectarAvantWrapper = !!(statusFinalAvantWrapper && (
                    typeof statusAvantProPedeLoginOuVinculo === 'function'
                        ? statusAvantProPedeLoginOuVinculo(statusFinalAvantWrapper)
                        : (statusFinalAvantWrapper.needsAccountLink || statusFinalAvantWrapper.accountActionRequired)
                ));
                if (precisaConectarAvantWrapper) {
                    await aguardarConexaoAvantProFavoritos(statusFinalAvantWrapper, { termo });
                    const retryAvantConectado = await tentarColetaAvant({ retryAvantConectado: true });
                    if (retryAvantConectado.length) return retryAvantConectado;
                }
                throw erroColetaMercadoLivreFavoritos(`Avant Pro nao retornou anuncios coletaveis para "${termo}". Confira se o Mercado Livre carregou resultados e se o Avant Pro esta conectado no navegador interno.`);
            } catch (err) {
                if (mlFavoritosCancelado || (err && err.canceladoFavoritos)) throw err;
                if (erroEhLoginMercadoLivreFavoritos(err)) {
                    mostrarBalaoFavoritosStatus(err && err.message ? err.message : 'O Mercado Livre pediu login/verificacao no navegador interno.', {
                        erro: true,
                        larga: true,
                        titulo: 'Login Mercado Livre necessario',
                        tempoMs: 12000
                    });
                    throw err;
                }
                if (erroEhLoginAvantProFavoritos(err)) {
                    const statusLogin = (err && err.avantStatus) || {
                        accountActionRequired: true,
                        message: err && err.message ? err.message : 'Avant Pro nao retornou dados coletaveis.'
                    };
                    await aguardarConexaoAvantProFavoritos(statusLogin, { termo });
                    const retryAvantConectado = await tentarColetaAvant({ retryAvantConectado: true });
                    if (retryAvantConectado.length) return retryAvantConectado;
                    throw erroLoginAvantProFavoritos(`Avant Pro ainda nao retornou dados coletaveis para "${termo}". Confirme manualmente no navegador interno se o Avant Pro esta pronto e tente novamente.`);
                }
                if (err && err.semResultadosMl) {
                    const statusSemResultados = typeof diagnosticarAvantProNoWebview === 'function'
                        ? await diagnosticarAvantProNoWebview().catch(() => null)
                        : null;
                    if (statusSemResultados && statusSemResultados.loadingScreen && Number(statusSemResultados.cardCount || 0) <= 0) {
                        throw erroColetaMercadoLivreFavoritos(`Mercado Livre ficou carregando a busca "${termo}" e nao exibiu anuncios. Reabra a pesquisa no navegador interno e tente novamente.`);
                    }
                    throw erroColetaMercadoLivreFavoritos(`O Mercado Livre indicou sem resultados para "${termo}" no navegador interno. Ajuste a pesquisa e tente novamente.`);
                }
                if (erroEhColetaMercadoLivreFavoritos(err)) throw err;
                throw erroColetaMercadoLivreFavoritos(`Nao consegui ler dados coletaveis do Avant Pro para "${termo}". ${err && err.message ? err.message : 'Confira o navegador interno e tente novamente.'}`);
            }
        }

        async function buscarAnunciosFavoritosPorTermoComAvantObrigatorio(termo, opcoes = {}) {
            const anuncios = await buscarAnunciosFavoritosPorTermo(termo, {
                ...opcoes,
                exigirAvantPro: true
            });
            if (!anuncios.length) {
                throw erroColetaMercadoLivreFavoritos(`Avant Pro nao retornou anuncios coletaveis para "${termo}". Confira se o Mercado Livre carregou resultados e se o Avant Pro esta conectado no navegador interno.`);
            }
            return anuncios;
        }

        function chaveAnuncioFavoritos(anuncio) {
            if (typeof chaveCanonicaAnuncioFavoritos === 'function') {
                return chaveCanonicaAnuncioFavoritos(anuncio);
            }
            const id = extrairItemIdAnuncio(anuncio && (anuncio.id || anuncio.url || anuncio.permalink || anuncio.link)) || String(anuncio && anuncio.id || '').trim().toUpperCase().replace(/-/g, '');
            if (id) return `mlb:${id}`;
            const url = String(anuncio && (anuncio.url || anuncio.permalink || anuncio.link) || '').split('#')[0].trim().toLowerCase();
            return url ? `link:${url}` : '';
        }

        function chavesAnuncioFavoritos(anuncio) {
            if (!anuncio) return [];
            const chaves = new Set();
            const id = extrairItemIdAnuncio(anuncio.id || anuncio.url || anuncio.permalink || anuncio.link) || String(anuncio.id || '').trim().toUpperCase().replace(/-/g, '');
            const chave = chaveAnuncioFavoritos(anuncio);
            if (id) chaves.add(`id:${id}`);
            [anuncio.url, anuncio.permalink, anuncio.link].forEach(urlValor => {
                const url = typeof limparLinkProdutoMercadoLivreFavoritos === 'function'
                    ? limparLinkProdutoMercadoLivreFavoritos(urlValor, id).toLowerCase()
                    : String(urlValor || '').split('#')[0].trim().toLowerCase();
                if (url) chaves.add(`url:${url}`);
            });
            if (chave) chaves.add(`chave:${chave}`);
            return Array.from(chaves);
        }

        function construirUrlAnuncioFavoritosRankingPorId(itemId) {
            if (typeof construirUrlAnuncioFavoritosPorItemId === 'function') {
                return construirUrlAnuncioFavoritosPorItemId(itemId);
            }
            if (typeof construirUrlProdutoMercadoLivreCanonico === 'function') {
                return construirUrlProdutoMercadoLivreCanonico(itemId);
            }
            const id = String(itemId || '').trim().toUpperCase().replace('-', '');
            const digitos = id.replace(/^MLB/i, '');
            if (!/^MLB\d+$/i.test(id) || digitos.length < 8) return '';
            return `https://produto.mercadolivre.com.br/${id.replace('MLB', 'MLB-')}`;
        }

        function normalizarUrlAnuncioFavoritosRanking(valor, itemId = '') {
            const texto = String(valor || '').trim();
            if (/^\/\//.test(texto)) return `https:${texto}`;
            if (/^https?:\/\//i.test(texto)) {
                return typeof limparLinkProdutoMercadoLivreFavoritos === 'function'
                    ? (limparLinkProdutoMercadoLivreFavoritos(texto, itemId) || texto)
                    : texto;
            }
            const id = extrairItemIdAnuncio(texto) || itemId;
            return construirUrlAnuncioFavoritosRankingPorId(id);
        }

        function tituloAnuncioFavoritosPrecisaComplemento(titulo, itemId = '') {
            const texto = String(titulo || '').replace(/\s+/g, ' ').trim();
            if (!texto) return true;
            if (tituloPareceFiltroOuCategoriaMl(texto)) return true;
            const normalizado = normalizarTextoMl(texto);
            if (/^jm$/i.test(normalizado)) return true;
            if (/^mlb\d+$/i.test(normalizado.replace(/-/g, ''))) return true;
            return !!itemId && normalizado.length <= 3;
        }

        function extrairTituloAnuncioFavoritosPorLink(link) {
            let texto = String(link || '');
            if (!texto) return '';
            try { texto = decodeURIComponent(texto); } catch (_err) {}
            const match = texto.match(/\/MLB-?\d+-([^?#]+?)(?:-_?JM|_JM|$)/i);
            if (!match || !match[1]) return '';
            const titulo = String(match[1] || '')
                .replace(/[-_]+/g, ' ')
                .replace(/\bJM\b/ig, ' ')
                .replace(/\s+/g, ' ')
                .trim();
            return tituloAnuncioFavoritosPrecisaComplemento(titulo) ? '' : titulo.slice(0, 240);
        }

        function anuncioFavoritosCandidatoRanking(anuncio) {
            if (!anuncio) return false;
            const id = extrairItemIdAnuncio(anuncio.id || anuncio.url || anuncio.permalink || anuncio.link)
                || String(anuncio.id || '').trim().toUpperCase().replace(/-/g, '');
            if (id) return true;
            const link = typeof limparLinkProdutoMercadoLivreFavoritos === 'function'
                ? limparLinkProdutoMercadoLivreFavoritos(anuncio.url || anuncio.permalink || anuncio.link)
                : String(anuncio.url || anuncio.permalink || anuncio.link || '').trim();
            return !!link;
        }

        function aplicarMetadataBasicaAnuncioFavoritos(alvo, fonte) {
            if (!alvo || !fonte) return false;
            let alterou = false;
            const id = extrairItemIdAnuncio(fonte.id || fonte.mlb || fonte.item_id || fonte.url || fonte.permalink || fonte.link)
                || String(fonte.id || fonte.mlb || fonte.item_id || '').trim().toUpperCase().replace(/-/g, '');
            if (id && !alvo.id) {
                alvo.id = id;
                alterou = true;
            }
            const idFinal = alvo.id || id;
            const url = normalizarUrlAnuncioFavoritosRanking(fonte.url || fonte.permalink || fonte.link, idFinal);
            const urlAtual = normalizarUrlAnuncioFavoritosRanking(alvo.url || alvo.permalink || alvo.link, idFinal);
            if (url && (!urlAtual || /-_JM$/i.test(urlAtual))) {
                alvo.url = url;
                alvo.permalink = url;
                alvo.link = url;
                alvo.link_normalizado = typeof limparLinkProdutoMercadoLivreFavoritos === 'function' ? limparLinkProdutoMercadoLivreFavoritos(url, idFinal) : url;
                alvo.linkFonte = fonte.linkFonte || fonte.link_fonte || fonte.source || fonte.origem_dados || 'mercado_livre_api';
                alvo.link_fonte = alvo.linkFonte;
                alterou = true;
            } else if (urlAtual) {
                if (!alvo.url) alvo.url = urlAtual;
                if (!alvo.permalink) alvo.permalink = urlAtual;
                if (!alvo.link) alvo.link = urlAtual;
            }
            const titulo = String(fonte.titulo || fonte.title || '').replace(/\s+/g, ' ').trim()
                || extrairTituloAnuncioFavoritosPorLink(fonte.url || fonte.permalink || fonte.link || url);
            if (titulo && !tituloAnuncioFavoritosPrecisaComplemento(titulo, idFinal) && tituloAnuncioFavoritosPrecisaComplemento(alvo.titulo || alvo.title, idFinal)) {
                alvo.titulo = titulo;
                alvo.title = titulo;
                alvo.tituloFonte = fonte.tituloFonte || fonte.titulo_fonte || fonte.source || fonte.origem_dados || 'mercado_livre_api';
                alvo.titulo_fonte = alvo.tituloFonte;
                alterou = true;
            }
            if (preencherImagemAnuncioFavoritos(alvo, fonte)) {
                alvo.fotoFonte = fonte.fotoFonte || fonte.foto_fonte || fonte.source || fonte.origem_dados || 'mercado_livre_api';
                alvo.foto_fonte = alvo.fotoFonte;
                alterou = true;
            }
            return alterou;
        }

        function normalizarAnuncioFavoritosPesquisa(anuncio, sku, pesquisa) {
            const id = extrairItemIdAnuncio(anuncio && (anuncio.id || anuncio.url || anuncio.permalink || anuncio.link))
                || String(anuncio && anuncio.id || '').trim().toUpperCase().replace(/-/g, '');
            const urlBase = normalizarUrlAnuncioFavoritosRanking(anuncio && (anuncio.url || anuncio.permalink || anuncio.link), id);
            const vendasFonteOriginal = normalizarFonte(anuncio && (anuncio.vendasFonte || anuncio.vendas_fonte || 'api_search'));
            const vendasFonte = fonteVendasConfiavel(vendasFonteOriginal) ? vendasFonteOriginal : '';
            const vendasAvant = fonteVendasConfiavel(vendasFonte) ? parseNumeroVendas(anuncio && anuncio.vendas) : null;
            const mediaMensalValor = parseNumeroDecimalFavoritos(anuncio && (
                anuncio.media_mensal ??
                anuncio.ritmo_atual ??
                anuncio.ritmo_vendas_mes ??
                anuncio.media_vendas_mensal ??
                ''
            ));
            const mediaMensalBruta = Number.isFinite(mediaMensalValor)
                ? mediaMensalValor
                : (anuncio && (
                    anuncio.media_mensal ??
                    anuncio.ritmo_atual ??
                    anuncio.ritmo_vendas_mes ??
                    anuncio.media_vendas_mensal ??
                    ''
                ));
            const mediaMensalFonte = normalizarFonte(anuncio && (
                anuncio.media_mensal_fonte ||
                anuncio.ritmo_atual_fonte ||
                anuncio.ritmo_vendas_mes_fonte ||
                ''
            )) || (Number.isFinite(mediaMensalValor) && vendasFonte ? vendasFonte : '');
            const imagem = obterImagemAnuncioFavoritos(anuncio);
            const precos = obterPrecosAnuncioFavoritos(anuncio);
            const descricao = obterDescricaoAnuncioFavoritosIa(anuncio);
            let tituloBruto = String(anuncio && (anuncio.titulo || anuncio.title) || '').trim();
            if (tituloAnuncioFavoritosPrecisaComplemento(tituloBruto, id)) {
                tituloBruto = extrairTituloAnuncioFavoritosPorLink(anuncio && (anuncio.url || anuncio.permalink || anuncio.link) || urlBase);
            }
            const tituloSeguro = typeof tituloValidoFavoritosCanonico === 'function' && !tituloValidoFavoritosCanonico(tituloBruto, id)
                ? ''
                : tituloBruto;
            const chaveCanonica = typeof chaveCanonicaAnuncioFavoritos === 'function'
                ? chaveCanonicaAnuncioFavoritos(anuncio)
                : chaveAnuncioFavoritos(anuncio);
            const linkNormalizado = typeof limparLinkProdutoMercadoLivreFavoritos === 'function'
                ? limparLinkProdutoMercadoLivreFavoritos(urlBase, id)
                : urlBase;
            const fontePreco = fontePrecoFavoritos(anuncio);
            const vendedorFonte = normalizarFonte(anuncio && (anuncio.vendedorFonte || anuncio.vendedor_fonte || (anuncio.vendedor ? 'api_search' : '')));
            const estadoQualidade = typeof classificarQualidadeAnuncioFavoritosCanonico === 'function'
                ? classificarQualidadeAnuncioFavoritosCanonico(anuncio)
                : (anuncio && anuncio.estado_qualidade || '');
            return {
                ...anuncio,
                id,
                mlb: id,
                chave_canonica: chaveCanonica,
                chaveCanonica,
                sku_favorito: sku,
                titulo: tituloSeguro,
                title: tituloSeguro,
                tituloFonte: anuncio && (anuncio.tituloFonte || anuncio.titulo_fonte || ''),
                titulo_fonte: anuncio && (anuncio.tituloFonte || anuncio.titulo_fonte || ''),
                descricao,
                url: urlBase,
                permalink: urlBase,
                link: urlBase,
                link_normalizado: linkNormalizado,
                linkFonte: anuncio && (anuncio.linkFonte || anuncio.link_fonte || ''),
                link_fonte: anuncio && (anuncio.linkFonte || anuncio.link_fonte || ''),
                imagem,
                thumbnail: imagem || (anuncio && anuncio.thumbnail) || '',
                foto: imagem || (anuncio && anuncio.foto) || '',
                fotoFonte: anuncio && (anuncio.fotoFonte || anuncio.foto_fonte || ''),
                foto_fonte: anuncio && (anuncio.fotoFonte || anuncio.foto_fonte || ''),
                preco: precos.preco !== null ? precos.preco : (anuncio && (anuncio.preco ?? anuncio.price ?? '')),
                price: precos.promocional !== null ? precos.promocional : (precos.preco !== null ? precos.preco : (anuncio && (anuncio.price ?? anuncio.preco ?? ''))),
                preco_original: precos.promocional !== null ? precos.preco : (anuncio && (anuncio.preco_original ?? anuncio.original_price ?? '')),
                original_price: precos.promocional !== null ? precos.preco : (anuncio && (anuncio.original_price ?? anuncio.preco_original ?? '')),
                preco_promocional: precos.promocional !== null ? precos.promocional : (anuncio && (anuncio.preco_promocional ?? anuncio.promotional_price ?? '')),
                promotional_price: precos.promocional !== null ? precos.promocional : (anuncio && (anuncio.promotional_price ?? anuncio.preco_promocional ?? '')),
                moeda: anuncio && (anuncio.moeda || anuncio.currency_id || anuncio.currency || 'BRL'),
                currency_id: anuncio && (anuncio.currency_id || anuncio.moeda || anuncio.currency || 'BRL'),
                discount_pct: precos.desconto || '',
                fonte_preco: fontePreco,
                precoFonte: anuncio && (anuncio.precoFonte || anuncio.preco_fonte || anuncio.fonte_preco || fontePreco),
                preco_fonte: anuncio && (anuncio.precoFonte || anuncio.preco_fonte || anuncio.fonte_preco || fontePreco),
                parcelamento_sem_juros: obterParcelamentoSemJurosFavoritos(anuncio),
                tipo_anuncio: obterTipoAnuncioFavoritos(anuncio),
                listing_type_id: anuncio && (anuncio.listing_type_id || anuncio.listingTypeId || ''),
                listing_type_name: anuncio && (anuncio.listing_type_name || anuncio.tipo_anuncio || ''),
                shipping: anuncio && (anuncio.shipping || anuncio.shipping_info || anuncio.shippingInfo || null),
                logistic_type: anuncio && (anuncio.logistic_type || anuncio.logisticType || anuncio.shipping_logistic_type || ''),
                shipping_mode: anuncio && (anuncio.shipping_mode || anuncio.shippingMode || ''),
                is_full: temIndicadorFullFavoritos(anuncio) ? obterFullAnuncioFavoritos(anuncio) : '',
                condicao: obterCondicaoAnuncioFavoritos(anuncio),
                condition: obterCondicaoAnuncioFavoritos(anuncio),
                item_condition: obterCondicaoAnuncioFavoritos(anuncio),
                vendedor: String(anuncio && anuncio.vendedor || '').trim(),
                vendedorFonte,
                vendedor_fonte: vendedorFonte,
                vendas: vendasAvant,
                vendasFonte,
                vendas_fonte: vendasFonte,
                media_mensal: mediaMensalBruta,
                ritmo_atual: mediaMensalBruta,
                ritmo_vendas_mes: mediaMensalBruta,
                media_mensal_fonte: mediaMensalFonte,
                ritmo_atual_fonte: mediaMensalFonte,
                visitas: anuncio && (anuncio.visitas ?? anuncio.views ?? ''),
                data_criacao: anuncio && (anuncio.data_criacao || anuncio.date_created || ''),
                dataCriacaoFonte: anuncio && (anuncio.dataCriacaoFonte || anuncio.data_criacao_fonte || ''),
                data_criacao_fonte: anuncio && (anuncio.dataCriacaoFonte || anuncio.data_criacao_fonte || ''),
                estado_qualidade: estadoQualidade,
                suspeito: !!(anuncio && anuncio.suspeito),
                posicao: Number(anuncio && anuncio.posicao) || 9999,
                pesquisas_origem: [pesquisa.termo],
                campos_origem: [`Pesquisa ${pesquisa.campo}`]
            };
        }

        function deduplicarAnunciosFavoritos(anuncios) {
            const mapa = new Map();
            anuncios.forEach(anuncio => {
                const chave = chaveAnuncioFavoritos(anuncio);
                if (!chave) return;
                const atual = mapa.get(chave);
                if (!atual) {
                    mapa.set(chave, { ...anuncio });
                    return;
                }
                aplicarMetadataBasicaAnuncioFavoritos(atual, anuncio);
                if (tituloAnuncioFavoritosPrecisaComplemento(atual.titulo, atual.id) && anuncio.titulo && !tituloAnuncioFavoritosPrecisaComplemento(anuncio.titulo, anuncio.id)) atual.titulo = anuncio.titulo;
                if (!atual.url && anuncio.url) atual.url = anuncio.url;
                if (!atual.id && anuncio.id) atual.id = anuncio.id;
                const descricao = obterDescricaoAnuncioFavoritosIa(anuncio);
                if (!atual.descricao && descricao) atual.descricao = descricao;
                preencherImagemAnuncioFavoritos(atual, anuncio);
                preencherPrecoAnuncioFavoritos(atual, anuncio);
                preencherTipoAnuncioFavoritos(atual, anuncio);
                preencherCondicaoAnuncioFavoritos(atual, anuncio);
                if (!atual.data_criacao && anuncio.data_criacao) atual.data_criacao = anuncio.data_criacao;
                if (deveAtualizarVendedor(atual.vendedor, atual.vendedorFonte, anuncio.vendedor, anuncio.vendedorFonte)) {
                    atual.vendedor = anuncio.vendedor;
                    atual.vendedorFonte = anuncio.vendedorFonte;
                }
                if (deveAtualizarVendas(atual.vendas, atual.vendasFonte, anuncio.vendas, anuncio.vendasFonte)) {
                    atual.vendas = anuncio.vendas;
                    atual.vendasFonte = anuncio.vendasFonte;
                }
                const mediaAtual = parseNumeroDecimalFavoritos(atual.media_mensal ?? atual.ritmo_atual ?? atual.ritmo_vendas_mes ?? '');
                const mediaNova = parseNumeroDecimalFavoritos(anuncio.media_mensal ?? anuncio.ritmo_atual ?? anuncio.ritmo_vendas_mes ?? '');
                const fonteMediaAtual = normalizarFonte(atual.media_mensal_fonte || atual.ritmo_atual_fonte || '');
                const fonteMediaNova = normalizarFonte(anuncio.media_mensal_fonte || anuncio.ritmo_atual_fonte || anuncio.vendasFonte || '');
                if (Number.isFinite(mediaNova) && (!Number.isFinite(mediaAtual) || (!fonteVendasConfiavel(fonteMediaAtual) && fonteVendasConfiavel(fonteMediaNova)))) {
                    atual.media_mensal = mediaNova;
                    atual.ritmo_atual = mediaNova;
                    atual.ritmo_vendas_mes = mediaNova;
                    atual.media_mensal_fonte = fonteMediaNova || fonteMediaAtual;
                    atual.ritmo_atual_fonte = fonteMediaNova || fonteMediaAtual;
                }
                if ((atual.visitas === null || atual.visitas === undefined || atual.visitas === '') && anuncio.visitas !== null && anuncio.visitas !== undefined && anuncio.visitas !== '') {
                    atual.visitas = anuncio.visitas;
                }
                atual.posicao = Math.min(Number(atual.posicao) || 9999, Number(anuncio.posicao) || 9999);
                (anuncio.pesquisas_origem || []).forEach(termo => {
                    if (termo && !atual.pesquisas_origem.includes(termo)) atual.pesquisas_origem.push(termo);
                });
                (anuncio.campos_origem || []).forEach(campo => {
                    if (campo && !atual.campos_origem.includes(campo)) atual.campos_origem.push(campo);
                });
            });
            return Array.from(mapa.values());
        }

        function criarContextoEnriquecimentoFavoritosExecucao() {
            const criarLimitador = (limite) => {
                const estado = { limite: Math.max(1, Number(limite) || 1), ativos: 0, fila: [] };
                const liberar = () => {
                    while (estado.ativos < estado.limite && estado.fila.length) {
                        const entrada = estado.fila.shift();
                        estado.ativos += 1;
                        Promise.resolve()
                            .then(entrada.tarefa)
                            .then(entrada.resolve, entrada.reject)
                            .finally(() => {
                                estado.ativos = Math.max(0, estado.ativos - 1);
                                liberar();
                            });
                    }
                };
                return {
                    estado,
                    executar(tarefa) {
                        return new Promise((resolve, reject) => {
                            estado.fila.push({ tarefa, resolve, reject });
                            liberar();
                        });
                    }
                };
            };
            return {
                cache: new Map(),
                backendInflight: new Map(),
                backendLimiter: criarLimitador(4),
                apiLimiter: criarLimitador(8),
                estatisticas: {
                    cache_hits: 0,
                    cache_misses: 0,
                    backend_solicitados: 0,
                    backend_resultados: 0,
                    backend_retries: 0,
                    backend_timeouts: 0,
                    completos_na_coleta: 0
                }
            };
        }

        function anuncioFavoritosEnriquecimentoCompleto(item) {
            if (!item || !(item.id || item.url || item.permalink || item.link)) return false;
            const fonteVendas = normalizarFonte(item.vendasFonte || item.vendas_fonte || item.fonte_vendas || '');
            return !tituloAnuncioFavoritosPrecisaComplemento(item.titulo || item.title, item.id)
                && !!normalizarUrlAnuncioFavoritosRanking(item.url || item.permalink || item.link, item.id)
                && !!obterImagemAnuncioFavoritos(item)
                && !precisaComplementoPrecoFavoritos(item)
                && vendedorValido(item.vendedor)
                && !!String(item.data_criacao || item.date_created || '').trim()
                && fonteVendasConfiavel(fonteVendas)
                && hasNumeroVendas(item.vendas)
                && !!obterTipoAnuncioFavoritos(item)
                && !fullAnuncioDesconhecidoFavoritos(item)
                && !!obterCondicaoAnuncioFavoritos(item);
        }

        function aplicarInfoEnriquecimentoFavoritos(alvo, info) {
            if (!alvo || !info) return false;
            let alterou = false;
            if (aplicarMetadataBasicaAnuncioFavoritos(alvo, info)) alterou = true;
            if (preencherPrecoAnuncioFavoritos(alvo, info)) alterou = true;
            if (preencherTipoAnuncioFavoritos(alvo, info)) alterou = true;
            if (preencherCondicaoAnuncioFavoritos(alvo, info)) alterou = true;
            const dataCriacao = String(info.data_criacao || info.date_created || '').trim();
            if (dataCriacao && alvo.data_criacao !== dataCriacao) {
                alvo.data_criacao = dataCriacao;
                alterou = true;
            }
            const fonteData = info.fonte_data_criacao || info.dataCriacaoFonte || info.data_criacao_fonte || '';
            if (fonteData && !alvo.dataCriacaoFonte) {
                alvo.dataCriacaoFonte = fonteData;
                alvo.data_criacao_fonte = fonteData;
            }
            const vendedor = String(info.vendedor || '').trim();
            const fonteVendedor = normalizarFonte(info.fonte_vendedor || info.vendedorFonte || info.vendedor_fonte || 'pagina_produto');
            if (deveAtualizarVendedor(alvo.vendedor, alvo.vendedorFonte, vendedor, fonteVendedor)) {
                alvo.vendedor = vendedor;
                alvo.vendedorFonte = fonteVendedor;
                alvo.vendedor_fonte = fonteVendedor;
                alterou = true;
            }
            const vendas = parseNumeroVendas(info.vendas);
            const fonteVendas = normalizarFonte(info.fonte_vendas || info.vendasFonte || info.vendas_fonte || '');
            if (deveAtualizarVendas(alvo.vendas, alvo.vendasFonte, vendas, fonteVendas)) {
                alvo.vendas = vendas;
                alvo.vendasFonte = fonteVendas;
                alvo.vendas_fonte = fonteVendas;
                alterou = true;
            }
            const mediaNova = parseNumeroDecimalFavoritos(info.media_mensal ?? info.ritmo_atual ?? info.ritmo_vendas_mes ?? '');
            const mediaAtual = parseNumeroDecimalFavoritos(alvo.media_mensal ?? alvo.ritmo_atual ?? alvo.ritmo_vendas_mes ?? '');
            const fonteMediaNova = normalizarFonte(info.media_mensal_fonte || info.ritmo_atual_fonte || fonteVendas || '');
            const fonteMediaAtual = normalizarFonte(alvo.media_mensal_fonte || alvo.ritmo_atual_fonte || '');
            if (Number.isFinite(mediaNova) && (!Number.isFinite(mediaAtual) || (!fonteVendasConfiavel(fonteMediaAtual) && fonteVendasConfiavel(fonteMediaNova)))) {
                alvo.media_mensal = mediaNova;
                alvo.ritmo_atual = mediaNova;
                alvo.ritmo_vendas_mes = mediaNova;
                alvo.media_mensal_fonte = fonteMediaNova;
                alvo.ritmo_atual_fonte = fonteMediaNova;
                alterou = true;
            }
            if ((alvo.visitas === null || alvo.visitas === undefined || alvo.visitas === '') && info.visitas !== null && info.visitas !== undefined && info.visitas !== '') {
                alvo.visitas = info.visitas;
                alterou = true;
            }
            return alterou;
        }

        function aplicarCacheEnriquecimentoFavoritos(contexto, item, opcoes = {}) {
            if (!contexto || !(contexto.cache instanceof Map) || !item) return false;
            const registro = chavesAnuncioFavoritos(item)
                .map(chave => contexto.cache.get(chave))
                .find(Boolean);
            if (!registro) return false;
            aplicarInfoEnriquecimentoFavoritos(item, registro.metadata);
            const requisicaoEmAndamento = contexto.backendInflight instanceof Map
                && chavesAnuncioFavoritos(item).some(chave => contexto.backendInflight.has(chave));
            if (requisicaoEmAndamento) return false;
            const completo = !!registro.completo && anuncioFavoritosEnriquecimentoCompleto(item);
            const tentativasBackend = Math.max(0, Number(registro.tentativasBackend) || 0);
            const backendConcluido = registro.backendConcluido === true;
            const fechamento = opcoes && opcoes.fechamento === true;
            const deveRepetirNoFechamento = fechamento && !backendConcluido && tentativasBackend < 2;
            const reutilizar = completo
                || backendConcluido
                || (tentativasBackend > 0 && !deveRepetirNoFechamento);
            if (reutilizar) contexto.estatisticas.cache_hits += 1;
            return reutilizar;
        }

        function registrarCacheEnriquecimentoFavoritos(contexto, item) {
            if (!contexto || !(contexto.cache instanceof Map) || !item) return null;
            const chavesIniciais = chavesAnuncioFavoritos(item);
            if (!chavesIniciais.length) return null;
            let registro = chavesIniciais.map(chave => contexto.cache.get(chave)).find(Boolean) || null;
            const metadata = { ...item };
            if (registro && registro.metadata) aplicarInfoEnriquecimentoFavoritos(metadata, registro.metadata);
            if (!registro) {
                registro = {
                    metadata: {},
                    completo: false,
                    chaves: new Set(),
                    tentativasBackend: 0,
                    backendConcluido: false
                };
            }
            registro.metadata = metadata;
            registro.completo = anuncioFavoritosEnriquecimentoCompleto(metadata);
            [...chavesIniciais, ...chavesAnuncioFavoritos(metadata)].forEach(chave => {
                if (!chave) return;
                registro.chaves.add(chave);
                contexto.cache.set(chave, registro);
            });
            return registro;
        }

        async function enriquecerAnunciosFavoritosRanking(anuncios, contextoEnriquecimento = null, opcoes = {}) {
            const pendentes = (anuncios || [])
                .filter(item => item && (item.url || item.id))
                .filter(item => !(typeof anuncioRankingHistoricoEstaticoFavoritos === 'function' && anuncioRankingHistoricoEstaticoFavoritos(item)));
            if (!pendentes.length) return;
            const origemHtmlPreload = (item) => normalizarFonte(item && item.origem_dados || '').includes('mercadolivre_html_preload');
            pendentes.filter(origemHtmlPreload).forEach(item => {
                if (!obterTipoAnuncioFavoritos(item)) {
                    item.tipo_anuncio = 'Classico';
                    item.listing_type_id = item.listing_type_id || 'gold_special';
                    item.listing_type_name = item.listing_type_name || 'Classico';
                }
                if (!obterCondicaoAnuncioFavoritos(item)) {
                    item.condicao = 'new';
                    item.condition = 'new';
                    item.item_condition = 'new';
                }
                if (fullAnuncioDesconhecidoFavoritos(item)) {
                    item.is_full = false;
                    item.full = false;
                    item._fullAnuncioVerificado = true;
                }
            });
            const payloadEnriquecimento = (item) => ({
                id: item.id || '',
                url: item.url || '',
                imagem: obterImagemAnuncioFavoritos(item),
                thumbnail: item.thumbnail || '',
                vendedor: item.vendedor || '',
                fonte_vendedor: item.fonte_vendedor || item.vendedorFonte || item.vendedor_fonte || '',
                vendas: item.vendas,
                fonte_vendas: item.fonte_vendas || item.vendasFonte || item.vendas_fonte || '',
                data_criacao: item.data_criacao || item.date_created || '',
                fonte_data_criacao: item.fonte_data_criacao || item.fonte || '',
                data_criacao_confianca: item.data_criacao_confianca || '',
                sku: item.sku || item.sku_favorito || '',
                listing_type_id: item.listing_type_id || item.listingTypeId || '',
                listing_type_name: item.listing_type_name || '',
                tipo_anuncio: obterTipoAnuncioFavoritos(item),
                parcelamento_sem_juros: obterParcelamentoSemJurosFavoritos(item),
                shipping: item.shipping || null,
                logistic_type: item.logistic_type || item.logisticType || '',
                shipping_mode: item.shipping_mode || item.shippingMode || '',
                is_full: fullAnuncioDesconhecidoFavoritos(item) ? null : obterFullAnuncioFavoritos(item),
                condicao: obterCondicaoAnuncioFavoritos(item),
                condition: obterCondicaoAnuncioFavoritos(item),
                item_condition: obterCondicaoAnuncioFavoritos(item)
            });
            const contexto = contextoEnriquecimento && contextoEnriquecimento.cache instanceof Map
                ? contextoEnriquecimento
                : null;
            const mapaAlvos = new Map();
            const registrarAlvo = (item) => {
                chavesAnuncioFavoritos(item).forEach(chave => {
                    if (!mapaAlvos.has(chave)) mapaAlvos.set(chave, new Set());
                    mapaAlvos.get(chave).add(item);
                });
            };
            pendentes.forEach(registrarAlvo);
            const gruposBackend = new Map();
            pendentes.forEach(item => {
                if (anuncioFavoritosEnriquecimentoCompleto(item)) {
                    registrarCacheEnriquecimentoFavoritos(contexto, item);
                    if (contexto) contexto.estatisticas.completos_na_coleta += 1;
                    return;
                }
                if (aplicarCacheEnriquecimentoFavoritos(contexto, item, opcoes)) return;
                const chave = chaveAnuncioFavoritos(item) || chavesAnuncioFavoritos(item)[0];
                if (!chave) return;
                registrarCacheEnriquecimentoFavoritos(contexto, item);
                if (!gruposBackend.has(chave)) gruposBackend.set(chave, item);
            });
            const pendentesBackend = Array.from(gruposBackend.values());
            if (contexto) {
                contexto.estatisticas.cache_misses += pendentesBackend.length;
                contexto.estatisticas.backend_solicitados += pendentesBackend.length;
            }
            let resultados = [];
            if (pendentesBackend.length) {
                if (contexto && !(contexto.backendInflight instanceof Map)) contexto.backendInflight = new Map();
                const novosBackend = [];
                const aguardandoBackend = [];
                pendentesBackend.forEach(item => {
                    if (!contexto) {
                        novosBackend.push({ item, entrada: null });
                        return;
                    }
                    const chaves = chavesAnuncioFavoritos(item);
                    const existente = chaves.map(chave => contexto.backendInflight.get(chave)).find(Boolean);
                    if (existente) {
                        aguardandoBackend.push(existente.promise);
                        return;
                    }
                    let resolveEntrada;
                    let rejectEntrada;
                    const promise = new Promise((resolve, reject) => {
                        resolveEntrada = resolve;
                        rejectEntrada = reject;
                    });
                    const entrada = { promise, resolve: resolveEntrada, reject: rejectEntrada, chaves };
                    chaves.forEach(chave => contexto.backendInflight.set(chave, entrada));
                    novosBackend.push({ item, entrada });
                });
                for (let inicio = 0; inicio < novosBackend.length; inicio += 200) {
                    const loteEntradas = novosBackend.slice(inicio, inicio + 200);
                    const lote = loteEntradas.map(entrada => entrada.item);
                    lote.forEach(item => {
                        if (!contexto) return;
                        const registro = chavesAnuncioFavoritos(item)
                            .map(chave => contexto.cache.get(chave))
                            .find(Boolean);
                        if (!registro) return;
                        registro.tentativasBackend = Math.max(0, Number(registro.tentativasBackend) || 0) + 1;
                        if (registro.tentativasBackend > 1) contexto.estatisticas.backend_retries += 1;
                    });
                    try {
                        if (typeof setTimeout === 'function') {
                            await new Promise(resolve => setTimeout(resolve, 25));
                        }
                        const sinalExecucao = sinalFavoritosAtual();
                        const executarRequest = () => executarComTimeoutFavoritos(async (signal) => {
                            const response = await fetch('/api/favoritos/ml/enriquecer-datas', {
                                method: 'POST',
                                headers: headersJsonAutenticado(),
                                signal,
                                body: JSON.stringify({
                                    max_anuncios: lote.length,
                                    anuncios: lote.map(payloadEnriquecimento)
                                })
                            });
                            if (!response.ok) return [];
                            const data = await response.json();
                            return Array.isArray(data.resultados) ? data.resultados : [];
                        }, 45000, sinalExecucao);
                        const resultadosLote = contexto && contexto.backendLimiter && typeof contexto.backendLimiter.executar === 'function'
                            ? await contexto.backendLimiter.executar(executarRequest)
                            : await executarRequest();
                        resultados.push(...resultadosLote);
                        loteEntradas.forEach(({ item, entrada }) => {
                            if (!entrada) return;
                            const info = resultadosLote.find(resultado => chavesAnuncioFavoritos(resultado).some(chave => entrada.chaves.includes(chave))) || null;
                            entrada.resolve(info);
                            entrada.chaves.forEach(chave => {
                                if (contexto.backendInflight.get(chave) === entrada) contexto.backendInflight.delete(chave);
                            });
                        });
                    } catch (err) {
                        if (contexto && err && err.favoritosTimeout) contexto.estatisticas.backend_timeouts += 1;
                        loteEntradas.forEach(({ entrada }) => {
                            if (!entrada) return;
                            if (mlFavoritosCancelado || (sinalFavoritosAtual() && sinalFavoritosAtual().aborted)) entrada.reject(err);
                            else entrada.resolve(null);
                            entrada.chaves.forEach(chave => {
                                if (contexto.backendInflight.get(chave) === entrada) contexto.backendInflight.delete(chave);
                            });
                        });
                        if (mlFavoritosCancelado || (sinalFavoritosAtual() && sinalFavoritosAtual().aborted)) throw err;
                        console.warn(`Nao foi possivel enriquecer o lote ${Math.floor(inicio / 200) + 1} do ranking pelo backend:`, err);
                    }
                }
                if (aguardandoBackend.length) {
                    const compartilhados = await Promise.all(aguardandoBackend);
                    resultados.push(...compartilhados.filter(Boolean));
                }
            }
            const mapa = new Map();
            anuncios.forEach(item => {
                chavesAnuncioFavoritos(item).forEach(chave => {
                    if (!mapa.has(chave)) mapa.set(chave, new Set());
                    mapa.get(chave).add(item);
                });
            });
            resultados.forEach(info => {
                const alvos = new Set();
                chavesAnuncioFavoritos(info).forEach(chave => {
                    (mapa.get(chave) || mapaAlvos.get(chave) || []).forEach(alvo => alvos.add(alvo));
                    const registro = contexto && contexto.cache.get(chave);
                    if (registro) registro.backendConcluido = true;
                });
                alvos.forEach(alvo => aplicarInfoEnriquecimentoFavoritos(alvo, info));
            });
            if (contexto) contexto.estatisticas.backend_resultados += resultados.length;
            const precisaDadosAvant = (item) => {
                if (!item || !item.url) return false;
                const fonteVendas = normalizarFonte(item.vendasFonte || item.vendas_fonte || item.fonte_vendas || '');
                return !vendedorValido(item.vendedor)
                    || !item.data_criacao
                    || !(fonteVendasConfiavel(fonteVendas) && hasNumeroVendas(item.vendas));
            };
            const permitirComplementoProdutoAvant = !mlFavoritosEmExecucao;
            const pendentesAvant = pendentes.filter(precisaDadosAvant).slice(0, ML_FAVORITOS_HISTORICO_ANUNCIOS_MAX);
            if (permitirComplementoProdutoAvant && pendentesAvant.length && typeof tentarDataCriacaoPeloElectron === 'function') {
                try {
                    mostrarBalaoFavoritosStatus(`Completando data, vendedor e vendas de ${pendentesAvant.length} anuncio(s) pelo navegador/Avant Pro...`, {
                        manterNavegadorVisivel: true
                    });
                    await tentarDataCriacaoPeloElectron(pendentesAvant);
                } catch (err) {
                    if (mlFavoritosCancelado || (err && err.name === 'AbortError') || (err && err.canceladoFavoritos)) throw err;
                    console.warn('Nao foi possivel completar dados do ranking pelo navegador/Avant Pro:', err);
                    mostrarBalaoFavoritosStatus(`Nao consegui completar todos os dados Avant de ${pendentesAvant.length} anuncio(s), mas vou salvar o ranking com os dados coletados.`, {
                        tempoMs: 5500,
                        larga: true
                    });
                }
            }
            const semComplemento = pendentes.filter(item => item && (!item.url || tituloAnuncioFavoritosPrecisaComplemento(item.titulo, item.id) || !vendedorValido(item.vendedor) || !obterImagemAnuncioFavoritos(item) || precisaComplementoPrecoFavoritos(item) || !obterTipoAnuncioFavoritos(item) || fullAnuncioDesconhecidoFavoritos(item) || !obterCondicaoAnuncioFavoritos(item)));
            const semComplementoUnicos = Array.from(new Map(semComplemento.map(item => [
                chaveAnuncioFavoritos(item) || chavesAnuncioFavoritos(item)[0],
                item
            ]).filter(([chave]) => Boolean(chave))).values());
            const deadlineApiDireta = Date.now() + (mlFavoritosEmExecucao ? 15000 : 45000);
            await executarComConcorrencia(semComplementoUnicos, Math.min(ML_API_WORKERS, 4), async (alvo) => {
                verificarCancelamentoFavoritos();
                const restanteMs = deadlineApiDireta - Date.now();
                if (restanteMs <= 0) return;
                const itemId = alvo.id || extrairItemIdAnuncio(alvo.url);
                if (!itemId) return;
                try {
                    const consultar = () => executarComTimeoutFavoritos(
                        () => consultarItemApiMercadoLivre(itemId),
                        Math.min(6000, restanteMs),
                        sinalFavoritosAtual()
                    );
                    const apiInfo = contexto && contexto.apiLimiter && typeof contexto.apiLimiter.executar === 'function'
                        ? await contexto.apiLimiter.executar(consultar)
                        : await consultar();
                    if (!apiInfo) return;
                    const alvos = new Set([alvo]);
                    chavesAnuncioFavoritos(alvo).forEach(chave => {
                        (mapaAlvos.get(chave) || []).forEach(item => alvos.add(item));
                    });
                    alvos.forEach(item => aplicarInfoEnriquecimentoFavoritos(item, apiInfo));
                } catch (err) {
                    if (mlFavoritosCancelado || (err && err.canceladoFavoritos)) throw err;
                    if (err && err.favoritosTimeout) return;
                    console.warn('Nao foi possivel preencher vendedor do ranking pelo MLB:', itemId, err);
                }
            });
            pendentes.forEach(item => registrarCacheEnriquecimentoFavoritos(contexto, item));
        }

        async function complementarTiposRankingFavoritos(sku, anuncios) {
            const chaveSku = skuChaveSku(sku);
            const pendentes = (Array.isArray(anuncios) ? anuncios : [])
                .filter(anuncio => anuncio && (!obterTipoAnuncioFavoritos(anuncio) || fullAnuncioDesconhecidoFavoritos(anuncio)) && !anuncio._tipoAnuncioVerificado && (anuncio.id || anuncio.url))
                .filter(anuncio => !(typeof anuncioRankingHistoricoEstaticoFavoritos === 'function' && anuncioRankingHistoricoEstaticoFavoritos(anuncio)));
            if (!chaveSku || !pendentes.length || mlFavoritosTiposRankingEmExecucao.has(chaveSku)) return;
            mlFavoritosTiposRankingEmExecucao.add(chaveSku);
            let alterou = false;
            try {
                let resultados = [];
                try {
                    const response = await fetch('/api/favoritos/ml/enriquecer-datas', {
                        method: 'POST',
                        headers: headersJsonAutenticado(),
                        body: JSON.stringify({
                            max_anuncios: Math.min(200, pendentes.length),
                            anuncios: pendentes.map(item => ({ id: item.id || '', url: item.url || '' }))
                        })
                    });
                    if (response.ok) {
                        const data = await response.json();
                        resultados = Array.isArray(data.resultados) ? data.resultados : [];
                    }
                } catch (err) {
                    console.warn('Nao foi possivel completar tipo do ranking pelo backend:', err);
                }

                const mapa = new Map();
                pendentes.forEach(item => chavesAnuncioFavoritos(item).forEach(chave => mapa.set(chave, item)));
                resultados.forEach(info => {
                    const alvo = chavesAnuncioFavoritos(info).map(chave => mapa.get(chave)).find(Boolean);
                    if (alvo && preencherTipoAnuncioFavoritos(alvo, info)) alterou = true;
                });

                const aindaPendentes = pendentes.filter(item => !obterTipoAnuncioFavoritos(item) || fullAnuncioDesconhecidoFavoritos(item));
                await executarComConcorrencia(aindaPendentes, Math.min(ML_API_WORKERS, 8), async (alvo) => {
                    const itemId = alvo.id || extrairItemIdAnuncio(alvo.url);
                    if (!itemId) return;
                    try {
                        const apiInfo = await consultarItemApiMercadoLivre(itemId);
                        if (apiInfo && preencherTipoAnuncioFavoritos(alvo, apiInfo)) alterou = true;
                    } catch (err) {
                        console.warn('Nao foi possivel completar tipo do anuncio:', itemId, err);
                    } finally {
                        alvo._tipoAnuncioVerificado = true;
                    }
                });
            } finally {
                mlFavoritosTiposRankingEmExecucao.delete(chaveSku);
            }
            if (alterou) {
                const historico = lerHistoricoFavoritos();
                if (historico.length) salvarHistoricoFavoritos(historico);
                if (skuChaveSku(favMlSkuSelecionado) === chaveSku) {
                    renderizarFavoritosOutrosAnuncios(favMlSkuSelecionado);
                }
                if (document.getElementById('aba-historico')?.classList.contains('active')) {
                    renderizarHistoricoFavoritos();
                }
            }
        }

        function ordenarAnunciosFavoritosRanking(anuncios, sku = '') {
            return filtrarAnunciosIgnoradosRanking(anuncios, sku)
                .map((anuncio, index) => ({ anuncio, index, metrica: calcularMetricasMediaVendas(anuncio) }))
                .sort((a, b) => {
                    const mediaA = Number.isFinite(a.metrica.media) ? a.metrica.media : -1;
                    const mediaB = Number.isFinite(b.metrica.media) ? b.metrica.media : -1;
                    if (Math.abs(mediaB - mediaA) > 0.0001) return mediaB - mediaA;
                    const vendasB = Number.isFinite(b.metrica.vendas) ? b.metrica.vendas : -1;
                    const vendasA = Number.isFinite(a.metrica.vendas) ? a.metrica.vendas : -1;
                    if (vendasB !== vendasA) return vendasB - vendasA;
                    const posicaoA = Number(a.anuncio.posicao) || 9999;
                    const posicaoB = Number(b.anuncio.posicao) || 9999;
                    if (posicaoA !== posicaoB) return posicaoA - posicaoB;
                    return a.index - b.index;
                })
                .map(item => {
                    const mediaCalculada = Number.isFinite(item.metrica.media) ? item.metrica.media : null;
                    item.anuncio.media_vendas_mensal = mediaCalculada;
                    item.anuncio.meses_desde_criacao = Number.isFinite(item.metrica.meses) ? item.metrica.meses : null;
                    if (mediaCalculada !== null && Number.isFinite(item.metrica.vendas) && item.metrica.dataCriacao) {
                        item.anuncio.media_mensal = mediaCalculada;
                        item.anuncio.ritmo_atual = mediaCalculada;
                        item.anuncio.ritmo_vendas_mes = mediaCalculada;
                        item.anuncio.media_mensal_fonte = 'calculado_vendas_dias';
                        item.anuncio.ritmo_atual_fonte = 'calculado_vendas_dias';
                    }
                    return item.anuncio;
                });
        }

        function limitarAnunciosFavoritosRanking(anuncios) {
            return (Array.isArray(anuncios) ? anuncios : [])
                .filter(Boolean)
                .slice(0, ML_FAVORITOS_RANKING_ANUNCIOS_MAX);
        }

        function normalizarIdAnuncioFavoritosIa(anuncio) {
            return (extrairItemIdAnuncio(anuncio && (anuncio.id || anuncio.mlb || anuncio.url || anuncio.permalink || anuncio.link))
                || String(anuncio && (anuncio.id || anuncio.mlb || '') || '').trim().toUpperCase().replace(/-/g, ''));
        }

        function obterDescricaoAnuncioFavoritosIa(anuncio) {
            if (!anuncio || typeof anuncio !== 'object') return '';
            const candidatos = [
                anuncio.descricao,
                anuncio.descricao_ml,
                anuncio.description,
                anuncio.description_plain,
                anuncio.plain_text,
                anuncio.text,
                anuncio.subtitle
            ];
            for (const valor of candidatos) {
                if (typeof valor === 'string' && valor.trim()) {
                    return valor.replace(/\s+/g, ' ').trim();
                }
            }
            const info = anuncio.description_info || anuncio.descriptionInfo || anuncio.descricao_info;
            if (info && typeof info === 'object') {
                for (const chave of ['plain_text', 'text', 'content', 'description']) {
                    const valor = info[chave];
                    if (typeof valor === 'string' && valor.trim()) {
                        return valor.replace(/\s+/g, ' ').trim();
                    }
                    if (valor && typeof valor === 'object') {
                        for (const subchave of ['plain_text', 'text', 'content']) {
                            const subvalor = valor[subchave];
                            if (typeof subvalor === 'string' && subvalor.trim()) {
                                return subvalor.replace(/\s+/g, ' ').trim();
                            }
                        }
                    }
                }
            }
            return '';
        }

        function anuncioFavoritosIaPayload(anuncio, index = 0) {
            const id = normalizarIdAnuncioFavoritosIa(anuncio);
            const imagem = obterImagemAnuncioFavoritos(anuncio);
            return {
                rank: index + 1,
                id,
                mlb: id,
                url: String(anuncio && (anuncio.url || anuncio.permalink || anuncio.link) || '').trim(),
                titulo: String(anuncio && (anuncio.titulo || anuncio.title) || '').trim().slice(0, 320),
                descricao: obterDescricaoAnuncioFavoritosIa(anuncio).slice(0, 1800),
                vendedor: String(anuncio && anuncio.vendedor || '').trim().slice(0, 140),
                imagem,
                thumbnail: imagem,
                preco: anuncio && (anuncio.preco ?? anuncio.price ?? ''),
                vendas: anuncio && anuncio.vendas,
                data_criacao: anuncio && (anuncio.data_criacao || anuncio.date_created || ''),
                tipo_anuncio: obterTipoAnuncioFavoritos(anuncio),
                condicao: obterCondicaoAnuncioFavoritos(anuncio),
                loja: String(anuncio && (anuncio.loja || anuncio.loja_sync || anuncio.loja_conta) || '').trim(),
                sku: String(anuncio && (anuncio.sku || anuncio.seller_sku || anuncio.sku_favorito) || '').trim()
            };
        }

        function normalizarAnuncioRemovidoIa(anuncio, motivo = '') {
            const base = { ...(anuncio || {}) };
            base.id = normalizarIdAnuncioFavoritosIa(base);
            base.imagem = obterImagemAnuncioFavoritos(base);
            base.thumbnail = base.imagem || base.thumbnail || '';
            base.motivo_ia = String(motivo || base.motivo_ia || base.motivo || 'Removido pela IA por nao parecer o mesmo produto.').trim();
            return base;
        }

        async function buscarAnunciosPropriosFavoritosIa(info, itemSidebar = null) {
            const sku = String(info && info.sku || '').trim();
            if (!sku || grupoRankingFavoritosEhAvulso(info)) return [];
            const loja = favoritosLojaSelecionadaParaApi((info && info.loja) || (itemSidebar && itemSidebar.loja) || '');
            const mlbs = extrairMlbsItemSkuSidebar(itemSidebar);
            const cacheKey = [
                skuChaveSku(sku),
                skuNormalizarLoja(loja),
                mlbs.join(',')
            ].join('|');
            if (mlFavoritosAnunciosPropriosIaCache.has(cacheKey)) {
                return mlFavoritosAnunciosPropriosIaCache.get(cacheKey).map(item => ({ ...item }));
            }
            const params = new URLSearchParams({ sku });
            if (loja) params.set('loja', loja);
            if (mlbs.length) params.set('mlbs', mlbs.join(','));
            const response = await fetch(`/api/favoritos/ml/anuncios-sku?${params.toString()}`, {
                headers: obterAuthHeaders(),
                cache: 'no-store',
                signal: sinalFavoritosAtual()
            });
            if (!response.ok) {
                let detalhe = `HTTP ${response.status}`;
                try {
                    const dataErro = await response.json();
                    detalhe = dataErro.detail || detalhe;
                } catch (_err) {}
                throw new Error(detalhe);
            }
            const data = await response.json();
            const anuncios = (Array.isArray(data.anuncios) ? data.anuncios : [])
                .filter(Boolean)
                .map((anuncio, index) => ({
                    ...anuncio,
                    rank: index + 1,
                    imagem: obterImagemAnuncioFavoritos(anuncio),
                    descricao: obterDescricaoAnuncioFavoritosIa(anuncio)
                }));
            mlFavoritosAnunciosPropriosIaCache.set(cacheKey, anuncios.map(item => ({ ...item })));
            return anuncios;
        }

        async function filtrarAnunciosFavoritosPorIa(info, anuncios, opcoes = {}) {
            const lista = Array.isArray(anuncios) ? anuncios.filter(Boolean) : [];
            const usarIa = !!(opcoes && opcoes.usarIa);
            if (!usarIa || !lista.length) {
                return { anuncios: lista, removidos: [], removidosTotal: 0, usouIa: false };
            }

            const maxConfirmados = Math.max(1, Math.min(20, Number(opcoes.maxConfirmados) || 8));
            const itemSidebar = opcoes.itemSidebar || null;
            let meusAnuncios = Array.isArray(opcoes.meusAnuncios) ? opcoes.meusAnuncios.filter(Boolean) : [];
            if (!meusAnuncios.length) {
                try {
                    meusAnuncios = await buscarAnunciosPropriosFavoritosIa(info, itemSidebar);
                } catch (err) {
                    console.warn('Nao foi possivel carregar nossos anuncios para IA:', err);
                    mostrarBalaoFavoritosStatus(`SKU ${info && info.sku || ''}: nao consegui carregar nossos anuncios para IA. Vou comparar usando o cadastro.`, {
                        erro: true,
                        tempoMs: 4500
                    });
                }
            }

            const pesquisas = normalizarTermosPesquisaFavoritos(info && info.termos)
                .map(item => item.termo)
                .filter(Boolean);
            mostrarBalaoFavoritosStatus(`SKU ${info && info.sku || ''}: IA verificando anuncios fora do produto sem limitar o ranking...`);
            const response = await fetch('/api/favoritos/ranking/filtrar-ia', {
                method: 'POST',
                headers: headersJsonAutenticado(),
                signal: sinalFavoritosAtual(),
                body: JSON.stringify({
                    sku: info && info.sku || '',
                    titulo: info && info.titulo || '',
                    descricao: info && info.descricao || '',
                    pesquisas,
                    meus_anuncios: meusAnuncios.map(anuncioFavoritosIaPayload).slice(0, 12),
                    anuncios: lista.map(anuncioFavoritosIaPayload),
                    max_anuncios: Math.min(180, lista.length),
                    max_confirmados: maxConfirmados,
                    usar_imagem: true
                })
            });
            if (!response.ok) {
                let detalhe = `HTTP ${response.status}`;
                try {
                    const dataErro = await response.json();
                    detalhe = dataErro.detail || detalhe;
                } catch (_err) {}
                throw new Error(`Erro na IA de favoritos: ${detalhe}`);
            }

            const data = await response.json();
            const manterIds = new Set((data.manter_ids || []).map(id => String(id || '').trim().toUpperCase().replace(/-/g, '')).filter(Boolean));
            const removerIds = new Set((data.remover_ids || []).map(id => String(id || '').trim().toUpperCase().replace(/-/g, '')).filter(Boolean));
            const motivos = new Map();
            (Array.isArray(data.removidos) ? data.removidos : []).forEach(item => {
                const id = String(item && (item.id || item.mlb) || '').trim().toUpperCase().replace(/-/g, '');
                if (id) motivos.set(id, String(item.motivo || item.reason || 'Removido pela IA').trim());
            });

            const removidos = [];
            const confirmados = [];
            lista.forEach(anuncio => {
                const id = normalizarIdAnuncioFavoritosIa(anuncio);
                if (id && (removerIds.has(id) || motivos.has(id))) {
                    removidos.push(normalizarAnuncioRemovidoIa(anuncio, motivos.get(id)));
                    return;
                }
                if (!manterIds.size || (id && manterIds.has(id))) {
                    confirmados.push(anuncio);
                }
            });

            return {
                anuncios: confirmados,
                removidos,
                removidosTotal: removidos.length,
                usouIa: true,
                confirmados: confirmados.length,
                meusAnunciosTotal: meusAnuncios.length,
                maxConfirmados
            };
        }
