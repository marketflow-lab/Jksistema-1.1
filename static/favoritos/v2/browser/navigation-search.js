(function (global) {
  'use strict';

  const browser = global.FavoritosV2.browser;
  const pageScripts = browser.pageScripts;

        function monitoramentoPaginaFavoritosAutomaticoAtivo() {
            return ML_FAVORITOS_MONITORAMENTO_PAGINA_AUTOMATICO === true;
        }

        function acaoUsuarioFavoritosPermiteLeituraPagina(opcoes = {}) {
            return !!(opcoes && (
                opcoes.acaoUsuario === true
                || opcoes.manual === true
                || opcoes.solicitadoPeloUsuario === true
                || opcoes.permitirMonitoramentoPagina === true
            ));
        }

        function statusMonitoramentoPaginaFavoritosDesativado(extra = {}) {
            return {
                ok: false,
                skipped: true,
                monitoramentoPaginaDesativado: true,
                reason: 'monitoramento_pagina_desativado',
                message: 'Monitoramento automatico da pagina desativado. Aguarde uma acao manual do usuario.',
                ...extra
            };
        }

        function cancelarAberturaMercadoLivreAoEntrar() {
            if (mlNavegadorAutoOpenTimer) {
                clearTimeout(mlNavegadorAutoOpenTimer);
                mlNavegadorAutoOpenTimer = null;
            }
        }

        function agendarAberturaMercadoLivreAoEntrar() {
            if (!monitoramentoPaginaFavoritosAutomaticoAtivo()) return;
            cancelarAberturaMercadoLivreAoEntrar();
            mlNavegadorAutoOpenTimer = setTimeout(() => {
                mlNavegadorAutoOpenTimer = null;
                const abaNavegadorAtiva = document.getElementById('aba-navegador')?.classList.contains('active');
                if (!abaNavegadorAtiva) return;
                if (!mlUrlInput.value || !/^https?:\/\//i.test(mlUrlInput.value.trim())) {
                    mlUrlInput.value = ML_DEFAULT_URL;
                }
                abrirMercadoLivreNoPrograma();
            }, 80);
        }

        function favoritosBrowserUrlUtils() {
            return window.FavoritosV2?.browser?.urlUtils || {};
        }

        function normalizarUrl(url) {
            return favoritosBrowserUrlUtils().normalizarUrl(url);
        }

        function normalizarUrlMercadoLivreParaComparacao(url) {
            return favoritosBrowserUrlUtils().normalizarUrlMercadoLivreParaComparacao(url);
        }

        function normalizarMlbFavoritosCanonico(valor) {
            return favoritosBrowserUrlUtils().normalizarMlbFavoritosCanonico(valor);
        }

        function construirUrlProdutoMercadoLivreCanonico(itemId) {
            return favoritosBrowserUrlUtils().construirUrlProdutoMercadoLivreCanonico(itemId);
        }

        function limparLinkProdutoMercadoLivreFavoritos(valor, itemId = '') {
            return favoritosBrowserUrlUtils().limparLinkProdutoMercadoLivreFavoritos(valor, itemId);
        }

        function chaveCanonicaAnuncioFavoritos(anuncio) {
            return favoritosBrowserUrlUtils().chaveCanonicaAnuncioFavoritos(anuncio);
        }

        function tituloFracoFavoritosCanonico(value, itemId = '') {
            return favoritosBrowserUrlUtils().tituloFracoFavoritosCanonico(value, itemId);
        }

        function tituloValidoFavoritosCanonico(value, itemId = '') {
            return favoritosBrowserUrlUtils().tituloValidoFavoritosCanonico(value, itemId);
        }

        function imagemValidaFavoritosCanonico(anuncio) {
            return favoritosBrowserUrlUtils().imagemValidaFavoritosCanonico(anuncio);
        }

        function precoValidoFavoritosCanonico(anuncio) {
            return favoritosBrowserUrlUtils().precoValidoFavoritosCanonico(anuncio);
        }

        function anuncioTemDadosAvantFavoritosCanonico(anuncio) {
            return favoritosBrowserUrlUtils().anuncioTemDadosAvantFavoritosCanonico(anuncio);
        }

        function similaridadeTitulosFavoritosCanonico(a, b) {
            return favoritosBrowserUrlUtils().similaridadeTitulosFavoritosCanonico(a, b);
        }

        function classificarQualidadeAnuncioFavoritosCanonico(anuncio) {
            return favoritosBrowserUrlUtils().classificarQualidadeAnuncioFavoritosCanonico(anuncio);
        }

        function prepararAnuncioMercadoLivreCanonico(anuncio, fontePadrao = 'mercado_livre_dom') {
            return favoritosBrowserUrlUtils().prepararAnuncioMercadoLivreCanonico(anuncio, fontePadrao);
        }

        function urlsMercadoLivreEquivalentes(urlAtual, urlAlvo) {
            return favoritosBrowserUrlUtils().urlsMercadoLivreEquivalentes(urlAtual, urlAlvo);
        }

        function construirUrlPesquisaMercadoLivre(termo) {
            const valor = (termo || '').trim();
            if (!valor) return ML_DEFAULT_URL;
            const slug = valor
                .normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '')
                .replace(/[^a-zA-Z0-9]+/g, '-')
                .replace(/^-+|-+$/g, '');
            return `https://lista.mercadolivre.com.br/${encodeURIComponent(slug || valor)}`;
        }

        function montarScriptGarantirPesquisaMercadoLivreSubmetida(valor, urlAlvo) {
            const termoSeguro = JSON.stringify(String(valor || '').trim());
            const urlSeguro = JSON.stringify(String(urlAlvo || '').trim());
            return pageScripts.render('montar-script-garantir-pesquisa-mercado-livre-submetida-1', { p0: (termoSeguro), p1: (urlSeguro) });
        }

        async function garantirPesquisaMercadoLivreSubmetida(valor, urlAlvo) {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return null;
            return await mlWebviewEl.executeJavaScript(
                montarScriptGarantirPesquisaMercadoLivreSubmetida(valor, urlAlvo),
                true
            ).catch((err) => ({
                ok: false,
                reason: 'erro_submit_pesquisa',
                error: err && err.message ? err.message : String(err)
            }));
        }

        function montarScriptDiagnosticarPesquisaMercadoLivreAtual(termo, urlAlvo) {
            const termoSeguro = JSON.stringify(String(termo || '').trim());
            const urlSeguro = JSON.stringify(String(urlAlvo || '').trim());
            return pageScripts.render('montar-script-diagnosticar-pesquisa-mercado-livre-atual-1', { p0: (termoSeguro), p1: (urlSeguro) });
        }

        async function aguardarPesquisaMercadoLivreAtual(termo, opcoes = {}) {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return null;
            const timeoutMs = Math.max(900, Number(opcoes.timeoutMs) || 9000);
            const pollMs = Math.max(120, Number(opcoes.pollMs) || 300);
            const urlAlvo = String(opcoes.url || construirUrlPesquisaMercadoLivre(termo) || '').trim();
            const inicio = Date.now();
            let ultimo = null;
            while (Date.now() - inicio <= timeoutMs) {
                ultimo = await mlWebviewEl.executeJavaScript(
                    montarScriptDiagnosticarPesquisaMercadoLivreAtual(termo, urlAlvo),
                    true
                ).catch((err) => ({
                    ok: false,
                    reason: 'erro_diagnostico_pesquisa',
                    error: err && err.message ? err.message : String(err)
                }));
                if (ultimo && ultimo.ok) return ultimo;
                if (ultimo && ultimo.reason === 'termo_nao_confere' && Date.now() - inicio > Math.min(1600, timeoutMs / 2)) {
                    await garantirPesquisaMercadoLivreSubmetida(termo, urlAlvo).catch(() => null);
                }
                await esperar(pollMs);
            }
            return ultimo || { ok: false, reason: 'timeout_pesquisa', termo };
        }

  const api = { monitoramentoPaginaFavoritosAutomaticoAtivo, acaoUsuarioFavoritosPermiteLeituraPagina, statusMonitoramentoPaginaFavoritosDesativado, cancelarAberturaMercadoLivreAoEntrar, agendarAberturaMercadoLivreAoEntrar, favoritosBrowserUrlUtils, normalizarUrl, normalizarUrlMercadoLivreParaComparacao, normalizarMlbFavoritosCanonico, construirUrlProdutoMercadoLivreCanonico, limparLinkProdutoMercadoLivreFavoritos, chaveCanonicaAnuncioFavoritos, tituloFracoFavoritosCanonico, tituloValidoFavoritosCanonico, imagemValidaFavoritosCanonico, precoValidoFavoritosCanonico, anuncioTemDadosAvantFavoritosCanonico, similaridadeTitulosFavoritosCanonico, classificarQualidadeAnuncioFavoritosCanonico, prepararAnuncioMercadoLivreCanonico, urlsMercadoLivreEquivalentes, construirUrlPesquisaMercadoLivre, montarScriptGarantirPesquisaMercadoLivreSubmetida, garantirPesquisaMercadoLivreSubmetida, montarScriptDiagnosticarPesquisaMercadoLivreAtual, aguardarPesquisaMercadoLivreAtual };
  browser.navigationSearch = Object.freeze(api);
  Object.assign(global, api);
})(window);
