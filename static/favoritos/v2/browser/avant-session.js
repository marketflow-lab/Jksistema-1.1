(function (global) {
  'use strict';

  const browser = global.FavoritosV2.browser;
  const pageScripts = browser.pageScripts;

        function mostrarAcaoConectarAvantPro(status = {}) {
            if (!mlFavoritosBalloonActionsEl) {
                window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Avant Pro nao retornou dados coletaveis. Confirme manualmente o login no navegador interno.', {
                    erro: true,
                    manterNavegadorVisivel: true,
                    larga: true,
                    titulo: 'Avant Pro sem dados'
                });
                return;
            }
            mlFavoritosBalloonActionsEl.innerHTML = '';

            const conectar = document.createElement('button');
            conectar.type = 'button';
            conectar.textContent = 'Conectar Avant Pro';
            conectar.addEventListener('click', async () => {
                conectar.disabled = true;
                window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Abrindo login do Avant Pro no navegador interno...', {
                    manterAcoes: true,
                    manterNavegadorVisivel: true,
                    larga: true,
                    titulo: 'Conectar Avant Pro'
                });
                const resultado = await abrirLoginAvantProNoWebview();
                const mensagem = resultado && resultado.success
                    ? 'Conclua o login do Avant Pro no navegador interno. Depois clique em Tentar novamente.'
                    : 'Nao encontrei o botao de login do Avant Pro. Abra Ferramentas > Fazer Login no navegador interno e depois tente novamente.';
                window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(mensagem, {
                    manterAcoes: true,
                    manterNavegadorVisivel: true,
                    larga: true,
                    erro: !(resultado && resultado.success),
                    titulo: 'Conectar Avant Pro'
                });
                conectar.disabled = false;
            });

            const tentarNovamente = document.createElement('button');
            tentarNovamente.type = 'button';
            tentarNovamente.textContent = 'Tentar novamente';
            tentarNovamente.addEventListener('click', async () => {
                tentarNovamente.disabled = true;
                window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Verificando dados do Avant Pro...', {
                    manterAcoes: true,
                    manterNavegadorVisivel: true,
                    larga: true,
                    titulo: 'Conectar Avant Pro'
                });
                await acionarControlesAvantProNoWebview({
                    forceClick: true,
                    permitirFerramentas: true,
                    maxClicks: 12
                }).catch(() => 0);
                const novoStatus = await aguardarAvantProNoWebview({
                    recarregarSeAusente: false,
                    timeoutMs: 7000,
                    pollMs: 350
                }).catch(() => null);
                if (statusAvantProTemDadosColetaveis(novoStatus)) {
                    window.FavoritosV2.searchRanking.publicApi.status.esconderBalaoFavoritosStatus();
                } else {
                    mostrarAcaoConectarAvantPro(novoStatus || status);
                }
                tentarNovamente.disabled = false;
            });

            const fechar = document.createElement('button');
            fechar.type = 'button';
            fechar.textContent = 'Fechar';
            fechar.addEventListener('click', window.FavoritosV2.searchRanking.publicApi.status.esconderBalaoFavoritosStatus);

            mlFavoritosBalloonActionsEl.appendChild(conectar);
            mlFavoritosBalloonActionsEl.appendChild(tentarNovamente);
            mlFavoritosBalloonActionsEl.appendChild(fechar);
            window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus('Avant Pro nao retornou dados coletaveis. Confirme manualmente se o login esta pronto no navegador interno.', {
                manterAcoes: true,
                manterNavegadorVisivel: true,
                erro: true,
                larga: true,
                titulo: 'Avant Pro sem dados'
            });
        }

        function criarErroAvantProObrigatorioFavoritos(mensagem, status = {}) {
            const erro = new Error(mensagem || 'Avant Pro nao retornou dados coletaveis. Confirme manualmente se o login esta pronto no navegador interno e tente novamente.');
            erro.loginAvantProNecessario = true;
            erro.avantStatus = status || null;
            return erro;
        }

        function criarErroConfirmacaoAvantProObrigatoriaFavoritos(termo, status = {}) {
            const termoLimpo = String(termo || '').trim();
            const contexto = termoLimpo ? ` para "${termoLimpo}"` : '';
            const erro = criarErroAvantProObrigatorioFavoritos(
                `Avant Pro nao confirmou o login${contexto}. Clique na bolinha do Avant Pro, abra Ferramentas, confirme o e-mail e aguarde a mensagem "Obrigado por usar nossa extensao" antes de iniciar a pesquisa.`,
                status || {}
            );
            erro.avantConfirmacaoObrigatoria = true;
            return erro;
        }

        function statusAvantProPedeLoginOuVinculo(status) {
            if (!status) return false;
            if (status.modalAvantPromocional && !status.paginaLoginReal && Number(status.avantLoginEmailInputs || 0) <= 0) {
                return false;
            }
            const cardCount = Number(status.cardCount || 0);
            const temDadosFortes = !!(
                status.hasAvantData
                || Number(status.rows || 0) > 0
                || Number(status.dataTextNodes || 0) > 0
                || Number(status.bodyDataLabels || status.avantLabels || 0) >= 2
            );
            const accountLinkButtonsGlobais = Number(status.accountLinkButtonsGlobais || 0);
            const accountLinkButtonsCards = Number(status.accountLinkButtonsCards || 0);
            const botoesLoginSemSeparacao = Number(status.botoesLoginSemSeparacao || 0);
            const shellAvantOperavel = !!(
                Number(status.widgets || 0) > 0
                || Number(status.actionButtons || 0) > 0
                || Number(status.toolsButtons || 0) > 0
                || Number(status.infoButtons || 0) > 0
                || status.extensionDetected
                || status.avantShellProntoParaColeta
            );
            const paginaLoginReal = !!(
                status.avantLoginDialog
                || Number(status.avantLoginEmailInputs || 0) > 0
                || status.paginaLoginReal
            );
            const loginRealBloqueante = paginaLoginReal && !temDadosFortes;
            if (shellAvantOperavel && !loginRealBloqueante) return false;
            const loginGlobalPendente = !!(
                status.needsAccountLink
                || status.accountActionRequired
                || status.avantLoginDialog
                || Number(status.avantLoginEmailInputs || 0) > 0
                || accountLinkButtonsGlobais > 0
                || botoesLoginSemSeparacao > 0
                || status.bodySugereLoginAvant
            );
            const cardsPedemLoginAvant = !!(accountLinkButtonsCards > 0 && cardCount > 0 && !temDadosFortes);
            if (cardsPedemLoginAvant) return true;
            const indicadoresLogin = !!(
                loginGlobalPendente
                || Number(status.accountLinkButtons || 0) > 0
            );
            if (!indicadoresLogin && status.loadingScreen && cardCount <= 0) return false;
            const temCardsComExtensao = cardCount > 0 && (
                status.extensionDetected
                || Number(status.widgets || 0) > 0
                || Number(status.actionButtons || 0) > 0
                || Number(status.toolsButtons || 0) > 0
            );
            if (temCardsComExtensao && !indicadoresLogin) {
                return false;
            }
            return indicadoresLogin;
        }

        function statusAvantProPedeEntradaAntesPesquisa(status) {
            if (!status) return true;
            if (status.modalAvantPromocional && !status.paginaLoginReal && Number(status.avantLoginEmailInputs || 0) <= 0) {
                return false;
            }
            const accountLinkButtonsTotal = Number(status.accountLinkButtons || 0);
            const accountLinkButtonsCards = Number(status.accountLinkButtonsCards || 0);
            const accountLinkButtonsGlobais = Number(status.accountLinkButtonsGlobais || 0);
            const accountLinkButtonsForaDosCards = Math.max(
                accountLinkButtonsGlobais,
                accountLinkButtonsTotal - accountLinkButtonsCards
            );
            return !!(
                status.needsAccountLink
                || status.accountActionRequired
                || status.avantLoginDialog
                || status.paginaLoginReal
                || Number(status.avantLoginEmailInputs || 0) > 0
                || Number(status.botoesLoginSemSeparacao || 0) > 0
                || accountLinkButtonsForaDosCards > 0
                || status.bodySugereLoginAvant
            );
        }

        function registrarLoginAvantProConfirmadoFavoritos() {
            try {
                localStorage.setItem(ML_FAVORITOS_AVANT_LOGIN_CONFIRMADO_KEY, String(Date.now()));
            } catch (_err) {}
            salvarMemoriaAvantProConfirmadaFavoritos('favoritos_login_confirmado').catch(() => null);
        }

        function limparConfirmacaoAvantProFavoritos() {
            try {
                localStorage.removeItem(ML_FAVORITOS_AVANT_LOGIN_CONFIRMADO_KEY);
            } catch (_err) {}
        }

        async function detectarConfirmacaoLoginAvantProNoWebview() {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') {
                return { confirmado: false, reason: 'navegador_indisponivel' };
            }
            return await mlWebviewEl.executeJavaScript(pageScripts.render('detectar-confirmacao-login-avant-pro-no-webview-1', {  }), true).catch((err) => ({
                confirmado: false,
                reason: 'erro_ao_detectar_confirmacao_avant',
                error: err && err.message ? err.message : String(err)
            }));
        }

        async function aguardarConfirmacaoLoginAvantProNoWebview(opcoes = {}) {
            const timeoutMs = Math.max(1200, Number(opcoes.timeoutMs) || 18000);
            const pollMs = Math.max(180, Number(opcoes.pollMs) || 450);
            const inicio = Date.now();
            let ultimo = null;
            let ultimaTentativaLogin = 0;
            while (Date.now() - inicio < timeoutMs) {
                ultimo = await detectarConfirmacaoLoginAvantProNoWebview().catch(() => null);
                if (ultimo && ultimo.confirmado) {
                    registrarLoginAvantProConfirmadoFavoritos();
                    return {
                        ...ultimo,
                        ok: true,
                        confirmed: true,
                        elapsedMs: Date.now() - inicio
                    };
                }
                if (opcoes.tentarPreencherEmail !== false && Date.now() - ultimaTentativaLogin >= 1600) {
                    ultimaTentativaLogin = Date.now();
                    if (typeof tentarLoginAvantProNoWebview === 'function') {
                        await tentarLoginAvantProNoWebview(mlWebviewEl, {
                            timeoutMs: 2600,
                            atrasos: [0, 180, 420, 900, 1500, 2300]
                        }).catch(() => null);
                    }
                }
                await esperar(pollMs);
            }
            return ultimo ? { ...ultimo, ok: false, timeout: true } : { ok: false, timeout: true };
        }

        async function prepararFerramentasAntesPesquisaAvant(opcoes) {
            const termo = String(opcoes.termo || '').trim();
            const contexto = termo ? ` para "${termo}"` : '';
            window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Conectando Avant Pro${contexto}: abrindo bolinha e Ferramentas...`, {
                manterNavegadorVisivel: true,
                larga: true,
                titulo: 'Conectar Avant Pro'
            });
            if (typeof forcarNavegadorMlShellVisivel === 'function') {
                forcarNavegadorMlShellVisivel();
            }
            const ferramentas = await abrirFerramentasAvantProAntesLogin({
                maxTentativas: 3,
                termo,
                permitirLoginGenerico: false
            }).catch((err) => ({
                success: false,
                reason: 'erro_ao_abrir_ferramentas_avant',
                error: err && err.message ? err.message : String(err)
            }));
            window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(
                ferramentas && ferramentas.success
                    ? `Conectando Avant Pro${contexto}: preenchendo e-mail e confirmando...`
                    : `Conectando Avant Pro${contexto}: Ferramentas nao abriu; procurando a tela de login...`,
                {
                manterNavegadorVisivel: true,
                larga: true,
                titulo: 'Conectar Avant Pro'
                }
            );
            return { termo, contexto, ferramentas };
        }

        async function entrarAvantProPorFerramentasAntesPesquisa(opcoes = {}) {
            const preparacao = await prepararFerramentasAntesPesquisaAvant(opcoes);
            const { termo, contexto, ferramentas } = preparacao;
            const digitacaoNativaTentativas = [];
            let confirmado = await aguardarConfirmacaoLoginAvantProNoWebview({
                timeoutMs: 2600,
                pollMs: Number(opcoes.pollMs) || 450,
                tentarPreencherEmail: false
            }).catch(() => null);
            if (!(confirmado && confirmado.confirmed)) {
                const loginElectronNativo = await tentarLoginAvantProPorElectronNativo({
                    termo,
                    esperaAposConfirmarMs: 900
                }).catch((err) => ({
                    success: false,
                    reason: 'erro_no_login_electron_nativo_avant',
                    error: err && err.message ? err.message : String(err)
                }));
                digitacaoNativaTentativas.push(loginElectronNativo);
                if (loginElectronNativo && loginElectronNativo.confirmed) {
                    confirmado = {
                        ...(loginElectronNativo.confirmacao || {}),
                        ok: true,
                        confirmed: true,
                        via: 'electron_native_login'
                    };
                }
                if (!(confirmado && confirmado.confirmed)
                    && loginElectronNativo
                    && loginElectronNativo.success === false
                    && /campo_email|iframe_auth|nao_encontrado/i.test(String(loginElectronNativo.reason || ''))
                ) {
                    mostrarStatusConexaoAvantPro('Ferramentas ainda nao exibiu e-mail; abrindo Ferramentas novamente', { termo });
                    const reforcoFerramentas = await abrirFerramentasAvantProAntesLogin({
                        maxTentativas: 2,
                        termo,
                        permitirLoginGenerico: false
                    }).catch((err) => ({
                        success: false,
                        reason: 'erro_ao_reabrir_ferramentas_avant',
                        error: err && err.message ? err.message : String(err)
                    }));
                    digitacaoNativaTentativas.push({
                        via: 'reforco_ferramentas_apos_electron_sem_campo',
                        result: reforcoFerramentas
                    });
                    const loginElectronReforco = await tentarLoginAvantProPorElectronNativo({
                        termo,
                        esperaAposConfirmarMs: 1200,
                        timeoutConfirmacaoMs: 8000
                    }).catch((err) => ({
                        success: false,
                        reason: 'erro_no_login_electron_nativo_avant_reforco_ferramentas',
                        error: err && err.message ? err.message : String(err)
                    }));
                    digitacaoNativaTentativas.push(loginElectronReforco);
                    if (loginElectronReforco && loginElectronReforco.confirmed) {
                        confirmado = {
                            ...(loginElectronReforco.confirmacao || {}),
                            ok: true,
                            confirmed: true,
                            via: 'electron_native_login_reforco_ferramentas'
                        };
                    }
                }
            }
            if (!(confirmado && confirmado.confirmed)) {
                window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Conectando Avant Pro${contexto}: tentando preencher pela tela visivel...`, {
                    manterNavegadorVisivel: true,
                    larga: true,
                    titulo: 'Conectar Avant Pro'
                });
                const digitacaoNativa = await tentarLoginAvantProPorDigitacaoNativa({
                    termo,
                    esperaAntesConfirmarMs: 260,
                    esperaAposConfirmarMs: 800
                }).catch((err) => ({
                    success: false,
                    reason: 'erro_na_digitacao_nativa_avant',
                    error: err && err.message ? err.message : String(err)
                }));
                digitacaoNativaTentativas.push(digitacaoNativa);
                confirmado = await aguardarConfirmacaoLoginAvantProNoWebview({
                    timeoutMs: 4800,
                    pollMs: Number(opcoes.pollMs) || 450,
                    tentarPreencherEmail: false
                }).catch(() => null);
            }
            if (!(confirmado && confirmado.confirmed)) {
                const digitacaoNativaDepoisLogin = await tentarLoginAvantProPorDigitacaoNativa({
                    termo,
                    esperaAntesConfirmarMs: 260,
                    esperaAposConfirmarMs: 900
                }).catch((err) => ({
                    success: false,
                    reason: 'erro_na_digitacao_nativa_avant_apos_login',
                    error: err && err.message ? err.message : String(err)
                }));
                digitacaoNativaTentativas.push(digitacaoNativaDepoisLogin);
                confirmado = await aguardarConfirmacaoLoginAvantProNoWebview({
                    timeoutMs: Math.max(12000, Number(opcoes.timeoutMs) || 18000),
                    pollMs: Number(opcoes.pollMs) || 450,
                    tentarPreencherEmail: false
                }).catch(() => null);
            }
            if (confirmado && confirmado.confirmed) {
                window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Avant Pro confirmou o login${contexto}. Iniciando a pesquisa...`, {
                    manterNavegadorVisivel: true,
                    larga: true,
                    titulo: 'Avant Pro conectado'
                });
            }
            return confirmado ? { ...confirmado, ferramentas, digitacaoNativaTentativas } : { ok: false, timeout: true, ferramentas, digitacaoNativaTentativas };
        }

        function statusAvantProTemDadosColetaveis(status) {
            if (!status) return false;
            if (statusAvantProPedeLoginOuVinculo(status)) return false;
            const labels = Number(status.bodyDataLabels || status.avantLabels || 0);
            return !!(
                status.hasAvantData
                || Number(status.rows || 0) > 0
                || Number(status.dataTextNodes || 0) > 0
                || labels >= 2
                || (status.bodyHasAvantInfo && labels > 0)
            );
        }

        function statusAvantProShellSemDados(status) {
            if (!status || statusAvantProTemDadosColetaveis(status)) return false;
            if (status.loadingScreen && Number(status.cardCount || 0) <= 0) return false;
            return !!(
                status.shellOnly
                || status.extensionDetected
                || Number(status.actionButtons || 0) > 0
                || Number(status.toolsButtons || 0) > 0
                || Number(status.widgets || 0) > 0
            );
        }

        function normalizarStatusAvantProCarregadoParaColeta(status) {
            return {
                ...(status || {}),
                avantShellProntoParaColeta: true,
                ok: !!(status && status.ok),
                hasAvantData: !!(status && status.hasAvantData)
            };
        }

        function normalizarStatusAvantProPronto(status) {
            if (!statusAvantProTemDadosColetaveis(status)) return status || null;
            return {
                ...(status || {}),
                ok: true,
                hasAvantData: true
            };
        }

        async function prepararAvantProAntesDaPesquisaFavoritos(opcoes = {}) {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') {
                return { ok: false, reason: 'navegador_indisponivel' };
            }
            if (!monitoramentoPaginaFavoritosAutomaticoAtivo() && !acaoUsuarioFavoritosPermiteLeituraPagina(opcoes)) {
                return statusMonitoramentoPaginaFavoritosDesativado({
                    etapa: 'preparar_avant_antes_pesquisa'
                });
            }
            const termo = String(opcoes.termo || '').trim();
            const timeoutMs = Math.max(2500, Number(opcoes.timeoutMs) || 7000);
            const pollMs = Math.max(180, Number(opcoes.pollMs) || 350);
            const contexto = termo ? ` para "${termo}"` : '';
            const diagnosticarAtual = () => diagnosticarAvantProNoWebview().catch(() => null);
            if (typeof garantirExtensoesNavegadorMl === 'function') {
                await garantirExtensoesNavegadorMl().catch(() => null);
            }
            window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Preparando Avant Pro${contexto} antes da pesquisa...`, {
                manterNavegadorVisivel: true,
                larga: true,
                titulo: 'Preparando Avant Pro'
            });
            await abrirHomeMercadoLivreParaLoginAvantPro(termo).catch(() => false);
            if (typeof forcarNavegadorMlShellVisivel === 'function') {
                setTimeout(() => forcarNavegadorMlShellVisivel(), 60);
                setTimeout(() => forcarNavegadorMlShellVisivel(), 360);
            }

            limparConfirmacaoAvantProFavoritos();
            let status = await diagnosticarAtual();
            const entrada = await entrarAvantProPorFerramentasAntesPesquisa({
                termo,
                timeoutMs: Math.max(timeoutMs, 22000),
                pollMs
            }).catch(() => null);
            if (entrada && entrada.confirmed) {
                status = await diagnosticarAtual();
                return {
                    ...(status || {}),
                    ok: true,
                    loginAvantConfirmado: true,
                    entrada
                };
            }
            status = await diagnosticarAtual();
            mostrarAcaoConectarAvantPro(status || {});
            throw criarErroConfirmacaoAvantProObrigatoriaFavoritos(termo, status || {});
        }

        async function garantirAvantProProntoParaFavoritos(opcoes = {}) {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') {
                throw criarErroAvantProObrigatorioFavoritos('Navegador interno indisponivel para conectar o Avant Pro.');
            }
            if (!monitoramentoPaginaFavoritosAutomaticoAtivo() && !acaoUsuarioFavoritosPermiteLeituraPagina(opcoes)) {
                return statusMonitoramentoPaginaFavoritosDesativado({
                    etapa: 'garantir_avant_pronto'
                });
            }
            const termo = String(opcoes.termo || '').trim();
            const timeoutMs = Math.max(3500, Number(opcoes.timeoutMs) || 9000);
            const pollMs = Math.max(180, Number(opcoes.pollMs) || 350);
            const contexto = termo ? ` para "${termo}"` : '';
            const diagnosticarAtual = () => diagnosticarAvantProNoWebview().catch(() => null);
            let tentouLoginAvant = false;
            let status = await diagnosticarAtual();
            if (statusAvantProTemDadosColetaveis(status)) return normalizarStatusAvantProPronto(status);

            const recarregarAposLoginSePreciso = async (statusAtual) => {
                if (!tentouLoginAvant || !(statusAtual && statusAtual.reloadAposLoginAvantRecomendado)) return statusAtual;
                if (typeof recarregarNavegadorMlAposLoginAvantProFavoritos !== 'function') return statusAtual;
                return await recarregarNavegadorMlAposLoginAvantProFavoritos(statusAtual, {
                    termo,
                    timeoutMs: Math.max(6500, timeoutMs),
                    pollMs
                }).catch(() => statusAtual);
            };

            const tentarAguardarSemReload = async (extra = {}) => {
                const aguardado = await aguardarAvantProNoWebview({
                    timeoutMs,
                    pollMs,
                    recarregarSeAusente: false,
                    autoLoginAvant: false,
                    acaoUsuario: acaoUsuarioFavoritosPermiteLeituraPagina(opcoes),
                    ...extra
                }).catch(() => null);
                const proximo = aguardado || await diagnosticarAtual();
                return statusAvantProTemDadosColetaveis(proximo) ? normalizarStatusAvantProPronto(proximo) : proximo;
            };

            if (statusAvantProPedeLoginOuVinculo(status)) {
                window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Avant Pro nao retornou dados coletaveis${contexto}. Confirme manualmente o login no navegador interno.`, {
                    larga: true,
                    titulo: 'Avant Pro sem dados'
                });
                status = await tentarAguardarSemReload();
                status = await recarregarAposLoginSePreciso(status);
                if (statusAvantProTemDadosColetaveis(status)) return normalizarStatusAvantProPronto(status);
            }

            if (!statusAvantProTemDadosColetaveis(status)) {
                window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Verificando Avant Pro${contexto} antes de coletar os dados...`, {
                    larga: true,
                    titulo: 'Verificando Avant Pro'
                });
                await acionarControlesAvantProNoWebview({
                    forceClick: true,
                    permitirFerramentas: true,
                    maxClicks: 12
                }).catch(() => 0);
                await esperar(450);
                status = await diagnosticarAtual();
                if (statusAvantProPedeLoginOuVinculo(status)) {
                    window.FavoritosV2.searchRanking.publicApi.status.mostrarBalaoFavoritosStatus(`Avant Pro nao retornou dados coletaveis${contexto}. Confirme manualmente o login no navegador interno.`, {
                        larga: true,
                        titulo: 'Avant Pro sem dados'
                    });
                }
                status = await tentarAguardarSemReload({
                    timeoutMs: Math.max(12000, timeoutMs)
                });
                status = await recarregarAposLoginSePreciso(status);
                if (statusAvantProTemDadosColetaveis(status)) return normalizarStatusAvantProPronto(status);
                if (!statusAvantProPedeLoginOuVinculo(status) && statusAvantProShellSemDados(status) && Number(status && status.cardCount || 0) > 0) {
                    return normalizarStatusAvantProCarregadoParaColeta(status);
                }
            }

            mostrarAcaoConectarAvantPro(status || {});
            throw criarErroAvantProObrigatorioFavoritos(
                `Avant Pro nao retornou dados coletaveis${contexto}. Confirme manualmente se o login esta pronto no navegador interno e tente Fazer favoritos novamente.`,
                status || {}
            );
        }

  const api = { mostrarAcaoConectarAvantPro, criarErroAvantProObrigatorioFavoritos, criarErroConfirmacaoAvantProObrigatoriaFavoritos, statusAvantProPedeLoginOuVinculo, statusAvantProPedeEntradaAntesPesquisa, registrarLoginAvantProConfirmadoFavoritos, limparConfirmacaoAvantProFavoritos, detectarConfirmacaoLoginAvantProNoWebview, aguardarConfirmacaoLoginAvantProNoWebview, entrarAvantProPorFerramentasAntesPesquisa, statusAvantProTemDadosColetaveis, statusAvantProShellSemDados, normalizarStatusAvantProCarregadoParaColeta, normalizarStatusAvantProPronto, prepararAvantProAntesDaPesquisaFavoritos, garantirAvantProProntoParaFavoritos };
  browser.avantSession = Object.freeze(api);
  Object.assign(global, api);
})(window);
