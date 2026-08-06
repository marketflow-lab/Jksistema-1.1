        const ML_FAVORITOS_MONITORAMENTO_PAGINA_AUTOMATICO = false;

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
            return `
                (function () {
                    var valor = ${termoSeguro};
                    var urlAlvo = ${urlSeguro};
                    var normalizar = function (value) {
                        return String(value || '')
                            .normalize('NFD')
                            .replace(/[\\u0300-\\u036f]/g, '')
                            .toLowerCase()
                            .replace(/[^a-z0-9]+/g, ' ')
                            .replace(/\\s+/g, ' ')
                            .trim();
                    };
                    var candidatos = Array.prototype.slice.call(document.querySelectorAll(
                        'input[name="as_word"], input[name="q"], input[type="search"], input[placeholder*="Buscar"], input[aria-label*="Buscar"]'
                    ));
                    var input = candidatos.find(function (node) {
                        if (!node || node.disabled || node.readOnly) return false;
                        var rect = node.getBoundingClientRect ? node.getBoundingClientRect() : null;
                        var visivel = !rect || (rect.width > 0 && rect.height > 0);
                        var alvo = normalizar([node.name, node.id, node.placeholder, node.getAttribute && node.getAttribute('aria-label')].join(' '));
                        return visivel && (/buscar|search|as word|q/.test(alvo) || node.type === 'search');
                    }) || candidatos[0] || null;
                    var form = input && input.form ? input.form : document.querySelector('form[action*="mercadolivre"], form[action*="lista"], form');
                    if (input) {
                        input.focus();
                        try { input.value = valor; } catch (_valueErr) {}
                        try { input.setAttribute('value', valor); } catch (_attrErr) {}
                        ['input', 'change'].forEach(function (name) {
                            try { input.dispatchEvent(new Event(name, { bubbles: true, cancelable: true })); } catch (_eventErr) {}
                        });
                        ['keydown', 'keypress', 'keyup'].forEach(function (name) {
                            try { input.dispatchEvent(new KeyboardEvent(name, { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true })); } catch (_keyErr) {}
                        });
                    }
                    if (form) {
                        try {
                            if (typeof form.requestSubmit === 'function') {
                                form.requestSubmit();
                                return { ok: true, method: 'requestSubmit', url: location.href };
                            }
                        } catch (_requestSubmitErr) {}
                        try {
                            form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
                            if (typeof form.submit === 'function') {
                                form.submit();
                                return { ok: true, method: 'formSubmit', url: location.href };
                            }
                        } catch (_formErr) {}
                    }
                    if (urlAlvo) {
                        setTimeout(function () {
                            try { location.assign(urlAlvo); } catch (_assignErr) { location.href = urlAlvo; }
                        }, input || form ? 180 : 0);
                        return { ok: true, method: 'location.assign', url: urlAlvo };
                    }
                    return { ok: false, reason: 'sem_input_form_url', url: location.href };
                })();
            `;
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
            return `
                (function () {
                    var termo = ${termoSeguro};
                    var urlAlvo = ${urlSeguro};
                    var normalizar = function (value) {
                        return String(value || '')
                            .normalize('NFD')
                            .replace(/[\\u0300-\\u036f]/g, '')
                            .toLowerCase()
                            .replace(/[^a-z0-9]+/g, ' ')
                            .replace(/\\s+/g, ' ')
                            .trim();
                    };
                    var termoNorm = normalizar(termo);
                    var tokens = termoNorm.split(' ').filter(function (token) { return token.length >= 3; }).slice(0, 6);
                    var urlAtual = String(location.href || '');
                    var tituloPagina = normalizar(document.title || '');
                    var textoBusca = normalizar([
                        urlAtual,
                        tituloPagina,
                        document.querySelector('input[name="as_word"], input[name="q"], input[type="search"]') && document.querySelector('input[name="as_word"], input[name="q"], input[type="search"]').value,
                        document.querySelector('h1, .ui-search-breadcrumb__title, .ui-search-search-result__quantity-results') && document.querySelector('h1, .ui-search-breadcrumb__title, .ui-search-search-result__quantity-results').textContent
                    ].filter(Boolean).join(' '));
                    var cards = document.querySelectorAll('li.ui-search-layout__item, .poly-card, [class*="poly-card"], [class*="ui-search-result"], [data-testid*="result"], [data-testid*="card"], [class*="product-card"], [class*="andes-card"]').length;
                    var resultadoVisual = /\b\d+\s+resultados?\b/.test(textoBusca)
                        || !!document.querySelector('[class*="quantity-results"], [class*="ui-search-search-result"], [class*="breadcrumb__title"]');
                    var paginaProduto = !!(
                        document.querySelector('.ui-pdp-title, [class*="ui-pdp-title"], .ui-pdp-container, [class*="pdp"] h1')
                    );
                    var paginaCombinaComTermo = !tokens.length || tokens.some(function (token) {
                        return textoBusca.indexOf(token) >= 0;
                    });
                    var carregando = !!document.querySelector('[class*="loading"], [aria-busy="true"], .ui-search-loader');
                    var semResultados = /sem resultados|nao encontramos|não encontramos|no encontramos/.test(textoBusca);
                    if (cards > 0 && paginaCombinaComTermo && !paginaProduto) {
                        return { ok: true, cards: cards, paginaProduto: paginaProduto, url: urlAtual, termo: termo };
                    }
                    if (resultadoVisual && paginaCombinaComTermo && !paginaProduto) {
                        cards = Math.max(cards, 1);
                        return { ok: true, cards: cards, resultadoVisual: true, paginaProduto: paginaProduto, url: urlAtual, termo: termo };
                    }
                    if (semResultados && paginaCombinaComTermo && !paginaProduto) {
                        return { ok: true, noResults: true, cards: cards, paginaProduto: paginaProduto, url: urlAtual, termo: termo };
                    }
                    return {
                        ok: false,
                        reason: paginaProduto ? 'pagina_produto' : (paginaCombinaComTermo ? 'aguardando_resultados' : 'termo_nao_confere'),
                        cards: cards,
                        paginaProduto: paginaProduto,
                        loading: carregando,
                        url: urlAtual,
                        urlAlvo: urlAlvo,
                        termo: termo
                    };
                })();
            `;
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

        const ML_FAVORITOS_AVANT_CACHE_KEY = 'jk_favoritos_avant_cache_v1';
        const ML_FAVORITOS_AVANT_CACHE_TTL_MS = 6 * 60 * 60 * 1000;
        const ML_FAVORITOS_AVANT_CACHE_MAX = 1000;
        const ML_FAVORITOS_AVANT_RELOAD_APOS_LOGIN_KEYS = [
            'jk_favoritos_avant_login_retomar_sem_reload',
            'jk_favoritos_avant_pos_login_at'
        ];
        const ML_FAVORITOS_AVANT_LOGIN_RECENTE_MS = 90000;
        const ML_FAVORITOS_AVANT_LOGIN_CONFIRMADO_KEY = 'jk_favoritos_avant_login_confirmado_at';
        let mlFavoritosAvantSnapshotPromise = null;
        let mlFavoritosAvantSnapshotAt = 0;

        function obterElectronApiFavoritosMlBrowser() {
            try {
                if (window.electronAPI) return window.electronAPI;
            } catch (_err) {}
            try {
                if (window.top && window.top !== window && window.top.electronAPI) return window.top.electronAPI;
            } catch (_err) {}
            return null;
        }

        function salvarMemoriaAvantProConfirmadaFavoritos(reason = 'favoritos_login_confirmado') {
            const api = obterElectronApiFavoritosMlBrowser();
            if (!api || typeof api.saveAvantProStorageSnapshot !== 'function') {
                return Promise.resolve(null);
            }
            const agora = Date.now();
            if (mlFavoritosAvantSnapshotPromise && agora - mlFavoritosAvantSnapshotAt < 7000) {
                return mlFavoritosAvantSnapshotPromise;
            }
            mlFavoritosAvantSnapshotAt = agora;
            mlFavoritosAvantSnapshotPromise = api.saveAvantProStorageSnapshot(reason, {
                source: 'favoritos',
                confirmedAt: agora
            })
                .then((resultado) => {
                    if (resultado && resultado.success) {
                        console.info('Sessao Avant Pro salva para reutilizacao.', resultado);
                    } else if (resultado && !resultado.skipped) {
                        console.warn('Sessao Avant Pro nao foi salva:', resultado);
                    }
                    return resultado || null;
                })
                .catch((err) => {
                    console.warn('Falha ao salvar sessao Avant Pro:', err && err.message ? err.message : err);
                    return null;
                })
                .finally(() => {
                    setTimeout(() => {
                        mlFavoritosAvantSnapshotPromise = null;
                    }, 500);
                });
            return mlFavoritosAvantSnapshotPromise;
        }

        function favoritosBrowserAvantCache() {
            return window.FavoritosV2?.browser?.avantCache || {};
        }

        function normalizarChaveCacheAvant(value) {
            return favoritosBrowserAvantCache().normalizarChaveCacheAvant(value);
        }

        function obterLojaContextoAvant(contexto = {}) {
            return favoritosBrowserAvantCache().obterLojaContextoAvant(contexto);
        }

        function obterSkuContextoAvant(contexto = {}) {
            return favoritosBrowserAvantCache().obterSkuContextoAvant(contexto);
        }

        function normalizarUrlCacheAvant(url) {
            return favoritosBrowserAvantCache().normalizarUrlCacheAvant(url);
        }

        function chaveCacheAvantAnuncio(anuncio, contexto = {}) {
            return favoritosBrowserAvantCache().chaveCacheAvantAnuncio(anuncio, contexto);
        }

        function carregarCacheAvantFavoritos() {
            return favoritosBrowserAvantCache().carregarCacheAvantFavoritos();
        }

        function salvarCacheAvantFavoritos(cache) {
            return favoritosBrowserAvantCache().salvarCacheAvantFavoritos(cache);
        }

        function cacheAvantAindaValido(item) {
            return favoritosBrowserAvantCache().cacheAvantAindaValido(item);
        }

        function dadosAvantCacheaveis(anuncio) {
            return favoritosBrowserAvantCache().dadosAvantCacheaveis(anuncio);
        }

        function aplicarCacheAvantAosAnuncios(anuncios, contexto = {}) {
            return favoritosBrowserAvantCache().aplicarCacheAvantAosAnuncios(anuncios, contexto);
        }

        function salvarCacheAvantDosAnuncios(anuncios, contexto = {}) {
            return favoritosBrowserAvantCache().salvarCacheAvantDosAnuncios(anuncios, contexto);
        }

        function obterCamposPesquisaAvulsaMl() {
            return [
                { input: mlSearchTermInput, campo: 1 },
                { input: mlSearchTerm2Input, campo: 2 },
                { input: mlSearchTerm3Input, campo: 3 }
            ].filter(item => item.input);
        }

        function normalizarTermosPesquisaFavoritos(termos) {
            const vistos = new Set();
            const saida = [];
            (Array.isArray(termos) ? termos : []).forEach((item, index) => {
                const termo = String(item && (item.termo || item.term || item.valor || item.value) || item || '').trim();
                const chave = termo.toLowerCase();
                if (!termo || vistos.has(chave)) return;
                vistos.add(chave);
                const campoRaw = item && (item.campo || item.field || item.numero || item.index);
                const campo = Number.parseInt(campoRaw, 10);
                saida.push({
                    campo: Number.isFinite(campo) && campo > 0 ? campo : index + 1,
                    termo
                });
            });
            return saida.slice(0, 3);
        }

        function obterTermosPesquisaAvulsaMl() {
            return normalizarTermosPesquisaFavoritos(
                obterCamposPesquisaAvulsaMl().map(item => ({
                    campo: item.campo,
                    termo: item.input.value || ''
                }))
            );
        }

        function formatarTermosPesquisaFavoritos(termos) {
            const normalizados = normalizarTermosPesquisaFavoritos(termos);
            return normalizados.length
                ? normalizados.map(item => `${item.campo}: ${item.termo}`).join(' | ')
                : '';
        }

        function preencherCamposPesquisaAvulsaMl(termos) {
            const normalizados = normalizarTermosPesquisaFavoritos(termos);
            const campos = obterCamposPesquisaAvulsaMl();
            campos.forEach(item => {
                item.input.value = '';
            });
            normalizados.forEach((termo, index) => {
                const alvo = campos.find(item => Number(item.campo) === Number(termo.campo)) || campos[index];
                if (alvo && alvo.input) alvo.input.value = termo.termo;
            });
        }

        function obterPrimeiroTermoPesquisaAvulsaMl() {
            const primeiro = obterTermosPesquisaAvulsaMl()[0];
            return primeiro ? primeiro.termo : '';
        }

        function formatarDataCriacao(valor) {
            if (!valor) return '';
            const texto = String(valor).trim();
            const br = texto.match(/^(\d{1,2})\/(\d{1,2})\/(\d{2,4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$/);
            if (br) {
                const ano = br[3].length === 2 ? `20${br[3]}` : br[3];
                return `${br[1].padStart(2, '0')}/${br[2].padStart(2, '0')}/${ano}${br[4] ? `, ${br[4].padStart(2, '0')}:${br[5]}:${br[6] || '00'}` : ''}`;
            }
            try {
                const data = new Date(valor);
                if (!Number.isNaN(data.getTime())) {
                    return data.toLocaleString('pt-BR');
                }
            } catch (e) {
                return texto;
            }
            return texto;
        }

        function parseDataCriacao(valor) {
            if (!valor) return null;
            if (valor instanceof Date) return Number.isNaN(valor.getTime()) ? null : valor;
            const texto = String(valor).trim();
            if (!texto) return null;

            const br = texto.match(/^(\d{1,2})\/(\d{1,2})\/(\d{2,4})(?:,?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$/);
            if (br) {
                const ano = Number(br[3].length === 2 ? `20${br[3]}` : br[3]);
                const mes = Number(br[2]) - 1;
                const dia = Number(br[1]);
                const hora = Number(br[4] || 0);
                const minuto = Number(br[5] || 0);
                const segundo = Number(br[6] || 0);
                const dataBr = new Date(ano, mes, dia, hora, minuto, segundo);
                return Number.isNaN(dataBr.getTime()) ? null : dataBr;
            }

            const isoBr = texto.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T\s](\d{2}):(\d{2})(?::(\d{2}))?)?/);
            if (isoBr && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(texto)) {
                const dataLocal = new Date(
                    Number(isoBr[1]),
                    Number(isoBr[2]) - 1,
                    Number(isoBr[3]),
                    Number(isoBr[4] || 0),
                    Number(isoBr[5] || 0),
                    Number(isoBr[6] || 0)
                );
                return Number.isNaN(dataLocal.getTime()) ? null : dataLocal;
            }

            const data = new Date(texto);
            return Number.isNaN(data.getTime()) ? null : data;
        }

        function formatarNumeroDecimal(valor, casas = 1) {
            if (!Number.isFinite(valor)) return '';
            return valor.toLocaleString('pt-BR', {
                minimumFractionDigits: casas,
                maximumFractionDigits: casas
            });
        }

        function parseNumeroDecimalFavoritos(valor) {
            if (valor === null || valor === undefined || valor === '') return null;
            if (typeof valor === 'number') return Number.isFinite(valor) ? valor : null;
            let texto = String(valor || '').replace(/\s+/g, '').trim();
            if (!texto) return null;
            const match = texto.match(/-?\d[\d.,]*/);
            if (!match) return null;
            texto = match[0];
            if (texto.includes('.') && texto.includes(',')) {
                texto = texto.lastIndexOf('.') > texto.lastIndexOf(',')
                    ? texto.replace(/,/g, '')
                    : texto.replace(/\./g, '').replace(',', '.');
            } else if (texto.includes(',')) {
                texto = texto.replace(/\./g, '').replace(',', '.');
            }
            const numero = Number(texto);
            return Number.isFinite(numero) ? numero : null;
        }

        function calcularMetricasMediaVendas(anuncio) {
            const vendas = parseVendasAvantPro(anuncio);
            const mediaDireta = parseNumeroDecimalFavoritos(
                anuncio && (
                    anuncio.media_vendas_mensal ??
                    anuncio.media_mensal ??
                    anuncio.ritmo_atual ??
                    anuncio.ritmo_vendas_mes ??
                    anuncio.media_vendas_mensal ??
                    ''
                )
            );
            const dataCriacao = parseDataCriacao(anuncio && anuncio.data_criacao);
            if (vendas === null || !dataCriacao) {
                return {
                    vendas,
                    meses: null,
                    media: Number.isFinite(mediaDireta) ? mediaDireta : null,
                    dataCriacao: dataCriacao || null
                };
            }

            const agora = new Date();
            const diffMs = agora.getTime() - dataCriacao.getTime();
            if (!Number.isFinite(diffMs) || diffMs < 0) {
                return {
                    vendas,
                    meses: null,
                    media: Number.isFinite(mediaDireta) ? mediaDireta : null,
                    dataCriacao
                };
            }

            const dias = Math.max(1, diffMs / 86400000);
            const meses = dias / 30.4375;
            const mediaCalculada = vendas / meses;
            const media = Number.isFinite(mediaCalculada)
                ? mediaCalculada
                : (Number.isFinite(mediaDireta) ? mediaDireta : null);
            return { vendas, meses, media, dataCriacao };
        }

        function formatarMediaVendas(anuncio) {
            const metrica = calcularMetricasMediaVendas(anuncio);
            if (!Number.isFinite(metrica.media)) return '';
            return formatarNumeroDecimal(metrica.media, metrica.media >= 10 ? 1 : 2);
        }

        function calcularDiasAnuncio(anuncio) {
            const dataCriacao = parseDataCriacao(anuncio && anuncio.data_criacao);
            if (!dataCriacao) return null;
            const diffMs = Date.now() - dataCriacao.getTime();
            if (!Number.isFinite(diffMs) || diffMs < 0) return null;
            return Math.max(0, Math.floor(diffMs / 86400000));
        }

        function formatarDiasAnuncio(anuncio) {
            const dias = calcularDiasAnuncio(anuncio);
            if (!Number.isFinite(dias)) return '';
            if (dias === 0) return 'Hoje';
            return `${dias} dia${dias === 1 ? '' : 's'}`;
        }

        function formatarMesesMedia(meses) {
            if (!Number.isFinite(meses)) return '';
            if (meses <= 1) return '1 mês';
            return `${formatarNumeroDecimal(meses, 1)} meses`;
        }

        function mostrarHintAbertura() {
            mlFrameHint.classList.remove('hidden');
        }

        function rankingSidebarEstaMinimizado() {
            try {
                return localStorage.getItem(ML_RANKING_SIDEBAR_COLLAPSED_KEY) === '1';
            } catch (_err) {
                return false;
            }
        }

        function aplicarEstadoRankingSidebar(minimizado) {
            if (!mlRankingMediaEl) return;
            mlRankingMediaEl.classList.toggle('is-collapsed', !!minimizado);
            document.body.classList.toggle('ml-ranking-sidebar-collapsed', !!minimizado);
            if (mlRankingToggleEl) {
                mlRankingToggleEl.textContent = minimizado ? '>' : '<';
                mlRankingToggleEl.title = minimizado ? 'Expandir ranking' : 'Minimizar ranking';
                mlRankingToggleEl.setAttribute('aria-label', minimizado ? 'Expandir ranking' : 'Minimizar ranking');
                mlRankingToggleEl.setAttribute('aria-expanded', minimizado ? 'false' : 'true');
            }
        }

        function alternarRankingSidebar() {
            const proximoEstado = !rankingSidebarEstaMinimizado();
            try {
                localStorage.setItem(ML_RANKING_SIDEBAR_COLLAPSED_KEY, proximoEstado ? '1' : '0');
            } catch (_err) {}
            aplicarEstadoRankingSidebar(proximoEstado);
            atualizarEstadoSidebarRanking();
        }

        function skuSidebarEstaMinimizado() {
            try {
                return localStorage.getItem(ML_SKU_SIDEBAR_COLLAPSED_KEY) === '1';
            } catch (_err) {
                return false;
            }
        }

        function aplicarEstadoSkuSidebar(minimizado = skuSidebarEstaMinimizado()) {
            document.querySelectorAll('.ml-sku-sidebar').forEach(sidebar => {
                sidebar.classList.toggle('is-hidden', !!minimizado);
            });
            document.body.classList.toggle('ml-sku-sidebar-collapsed', !!minimizado);
            mlSkuSidebarToggleEls.forEach(botao => {
                botao.textContent = minimizado ? '>' : '<';
                botao.title = minimizado ? 'Expandir lista de SKU' : 'Minimizar lista de SKU';
                botao.setAttribute('aria-label', minimizado ? 'Expandir lista de SKU' : 'Minimizar lista de SKU');
                botao.setAttribute('aria-expanded', minimizado ? 'false' : 'true');
            });
        }

        function alternarSkuSidebar() {
            const proximoEstado = !skuSidebarEstaMinimizado();
            try {
                localStorage.setItem(ML_SKU_SIDEBAR_COLLAPSED_KEY, proximoEstado ? '1' : '0');
            } catch (_err) {}
            aplicarEstadoSkuSidebar(proximoEstado);
            atualizarEstadoSidebarRanking();
        }

        function limitarLarguraSidebarRanking(valor) {
            const numero = Number(valor);
            if (!Number.isFinite(numero)) return 320;
            const maximo = Math.min(560, Math.max(300, window.innerWidth - 120));
            return Math.max(260, Math.min(maximo, numero));
        }

        function aplicarLarguraSidebarRanking(valor) {
            const largura = limitarLarguraSidebarRanking(valor);
            document.documentElement.style.setProperty('--ml-ranking-sidebar-width', `${largura}px`);
            return largura;
        }

        function carregarLarguraSidebarRanking() {
            try {
                const salva = Number(localStorage.getItem(ML_RANKING_SIDEBAR_WIDTH_KEY));
                if (Number.isFinite(salva) && salva > 0) return salva;
            } catch (_err) {}
            return 320;
        }

        function salvarLarguraSidebarRanking(valor) {
            try {
                localStorage.setItem(ML_RANKING_SIDEBAR_WIDTH_KEY, String(Math.round(limitarLarguraSidebarRanking(valor))));
            } catch (_err) {}
        }

        function limitarLarguraSkuSidebar(valor) {
            const numero = Number(valor);
            if (!Number.isFinite(numero)) return 300;
            const maximo = Math.min(520, Math.max(280, window.innerWidth - 120));
            return Math.max(240, Math.min(maximo, numero));
        }

        function aplicarLarguraSkuSidebar(valor) {
            const largura = limitarLarguraSkuSidebar(valor);
            document.documentElement.style.setProperty('--ml-sku-sidebar-width', `${largura}px`);
            return largura;
        }

        function carregarLarguraSkuSidebar() {
            try {
                const salva = Number(localStorage.getItem(ML_SKU_SIDEBAR_WIDTH_KEY));
                if (Number.isFinite(salva) && salva > 0) return salva;
            } catch (_err) {}
            return 300;
        }

        function salvarLarguraSkuSidebar(valor) {
            try {
                localStorage.setItem(ML_SKU_SIDEBAR_WIDTH_KEY, String(Math.round(limitarLarguraSkuSidebar(valor))));
            } catch (_err) {}
        }

        function iniciarAjusteLarguraSidebar(event) {
            if (!mlRankingMediaEl || rankingSidebarEstaMinimizado()) return;
            if (event.button !== undefined && event.button !== 0) return;
            event.preventDefault();
            document.body.classList.add('ml-sidebar-resizing');

            const mover = (moveEvent) => {
                const clientX = Number(moveEvent.clientX);
                if (!Number.isFinite(clientX)) return;
                const largura = aplicarLarguraSidebarRanking(window.innerWidth - clientX - 16);
                salvarLarguraSidebarRanking(largura);
            };

            const parar = () => {
                document.body.classList.remove('ml-sidebar-resizing');
                window.removeEventListener('pointermove', mover);
                window.removeEventListener('pointerup', parar);
                window.removeEventListener('pointercancel', parar);
            };

            window.addEventListener('pointermove', mover);
            window.addEventListener('pointerup', parar);
            window.addEventListener('pointercancel', parar);
        }

        function iniciarAjusteLarguraSkuSidebar(event) {
            if (!mlSkuSidebarSectionEl || skuSidebarEstaMinimizado()) return;
            if (event.button !== undefined && event.button !== 0) return;
            event.preventDefault();
            document.body.classList.add('ml-sidebar-resizing');

            const mover = (moveEvent) => {
                const clientX = Number(moveEvent.clientX);
                if (!Number.isFinite(clientX)) return;
                const largura = aplicarLarguraSkuSidebar(clientX - 16);
                salvarLarguraSkuSidebar(largura);
            };

            const parar = () => {
                document.body.classList.remove('ml-sidebar-resizing');
                window.removeEventListener('pointermove', mover);
                window.removeEventListener('pointerup', parar);
                window.removeEventListener('pointercancel', parar);
            };

            window.addEventListener('pointermove', mover);
            window.addEventListener('pointerup', parar);
            window.addEventListener('pointercancel', parar);
        }

        function atualizarEstadoSidebarRanking() {
            const abaNavegadorAtiva = document.getElementById('aba-navegador')?.classList.contains('active');
            const moduloFavoritosPronto = !document.body.classList.contains('favoritos-store-pending');
            const temAnuncios = Array.isArray(mlAnunciosPrimeiraPaginaAtuais) && mlAnunciosPrimeiraPaginaAtuais.length > 0;
            const navegadorEmBalao = balaoResultadosMlAberto();
            document.body.classList.toggle('ml-ranking-sidebar-active', !!(abaNavegadorAtiva && temAnuncios));
            document.body.classList.toggle('ml-sku-sidebar-active', !!(moduloFavoritosPronto && mlSkuSidebarSectionEl));
            if (mlShellBrowserProxy) {
                if (navegadorEmBalao && mlShellBrowserProxy.__visible) atualizarPosicaoNavegadorMlShell();
                else ocultarNavegadorMlShellDefinitivo();
            }
            aplicarEstadoRankingSidebar(rankingSidebarEstaMinimizado());
            aplicarEstadoSkuSidebar(skuSidebarEstaMinimizado());
        }

        function setBrowserStatus(message) {
            ocultarNavegadorMlShellDefinitivo();
            mlBrowserHost.innerHTML = `
                <div class="browser-warning">
                    <strong>${message}</strong>
                    <span>Você pode alterar a URL e clicar em "Abrir no Programa" novamente.</span>
                </div>
            `;
        }

        function aplicarScrollbarsDiscretasNoWebview(webview) {
            if (!webview || typeof webview.insertCSS !== 'function') return;
            try {
                const resultado = webview.insertCSS(ML_DISCREET_SCROLLBAR_CSS);
                if (resultado && typeof resultado.catch === 'function') {
                    resultado.catch(() => {});
                }
            } catch (_err) {}
        }

        function inicializarBalaoResultadosMl() {
            if (mlWorkModalInicializado) return;
            mlWorkModalInicializado = true;

            if (mlWorkModalBrowserSlotEl && mlBrowserFrameWrapEl) {
                mlWorkModalBrowserSlotEl.appendChild(mlBrowserFrameWrapEl);
            }

            if (mlWorkModalResultsSlotEl) {
                const painelPrimeiraPagina = mlPrimeiraPaginaStatusEl ? mlPrimeiraPaginaStatusEl.closest('.panel') : null;
                if (painelPrimeiraPagina) {
                    painelPrimeiraPagina.classList.add('ml-work-modal-panel');
                    mlWorkModalResultsSlotEl.appendChild(painelPrimeiraPagina);
                }
                if (mlFavoritosPanelEl) {
                    mlFavoritosPanelEl.classList.add('ml-work-modal-panel');
                    mlWorkModalResultsSlotEl.appendChild(mlFavoritosPanelEl);
                }
            }
        }

        function balaoResultadosMlAberto() {
            const abaNavegadorAtiva = document.getElementById('aba-navegador')?.classList.contains('active');
            return !!(abaNavegadorAtiva && mlWorkModalEl && !mlWorkModalEl.classList.contains('hidden'));
        }

        function navegadorMlEmSegundoPlano() {
            return !!(
                (mlFavoritosEmExecucao && mlFavoritosExecucaoEmSegundoPlano)
                || window.__JK_FAVORITOS_WORKER_BROWSER_ACTIVE === true
            );
        }

        function abrirBalaoResultadosMl(opcoes = {}) {
            inicializarBalaoResultadosMl();
            if (mlWorkModalTitleEl) mlWorkModalTitleEl.textContent = opcoes.titulo || 'Resultados do Mercado Livre';
            if (mlWorkModalSubtitleEl) mlWorkModalSubtitleEl.textContent = opcoes.subtitulo || '';
            if (mlFavoritosPanelEl && opcoes.mostrarFavoritos) mlFavoritosPanelEl.classList.remove('hidden');
            if (mlFavoritosPanelEl && !opcoes.mostrarFavoritos && !mlFavoritosEmExecucao) mlFavoritosPanelEl.classList.add('hidden');
            const browserCompleto = !!(opcoes.browserCompleto || mlFavoritosEmExecucao);
            const manterSegundoPlano = navegadorMlEmSegundoPlano() && !opcoes.forcarExibicao;
            if (mlWorkModalEl) {
                mlWorkModalEl.dataset.browserOnly = browserCompleto ? '1' : '0';
                mlWorkModalEl.classList.toggle('is-browser-only', browserCompleto);
                if (manterSegundoPlano) {
                    mlWorkModalEl.classList.add('hidden');
                    document.body.classList.remove('ml-work-modal-open');
                } else {
                    mlWorkModalEl.classList.remove('hidden');
                    document.body.classList.add('ml-work-modal-open');
                }
            }
            atualizarFiltroAzulFavoritos();
            posicionarBalaoFavoritosStatus();
            if (!manterSegundoPlano) {
                setTimeout(() => {
                    atualizarPosicaoNavegadorMlShell();
                    agendarAtualizacaoPosicaoNavegadorMlShell();
                }, 80);
            }
        }

        function fecharBalaoResultadosMl(opcoes = {}) {
            const forcar = !!(opcoes && opcoes.forcar);
            const ocultandoExecucao = mlFavoritosEmExecucao && !forcar;
            if (ocultandoExecucao) mlFavoritosExecucaoEmSegundoPlano = true;
            if (mlWorkModalEl) {
                mlWorkModalEl.dataset.browserOnly = '0';
                mlWorkModalEl.classList.add('hidden');
                mlWorkModalEl.classList.remove('is-browser-only');
                mlWorkModalEl.classList.remove('is-favoritos-running');
            }
            if (mlBrowserFrameWrapEl) {
                mlBrowserFrameWrapEl.classList.remove('is-favoritos-running');
            }
            if (mlWorkModalCloseEl) {
                mlWorkModalCloseEl.disabled = false;
            }
            if (mlWorkModalCancelEl) {
                mlWorkModalCancelEl.classList.add('hidden');
                mlWorkModalCancelEl.disabled = true;
            }
            document.body.classList.remove('ml-work-modal-open');
            posicionarBalaoFavoritosStatus();
            ocultarNavegadorMlShellDefinitivo({
                descarregarConteudo: !!(opcoes && (opcoes.descarregarConteudo || opcoes.destroy || opcoes.unload)),
                reason: opcoes.reason || 'favoritos-modal-close',
                preserveAvantProSession: opcoes.preserveAvantProSession !== false
            });
            if (ocultandoExecucao) {
                mostrarBalaoFavoritosStatus('Favoritos continua rodando em segundo plano. Voce pode usar outras abas e modulos; use Cancelar favoritos no sidebar se precisar parar.', {
                    tempoMs: 6000,
                    larga: true
                });
            }
        }

        function garantirCamadaBalaoFavoritosStatus() {
            return window.FavoritosV2?.ui?.statusModal?.garantirCamadaBalaoFavoritosStatus?.() || null;
        }

        function posicionarBalaoFavoritosStatus() {
            return window.FavoritosV2?.ui?.statusModal?.posicionarBalaoFavoritosStatus?.();
        }

        function criarMlWebview() {
            const deveUsarWorkerFavoritos = navegadorMlEmSegundoPlano()
                && usarNavegadorMlNoShellElectron();
            if (deveUsarWorkerFavoritos) {
                if (mlWebviewEl && !mlWebviewEl.__isShellBrowserProxy) {
                    try {
                        if (mlWebviewEl.parentNode) mlWebviewEl.parentNode.removeChild(mlWebviewEl);
                    } catch (_removeErr) {}
                    mlWebviewEl = null;
                }
                mlBrowserHost.innerHTML = `
                    <div class="browser-warning">
                        <strong>Favoritos rodando no navegador trabalhador.</strong>
                        <span>Use o botao Ver para acompanhar a coleta.</span>
                    </div>
                `;
                mlWebviewEl = criarProxyNavegadorMlShell();
                aplicarScrollbarsDiscretasNoWebview(mlWebviewEl);
                return mlWebviewEl;
            }

            if (mlWebviewEl && mlWebviewEl.parentNode) {
                aplicarScrollbarsDiscretasNoWebview(mlWebviewEl);
                return mlWebviewEl;
            }

            mlBrowserHost.innerHTML = '';
            if (usarNavegadorMlNoShellElectron()) {
                mlBrowserHost.innerHTML = `
                    <div class="browser-warning">
                        <strong>Carregando Mercado Livre no navegador interno...</strong>
                        <span>Se a pagina pedir login, conclua no quadro abaixo.</span>
                    </div>
                `;
                mlWebviewEl = criarProxyNavegadorMlShell();
                aplicarScrollbarsDiscretasNoWebview(mlWebviewEl);
                return mlWebviewEl;
            }

            mlWebviewEl = document.createElement('webview');
            mlWebviewEl.className = 'browser-webview';
            mlWebviewEl.setAttribute('partition', obterParticaoNavegadorPersistente());
            mlWebviewEl.setAttribute('allowpopups', 'true');
            mlWebviewEl.setAttribute('webpreferences', 'contextIsolation=yes,nodeIntegration=no');

            const isAllowedWebUrl = (targetUrl) => {
                const value = String(targetUrl || '').trim();
                if (!value) return true;
                return /^(https?:\/\/|about:blank$|chrome-extension:\/\/)/i.test(value);
            };
            const blockIfExternalProtocol = (event) => {
                const targetUrl = event && event.url ? event.url : '';
                if (!isAllowedWebUrl(targetUrl)) {
                    event.preventDefault();
                }
            };

            const syncUrl = (event) => {
                const nextUrl = (event && event.url) || (typeof mlWebviewEl.getURL === 'function' ? mlWebviewEl.getURL() : '');
                if (nextUrl && /^https?:\/\//i.test(nextUrl)) {
                    mlUrlInput.value = nextUrl;
                }
            };

            mlWebviewEl.addEventListener('did-navigate', syncUrl);
            mlWebviewEl.addEventListener('did-navigate-in-page', syncUrl);
            mlWebviewEl.addEventListener('dom-ready', () => {
                aplicarScrollbarsDiscretasNoWebview(mlWebviewEl);
                tentarLoginAvantProNoWebview(mlWebviewEl, { somenteSeAutorizado: true, verificarDadosAntes: true });
            });
            mlWebviewEl.addEventListener('did-finish-load', () => {
                aplicarScrollbarsDiscretasNoWebview(mlWebviewEl);
                tentarLoginAvantProNoWebview(mlWebviewEl, { somenteSeAutorizado: true, verificarDadosAntes: true });
            });
            mlWebviewEl.addEventListener('did-finish-load', salvarSessaoNavegadorElectron);
            mlWebviewEl.addEventListener('did-navigate', salvarSessaoNavegadorElectron);
            mlWebviewEl.addEventListener('will-navigate', blockIfExternalProtocol);
            mlWebviewEl.addEventListener('will-frame-navigate', blockIfExternalProtocol);
            mlWebviewEl.addEventListener('new-window', (event) => {
                if (event && event.url) {
                    event.preventDefault();
                    if (isAllowedWebUrl(event.url)) {
                        navegarMlWebview(event.url).catch(() => {});
                    }
                }
            });

            mlBrowserHost.appendChild(mlWebviewEl);
            return mlWebviewEl;
        }

        function navegarMlWebviewUmaTentativa(webview, url, opcoes = {}) {
            const timeoutMs = Number(opcoes.timeoutMs) > 0 ? Number(opcoes.timeoutMs) : 25000;
            const aceitarDidNavigate = !!opcoes.aceitarDidNavigate;
            const exigirUrlAlvo = !!opcoes.exigirUrlAlvo;
            const timeoutMessage = opcoes.timeoutMessage || 'Tempo limite ao abrir a pagina no quadro interno.';
            const urlComparavel = (value) => {
                try {
                    const parsed = new URL(String(value || ''), window.location.href);
                    parsed.hash = '';
                    return parsed.toString();
                } catch (_err) {
                    return String(value || '').replace(/#.*$/, '');
                }
            };
            const urlCorrespondeAoAlvo = (alvo) => {
                if (!exigirUrlAlvo) return true;
                const atual = urlComparavel(alvo || '');
                const esperado = urlComparavel(url || '');
                return !!(atual && esperado && atual === esperado);
            };
            const erroNavegacaoTransitorio = (event) => {
                const codigo = Number(event && event.errorCode);
                const descricao = String((event && event.errorDescription) || event && event.message || '');
                const alvo = String((event && event.url) || (webview && (webview.currentUrl || (typeof webview.getURL === 'function' ? webview.getURL() : ''))) || url || '');
                if (!/mercadolivre\.com\.br|mercadolibre\.com/i.test(alvo)) return false;
                return codigo === -3
                    || /\bERR_ABORTED\b|\(-3\)|falha ao carregar pagina \(-3\)|timeout/i.test(descricao);
            };
            return new Promise((resolve, reject) => {
                let done = false;
                const finish = (err) => {
                    if (done) return;
                    done = true;
                    clearTimeout(timer);
                    webview.removeEventListener('did-finish-load', onLoad);
                    webview.removeEventListener('did-fail-load', onFail);
                    webview.removeEventListener('did-navigate', onNavigate);
                    webview.removeEventListener('did-navigate-in-page', onNavigate);
                    if (err) reject(err);
                    else resolve();
                };
                const onLoad = (event) => {
                    const alvo = event && event.url
                        ? event.url
                        : (webview && (webview.currentUrl || (typeof webview.getURL === 'function' ? webview.getURL() : '')));
                    if (!urlCorrespondeAoAlvo(alvo)) {
                        setBrowserStatus('Aguardando o Mercado Livre abrir a pesquisa correta...');
                        return;
                    }
                    finish();
                };
                const onFail = (event) => {
                    if (erroNavegacaoTransitorio(event)) {
                        setBrowserStatus('Mercado Livre ainda esta carregando no navegador interno...');
                        return;
                    }
                    finish(new Error((event && event.errorDescription) || 'Falha ao carregar pagina.'));
                };
                const onNavigate = (event) => {
                    if (!aceitarDidNavigate) return;
                    const alvo = String((event && event.url) || '').trim();
                    if (!/^https?:\/\//i.test(alvo)) return;
                    if (!urlCorrespondeAoAlvo(alvo)) return;
                    finish();
                };
                const timer = setTimeout(() => finish(new Error(timeoutMessage)), timeoutMs);

                webview.addEventListener('did-finish-load', onLoad);
                webview.addEventListener('did-fail-load', onFail);
                if (aceitarDidNavigate) {
                    webview.addEventListener('did-navigate', onNavigate);
                    webview.addEventListener('did-navigate-in-page', onNavigate);
                }
                webview.src = url;
            });
        }

        function urlPertenceAoMercadoLivre(url) {
            try {
                const host = new URL(String(url || '')).hostname.toLowerCase();
                return host === 'mercadolivre.com.br'
                    || host.endsWith('.mercadolivre.com.br')
                    || host === 'mercadolibre.com'
                    || host.endsWith('.mercadolibre.com');
            } catch (_err) {
                return false;
            }
        }

        function urlNavegadorMlSeguraParaLog(url) {
            try {
                const parsed = new URL(String(url || ''));
                return `${parsed.origin}${parsed.pathname}`;
            } catch (_err) {
                return '';
            }
        }

        async function confirmarAberturaNavegadorMlAposTimeout(urlEsperada, erroOriginal = null) {
            if (!usarNavegadorMlNoShellElectron()) {
                return { confirmado: false, reason: 'shell-indisponivel', url: '' };
            }
            const bridge = favoritosBrowserShellBridge;
            if (!bridge || typeof bridge.verificarEstado !== 'function') {
                return { confirmado: false, reason: 'verificacao-indisponivel', url: '' };
            }
            try {
                const estado = await bridge.verificarEstado(urlEsperada, 2600);
                const urlAtual = String(estado && estado.url || '').trim();
                const confirmado = !!(
                    estado
                    && estado.success !== false
                    && estado.attached !== false
                    && urlPertenceAoMercadoLivre(urlEsperada)
                    && urlPertenceAoMercadoLivre(urlAtual)
                );
                if (!confirmado) {
                    return {
                        confirmado: false,
                        reason: estado && estado.reason || 'url-real-nao-confirmada',
                        url: urlAtual,
                        estado
                    };
                }
                if (mlWebviewEl && mlWebviewEl.__isShellBrowserProxy) {
                    mlWebviewEl.currentUrl = urlAtual;
                    mlWebviewEl.__visible = !navegadorMlEmSegundoPlano();
                }
                if (mlUrlInput && urlAtual) mlUrlInput.value = urlAtual;
                if (!navegadorMlEmSegundoPlano()) agendarAtualizacaoPosicaoNavegadorMlShell();
                console.warn('A confirmacao de carregamento nao chegou, mas o navegador interno esta aberto.', {
                    erro: erroOriginal && erroOriginal.message ? erroOriginal.message : String(erroOriginal || ''),
                    urlEsperada: urlNavegadorMlSeguraParaLog(urlEsperada),
                    urlAtual: urlNavegadorMlSeguraParaLog(urlAtual)
                });
                return { confirmado: true, url: urlAtual, estado };
            } catch (err) {
                return {
                    confirmado: false,
                    reason: err && err.message ? err.message : String(err),
                    url: ''
                };
            }
        }

        async function navegarMlWebview(url) {
            await garantirExtensoesNavegadorMl();
            const webview = criarMlWebview();
            const viaShell = !!(webview && webview.__isShellBrowserProxy);
            if (viaShell) {
                return (async () => {
                    const timeoutShellMs = navegadorMlEmSegundoPlano() ? 18000 : 9000;
                    try {
                        await navegarMlWebviewUmaTentativa(webview, url, {
                            timeoutMs: timeoutShellMs,
                            aceitarDidNavigate: true,
                            exigirUrlAlvo: true,
                            timeoutMessage: 'Tempo limite ao abrir a pagina no quadro interno.'
                        });
                        return;
                    } catch (primeiroErro) {
                        if (mlFavoritosEmExecucao) {
                            setBrowserStatus('Mercado Livre demorou para confirmar a pesquisa no quadro. Seguindo para a coleta sem reenviar a navegacao.');
                            webview.currentUrl = webview.currentUrl || url;
                            await esperar(700);
                            return;
                        }
                        setBrowserStatus('Mercado Livre demorou para abrir no quadro. Tentando novamente...');
                        await esperar(220);
                        agendarAtualizacaoPosicaoNavegadorMlShell();
                        try {
                            await navegarMlWebviewUmaTentativa(webview, url, {
                                timeoutMs: timeoutShellMs,
                                aceitarDidNavigate: true,
                                exigirUrlAlvo: true,
                                timeoutMessage: 'Tempo limite ao abrir a pagina no quadro interno.'
                            });
                        } catch (segundoErro) {
                            webview.currentUrl = url;
                            await esperar(700);
                            throw segundoErro;
                        }
                    }
                })();
            }
            return new Promise((resolve, reject) => {
                let done = false;
                const finish = (err) => {
                    if (done) return;
                    done = true;
                    clearTimeout(timer);
                    webview.removeEventListener('did-finish-load', onLoad);
                    webview.removeEventListener('did-fail-load', onFail);
                    if (err) reject(err);
                    else resolve();
                };
                const onLoad = () => finish();
                const onFail = (event) => finish(new Error((event && event.errorDescription) || 'Falha ao carregar página.'));
                const timer = setTimeout(() => finish(new Error('Tempo limite ao abrir a pagina no quadro interno.')), 25000);

                webview.addEventListener('did-finish-load', onLoad);
                webview.addEventListener('did-fail-load', onFail);
                webview.src = url;
            });
        }

        const ML_WEBVIEW_EXTRACT_SCRIPT = `
            (async function () {
                try {
                    var sleep = function (ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); };
                    var currentUrl = String(window.location.href || '');
                    var fastLinksOnly = !!window.__JK_ML_FAST_LINKS;
                    var queryAllDeep = function (selector, root) {
                        var found = [];
                        var visited = [];
                        var visit = function (base) {
                            if (!base || visited.indexOf(base) >= 0) return;
                            visited.push(base);
                            try {
                                if (base.querySelectorAll) {
                                    var nodes = Array.prototype.slice.call(base.querySelectorAll(selector));
                                    for (var n = 0; n < nodes.length; n += 1) {
                                        if (found.indexOf(nodes[n]) < 0) found.push(nodes[n]);
                                    }
                                    var all = Array.prototype.slice.call(base.querySelectorAll('*'));
                                    for (var i = 0; i < all.length; i += 1) {
                                        if (all[i] && all[i].shadowRoot) visit(all[i].shadowRoot);
                                    }
                                }
                            } catch (e) {}
                        };
                        visit(root || document);
                        return found;
                    };
                    var queryOneDeep = function (selector, root) {
                        var nodes = queryAllDeep(selector, root);
                        return nodes.length ? nodes[0] : null;
                    };
                    var normalizeSearchText = function (value) {
                        var text = String(value || '');
                        return text.normalize ? text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase() : text.toLowerCase();
                    };
                    var nodeSearchText = function (node) {
                        if (!node) return '';
                        var parts = [];
                        try {
                            parts.push(node.innerText || '');
                            parts.push(node.textContent || '');
                            if (node.value) parts.push(node.value);
                            if (node.getAttribute) {
                                parts.push(node.getAttribute('aria-label') || '');
                                parts.push(node.getAttribute('title') || '');
                                parts.push(node.getAttribute('placeholder') || '');
                                parts.push(node.getAttribute('class') || '');
                                parts.push(node.getAttribute('id') || '');
                            }
                        } catch (e) {}
                        return parts.join(' ');
                    };
                    var bodyText = (function () {
                        var parts = [String(document.body && (document.body.innerText || document.body.textContent) || '')];
                        var seen = Object.create(null);
                        try {
                            var nodes = queryAllDeep('*').slice(0, 1800);
                            for (var i = 0; i < nodes.length; i += 1) {
                                var text = nodeSearchText(nodes[i]).replace(/\\s+/g, ' ').trim();
                                if (!text || seen[text]) continue;
                                seen[text] = true;
                                parts.push(text);
                            }
                        } catch (e) {}
                        return parts.join(' ').replace(/\\s+/g, ' ').trim();
                    })();
                    var lowerText = bodyText.toLowerCase();
                    var needsLogin =
                        currentUrl.indexOf('/gz/account-verification') >= 0 ||
                        currentUrl.indexOf('/jms/mlb/lgz/login') >= 0 ||
                        lowerText.indexOf('para continuar, acesse sua conta') >= 0;
                    var plainText = normalizeSearchText(bodyText);
                    var noResults =
                        plainText.indexOf('nao encontramos resultados') >= 0 ||
                        plainText.indexOf('nao ha resultados') >= 0 ||
                        plainText.indexOf('sem resultados') >= 0 ||
                        plainText.indexOf('verifique a ortografia') >= 0;
                    var hasRealAvantData =
                        /vendas?\\s+do\\s+(?:produto|anuncio|item)|vendas?\\s+estimad|ritmo\\s+atual|visitas\\s+do\\s+anuncio|participacao\\b|faturamento\\s+do\\s+produto|nome\\s+do\\s+vendedor|anuncio\\s+(?:ganhador\\s+)?criado\\s+em|comissao\\b|localizacao\\s+do\\s+vendedor/.test(plainText) ||
                        !!queryOneDeep('.avantpro-product-info-row, .created-time-card');
                    var cardSelectorsParaLoginAvant = [
                        'li.ui-search-layout__item',
                        'div.ui-search-result__wrapper',
                        'div.ui-search-result',
                        'div.poly-card',
                        'section.poly-card',
                        'article.poly-card',
                        'article.ui-search-result',
                        '[data-testid="product-card"]',
                        '[data-testid="item-card"]',
                        '[class*="product-card"]',
                        '[class*="andes-card"]',
                        '[class*="poly-card"]',
                        '[class*="ui-search-result"]',
                        '[class*="ui-search-layout__item"]',
                        '[class*="shops__layout-item"]',
                        '[data-testid*="item"]',
                        '[data-testid*="card"]',
                        '[data-testid*="result"]'
                    ].join(',');
                    var cardLoginPanelsAvant = queryAllDeep(cardSelectorsParaLoginAvant).filter(function (card) {
                        var cardBusca = normalizeSearchText(card && (card.innerText || card.textContent) || '');
                        return /avant\\s*pro|avantpro|avantprocloud/.test(cardBusca)
                            && /vincular\\s+(?:conta|agora)|conectar\\s+conta|fazer\\s+login|entrar\\s+no\\s+avant|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos/.test(cardBusca);
                    }).length;
                    var needsAvantLoginGlobal =
                        /avant\\s*pro|avantpro|avantprocloud/.test(plainText) &&
                        /vincule\\s+o\\s+avantpro|vincular\\s+agora|vincular\\s+conta|comece\\s+a\\s+usar|entre\\s+na\\s+sua\\s+conta|liberar\\s+os\\s+recursos|iniciar\\s+sessao|insira\\s+suas\\s+credenciais|seu\\s+e-?mail|seu\\s+email|chame\\s+o\\s+suporte/.test(plainText) &&
                        !hasRealAvantData;
                    var needsAvantLoginCards = cardLoginPanelsAvant > 0 && !hasRealAvantData;
                    var needsAvantLogin = needsAvantLoginGlobal || needsAvantLoginCards;

                    var selectors = [
                        'li.ui-search-layout__item',
                        'div.ui-search-result__wrapper',
                        'div.ui-search-result',
                        'div.poly-card',
                        'section.poly-card',
                        'article.poly-card',
                        'article.ui-search-result',
                        '[data-testid="product-card"]',
                        '[data-testid="item-card"]',
                        '[data-testid*="product"]',
                        '[class*="product-card"]',
                        '[class*="andes-card"]',
                        '[class*="poly-card"]',
                        '[class*="ui-search-result"]',
                        '[class*="ui-search-layout__item"]',
                        '[class*="ui-search-layout"] > li',
                        '[class*="shops__layout-item"]',
                        '[class*="item__info"]',
                        '[class*="ui-search-gallery"]',
                        '[data-testid*="item"]',
                        '[data-testid*="card"]',
                        '[data-testid*="result"]',
                        'main ol > li',
                        'main ul > li'
                    ].join(',');

                    var hasProductSignal = function () {
                        if (queryOneDeep(selectors)) return true;
                        if (queryOneDeep('a[href*="MLB"], a[href*="/p/MLB"], a[href*="wid=MLB"], a[href*="item_id"]')) return true;
                        return false;
                    };

                    var maxRounds = fastLinksOnly ? 24 : 30;
                    for (var round = 0; round < maxRounds; round += 1) {
                        if (hasProductSignal()) break;
                        await sleep(250);
                    }
                    if (noResults
                        && !document.querySelectorAll('.ui-search-loading-screen, [class*="ui-search-loading-screen"], [class*="loading-screen"], .andes-progress-indicator-circular').length
                        && !hasProductSignal()) {
                        return {
                            success: true,
                            currentUrl: currentUrl,
                            needsLogin: needsLogin,
                            needsAvantLogin: needsAvantLogin,
                            hasAvantData: hasRealAvantData,
                            noResults: true,
                            total: 0,
                            anuncios: [],
                            debug: {
                                linkCount: document.links ? document.links.length : 0,
                                cardCount: 0,
                                title: document.title || '',
                                fastLinksOnly: fastLinksOnly
                            }
                        };
                    }

                    var isValidItemId = function (itemId) {
                        itemId = String(itemId || '').trim().toUpperCase().replace('-', '');
                        var match = itemId.match(/^MLB(\\d+)$/);
                        return !!(match && match[1] && match[1].length >= 8);
                    };

                    var isProductUrl = function (href) {
                        if (!href) return false;
                        href = cleanUrl(String(href));
                        var idMatch = href.match(/\\bMLB-?(\\d{6,})\\b/i);
                        if (idMatch && !isValidItemId('MLB' + idMatch[1])) return false;
                        var normalizedHref = href.toLowerCase();
                        if (normalizedHref.indexOf('mercadolivre.com.br') < 0 && normalizedHref.indexOf('/mlb') !== 0 && normalizedHref.indexOf('/p/mlb') !== 0 && normalizedHref.indexOf('/up/mlb') !== 0) return false;
                        if (/\\/(?:ajuda|ofertas|cupons|categorias|supermercado|moda|mercado-play|vender|contato|compras|favoritos|gz|jms|login|registration|cart|publicidade|navigation|perfil|stores?|loja)\\b/i.test(normalizedHref)) return false;
                        var hasExplicitItemSignal = (
                            href.indexOf('/MLB-') >= 0 ||
                            href.indexOf('/p/MLB') >= 0 ||
                            href.indexOf('/up/MLB') >= 0 ||
                            /\\/up\\/MLBU/i.test(href) ||
                            href.indexOf('pdp_filters=item_id%3AMLB') >= 0 ||
                            href.indexOf('pdp_filters=item_id:MLB') >= 0 ||
                            href.indexOf('wid=MLB') >= 0 ||
                            /\\bMLB-?\\d{6,}\\b/i.test(href)
                        );
                        if (/https?:\\/\\/lista\\.mercadolivre\\.com\\.br\\//i.test(href)) return hasExplicitItemSignal;
                        return hasExplicitItemSignal;
                    };

                    var cleanUrl = function (href) {
                        if (!href) return '';
                        href = String(href).split('#')[0].trim();
                        if (/^\\/\\//.test(href)) return 'https:' + href;
                        if (/^\\//.test(href)) return 'https://www.mercadolivre.com.br' + href;
                        return href;
                    };

                    var urlFromItemId = function (itemId) {
                        itemId = String(itemId || '').trim().toUpperCase().replace('-', '');
                        if (!isValidItemId(itemId)) return '';
                        return 'https://produto.mercadolivre.com.br/' + itemId.replace('MLB', 'MLB-');
                    };
                    var safeDecode = function (value) {
                        var text = String(value || '');
                        try {
                            return decodeURIComponent(text);
                        } catch (e) {
                            try { return decodeURI(text); } catch (e2) { return text; }
                        }
                    };

                    var extractItemId = function (href) {
                        if (!href) return '';
                        href = safeDecode(href);
                        var patterns = [
                            /[?&]wid=(MLB\\d+)/i,
                            /[?&]item_id=(MLB\\d+)/i,
                            /item_id:?(MLB\\d+)/i,
                            /item_id%3A(MLB\\d+)/i,
                            /\\/(MLB-?\\d+)/i,
                            /\\b(MLB-?\\d{6,})\\b/i
                        ];
                        for (var p = 0; p < patterns.length; p += 1) {
                            var match = href.match(patterns[p]);
                            if (match && match[1]) {
                                var candidate = match[1].replace('-', '').toUpperCase();
                                if (isValidItemId(candidate)) return candidate;
                            }
                        }
                        return '';
                    };
                    var extractItemIdFromNode = function (node, fallbackHref) {
                        var sources = [fallbackHref || ''];
                        try {
                            if (node) {
                                sources.push(node.getAttribute('data-item-id') || '');
                                sources.push(node.getAttribute('data-id') || '');
                                sources.push(node.getAttribute('id') || '');
                                sources.push(node.innerHTML || '');
                                var descendants = Array.prototype.slice.call(node.querySelectorAll('[id], [data-item-id], [data-id], [href]'));
                                for (var d = 0; d < descendants.length && d < 80; d += 1) {
                                    sources.push(descendants[d].getAttribute('data-item-id') || '');
                                    sources.push(descendants[d].getAttribute('data-id') || '');
                                    sources.push(descendants[d].getAttribute('id') || '');
                                    sources.push(descendants[d].getAttribute('href') || '');
                                }
                            }
                        } catch (e) {}
                        for (var s = 0; s < sources.length; s += 1) {
                            var id = extractItemId(sources[s]);
                            if (id) return id;
                        }
                        return '';
                    };
                    var findProductHrefInNode = function (node) {
                        if (!node) return '';
                        var sources = [];
                        try {
                            sources.push(node.href || '');
                            sources.push(node.getAttribute && node.getAttribute('href') || '');
                            sources.push(node.getAttribute && node.getAttribute('data-href') || '');
                            sources.push(node.getAttribute && node.getAttribute('data-url') || '');
                            sources.push(node.getAttribute && node.getAttribute('data-permalink') || '');
                            sources.push(node.getAttribute && node.getAttribute('data-item-id') || '');
                            sources.push(node.getAttribute && node.getAttribute('data-id') || '');
                            sources.push(node.getAttribute && node.getAttribute('id') || '');
                            var descendants = Array.prototype.slice.call(node.querySelectorAll('a[href], [href], [data-href], [data-url], [data-permalink], [data-item-id], [data-id], [id]'));
                            for (var d = 0; d < descendants.length && d < 80; d += 1) {
                                var child = descendants[d];
                                sources.push(child.href || '');
                                sources.push(child.getAttribute('href') || '');
                                sources.push(child.getAttribute('data-href') || '');
                                sources.push(child.getAttribute('data-url') || '');
                                sources.push(child.getAttribute('data-permalink') || '');
                                sources.push(child.getAttribute('data-item-id') || '');
                                sources.push(child.getAttribute('data-id') || '');
                                sources.push(child.getAttribute('id') || '');
                            }
                        } catch (e) {}
                        var idFound = '';
                        for (var s = 0; s < sources.length; s += 1) {
                            var value = String(sources[s] || '');
                            if (!idFound) idFound = extractItemId(value);
                            if (isProductUrl(value)) return cleanUrl(value);
                        }
                        try {
                            var html = String(node.outerHTML || '').slice(0, 16000)
                                .replace(/\\u002F/g, '/')
                                .replace(/\\\//g, '/')
                                .replace(/&amp;/g, '&');
                            var hrefMatch = html.match(/https?:\\/\\/(?:www\\.)?mercadolivre\\.com\\.br\\/[^"' <>\\s]*?(?:MLB-?\\d{6,}|\\/p\\/MLB\\d+|wid=MLB\\d+|item_id%3AMLB\\d+|item_id:MLB\\d+)[^"' <>\\s]*/i);
                            if (hrefMatch && hrefMatch[0]) return cleanUrl(hrefMatch[0]);
                            if (!idFound) idFound = extractItemId(html);
                        } catch (e) {}
                        return idFound ? urlFromItemId(idFound) : '';
                    };

                    var titleFrom = function (node) {
                        if (!node) return '';
                        var cleanTitle = function (value) {
                            return String(value || '').replace(/\\s+/g, ' ').trim();
                        };
                        var badTitle = function (value) {
                            var text = cleanTitle(value);
                            if (!text) return true;
                            var normalized = text
                                .normalize('NFD')
                                .replace(/[\\u0300-\\u036f]/g, '')
                                .toLowerCase()
                                .replace(/\\s+/g, ' ')
                                .trim();
                            if (/^(novo|usado|resultados|patrocinado|mais vendido|loja oficial)$/i.test(text)) return true;
                            if (/^(r\\$|frete|chegar|vendid[oa]s?|mercadolider|mercado lider|op[cç][oõ]es de compra|produto relacionado)/i.test(normalized)) return true;
                            if (/^(pecas de|lubrificantes|acessorios|categorias|condicao|tipo de envio|custo de envio|tempo de entrega)/i.test(normalized)) return true;
                            var words = text.split(/\\s+/).filter(Boolean);
                            var hasDigit = /\\d/.test(text);
                            var hasLower = /[a-záéíóúâêôãõç]/.test(text);
                            if (!hasDigit && !hasLower && words.length <= 3) return true;
                            return text.length < 10;
                        };
                        var candidates = [];
                        if (node.querySelectorAll) {
                            var selectors = [
                                'a.poly-component__title',
                                '.poly-component__title',
                                'h2.poly-component__title-wrapper a',
                                'h3.poly-component__title-wrapper a',
                                'a.ui-search-link',
                                '.ui-search-item__title',
                                '[class*="ui-search-item__title"]',
                                'a[href*="/MLB-"][title]',
                                'a[href*="/p/MLB"][title]',
                                'a[href*="wid=MLB"][title]'
                            ];
                            selectors.forEach(function (selector) {
                                Array.prototype.slice.call(node.querySelectorAll(selector)).forEach(function (el) {
                                    candidates.push(el.textContent || '');
                                    candidates.push(el.getAttribute && el.getAttribute('title') || '');
                                    candidates.push(el.getAttribute && el.getAttribute('aria-label') || '');
                                });
                            });
                        }
                        if (node.textContent) {
                            candidates = candidates.concat(String(node.textContent).split('\\n'));
                        }
                        for (var i = 0; i < candidates.length; i += 1) {
                            var txt = cleanTitle(candidates[i]);
                            if (!badTitle(txt)) return txt.slice(0, 240);
                        }
                        return '';
                    };
                    var normalizeListingTitle = function (value) {
                        return String(value || '')
                            .normalize('NFD')
                            .replace(/[\\u0300-\\u036f]/g, '')
                            .toLowerCase()
                            .replace(/\\s+/g, ' ')
                            .trim();
                    };
                    var shouldSkipListingCandidate = function (node, title) {
                        var normalized = normalizeListingTitle(title);
                        if (!normalized) return true;
                        if (normalized === 'resultados') return true;
                        var categoryTitles = {
                            'pecas de motos e quadriciclos': true,
                            'lubrificantes e fluidos': true,
                            'pecas de linha pesada': true,
                            'pecas de carros e caminhonetes': true,
                            'acessorios de motos e quadriciclos': true,
                            'categorias': true,
                            'condicao': true,
                            'tipo de envio': true,
                            'custo de envio': true,
                            'tempo de entrega': true
                        };
                        if (!categoryTitles[normalized]) return false;
                        var text = String(node && (node.innerText || node.textContent) ? (node.innerText || node.textContent) : '');
                        return !/(R\\$|vendid[oa]s?|frete\\s+gr[aá]tis|avantpro|carregar\\s+dados)/i.test(text);
                    };
                    var parseHumanNumber = function (value, suffix) {
                        if (value === null || value === undefined) return null;
                        var raw = String(value).trim().toLowerCase();
                        if (!raw) return null;
                        var normalized = raw.replace(/\\s+/g, '');
                        if (normalized.indexOf('.') >= 0 && normalized.indexOf(',') >= 0) {
                            normalized = normalized.lastIndexOf('.') > normalized.lastIndexOf(',')
                                ? normalized.replace(/,/g, '')
                                : normalized.replace(/\\./g, '').replace(/,/g, '.');
                        } else if (normalized.indexOf(',') >= 0) {
                            normalized = /^\\d{1,3}(?:,\\d{3})+$/.test(normalized)
                                ? normalized.replace(/,/g, '')
                                : normalized.replace(/,/g, '.');
                        } else if (normalized.indexOf('.') >= 0) {
                            normalized = /^\\d{1,3}(?:\\.\\d{3})+$/.test(normalized)
                                ? normalized.replace(/\\./g, '')
                                : normalized;
                        }
                        var parsed = parseFloat(normalized);
                        if (!isFinite(parsed)) return null;
                        suffix = String(suffix || '').toLowerCase();
                        if (suffix === 'mil' || suffix === 'k') parsed *= 1000;
                        return Math.round(parsed);
                    };
                    var looksLikeAvantText = function (text) {
                        return /an[uú]ncio\\s+(?:ganhador\\s+)?criado\\s+em|cat[aá]logo\\s+criado\\s+em|nome\\s+do\\s+vendedor|vendid[oa]s?|\\bvendas\\b|faturamento\\s+do\\s+produto|reputa[cç][aã]o\\s+do\\s+vendedor/i.test(String(text || ''));
                    };
                    var normalizeAvantSearchText = function (value) {
                        return String(value || '').normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase();
                    };
                    looksLikeAvantText = function (text) {
                        var value = normalizeAvantSearchText(text);
                        return /anuncio\\s+(?:ganhador\\s+)?criado\\s+em|catalogo\\s+criado\\s+em|nome\\s+do\\s+vendedor|vendas?\\s+do\\s+(?:produto|catalogo|anuncio)|vendas?\\s+estimad|ritmo\\s+atual|visitas\\s+do\\s+anuncio|participacao\\b|faturamento\\s+do\\s+produto|reputacao\\s+do\\s+vendedor/i.test(value);
                    };
                    var getNearbyInfoNodes = function (node) {
                        var found = [];
                        try {
                            var rect = node.getBoundingClientRect();
                            var selectorsInfo = [
                                '[class*="avant"]',
                                '[id*="avant"]',
                                '[class*="Avant"]',
                                '[id*="Avant"]',
                                '[data-testid*="avant"]',
                                '[class*="product-info"]',
                                '[class*="info-row"]',
                                '[class*="metric"]',
                                '[class*="detail"]',
                                '.created-time-card',
                                '.avantpro-product-info-row',
                                '.avantpro-product-info-row *',
                                'section',
                                'article',
                                'li',
                                'div',
                                'span',
                                'p'
                            ].join(',');
                            var candidates = queryAllDeep(selectorsInfo).slice(0, 5000);
                            var seenNodes = [];
                            for (var nb = 0; nb < candidates.length; nb += 1) {
                                var candidate = candidates[nb];
                                if (!candidate || candidate === node || node.contains(candidate)) continue;
                                var text = String(candidate.innerText || candidate.textContent || '').replace(/\\s+/g, ' ').trim();
                                if (!text || text.length > 1400 || !looksLikeAvantText(text)) continue;
                                var nr = candidate.getBoundingClientRect();
                                if (!nr.width || !nr.height) continue;
                                var overlapX = Math.max(0, Math.min(rect.right, nr.right) - Math.max(rect.left, nr.left));
                                var cardCenterY = (rect.top + rect.bottom) / 2;
                                var nodeCenterY = (nr.top + nr.bottom) / 2;
                                var nearY = Math.abs(nodeCenterY - cardCenterY) < Math.max(560, rect.height * 1.25);
                                var sameColumn = overlapX > Math.max(20, Math.min(rect.width, nr.width) * 0.12);
                                var cardContainsOverlay = nr.left >= rect.left - 45 && nr.right <= rect.right + 45 && nr.top >= rect.top - 100 && nr.top <= rect.bottom + 460;
                                if ((sameColumn && nearY) || cardContainsOverlay) {
                                    var duplicate = seenNodes.some(function (existing) { return existing.contains(candidate); });
                                    if (duplicate) continue;
                                    seenNodes.push(candidate);
                                    found.push(text);
                                }
                            }
                        } catch (e) {}
                        return found;
                    };
                    var textWithNearbyAvant = function (node) {
                        var parts = [String(node && node.innerText ? node.innerText : '')];
                        try {
                            parts = parts.concat(getNearbyInfoNodes(node));
                        } catch (e) {}
                        return parts.join(' ').replace(/\\s+/g, ' ').trim();
                    };
                    var normalizeAvantLabel = function (value) {
                        return String(value || '')
                            .normalize('NFD')
                            .replace(/[\\u0300-\\u036f]/g, '')
                            .toLowerCase()
                            .replace(/\\s+/g, ' ')
                            .trim();
                    };
                    var canonicalAvantLabel = function (value) {
                        return normalizeAvantLabel(value)
                            .replace(/[^a-z0-9]+/g, ' ')
                            .replace(/\\bvendas?\\b/g, 'venda')
                            .replace(/\\banuncios?\\b/g, 'anuncio')
                            .replace(/\\bitens?\\b/g, 'item')
                            .replace(/\\s+/g, ' ')
                            .trim();
                    };
                    var avantLabelMatches = function (label, wantedLabels) {
                        var labelText = normalizeAvantLabel(label).replace(/[:=\\-]+$/g, '').trim();
                        var labelKey = canonicalAvantLabel(labelText);
                        return wantedLabels.some(function (wanted) {
                            var wantedText = normalizeAvantLabel(wanted).replace(/[:=\\-]+$/g, '').trim();
                            var wantedKey = canonicalAvantLabel(wantedText);
                            return labelText === wantedText
                                || labelKey === wantedKey
                                || labelText.indexOf(wantedText + ' ') === 0
                                || labelKey.indexOf(wantedKey + ' ') === 0;
                        });
                    };
                    var isNearAvantCard = function (card, row) {
                        try {
                            var rect = card.getBoundingClientRect();
                            var rr = row.getBoundingClientRect();
                            if (!rr.width || !rr.height) return false;
                            var overlapX = Math.max(0, Math.min(rect.right, rr.right) - Math.max(rect.left, rr.left));
                            var cardCenterY = (rect.top + rect.bottom) / 2;
                            var rowCenterY = (rr.top + rr.bottom) / 2;
                            var nearY = Math.abs(rowCenterY - cardCenterY) < Math.max(420, rect.height * 1.15);
                            var sameColumn = overlapX > Math.max(20, Math.min(rect.width, rr.width) * 0.12);
                            var insideOverlay = rr.left >= rect.left - 60 && rr.right <= rect.right + 60 && rr.top >= rect.top - 80 && rr.top <= rect.bottom + 460;
                            return (sameColumn && nearY) || insideOverlay;
                        } catch (e) {
                            return false;
                        }
                    };
                    var extractValueFromTextByLabels = function (text, labels) {
                        var original = String(text || '').replace(/\\s+/g, ' ').trim();
                        if (!original) return '';
                        var normalized = normalizeAvantLabel(original);
                        for (var i = 0; i < labels.length; i += 1) {
                            var labelOriginal = String(labels[i] || '').replace(/\\s+/g, ' ').trim();
                            var label = normalizeAvantLabel(labelOriginal).replace(/[:=\\-]+$/g, '').trim();
                            if (!label) continue;
                            var idx = normalized.indexOf(label);
                            if (idx < 0) continue;
                            var afterOriginal = original.slice(Math.min(original.length, idx + labelOriginal.length));
                            afterOriginal = afterOriginal.replace(/^[\\s:=\\-]+/, '').trim();
                            if (!afterOriginal) continue;
                            var stop = afterOriginal.search(/\\b(?:marca|vendas?\\s+do|vendas?\\s+estimad|participa[cç][aã]o|ritmo\\s+atual|visitas\\s+do|nome\\s+do\\s+vendedor|localiza[cç][aã]o|comiss[aã]o|reputa[cç][aã]o|an[uú]ncio\\s+criado)\\b/i);
                            if (stop > 0) afterOriginal = afterOriginal.slice(0, stop).trim();
                            return afterOriginal.slice(0, 160).trim();
                        }
                        return '';
                    };
                    var avantInfoRowSelectors = [
                        '.avantpro-product-info-row',
                        '[class*="avant"][class*="row"]',
                        '[class*="Avant"][class*="row"]',
                        '[class*="product-info"]',
                        '[class*="info-row"]',
                        '[class*="metric"]',
                        '[class*="detail"]',
                        '[data-testid*="avant"]',
                        'li',
                        'div'
                    ].join(',');
                    var extractAvantRowValue = function (card, labels) {
                        var rows = queryAllDeep(avantInfoRowSelectors).slice(0, 5000);
                        for (var r = 0; r < rows.length; r += 1) {
                            var row = rows[r];
                            if (!isNearAvantCard(card, row)) continue;
                            var labelNode = queryOneDeep('.avantpro-product-info-row-label', row) || row.querySelector('.avantpro-product-info-row-label');
                            var valueNode = queryOneDeep('.avantpro-product-info-row-value', row) || row.querySelector('.avantpro-product-info-row-value');
                            var label = normalizeAvantLabel(labelNode && labelNode.textContent);
                            if (label && avantLabelMatches(label, labels) && valueNode) {
                                return String(valueNode.textContent || '').replace(/\\s+/g, ' ').trim();
                            }
                            var rowText = String(row && (row.innerText || row.textContent) || '').replace(/\\s+/g, ' ').trim();
                            if (!looksLikeAvantText(rowText)) continue;
                            var parsed = extractValueFromTextByLabels(rowText, labels);
                            if (parsed) return parsed;
                        }
                        return '';
                    };
                    var extractAvantRowInfo = function (card, labels) {
                        var rows = queryAllDeep(avantInfoRowSelectors).slice(0, 5000);
                        for (var r = 0; r < rows.length; r += 1) {
                            var row = rows[r];
                            if (!isNearAvantCard(card, row)) continue;
                            var labelNode = queryOneDeep('.avantpro-product-info-row-label', row) || row.querySelector('.avantpro-product-info-row-label');
                            var valueNode = queryOneDeep('.avantpro-product-info-row-value', row) || row.querySelector('.avantpro-product-info-row-value');
                            var label = normalizeAvantLabel(labelNode && labelNode.textContent);
                            if (label && avantLabelMatches(label, labels) && valueNode) {
                                return {
                                    label: label,
                                    value: String(valueNode.textContent || '').replace(/\\s+/g, ' ').trim()
                                };
                            }
                            var rowText = String(row && (row.innerText || row.textContent) || '').replace(/\\s+/g, ' ').trim();
                            if (!looksLikeAvantText(rowText)) continue;
                            var parsed = extractValueFromTextByLabels(rowText, labels);
                            if (parsed) {
                                return {
                                    label: normalizeAvantLabel(labels[0] || ''),
                                    value: parsed
                                };
                            }
                        }
                        return null;
                    };
                    var sanitizeAvantSeller = function (value) {
                        var seller = String(value || '').replace(/\\s+/g, ' ').trim();
                        seller = seller.replace(/^(?:nome\\s+do\\s+vendedor|vendedor|loja\\s+oficial|vendido\\s+por|atual\\s+ganhador|ganhador)\\s*[:\\-]?\\s*/i, '').trim();
                        if (!seller || seller.length > 120 || /^\\d+$/.test(seller)) return '';
                        if (/^(?:sim|nao|n[aã]o|nao\\s+informado|n[aã]o\\s+informado|carregando|indisponivel|indispon[ií]vel|assinantes?)$/i.test(seller)) return '';
                        return seller;
                    };
                    var extractSeller = function (node) {
                        var exactSeller = extractAvantRowValue(node, [
                            'Nome do vendedor',
                            'Vendedor',
                            'Vendedor do anuncio',
                            'Vendedor do anúncio',
                            'Nome do vendedor ganhador',
                            'Vendedor ganhador',
                            'Loja oficial'
                        ]);
                        return sanitizeAvantSeller(exactSeller);
                    };
                    var extractAvantDate = function (node) {
                        if (fastLinksOnly) return '';
                        var exactDate = extractAvantRowValue(node, ['Anúncio criado em', 'Anuncio criado em', 'Anúncio ganhador criado em', 'Anuncio ganhador criado em']);
                        if (exactDate) {
                            var exactBr = exactDate.match(/\\b\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}\\b/);
                            if (exactBr && exactBr[0]) return exactBr[0];
                            var exactIso = exactDate.match(/\\b20\\d{2}-\\d{2}-\\d{2}(?:T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:?\\d{2})?)?\\b/);
                            if (exactIso && exactIso[0]) return exactIso[0];
                        }
                        var text = textWithNearbyAvant(node);
                        var labels = [
                            'Anúncio criado em',
                            'Anuncio criado em',
                            'Anúncio ganhador criado em',
                            'Anuncio ganhador criado em',
                            'Catálogo criado em',
                            'Catalogo criado em',
                            'Criado em'
                        ];
                        for (var l = 0; l < labels.length; l += 1) {
                            var idx = text.toLowerCase().indexOf(labels[l].toLowerCase());
                            if (idx < 0) continue;
                            var trecho = text.slice(idx + labels[l].length, idx + labels[l].length + 120);
                            var br = trecho.match(/\\b\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}\\b/);
                            if (br && br[0]) return br[0];
                            var iso = trecho.match(/\\b20\\d{2}-\\d{2}-\\d{2}(?:T\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:?\\d{2})?)?\\b/);
                            if (iso && iso[0]) return iso[0];
                        }
                        return '';
                    };
                    var extractVendas = function (node) {
                        if (fastLinksOnly) return null;
                        var labelEhVendasAnuncio = function (value) {
                            var label = canonicalAvantLabel(value).replace(/[:=\\-]+$/g, '').trim();
                            return (
                                /^venda\\s+do\\s+(?:anuncio|item)(?:\\s+ganhador)?\\b/.test(label) ||
                                /^venda\\s+do\\s+produto\\b/.test(label) ||
                                /^venda\\s+estimad/.test(label) ||
                                /^venda\\s+deste\\s+anuncio\\b/.test(label) ||
                                /^venda\\s+do\\s+vendedor\\s+neste\\s+anuncio\\b/.test(label)
                            );
                        };
                        var parseValorVendas = function (value) {
                            var match = String(value || '').match(/(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i);
                            return match && match[1] ? parseHumanNumber(match[1], match[2]) : null;
                        };
                        var scoreLinhaVendas = function (row) {
                            try {
                                var rect = node.getBoundingClientRect();
                                var rr = row.getBoundingClientRect();
                                if (!rr.width || !rr.height) return null;
                                var overlapX = Math.max(0, Math.min(rect.right, rr.right) - Math.max(rect.left, rr.left));
                                var cardCenterY = (rect.top + rect.bottom) / 2;
                                var rowCenterY = (rr.top + rr.bottom) / 2;
                                var distanceY = Math.abs(rowCenterY - cardCenterY);
                                if (isNearAvantCard(node, row)) return distanceY - Math.min(overlapX, rect.width) * 0.05;
                                var alignedX = overlapX > Math.max(14, Math.min(rect.width, rr.width) * 0.08)
                                    || (rr.left <= rect.right + 140 && rr.right >= rect.left - 140);
                                var closeY = rr.top >= rect.top - 80 && rr.top <= rect.bottom + Math.max(420, rect.height * 1.5);
                                if (!alignedX || !closeY || distanceY > Math.max(520, rect.height * 1.9)) return null;
                                return 1000 + distanceY - Math.min(overlapX, rect.width) * 0.04;
                            } catch (e) {
                                return null;
                            }
                        };
                        var rows = queryAllDeep('.avantpro-product-info-row');
                        var melhor = null;
                        for (var r = 0; r < rows.length; r += 1) {
                            var row = rows[r];
                            var score = scoreLinhaVendas(row);
                            if (!Number.isFinite(score)) continue;
                            var labelNode = queryOneDeep('.avantpro-product-info-row-label', row) || row.querySelector('.avantpro-product-info-row-label');
                            var valueNode = queryOneDeep('.avantpro-product-info-row-value', row) || row.querySelector('.avantpro-product-info-row-value');
                            if (!valueNode || !labelEhVendasAnuncio(labelNode && labelNode.textContent)) continue;
                            var valor = parseValorVendas(valueNode.textContent);
                            if (Number.isFinite(valor) && (!melhor || score < melhor.score)) {
                                melhor = { valor: valor, score: score };
                            }
                        }
                        return melhor ? melhor.valor : null;
                    };
                    var extractVendasTextoSimples = function (node) {
                        if (fastLinksOnly) return null;
                        var text = textWithNearbyAvant(node)
                            .normalize('NFD')
                            .replace(/[\\u0300-\\u036f]/g, '')
                            .replace(/\\s+/g, ' ')
                            .trim();
                        try {
                            var rows = queryAllDeep('.avantpro-product-info-row, [class*="avantpro"], [class*="Avantpro"], [class*="product-info"]');
                            var extras = [];
                            for (var r = 0; r < rows.length && extras.length < 80; r += 1) {
                                var row = rows[r];
                                if (!isNearAvantCard(node, row)) continue;
                                var rowText = String(row && (row.innerText || row.textContent) || '')
                                    .normalize('NFD')
                                    .replace(/[\\u0300-\\u036f]/g, '')
                                    .replace(/\\s+/g, ' ')
                                    .trim();
                                if (rowText) extras.push(rowText);
                            }
                            if (extras.length) text = (text + ' ' + extras.join(' ')).replace(/\\s+/g, ' ').trim();
                        } catch (e) {}
                        var patterns = [
                            /vendas?\\s+do\\s+(?:produto|anuncio|item)(?:\\s+ganhador)?\\s*[:\\-]?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i,
                            /vendas?\\s+estimad[ao]s?\\s*[:\\-]?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i,
                            /ritmo\\s+atual(?:\\s*\\(vendas\\/mes\\))?\\s*[:\\-]?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i
                        ];
                        for (var p = 0; p < patterns.length; p += 1) {
                            var match = text.match(patterns[p]);
                            if (match && match[1]) {
                                var parsed = parseHumanNumber(match[1], match[2]);
                                if (Number.isFinite(parsed)) return parsed;
                            }
                        }
                        return null;
                    };
                    var extractImage = function (node) {
                        try {
                            var imgs = Array.prototype.slice.call(node.querySelectorAll('img'));
                            for (var imgIndex = 0; imgIndex < imgs.length; imgIndex += 1) {
                                var img = imgs[imgIndex];
                                var src = img.currentSrc
                                    || img.getAttribute('data-src')
                                    || img.getAttribute('data-original')
                                    || img.getAttribute('data-lazy')
                                    || img.getAttribute('src')
                                    || '';
                                src = String(src || '').trim();
                                if (!src || /^data:/i.test(src) || /sprite|logo|placeholder/i.test(src)) continue;
                                if (src.indexOf('//') === 0) return 'https:' + src;
                                if (/^https?:\\/\\//i.test(src)) return src;
                            }
                        } catch (e) {}
                        return '';
                    };
                    var parseMoneyValue = function (value) {
                        var raw = String(value || '').replace(/\\s+/g, ' ').trim();
                        if (!raw) return null;
                        var ariaReais = raw.match(/(\\d[\\d\\.]*)\\s*reais?(?:\\s*(?:e|,)?\\s*(\\d{1,2})\\s*centavos?)?/i);
                        if (ariaReais && ariaReais[1]) {
                            var reais = Number(String(ariaReais[1]).replace(/\\./g, ''));
                            var cents = ariaReais[2] ? Number(ariaReais[2]) : 0;
                            if (Number.isFinite(reais)) return reais + (Number.isFinite(cents) ? cents / 100 : 0);
                        }
                        var match = raw.replace(/R\\$\\s*/gi, '').replace(/\\s+/g, '').match(/\\d[\\d\\.,]*/);
                        if (!match) return null;
                        var normalized = match[0];
                        if (normalized.indexOf('.') >= 0 && normalized.indexOf(',') >= 0) {
                            normalized = normalized.lastIndexOf('.') > normalized.lastIndexOf(',')
                                ? normalized.replace(/,/g, '')
                                : normalized.replace(/\\./g, '').replace(/,/g, '.');
                        } else if (normalized.indexOf(',') >= 0) {
                            normalized = normalized.replace(/\\./g, '').replace(/,/g, '.');
                        }
                        var parsed = Number(normalized);
                        return Number.isFinite(parsed) ? parsed : null;
                    };
                    var readMoneyAmount = function (el) {
                        if (!el) return null;
                        var aria = el.getAttribute && (el.getAttribute('aria-label') || el.getAttribute('title'));
                        var ariaValue = parseMoneyValue(aria);
                        if (Number.isFinite(ariaValue)) return ariaValue;
                        var fraction = el.querySelector && el.querySelector('.andes-money-amount__fraction');
                        var cents = el.querySelector && el.querySelector('.andes-money-amount__cents, .andes-money-amount__cents-superscript');
                        if (fraction && String(fraction.textContent || '').trim()) {
                            var composed = String(fraction.textContent || '').trim();
                            if (cents && String(cents.textContent || '').trim()) composed += ',' + String(cents.textContent || '').trim();
                            var composedValue = parseMoneyValue(composed);
                            if (Number.isFinite(composedValue)) return composedValue;
                        }
                        return parseMoneyValue(el.textContent || '');
                    };
                    var extractPrice = function (node) {
                        var result = { preco: null, preco_original: null, preco_promocional: null };
                        try {
                            var amounts = Array.prototype.slice.call(node.querySelectorAll('.andes-money-amount, [class*="money-amount"], [class*="price-tag"]'));
                            for (var ai = 0; ai < amounts.length; ai += 1) {
                                var amountEl = amounts[ai];
                                var value = readMoneyAmount(amountEl);
                                if (!Number.isFinite(value)) continue;
                                var textContext = normalizeListingTitle(String((amountEl.className || '') + ' ' + (amountEl.closest && amountEl.closest('s, del, [class*="previous"], [class*="original"], [class*="old"], [class*="strike"]') ? ' previous' : '') + ' ' + (amountEl.parentElement && amountEl.parentElement.className || '')));
                                var isOriginal = /previous|original|old|strike|tachado|riscado/.test(textContext) || !!(amountEl.closest && amountEl.closest('s, del'));
                                if (isOriginal && result.preco_original === null) {
                                    result.preco_original = value;
                                } else if (!isOriginal && result.preco === null) {
                                    result.preco = value;
                                }
                            }
                            if (result.preco_original !== null && result.preco !== null && result.preco_original > result.preco) {
                                result.preco_promocional = result.preco;
                            }
                        } catch (e) {}
                        return result;
                    };
                    var extractParcelamentoSemJuros = function (node) {
                        try {
                            var text = String(node && (node.innerText || node.textContent) ? (node.innerText || node.textContent) : '')
                                .normalize('NFD')
                                .replace(/[\\u0300-\\u036f]/g, '')
                                .toLowerCase()
                                .replace(/\\s+/g, ' ')
                                .trim();
                            return /\\bsem\\s+juros\\b|\\b0\\s*%?\\s*de?\\s*juros\\b/.test(text);
                        } catch (e) {
                            return false;
                        }
                    };
                    var extractFull = function (node) {
                        try {
                            if (!node || !node.querySelectorAll) return false;
                            var attrs = Array.prototype.slice.call(node.querySelectorAll('[aria-label], [title], img[alt], [class], [data-testid], [data-full]'));
                            for (var fi = 0; fi < attrs.length; fi += 1) {
                                var el = attrs[fi];
                                var texto = String([
                                    el.getAttribute && el.getAttribute('aria-label'),
                                    el.getAttribute && el.getAttribute('title'),
                                    el.getAttribute && el.getAttribute('alt'),
                                    el.getAttribute && el.getAttribute('class'),
                                    el.getAttribute && el.getAttribute('data-testid'),
                                    el.getAttribute && el.getAttribute('data-full')
                                ].filter(Boolean).join(' '))
                                    .normalize('NFD')
                                    .replace(/[\\u0300-\\u036f]/g, '')
                                    .toLowerCase()
                                    .replace(/\\s+/g, ' ')
                                    .trim();
                                if (/\\bfull\\b|fulfillment/.test(texto)) return true;
                            }
                        } catch (e) {}
                        return false;
                    };

                    var out = [];
                    var seen = {};
                    var cardMaisProximo = function (anchor) {
                        if (!anchor || !anchor.closest) return anchor;
                        var atual = anchor;
                        for (var nivel = 0; atual && nivel < 10; nivel += 1) {
                            if (temSinalProdutoVisual(atual)) return atual;
                            atual = atual.parentElement;
                        }
                        var candidato = anchor.closest([
                            'li.ui-search-layout__item',
                            'div.ui-search-result__wrapper',
                            'div.ui-search-result',
                            'div.poly-card',
                            'section.poly-card',
                            'article.poly-card',
                            'article.ui-search-result',
                            '[data-testid="product-card"]',
                            '[data-testid="item-card"]',
                            '[class*="product-card"]',
                            '[class*="andes-card"]',
                            '[class*="poly-card"]',
                            '[class*="ui-search-result"]',
                            '[class*="shops__layout-item"]',
                            'main ol > li',
                            'main ul > li',
                            'li',
                            'article',
                            'section'
                        ].join(',')) || anchor;
                        if (temSinalProdutoVisual(candidato)) return candidato;
                        return anchor;
                    };
                    var cards = queryAllDeep(selectors);
                    queryAllDeep('a[href]').forEach(function (anchor) {
                        var hrefAnchor = cleanUrl(anchor.href || anchor.getAttribute('href') || '');
                        if (!isProductUrl(hrefAnchor)) return;
                        var cardAnchor = cardMaisProximo(anchor);
                        if (cardAnchor && cards.indexOf(cardAnchor) < 0) cards.push(cardAnchor);
                    });
                    for (var i = 0; i < cards.length; i += 1) {
                        var card = cards[i];
                        var href = findProductHrefInNode(card);
                        if (!href || seen[href]) continue;
                        var idCard = extractItemIdFromNode(card, href);
                        var titleCard = titleFrom(card);
                        if (shouldSkipListingCandidate(card, titleCard)) continue;
                        seen[href] = true;
                        var vendasCard = extractVendas(card);
                        if (vendasCard === null || vendasCard === undefined) vendasCard = extractVendasTextoSimples(card);
                        var vendedorCard = extractSeller(card);
                        var imagemCard = extractImage(card);
                        var precoCard = extractPrice(card);
                        var semJurosCard = extractParcelamentoSemJuros(card);
                        var fullCard = extractFull(card);
                        out.push({ posicao: out.length + 1, id: idCard || '', url: href, titulo: titleCard, imagem: imagemCard, thumbnail: imagemCard, preco: precoCard.preco, price: precoCard.preco, preco_original: precoCard.preco_original, original_price: precoCard.preco_original, preco_promocional: precoCard.preco_promocional, parcelamento_sem_juros: semJurosCard, tipo_anuncio: semJurosCard ? 'Premium' : 'Classico', is_full: fullCard ? true : '', full: fullCard ? true : '', vendedor: vendedorCard, vendedorFonte: vendedorCard ? 'avantpro_vendedor' : '', vendedor_fonte: vendedorCard ? 'avantpro_vendedor' : '', data_criacao: extractAvantDate(card), vendas: vendasCard, vendasFonte: vendasCard !== null && vendasCard !== undefined ? 'avantpro_anuncio' : '' });
                    }

                    if (!out.length) {
                        var links = queryAllDeep('a[href]');
                        for (var k = 0; k < links.length; k += 1) {
                            var linkHref = cleanUrl(links[k].href);
                            if (!isProductUrl(linkHref) || seen[linkHref]) continue;
                            var linkId = extractItemId(linkHref);
                            var linkTitle = (links[k].textContent || links[k].getAttribute('title') || '').trim().slice(0, 240);
                            if (shouldSkipListingCandidate(links[k], linkTitle)) continue;
                            seen[linkHref] = true;
                                var linkCard = links[k].closest && (links[k].closest('li, article, section, div.poly-card, div.ui-search-result') || links[k]) || links[k];
                                var precoLink = extractPrice(linkCard);
                                var semJurosLink = extractParcelamentoSemJuros(linkCard);
                                var fullLink = extractFull(linkCard);
                                var vendasLink = extractVendas(linkCard);
                                if (vendasLink === null || vendasLink === undefined) vendasLink = extractVendasTextoSimples(linkCard);
                                out.push({ posicao: out.length + 1, id: linkId || '', url: linkHref, titulo: linkTitle, imagem: extractImage(linkCard), preco: precoLink.preco, price: precoLink.preco, preco_original: precoLink.preco_original, original_price: precoLink.preco_original, preco_promocional: precoLink.preco_promocional, parcelamento_sem_juros: semJurosLink, tipo_anuncio: semJurosLink ? 'Premium' : 'Classico', is_full: fullLink ? true : '', full: fullLink ? true : '', vendas: vendasLink, vendasFonte: vendasLink !== null && vendasLink !== undefined ? 'avantpro_card' : '' });
                            if (out.length >= 100) break;
                        }
                    }

                    if (!out.length) {
                        var candidates = Array.prototype.slice.call(document.querySelectorAll(selectors + ', a[href*="MLB"], a[href*="/p/MLB"], a[href*="wid=MLB"], a[href*="item_id"], [data-item-id], [data-id*="MLB"]'));
                        for (var c = 0; c < candidates.length && out.length < 100; c += 1) {
                            var node = candidates[c];
                            var hrefFound = findProductHrefInNode(node);
                            if (!hrefFound || seen[hrefFound]) continue;
                            var cardNode = node.closest && (node.closest('li, article, section, div.poly-card, div.ui-search-result, div[class*="poly"], div[class*="search"]') || node);
                            var candidateId = extractItemId(hrefFound);
                            var candidateTitle = titleFrom(cardNode) || String(node.textContent || '').trim().slice(0, 240);
                            if (shouldSkipListingCandidate(cardNode, candidateTitle)) continue;
                            seen[hrefFound] = true;
                                var vendasCardNode = extractVendas(cardNode);
                                if (vendasCardNode === null || vendasCardNode === undefined) vendasCardNode = extractVendasTextoSimples(cardNode);
                            var vendedorCardNode = extractSeller(cardNode);
                            var imagemCardNode = extractImage(cardNode);
                            var precoCardNode = extractPrice(cardNode);
                            var semJurosCardNode = extractParcelamentoSemJuros(cardNode);
                            var fullCardNode = extractFull(cardNode);
                            out.push({
                                posicao: out.length + 1,
                                id: candidateId || '',
                                url: hrefFound,
                                titulo: candidateTitle,
                                imagem: imagemCardNode,
                                thumbnail: imagemCardNode,
                                preco: precoCardNode.preco,
                                price: precoCardNode.preco,
                                preco_original: precoCardNode.preco_original,
                                original_price: precoCardNode.preco_original,
                                preco_promocional: precoCardNode.preco_promocional,
                                parcelamento_sem_juros: semJurosCardNode,
                                tipo_anuncio: semJurosCardNode ? 'Premium' : 'Classico',
                                is_full: fullCardNode ? true : '',
                                full: fullCardNode ? true : '',
                                vendedor: vendedorCardNode,
                                vendedorFonte: vendedorCardNode ? 'avantpro_vendedor' : '',
                                vendedor_fonte: vendedorCardNode ? 'avantpro_vendedor' : '',
                                data_criacao: extractAvantDate(cardNode),
                                vendas: vendasCardNode,
                                vendasFonte: vendasCardNode !== null && vendasCardNode !== undefined ? 'avantpro_anuncio' : ''
                            });
                        }
                    }

                    if (!out.length) {
                        var html = String(document.documentElement && document.documentElement.outerHTML ? document.documentElement.outerHTML : '');
                        var decodeHtmlMl = function(rawValue) {
                            var text = String(rawValue || '');
                            for (var pass = 0; pass < 4; pass += 1) {
                                text = text
                                    .split('\\\\u002F').join('/')
                                    .split('\\\\u002f').join('/')
                                    .split('\\\\u003A').join(':')
                                    .split('\\\\u003a').join(':')
                                    .split('\\\\u003D').join('=')
                                    .split('\\\\u003d').join('=')
                                    .split('\\\\u0026').join('&')
                                    .split('\\\\u002D').join('-')
                                    .split('\\\\u002d').join('-')
                                    .split('\\\\u002E').join('.')
                                    .split('\\\\u002e').join('.')
                                    .split('\\\\/').join('/')
                                    .split('\\\\\\"').join('"')
                                    .split('&quot;').join('"')
                                    .split('&amp;').join('&')
                                    .split('\\\\&').join('&');
                                try {
                                    var decoded = decodeURIComponent(text);
                                    if (decoded === text) break;
                                    text = decoded;
                                } catch (_decodeErr) {
                                    break;
                                }
                            }
                            return text;
                        };
                        var decodedHtml = decodeHtmlMl(html);
                        var decodeMaybe = function(value) {
                            var text = decodeHtmlMl(value);
                            for (var pass = 0; pass < 3; pass += 1) {
                                try {
                                    var decoded = decodeURIComponent(text);
                                    if (decoded === text) break;
                                    text = decoded;
                                } catch (_err) {
                                    break;
                                }
                            }
                            return text;
                        };
                        var tituloFromUrl = function(urlValue) {
                            var text = String(urlValue || '');
                            var match = text.match(/\\/MLB-?\\d+-([^?#]+?)(?:-_JM|_JM|$)/i);
                            if (!match) return '';
                            return decodeMaybe(match[1])
                                .replace(/[-_]+/g, ' ')
                                .replace(/\\s+/g, ' ')
                                .trim();
                        };
                        var limparLinkPreload = function(urlValue, itemIdValue) {
                            var href = decodeMaybe(urlValue || '')
                                .replace(/\\+/g, '')
                                .replace(/&quot;/g, '"')
                                .replace(/"/g, '')
                                .trim();
                            var urldestMatch = href.match(/[?&]urldest=([^&#]+)/i);
                            if (urldestMatch && urldestMatch[1]) {
                                href = decodeMaybe(urldestMatch[1]);
                            }
                            href = href.split('#').shift();
                            href = href.replace(/[),.;]+$/g, '');
                            if (href && href.indexOf('produto.mercadolivre.com.br') === -1) {
                                var insideMatch = href.match(/(https?:\\/\\/produto\\.mercadolivre\\.com\\.br\\/[^"'<>\\\\\\s]+MLB-?\\d+[^"'<>\\\\\\s]*)/i);
                                if (insideMatch) href = insideMatch[1];
                            }
                            if (!isProductUrl(href) && itemIdValue) {
                                href = urlFromItemId(itemIdValue);
                            }
                            return href;
                        };
                        var htmlPreloadMax = 20;
                        var adicionarPreload = function(rawItemId, rawUrl, rawTitle, rawPrice, rawImage) {
                            var itemId = String(rawItemId || '').replace('-', '').toUpperCase();
                            if (!itemId || !/^MLB\\d{6,}$/.test(itemId)) return false;
                            var href = limparLinkPreload(rawUrl, itemId);
                            if (!href || !isProductUrl(href)) return false;
                            var hrefId = extractItemId(href);
                            if (hrefId && hrefId !== itemId) {
                                href = urlFromItemId(itemId);
                            }
                            var key = itemId || href;
                            if (seen['html:' + key]) return false;
                            seen['html:' + key] = true;
                            seen[href] = true;
                            var preco = rawPrice !== null && rawPrice !== undefined && rawPrice !== '' ? Number(rawPrice) : null;
                            if (!Number.isFinite(preco)) preco = null;
                            var imagem = rawImage ? decodeMaybe(rawImage) : '';
                            var titulo = decodeMaybe(rawTitle || '')
                                .replace(/<[^>]+>/g, ' ')
                                .replace(/\\s+/g, ' ')
                                .trim()
                                .slice(0, 240);
                            if (!titulo) titulo = tituloFromUrl(href);
                            out.push({
                                posicao: out.length + 1,
                                id: itemId,
                                url: href,
                                titulo: titulo,
                                imagem: imagem,
                                thumbnail: imagem,
                                preco: preco,
                                price: preco,
                                origem_dados: 'mercadolivre_html_preload'
                            });
                            return true;
                        };
                        var preloadIdRegex = /"id"\\s*:\\s*"(MLB\\d{6,})"/gi;
                        var preloadMatch = null;
                        while ((preloadMatch = preloadIdRegex.exec(decodedHtml)) && out.length < htmlPreloadMax) {
                            var start = Math.max(0, preloadMatch.index - 2600);
                            var end = Math.min(decodedHtml.length, preloadMatch.index + 4200);
                            var chunk = decodedHtml.slice(start, end);
                            var idPreload = preloadMatch[1];
                            var linkMatch = chunk.match(/"link"\\s*:\\s*"(https?:\\/\\/[^"]*?(?:MLB-?\\d+|item_id=MLB\\d+|item_id%3DMLB\\d+)[^"]*)"/i)
                                || chunk.match(/(https?:\\/\\/produto\\.mercadolivre\\.com\\.br\\/[^"'<>\\\\\\s]*MLB-?\\d+[^"'<>\\\\\\s]*)/i);
                            var urlPreload = linkMatch && linkMatch[1] ? linkMatch[1] : '';
                            if (!urlPreload) {
                                var urlDestMatch = chunk.match(/urldest=([^"'<>\\\\\\s&]+)/i);
                                if (urlDestMatch && urlDestMatch[1]) urlPreload = urlDestMatch[1];
                            }
                            var titleMatch = chunk.match(/"title"\\s*:\\s*"([^"]{3,220})"/i)
                                || chunk.match(/"name"\\s*:\\s*"([^"]{3,220})"/i);
                            var priceMatch = chunk.match(/"price"\\s*:\\s*\\{[^}]*"amount"\\s*:\\s*([0-9]+(?:\\.[0-9]+)?)/i)
                                || chunk.match(/"amount"\\s*:\\s*([0-9]+(?:\\.[0-9]+)?)/i);
                            var pictureMatch = chunk.match(/"picture"\\s*:\\s*"(https?:\\/\\/[^"]+)"/i)
                                || chunk.match(/"thumbnail"\\s*:\\s*"(https?:\\/\\/[^"]+)"/i);
                            adicionarPreload(
                                idPreload,
                                urlPreload,
                                titleMatch && titleMatch[1] ? decodeMaybe(titleMatch[1]) : '',
                                priceMatch && priceMatch[1] ? priceMatch[1] : null,
                                pictureMatch && pictureMatch[1] ? pictureMatch[1] : ''
                            );
                        }
                        var productUrlRegex = new RegExp("https?:\\\\/\\\\/(?:www\\\\.)?mercadolivre\\\\.com\\\\.br\\\\/(?:[^\\\"'<>\\\\s]*?(?:MLB-?\\\\d{6,}|\\\\/p\\\\/MLB\\\\d+|wid=MLB\\\\d+|item_id%3AMLB\\\\d+|item_id:MLB\\\\d+)[^\\\"'<>\\\\s]*)", "gi");
                        var matches = decodedHtml.match(productUrlRegex) || [];
                        for (var m = 0; m < matches.length; m += 1) {
                            var matchHref = limparLinkPreload(matches[m], '');
                            if (!isProductUrl(matchHref) || seen[matchHref]) continue;
                            var matchId = extractItemId(matchHref);
                            if (!matchId) continue;
                            seen[matchHref] = true;
                            adicionarPreload(matchId, matchHref, tituloFromUrl(matchHref), null, '');
                            if (out.length >= htmlPreloadMax) break;
                        }
                        if (!out.length) {
                            var idMatches = decodedHtml.match(/\\bMLB-?\\d{6,}\\b/gi) || [];
                            for (var im = 0; im < idMatches.length && out.length < htmlPreloadMax; im += 1) {
                                var htmlId = String(idMatches[im] || '').replace('-', '').toUpperCase();
                                var htmlUrl = urlFromItemId(htmlId);
                                if (!htmlUrl || seen[htmlUrl]) continue;
                                seen[htmlUrl] = true;
                                out.push({ posicao: out.length + 1, id: htmlId, url: htmlUrl, titulo: '' });
                            }
                        }
                    }

                    return {
                        success: true,
                        currentUrl: currentUrl,
                        needsLogin: needsLogin,
                        needsAvantLogin: needsAvantLogin,
                        hasAvantData: hasRealAvantData,
                        noResults: noResults && !out.length,
                        total: out.length,
                        anuncios: out,
                        debug: {
                            linkCount: document.links ? document.links.length : 0,
                            cardCount: cards.length,
                            title: document.title || '',
                            fastLinksOnly: fastLinksOnly
                        }
                    };
                } catch (err) {
                    return { success: false, total: 0, anuncios: [], error: err && (err.stack || err.message) ? String(err.stack || err.message) : String(err) };
                }
            })();
        `;

        function promiseComTimeout(promise, ms, mensagem) {
            let timer = null;
            const timeout = new Promise((_, reject) => {
                timer = setTimeout(() => reject(new Error(mensagem || 'Tempo limite excedido.')), ms);
            });
            return Promise.race([promise, timeout]).finally(() => {
                if (timer) clearTimeout(timer);
            });
        }

        function esperar(ms) {
            return new Promise(resolve => setTimeout(resolve, ms));
        }

        function usarNavegadorMlNoShellElectron() {
            if (window.FavoritosV2?.browser?.shellBridge?.usarNavegadorMlNoShellElectron?.()) return true;
            try {
                return !!(window.electronAPI && (
                    typeof window.electronAPI.startFavoritosWorkerBrowser === 'function'
                    || typeof window.electronAPI.showEmbeddedMlBrowser === 'function'
                ));
            } catch (_err) {
                return false;
            }
        }

        function obterBoundsNavegadorMl() {
            if (navegadorMlEmSegundoPlano()) {
                return {
                    left: 0,
                    top: 0,
                    width: 1280,
                    height: 900,
                    background: true
                };
            }
            const alvo = mlBrowserHost || mlBrowserFrameWrapEl;
            if (!alvo || typeof alvo.getBoundingClientRect !== 'function') return null;
            const rect = alvo.getBoundingClientRect();
            const limite = balaoResultadosMlAberto() && mlWorkModalDialogEl && typeof mlWorkModalDialogEl.getBoundingClientRect === 'function'
                ? mlWorkModalDialogEl.getBoundingClientRect()
                : null;
            const viewport = {
                left: 0,
                top: 0,
                right: window.innerWidth || rect.right,
                bottom: window.innerHeight || rect.bottom
            };
            const clip = limite
                ? {
                    left: Math.max(limite.left, viewport.left),
                    top: Math.max(limite.top, viewport.top),
                    right: Math.min(limite.right, viewport.right),
                    bottom: Math.min(limite.bottom, viewport.bottom)
                }
                : viewport;
            const left = Math.max(rect.left, clip.left);
            const top = Math.max(rect.top, clip.top);
            const right = Math.min(rect.right, clip.right);
            const bottom = Math.min(rect.bottom, clip.bottom);
            const width = Math.max(0, right - left);
            const height = Math.max(0, bottom - top);
            if (width < 20 || height < 20) return null;
            return {
                left,
                top,
                width,
                height
            };
        }

        const favoritosBrowserShellBridge = window.FavoritosV2?.browser?.shellBridge?.createBridge?.({
            getProxy: () => mlShellBrowserProxy,
            setProxy: (proxy) => {
                mlShellBrowserProxy = proxy;
                return proxy;
            },
            getHost: () => mlBrowserHost,
            getBounds: () => obterBoundsNavegadorMl(),
            getUrlInput: () => mlUrlInput,
            getDefaultUrl: () => ML_DEFAULT_URL,
            isBackground: () => navegadorMlEmSegundoPlano(),
            isBalloonOpen: () => balaoResultadosMlAberto(),
            areUrlsEquivalent: (atual, alvo) => urlsMercadoLivreEquivalentes(atual, alvo),
            getHideOnReturnOptions: () => {
                const ocultandoExecucao = !!(mlFavoritosEmExecucao && mlFavoritosExecucaoEmSegundoPlano);
                return {
                    descarregarConteudo: !ocultandoExecucao,
                    reason: ocultandoExecucao ? 'favoritos-hide-background' : 'favoritos-modal-close'
                };
            }
        }) || null;

        function enviarNavegadorMlParaShell(channel, payload = {}) {
            return favoritosBrowserShellBridge?.enviar(channel, payload);
        }

        function ocultarNavegadorMlShellDefinitivo(opcoes = {}) {
            return favoritosBrowserShellBridge?.ocultarDefinitivo(opcoes);
        }

        function atualizarPosicaoNavegadorMlShell() {
            return favoritosBrowserShellBridge?.atualizarPosicao();
        }

        function ocultarNavegadorMlShellTemporariamente() {
            return favoritosBrowserShellBridge?.ocultarTemporariamente();
        }

        function restaurarNavegadorMlShellSeVisivel() {
            return favoritosBrowserShellBridge?.restaurarSeVisivel();
        }

        function agendarAtualizacaoPosicaoNavegadorMlShell() {
            return favoritosBrowserShellBridge?.agendarAtualizacaoPosicao();
        }

        function criarProxyNavegadorMlShell() {
            return favoritosBrowserShellBridge?.criarProxy() || null;
        }

        function forcarProxyNavegadorFavoritosWorker(urlAtual = '') {
            window.__JK_FAVORITOS_WORKER_BROWSER_ACTIVE = true;
            const url = String(urlAtual || '').trim();
            if (mlWebviewEl && mlWebviewEl.__isShellBrowserProxy) {
                if (url) mlWebviewEl.currentUrl = url;
                return mlWebviewEl;
            }
            if (mlWebviewEl && !mlWebviewEl.__isShellBrowserProxy) {
                try {
                    if (mlWebviewEl.parentNode) mlWebviewEl.parentNode.removeChild(mlWebviewEl);
                } catch (_removeErr) {}
            }
            if (mlBrowserHost) {
                mlBrowserHost.innerHTML = `
                    <div class="browser-warning">
                        <strong>Favoritos rodando no navegador trabalhador.</strong>
                        <span>Use o botao Ver para acompanhar a coleta.</span>
                    </div>
                `;
            }
            mlWebviewEl = criarProxyNavegadorMlShell();
            if (mlWebviewEl && url) mlWebviewEl.currentUrl = url;
            aplicarScrollbarsDiscretasNoWebview(mlWebviewEl);
            return mlWebviewEl;
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
            if (!data || typeof data !== 'object') return;
            favoritosBrowserShellBridge?.handleMessage(data);
        });

        function reexibirNavegadorMlShellAoRetornar() {
            return favoritosBrowserShellBridge?.reexibirAoRetornar();
        }

        function forcarNavegadorMlShellVisivel() {
            return !!favoritosBrowserShellBridge?.forcarVisivel();
        }

        async function abrirHomeMercadoLivreParaLoginAvantPro(termo = '') {
            const contexto = termo ? ` para "${termo}"` : '';
            const urlHomeMl = typeof ML_DEFAULT_URL !== 'undefined'
                ? ML_DEFAULT_URL
                : 'https://www.mercadolivre.com.br/';
            if (mlUrlInput) mlUrlInput.value = urlHomeMl;
            mostrarBalaoFavoritosStatus(`Conectando Avant Pro${contexto}: abrindo Mercado Livre antes do login...`, {
                manterNavegadorVisivel: true,
                larga: true,
                titulo: 'Conectar Avant Pro'
            });
            if (typeof abrirMercadoLivreNoPrograma === 'function') {
                const abriu = await abrirMercadoLivreNoPrograma({
                    titulo: 'Conectar Avant Pro',
                    subtitulo: 'Mercado Livre',
                    browserCompleto: false,
                    forcarExibicao: true
                }).catch(() => false);
                await esperar(700);
                return !!abriu;
            }
            if (typeof navegarMlWebview === 'function') {
                await navegarMlWebview(urlHomeMl).catch(() => null);
                await esperar(700);
                return true;
            }
            return false;
        }



        function montarScriptAcionarControlesAvantPro(opcoes = {}) {
            const forceClick = !!opcoes.forceClick;
            const permitirFerramentas = opcoes.permitirFerramentas !== false;
            const somenteFerramentas = opcoes.somenteFerramentas === true;
            const clicarCardsSemDados = opcoes.clicarCardsSemDados !== false;
            const maxClicks = Math.max(1, Math.min(16, Number(opcoes.maxClicks) || 10));
            return `
                (async function () {
                    var clicked = 0;
                    var forceClick = ${forceClick ? 'true' : 'false'};
                    var permitirFerramentas = ${permitirFerramentas ? 'true' : 'false'};
                    var somenteFerramentas = ${somenteFerramentas ? 'true' : 'false'};
                    var clicarCardsSemDados = ${clicarCardsSemDados ? 'true' : 'false'};
                    var maxClicks = ${JSON.stringify(maxClicks)};
                    var lastClickAt = Number(window.__JK_AVANT_PRO_CLICKED_AT || 0);
                    if (!forceClick && lastClickAt && Date.now() - lastClickAt < 3500) return 0;
                    var sleep = function (ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); };
                    var normalizar = function (value) {
                        var text = String(value || '');
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (e) {}
                        return text.toLowerCase().replace(/\\s+/g, ' ').trim();
                    };
                    var isVisible = function (node) {
                        try {
                            var rect = node && node.getBoundingClientRect ? node.getBoundingClientRect() : null;
                            var style = node && window.getComputedStyle ? window.getComputedStyle(node) : null;
                            return !!(rect && rect.width > 0 && rect.height > 0 && (!style || (style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) !== 0)));
                        } catch (_err) {
                            return false;
                        }
                    };
                    var queryAllDeep = function (selector, root) {
                        var found = [];
                        var seen = [];
                        var walk = function (base) {
                            if (!base || seen.indexOf(base) >= 0) return;
                            seen.push(base);
                            try {
                                if (base.querySelectorAll) {
                                    found = found.concat(Array.prototype.slice.call(base.querySelectorAll(selector)));
                                }
                            } catch (_err) {}
                            var nodes = [];
                            try {
                                nodes = base.querySelectorAll ? Array.prototype.slice.call(base.querySelectorAll('*')) : [];
                            } catch (_err2) {}
                            for (var i = 0; i < nodes.length; i += 1) {
                                if (nodes[i] && nodes[i].shadowRoot) walk(nodes[i].shadowRoot);
                            }
                        };
                        walk(root || document);
                        return found.filter(function (node, index) { return found.indexOf(node) === index; });
                    };
                    var composedHost = function (node) {
                        try {
                            var root = node && node.getRootNode ? node.getRootNode() : null;
                            return root && root.host ? root.host : null;
                        } catch (_err) {
                            return null;
                        }
                    };
                    var textoNode = function (node) {
                        if (!node) return '';
                        return normalizar([
                            node.innerText,
                            node.textContent,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title'),
                            node.getAttribute && node.getAttribute('class'),
                            node.getAttribute && node.getAttribute('id')
                        ].filter(Boolean).join(' '));
                    };
                    var textoVisivelNode = function (node) {
                        if (!node) return '';
                        var text = [
                            node.innerText,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title')
                        ].filter(Boolean).join(' ');
                        if (!text && node.textContent) text = node.textContent;
                        return normalizar(text);
                    };
                    var contextoNode = function (node) {
                        if (!node) return '';
                        var root = null;
                        try {
                            root = node.closest && node.closest('form, article, section, aside, li, [role="dialog"], [class*="avant"], [id*="avant"], [class*="poly-card"], [class*="ui-search-result"], [class*="modal"], [class*="login"], [class*="auth"]');
                        } catch (_err) {}
                        var host = composedHost(node);
                        root = root || node.parentElement || node;
                        return normalizar([
                            textoNode(node),
                            root && (root.innerText || root.textContent),
                            root && root.getAttribute && root.getAttribute('class'),
                            root && root.getAttribute && root.getAttribute('id'),
                            host && textoNode(host),
                            host && (host.innerText || host.textContent)
                        ].filter(Boolean).join(' '));
                    };
                    var ehLinkProduto = function (node) {
                        var href = node && node.getAttribute && node.getAttribute('href');
                        if (!href) return false;
                        return /\\/MLB-?\\d{5,}|\\/p\\/MLB|wid=MLB|item_id=/i.test(String(href));
                    };
                    var hrefNode = function (node) {
                        if (!node || !node.getAttribute) return '';
                        return String(node.href || node.getAttribute('href') || node.getAttribute('data-href') || '').trim();
                    };
                    var textoAlvoClique = function (node) {
                        if (!node) return '';
                        return normalizar([
                            node.innerText,
                            node.textContent,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title'),
                            node.getAttribute && node.getAttribute('class'),
                            node.getAttribute && node.getAttribute('id'),
                            node.getAttribute && node.getAttribute('data-testid'),
                            node.getAttribute && node.getAttribute('download'),
                            hrefNode(node)
                        ].filter(Boolean).join(' '));
                    };
                    var textoExplicitoClique = function (node) {
                        if (!node) return '';
                        return normalizar([
                            node.innerText,
                            node.textContent,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title'),
                            node.getAttribute && node.getAttribute('data-testid')
                        ].filter(Boolean).join(' '));
                    };
                    var ehAncoraNavegavel = function (node) {
                        var tag = String(node && node.tagName || '').toUpperCase();
                        var href = hrefNode(node);
                        return tag === 'A' && !!href && !/^javascript:/i.test(href);
                    };
                    var ehDownloadOuMidiaAvant = function (node) {
                        if (!node) return false;
                        var href = hrefNode(node);
                        var alvo = textoAlvoClique(node);
                        var hrefNormalizado = normalizar(href);
                        try {
                            if (node.closest && node.closest('a[download], [download]')) return true;
                        } catch (_closestErr) {}
                        if (/^(blob|data):/i.test(href)) return true;
                        if (/\\.zip(?:$|[?#])|\\/download\\b|download=|filename=|imagens?\\.zip|images?\\.zip/i.test(href)) return true;
                        return /(^|[\\s_-])(baixar|download|imagens?|images?|fotos?|photos?|foto|photo|zip|exportar?|salvar|gallery|galeria)([\\s_-]|$)/.test(alvo + ' ' + hrefNormalizado);
                    };
                    var clicarCardsAvantSemDados = function (item) {
                        if (!clicarCardsSemDados || !item || !estaDentroDeCardProduto(item.node)) return false;
                        if (!/informacoes?\\s+avant|informacoes?\\s+avantpro|avantpro\\s+info|carregar\\s+dado?s?\\s+avant|atualizar\\s+dado?s?\\s+avant/.test(item.text + ' ' + item.context)) return false;
                        var jkAvantCardClickCount = Number(window.jkAvantCardClickCount || 0);
                        var jkAvantCardClickedAt = Number(window.jkAvantCardClickedAt || 0);
                        var ultimoClique = jkAvantCardClickedAt;
                        if (!forceClick && ultimoClique && Date.now() - ultimoClique < 1800) return false;
                        if (jkAvantCardClickCount > 0 && !forceClick && Date.now() - ultimoClique < 4500) return false;
                        return true;
                    };
                    var estaDentroDeCardProduto = function (node) {
                        try {
                            return !!(node && node.closest && node.closest('li.ui-search-layout__item, .poly-card, [class*="poly-card"], [class*="ui-search-result"], [data-testid*="result"], [data-testid*="card"]'));
                        } catch (_err) {
                            return false;
                        }
                    };
                    var ehAcaoVinculoConta = function (text, context) {
                        var alvo = (text || '') + ' ' + (context || '');
                        return /vincular\\s+(?:conta|agora)|conectar\\s+conta|autorizar\\s+(?:mercado\\s+livre|conta)|permitir\\s+acesso/.test(alvo)
                            && /avant\\s*pro|avantpro|mercado\\s+livre|conta|vincul/.test(alvo);
                    };
                    var ehMenuFlutuanteAvant = function (text, context) {
                        var alvo = (text || '') + ' ' + (context || '');
                        return /abrir\\s+menu\\s+avantpro|avantpro-floating-button|avantpro-speed-dial-fab|speed-dial/.test(alvo)
                            && /avant\\s*pro|avantpro|speed-dial/.test(alvo);
                    };
                    var ehFerramentasAvant = function (text, context, visibleText) {
                        var alvo = (text || '') + ' ' + (context || '');
                        var rotulo = visibleText || text || '';
                        if (/conectando\\s+avant|fazendo\\s+favorito|tentativa|sku\\s*\\d|aguarde|status/.test(alvo)) return false;
                        return /^(ferramentas|tools)$/.test(rotulo)
                            && /avant\\s*pro|avantpro|speed-dial|ferramentas/.test(alvo);
                    };
                    var scoreAcao = function (text, context, visibleText) {
                        if (!text) return 0;
                        if (somenteFerramentas) {
                            if (permitirFerramentas && ehFerramentasAvant(text, context, visibleText)) return 5;
                            if (permitirFerramentas && ehMenuFlutuanteAvant(text, context)) return 10;
                            return 0;
                        }
                        if (/assine\\s+ja|assinar|cancelar\\s+favoritos|ocultar/.test(text)) return 0;
                        if (ehAcaoVinculoConta(text, context)) return 0;
                        if (/\\blogin\\b|fazer\\s+login|entrar\\s+no\\s+avant|login\\s+avant/.test(text)) {
                            if (/avant\\s*pro|avantpro|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos|extensao/.test(context || text)) return 0;
                            return 0;
                        }
                        if (/carregar\\s+dado?s?\\s+avant|atualizar\\s+dado?s?\\s+avant|extrair\\s+dado?s?\\s+avant/.test(text)) return 5;
                        if (/informacoes?\\s+avant|informacoes?\\s+avantpro|avantpro\\s+info/.test(text)) return 15;
                        if (/rotulos\\s+visuais|r[oó]tulos\\s+visuais/.test(text)) return 0;
                        if (permitirFerramentas && ehFerramentasAvant(text, context, visibleText)) return 70;
                        if (permitirFerramentas && ehMenuFlutuanteAvant(text, context)) return 80;
                        return 0;
                    };
                    var clicarCandidatos = async function (incluiFerramentas) {
                        var bodyAtual = normalizar(document.body && (document.body.innerText || document.body.textContent) || '');
                        if (/vincule\\s+o\\s+avantpro|vincular\\s+agora/.test(bodyAtual)
                            && /avant\\s*pro|avantpro/.test(bodyAtual)) {
                            window.__JK_AVANT_AUTO_CLICK_BLOCKED_BY_LINK_MODAL_AT = Date.now();
                            return 0;
                        }
                        var candidates = queryAllDeep('button, a, input[type="button"], input[type="submit"], [role="button"], [aria-label], [title], [class*="andes-button"], div, span').map(function (node) {
                            var text = textoNode(node);
                            var visibleText = textoVisivelNode(node);
                            var context = contextoNode(node);
                            var score = scoreAcao(text, context, visibleText);
                            return { node: node, text: text, visibleText: visibleText, context: context, score: score };
                        }).filter(function (item) {
                            var cardAvantSemDados = clicarCardsAvantSemDados(item);
                            if (!item.score || (((!forceClick && item.node.dataset.jkAvantClicked === '1') && !cardAvantSemDados))) return false;
                            if (ehDownloadOuMidiaAvant(item.node)) return false;
                            if (!somenteFerramentas && ehAncoraNavegavel(item.node)) return false;
                            if (!incluiFerramentas && item.score >= 70) return false;
                            if (ehLinkProduto(item.node)) return false;
                            if (somenteFerramentas && estaDentroDeCardProduto(item.node)) return false;
                            if (!somenteFerramentas && estaDentroDeCardProduto(item.node) && /informacoes?\\s+avant|informacoes?\\s+avantpro|avantpro\\s+info/.test(item.text) && !cardAvantSemDados) return false;
                            if (!isVisible(item.node)) return false;
                            if (ehFerramentasAvant(item.text, item.context, item.visibleText) || ehMenuFlutuanteAvant(item.text, item.context)) {
                                var rect = item.node.getBoundingClientRect ? item.node.getBoundingClientRect() : null;
                                if (!rect || rect.width < 40 || rect.height < 20) return false;
                            }
                            return true;
                        }).sort(function (a, b) {
                            return a.score - b.score;
                        });

                        var clicou = 0;
                        for (var i = 0; i < candidates.length && clicked < maxClicks; i += 1) {
                            var item = candidates[i];
                            item.node.dataset.jkAvantClicked = '1';
                            try {
                                item.node.scrollIntoView && item.node.scrollIntoView({ block: 'center', inline: 'center' });
                            } catch (_err) {}
                            try {
                                var rect = item.node.getBoundingClientRect ? item.node.getBoundingClientRect() : null;
                                var opts = rect ? { bubbles: true, cancelable: true, view: window, clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 } : { bubbles: true, cancelable: true, view: window };
                                window.__JK_AVANT_LAST_AUTO_CLICK = {
                                    at: Date.now(),
                                    text: String(item.text || '').slice(0, 160),
                                    visibleText: String(item.visibleText || '').slice(0, 160),
                                    score: item.score,
                                    insideCard: !!estaDentroDeCardProduto(item.node),
                                    context: String(item.context || '').slice(0, 220)
                                };
                                try { item.node.dispatchEvent(new MouseEvent('mousedown', opts)); } catch (_downErr) {}
                                try { item.node.dispatchEvent(new MouseEvent('mouseup', opts)); } catch (_upErr) {}
                                try { item.node.dispatchEvent(new MouseEvent('click', opts)); } catch (_clickErr) {}
                                item.node.click();
                                if (estaDentroDeCardProduto(item.node)) {
                                    window.jkAvantCardClickCount = Number(window.jkAvantCardClickCount || 0) + 1;
                                    window.jkAvantCardClickedAt = Date.now();
                                    window.__JK_AVANT_CARD_CLICKED_AT = window.jkAvantCardClickedAt;
                                }
                                clicked += 1;
                                clicou += 1;
                            } catch (_err) {}
                            if (item.score >= 70) break;
                            await sleep(120);
                        }
                        return clicou;
                    };

                    var primeiraRodada = await clicarCandidatos(somenteFerramentas);
                    if (primeiraRodada) {
                        await sleep(850);
                        await clicarCandidatos(somenteFerramentas);
                    }
                    if (!clicked && permitirFerramentas) {
                        var abriuFerramentas = await clicarCandidatos(true);
                        if (abriuFerramentas) {
                            await sleep(700);
                            await clicarCandidatos(somenteFerramentas);
                        }
                    }
                    if (clicked) window.__JK_AVANT_PRO_CLICKED_AT = Date.now();
                    return clicked;
                })();
            `;
        }

        async function acionarControlesAvantProNoWebview(opcoes = {}) {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return 0;
            return await mlWebviewEl.executeJavaScript(montarScriptAcionarControlesAvantPro(opcoes), true).catch(() => 0);
        }

        function montarScriptLocalizarBolinhaAvantPro() {
            return `
                (function () {
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var visivel = function (node) {
                        if (!node || !node.getBoundingClientRect) return false;
                        var rect = node.getBoundingClientRect();
                        var style = window.getComputedStyle ? window.getComputedStyle(node) : null;
                        return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0
                            && rect.left < (window.innerWidth || document.documentElement.clientWidth || 0)
                            && rect.top < (window.innerHeight || document.documentElement.clientHeight || 0)
                            && (!style || (style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) !== 0));
                    };
                    var queryAllDeep = function (selector, root) {
                        var found = [];
                        var seen = [];
                        var walk = function (base) {
                            if (!base || seen.indexOf(base) >= 0) return;
                            seen.push(base);
                            try {
                                if (base.querySelectorAll) {
                                    found = found.concat(Array.prototype.slice.call(base.querySelectorAll(selector)));
                                }
                            } catch (_err) {}
                            var nodes = [];
                            try {
                                nodes = base.querySelectorAll ? Array.prototype.slice.call(base.querySelectorAll('*')) : [];
                            } catch (_err2) {}
                            for (var i = 0; i < nodes.length; i += 1) {
                                if (nodes[i] && nodes[i].shadowRoot) walk(nodes[i].shadowRoot);
                            }
                        };
                        walk(root || document);
                        return found.filter(function (node, index) { return found.indexOf(node) === index; });
                    };
                    var textoNode = function (node) {
                        if (!node) return '';
                        return [
                            node.innerText,
                            node.textContent,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title'),
                            node.getAttribute && node.getAttribute('class'),
                            node.getAttribute && node.getAttribute('id')
                        ].filter(Boolean).join(' ');
                    };
                    var contextoNode = function (node) {
                        var root = null;
                        try {
                            root = node && node.closest && node.closest('[id*="avant"], [class*="avant"], [class*="speed"], [class*="menu"], [role="dialog"], [aria-modal="true"]');
                        } catch (_err) {}
                        root = root || node && node.parentElement || node;
                        return normalizar([textoNode(node), root && textoNode(root), root && (root.innerText || root.textContent)].filter(Boolean).join(' '));
                    };
                    var dentroCardProduto = function (node) {
                        try {
                            return !!(node && node.closest && node.closest('li.ui-search-layout__item, .poly-card, [class*="poly-card"], [class*="ui-search-result"], [class*="dynamic-access"], [class*="recommend"], [class*="andes-card"], [data-testid*="result"], [data-testid*="card"]'));
                        } catch (_err) {
                            return false;
                        }
                    };
                    var vw = window.innerWidth || document.documentElement.clientWidth || 1280;
                    var vh = window.innerHeight || document.documentElement.clientHeight || 900;
                    var bodyBusca = normalizar(document.body && (document.body.innerText || document.body.textContent) || '');
                    var ferramentasVisivel = /\\bferramentas\\b/.test(bodyBusca)
                        && queryAllDeep('button, a, [role="button"], [aria-label], [title], [class*="avant"], [id*="avant"], [class*="speed"], div, span').some(function (node) {
                            if (!visivel(node) || dentroCardProduto(node)) return false;
                            var rect = node.getBoundingClientRect();
                            var texto = normalizar(textoNode(node));
                            var contexto = contextoNode(node);
                            if (/conectando\\s+avant|fazendo\\s+favorito|tentativa|sku\\s*\\d|aguarde|status/.test(texto + ' ' + contexto)) return false;
                            return /\\bferramentas\\b|\\btools\\b/.test(texto)
                                && rect.left > vw * 0.55
                                && /avant\\s*pro|avantpro|speed-dial|ferramentas/.test(texto + ' ' + contexto);
                        });
                    var candidatos = queryAllDeep('button, a, [role="button"], [tabindex], [aria-label], [title], [class*="avant"], [id*="avant"], [class*="speed"], div, span')
                        .map(function (node, index) {
                            if (!visivel(node) || dentroCardProduto(node)) return null;
                            var rect = node.getBoundingClientRect();
                            var texto = normalizar(textoNode(node));
                            var contexto = contextoNode(node);
                            var classeId = normalizar(String(node.className || '') + ' ' + String(node.id || ''));
                            var alvo = texto + ' ' + contexto + ' ' + classeId;
                            if (/assine\\s+ja|suporte|ferramentas|compras|favoritos/.test(texto)) return null;
                            var ehClasseBolinha = /avantpro-speed-dial-fab|speed-dial-fab|avantpro-floating-button|abrir\\s+menu\\s+avantpro|avantpro-menu/.test(alvo);
                            var temSinalAvant = /avant\\s*pro|avantpro|speed-dial|floating|abrir\\s+menu\\s+avantpro/.test(alvo);
                            var ehElementoMercadoLivre = /dynamic-access|andes-card|recommend|navigation|carousel|home|poly-card|ui-search|nav-/.test(alvo);
                            var ehTamanhoBolinha = rect.width >= 42 && rect.height >= 42 && rect.width <= 110 && rect.height <= 110;
                            var ehCantoInferiorDireito = rect.left > vw * 0.78 && rect.top > vh * 0.54;
                            var ehPosicaoFlutuante = false;
                            try {
                                var style = window.getComputedStyle ? window.getComputedStyle(node) : null;
                                ehPosicaoFlutuante = !!(style && /fixed|absolute|sticky/.test(String(style.position || '')));
                            } catch (_styleErr) {}
                            if (ehElementoMercadoLivre && !temSinalAvant) return null;
                            if (!ehClasseBolinha && !temSinalAvant) return null;
                            if (!ehClasseBolinha && !(ehTamanhoBolinha && ehCantoInferiorDireito && ehPosicaoFlutuante)) return null;
                            var score = 0;
                            if (ehClasseBolinha) score += 220;
                            if (temSinalAvant) score += 80;
                            if (rect.left > vw * 0.55) score += 40;
                            if (rect.top > vh * 0.45) score += 35;
                            if (ehTamanhoBolinha) score += 90;
                            if (ehCantoInferiorDireito) score += 120;
                            if (score <= 0) return null;
                            return {
                                node: node,
                                index: index,
                                score: score,
                                area: rect.width * rect.height,
                                x: rect.left + rect.width / 2,
                                y: rect.top + rect.height / 2,
                                width: rect.width,
                                height: rect.height,
                                label: String(textoNode(node) || '').replace(/\\s+/g, ' ').trim().slice(0, 120)
                            };
                        })
                        .filter(Boolean)
                        .sort(function (a, b) { return b.score - a.score || b.area - a.area || a.index - b.index; });
                    if (candidatos.length) {
                        var c = candidatos[0];
                        return {
                            success: true,
                            source: 'bolinha_avant_dom',
                            x: c.x,
                            y: c.y,
                            width: c.width,
                            height: c.height,
                            score: c.score,
                            label: c.label || 'Bolinha Avant Pro',
                            url: location.href
                        };
                    }
                    if (/avant\\s*pro|avantpro|assine\\s+ja|suporte/.test(bodyBusca)) {
                        return {
                            success: true,
                            source: 'bolinha_avant_estimado',
                            x: Math.max(40, vw - 84),
                            y: Math.max(40, vh - 84),
                            width: 64,
                            height: 64,
                            label: 'Bolinha Avant Pro',
                            url: location.href
                        };
                    }
                    return { success: false, reason: 'bolinha_avant_nao_localizada', url: location.href };
                })();
            `;
        }

        async function abrirBolinhaAvantProSeNecessario() {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') {
                return { success: false, reason: 'navegador_indisponivel' };
            }
            const alvo = await mlWebviewEl.executeJavaScript(montarScriptLocalizarBolinhaAvantPro(), true).catch((err) => ({
                success: false,
                reason: 'erro_ao_localizar_bolinha_avant',
                error: err && err.message ? err.message : String(err)
            }));
            if (!(alvo && alvo.success)) return alvo || { success: false, reason: 'bolinha_avant_nao_localizada' };
            if (alvo.skipped) return alvo;
            const alvoClique = String(alvo.source || '') === 'bolinha_avant_dom'
                ? {
                    ...alvo,
                    x: Number(alvo.x) + 24,
                    y: Number(alvo.y) + 30,
                    source: 'bolinha_avant_dom_ajuste_borda'
                }
                : alvo;
            const click = await clicarNavegadorMlPorCoordenada(alvoClique).catch((err) => ({
                success: false,
                reason: 'erro_no_clique_real_bolinha_avant',
                error: err && err.message ? err.message : String(err)
            }));
            await esperar(650);
            return {
                success: !!(click && click.success !== false),
                target: alvoClique,
                originalTarget: alvo,
                click
            };
        }

        function montarScriptLocalizarFerramentasAvantPro() {
            return `
                (function () {
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var visivel = function (node) {
                        if (!node || !node.getBoundingClientRect) return false;
                        var rect = node.getBoundingClientRect();
                        var style = window.getComputedStyle ? window.getComputedStyle(node) : null;
                        return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0
                            && rect.left < (window.innerWidth || document.documentElement.clientWidth || 0)
                            && rect.top < (window.innerHeight || document.documentElement.clientHeight || 0)
                            && (!style || (style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) !== 0));
                    };
                    var queryAllDeep = function (selector, root) {
                        var found = [];
                        var seen = [];
                        var walk = function (base) {
                            if (!base || seen.indexOf(base) >= 0) return;
                            seen.push(base);
                            try {
                                if (base.querySelectorAll) {
                                    found = found.concat(Array.prototype.slice.call(base.querySelectorAll(selector)));
                                }
                            } catch (_err) {}
                            var nodes = [];
                            try {
                                nodes = base.querySelectorAll ? Array.prototype.slice.call(base.querySelectorAll('*')) : [];
                            } catch (_err2) {}
                            for (var i = 0; i < nodes.length; i += 1) {
                                if (nodes[i] && nodes[i].shadowRoot) walk(nodes[i].shadowRoot);
                            }
                        };
                        walk(root || document);
                        return found.filter(function (node, index) { return found.indexOf(node) === index; });
                    };
                    var composedHost = function (node) {
                        try {
                            var root = node && node.getRootNode ? node.getRootNode() : null;
                            return root && root.host ? root.host : null;
                        } catch (_err) {
                            return null;
                        }
                    };
                    var textoBotao = function (node) {
                        if (!node) return '';
                        return [
                            node.innerText,
                            node.textContent,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title'),
                            node.getAttribute && node.getAttribute('class'),
                            node.getAttribute && node.getAttribute('id')
                        ].filter(Boolean).join(' ');
                    };
                    var textoVisivelBotao = function (node) {
                        if (!node) return '';
                        var text = [
                            node.innerText,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title')
                        ].filter(Boolean).join(' ');
                        if (!text && node.textContent) text = node.textContent;
                        return text;
                    };
                    var contextoBotao = function (node) {
                        var root = null;
                        try {
                            root = node && node.closest && node.closest('form, article, section, aside, li, [role="dialog"], [aria-modal="true"], [class*="avant"], [id*="avant"], [class*="speed"], [class*="menu"], [class*="modal"], [class*="popup"], [class*="drawer"], [class*="login"], [class*="auth"]');
                        } catch (_err) {}
                        var host = composedHost(node);
                        root = root || (node && node.parentElement) || node;
                        return normalizar([
                            textoBotao(node),
                            root && textoBotao(root),
                            root && (root.innerText || root.textContent),
                            host && textoBotao(host),
                            host && (host.innerText || host.textContent)
                        ].filter(Boolean).join(' '));
                    };
                    var dentroCardProduto = function (node) {
                        try {
                            return !!(node && node.closest && node.closest('li.ui-search-layout__item, .poly-card, [class*="poly-card"], [class*="ui-search-result"], [class*="dynamic-access"], [class*="recommend"], [class*="andes-card"], [data-testid*="result"], [data-testid*="card"]'));
                        } catch (_err) {
                            return false;
                        }
                    };
                    var escolherAlvoClique = function (node) {
                        var candidatos = [];
                        var incluir = function (item, bonus) {
                            if (!item || candidatos.indexOf(item) >= 0 || !visivel(item) || dentroCardProduto(item)) return;
                            var rect = item.getBoundingClientRect();
                            if (rect.width < 24 || rect.height < 12) return;
                            var texto = normalizar(textoVisivelBotao(item));
                            var contexto = contextoBotao(item);
                            var alvo = texto + ' ' + contexto;
                            if (!/^(ferramentas|tools)$/.test(texto)) return;
                            if (/assine\\s+ja|assinar|suporte/.test(texto) && !/^ferramentas$|^tools$/.test(texto)) return;
                            var area = rect.width * rect.height;
                            var score = Number(bonus) || 0;
                            if (/button|a/i.test(item.tagName || '')) score += 80;
                            if (item.getAttribute && item.getAttribute('role') === 'button') score += 70;
                            if (rect.width >= 70 && rect.height >= 28 && rect.width <= 280 && rect.height <= 120) score += 90;
                            if (/avant|speed|dial|menu|tool|ferramentas/.test(String(item.className || '') + ' ' + String(item.id || ''))) score += 50;
                            if (area > 1200 && area < 32000) score += 40;
                            candidatos.push({ node: item, score: score, area: area, rect: rect });
                        };
                        incluir(node, 0);
                        try {
                            incluir(node.closest && node.closest('button, a, [role="button"], [class*="speed-dial-action"], [class*="speed-dial-item"], [class*="floating-button"], [class*="avantpro"]'), 50);
                        } catch (_err) {}
                        var atual = node && node.parentElement;
                        for (var nivel = 0; atual && nivel < 5; nivel += 1) {
                            incluir(atual, 40 - nivel * 5);
                            atual = atual.parentElement;
                        }
                        candidatos.sort(function (a, b) {
                            return b.score - a.score || b.area - a.area;
                        });
                        return candidatos.length ? candidatos[0].node : node;
                    };
                    var vw = window.innerWidth || document.documentElement.clientWidth || 1280;
                    var vh = window.innerHeight || document.documentElement.clientHeight || 900;
                    var todosCandidatos = queryAllDeep('button, a, input[type="button"], input[type="submit"], [role="button"], [tabindex], [aria-label], [title], [class*="andes-button"], [class*="avant"], [id*="avant"], [class*="speed"], [class*="menu"], div, span')
                        .filter(function (node) {
                            if (!visivel(node)) return false;
                            if (dentroCardProduto(node)) return false;
                            var texto = normalizar(textoVisivelBotao(node));
                            var contexto = contextoBotao(node);
                            var alvo = texto + ' ' + contexto;
                            var rect = node.getBoundingClientRect();
                            if (/assine\\s+ja|assinar|suporte|compras|favoritos|categorias|ofertas/.test(texto)) return false;
                            if (/conectando\\s+avant|fazendo\\s+favorito|tentativa|sku\\s*\\d|aguarde|status/.test(alvo)) return false;
                            if (!/^(ferramentas|tools)$/.test(texto)) return false;
                            if (rect.left < Math.max(vw * 0.68, vw - 420)) return false;
                            if (rect.top < Math.max(120, vh - 230)) return false;
                            return /\\bferramentas\\b|\\btools\\b/.test(alvo);
                        })
                        .map(function (node, index) {
                            var clickNode = escolherAlvoClique(node);
                            var rect = clickNode.getBoundingClientRect();
                            var texto = normalizar(textoVisivelBotao(node));
                            var contexto = contextoBotao(clickNode);
                            var alvo = texto + ' ' + contexto;
                            var score = 0;
                            if (/^ferramentas$|^tools$/.test(texto)) score += 260;
                            if (/\\bferramentas\\b|\\btools\\b/.test(texto)) score += 180;
                            if (/avant\\s*pro|avantpro|speed-dial|menu/.test(alvo)) score += 80;
                            if (rect.left > vw * 0.55) score += 60;
                            if (rect.width >= 70 && rect.width <= 260 && rect.height >= 28 && rect.height <= 90) score += 35;
                            if (clickNode !== node && rect.width >= 70 && rect.height >= 24) score += 70;
                            if (rect.left > vw - 260) score += 35;
                            if (rect.top > 80 && rect.top < vh - 80) score += 20;
                            return {
                                node: clickNode,
                                index: index,
                                score: score,
                                x: rect.left + rect.width / 2,
                                y: rect.top + rect.height / 2,
                                width: rect.width,
                                height: rect.height,
                                label: String(textoVisivelBotao(node) || textoBotao(node) || '').replace(/\\s+/g, ' ').trim().slice(0, 120)
                            };
                        });
                    var candidatos = todosCandidatos
                        .filter(function (item) { return item.score > 0 && item.width >= 40 && item.height >= 20; })
                        .sort(function (a, b) { return b.score - a.score || a.index - b.index; });
                    if (candidatos.length) {
                        var c = candidatos[0];
                        return {
                            success: true,
                            source: 'ferramentas_dom',
                            x: c.x,
                            y: c.y,
                            width: c.width,
                            height: c.height,
                            score: c.score,
                            label: c.label || 'Ferramentas',
                            url: location.href
                        };
                    }
                    var bodyBusca = normalizar(document.body && (document.body.innerText || document.body.textContent) || '');
                    var rotuloFerramentasMenuVisivel = queryAllDeep('button, a, [role="button"], [aria-label], [title], [class*="avant"], [id*="avant"], [class*="speed"], [class*="menu"], div, span').some(function (node) {
                        if (!visivel(node) || dentroCardProduto(node)) return false;
                        var rect = node.getBoundingClientRect();
                        if (rect.left < Math.max(vw * 0.68, vw - 420)) return false;
                        if (rect.top < Math.max(120, vh - 260)) return false;
                        var texto = normalizar(textoVisivelBotao(node));
                        var contexto = contextoBotao(node);
                        return /^(ferramentas|tools)$/.test(texto)
                            && /avant\\s*pro|avantpro|speed-dial|ferramentas/.test(texto + ' ' + contexto);
                    });
                    var menuLateralAvantVisivel = rotuloFerramentasMenuVisivel
                        && (/\\bsuporte\\b|assine\\s+ja|avant\\s*pro|avantpro/.test(bodyBusca)
                            || queryAllDeep('[class*="avant"], [id*="avant"], [class*="speed-dial"]').some(visivel));
                    if (menuLateralAvantVisivel) {
                        return {
                            success: true,
                            source: 'ferramentas_menu_lateral_estimado',
                            x: Math.max(40, vw - 110),
                            y: Math.max(40, vh - 180),
                            width: 120,
                            height: 46,
                            label: 'Ferramentas',
                            url: location.href
                        };
                    }
                    var rotulosPequenos = todosCandidatos
                        .filter(function (item) { return item.score > 0 && (item.width < 40 || item.height < 20) && item.x > vw * 0.55; })
                        .sort(function (a, b) { return b.score - a.score || a.index - b.index; });
                    if (rotulosPequenos.length) {
                        var r = rotulosPequenos[0];
                        return {
                            success: true,
                            source: 'ferramentas_rotulo_estimado',
                            x: Math.max(40, Math.min(Math.round(r.x - 30), vw - 1)),
                            y: Math.max(40, Math.min(Math.round(r.y), vh - 1)),
                            width: 120,
                            height: 46,
                            score: r.score,
                            label: r.label || 'Ferramentas',
                            url: location.href
                        };
                    }
                    return {
                        success: false,
                        reason: 'ferramentas_avant_nao_localizado',
                        hasFerramentasText: /\\bferramentas\\b/.test(bodyBusca),
                        url: location.href
                    };
                })();
            `;
        }

        async function clicarFerramentasAvantProPorCoordenada() {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') {
                return { success: false, reason: 'navegador_indisponivel' };
            }
            const alvo = await mlWebviewEl.executeJavaScript(montarScriptLocalizarFerramentasAvantPro(), true).catch((err) => ({
                success: false,
                reason: 'erro_ao_localizar_ferramentas_avant',
                error: err && err.message ? err.message : String(err)
            }));
            if (!(alvo && alvo.success)) return alvo || { success: false, reason: 'ferramentas_avant_nao_localizado' };
            const alvosClique = [alvo];
            if (/estimado|rotulo|dom/i.test(String(alvo.source || ''))) {
                const baseX = Number(alvo.x);
                const baseY = Number(alvo.y);
                if (Number.isFinite(baseX) && Number.isFinite(baseY)) {
                    [-45, 45, -75].forEach((dx) => {
                        alvosClique.push({
                            ...alvo,
                            x: Math.max(20, baseX + dx),
                            y: baseY,
                            source: `${alvo.source}_ajuste_${dx}`
                        });
                    });
                    [60, 95, 130].forEach((dy) => {
                        [0, -45, 45].forEach((dx) => {
                            alvosClique.push({
                                ...alvo,
                                x: Math.max(20, baseX + dx),
                                y: Math.max(20, baseY + dy),
                                source: `${alvo.source}_ajuste_${dx}_${dy}`
                            });
                        });
                    });
                }
            }
            const cliques = [];
            let click = null;
            for (let i = 0; i < alvosClique.length; i += 1) {
                const alvoClique = alvosClique[i];
                click = await clicarNavegadorMlPorCoordenada(alvoClique).catch((err) => ({
                    success: false,
                    reason: 'erro_no_clique_real_ferramentas',
                    error: err && err.message ? err.message : String(err)
                }));
                cliques.push({ target: alvoClique, click });
                if (!(click && click.success !== false)) continue;
                await esperar(i === 0 ? 360 : 520);
                const entrada = await diagnosticarEntradaAvantProNoWebview().catch(() => null);
                cliques[cliques.length - 1].entrada = entrada;
                if (entrada && entrada.prontoParaLogin) break;
            }
            return {
                success: !!(click && click.success !== false),
                target: alvo,
                click,
                cliques
            };
        }

        function montarScriptLocalizarVincularContaAvantPro() {
            return `
                (function () {
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var visivel = function (node) {
                        if (!node || !node.getBoundingClientRect) return false;
                        var rect = node.getBoundingClientRect();
                        var style = window.getComputedStyle ? window.getComputedStyle(node) : null;
                        return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0
                            && rect.left < (window.innerWidth || document.documentElement.clientWidth || 0)
                            && rect.top < (window.innerHeight || document.documentElement.clientHeight || 0)
                            && (!style || (style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) !== 0));
                    };
                    var queryAllDeep = function (selector, root) {
                        var found = [];
                        var seen = [];
                        var walk = function (base) {
                            if (!base || seen.indexOf(base) >= 0) return;
                            seen.push(base);
                            try {
                                if (base.querySelectorAll) {
                                    found = found.concat(Array.prototype.slice.call(base.querySelectorAll(selector)));
                                }
                            } catch (_err) {}
                            var nodes = [];
                            try {
                                nodes = base.querySelectorAll ? Array.prototype.slice.call(base.querySelectorAll('*')) : [];
                            } catch (_err2) {}
                            for (var i = 0; i < nodes.length; i += 1) {
                                if (nodes[i] && nodes[i].shadowRoot) walk(nodes[i].shadowRoot);
                            }
                        };
                        walk(root || document);
                        return found.filter(function (node, index) { return found.indexOf(node) === index; });
                    };
                    var textoNode = function (node) {
                        if (!node) return '';
                        return [
                            node.innerText,
                            node.textContent,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title'),
                            node.getAttribute && node.getAttribute('class'),
                            node.getAttribute && node.getAttribute('id')
                        ].filter(Boolean).join(' ');
                    };
                    var contextoNode = function (node) {
                        var root = null;
                        try {
                            root = node && node.closest && node.closest('form, section, aside, [role="dialog"], [aria-modal="true"], [class*="avant"], [id*="avant"], [class*="speed"], [class*="menu"], [class*="drawer"], [class*="popup"]');
                        } catch (_err) {}
                        root = root || (node && node.parentElement) || node;
                        return normalizar([
                            textoNode(node),
                            root && textoNode(root),
                            root && (root.innerText || root.textContent)
                        ].filter(Boolean).join(' '));
                    };
                    var dentroCardProduto = function (node) {
                        try {
                            return !!(node && node.closest && node.closest('li.ui-search-layout__item, .poly-card, [class*="poly-card"], [class*="ui-search-result"], [class*="dynamic-access"], [class*="recommend"], [class*="andes-card"], [data-testid*="result"], [data-testid*="card"]'));
                        } catch (_err) {
                            return false;
                        }
                    };
                    var vw = window.innerWidth || document.documentElement.clientWidth || 1280;
                    var vh = window.innerHeight || document.documentElement.clientHeight || 900;
                    var candidatos = queryAllDeep('button, a, input[type="button"], input[type="submit"], [role="button"], [tabindex], [aria-label], [title], [class*="andes-button"], [class*="avant"], [id*="avant"], [class*="speed"], [class*="menu"], div, span')
                        .filter(function (node) {
                            if (!visivel(node) || dentroCardProduto(node)) return false;
                            var rect = node.getBoundingClientRect();
                            var texto = normalizar(textoNode(node));
                            var contexto = contextoNode(node);
                            var alvo = texto + ' ' + contexto;
                            if (rect.left < vw * 0.55) return false;
                            if (rect.top < 100 || rect.top > vh - 40) return false;
                            if (/conectando\\s+avant|fazendo\\s+favorito|tentativa|sku\\s*\\d|aguarde|status/.test(alvo)) return false;
                            var textoTemVinculo = /vincular\\s+(?:conta|agora)|conectar\\s+conta|autorizar\\s+(?:mercado\\s+livre|conta)|permitir\\s+acesso/.test(texto);
                            return textoTemVinculo
                                && /avant\\s*pro|avantpro|speed-dial|vincular|conta|mercado\\s+livre/.test(alvo);
                        })
                        .map(function (node, index) {
                            var rect = node.getBoundingClientRect();
                            var texto = normalizar(textoNode(node));
                            var contexto = contextoNode(node);
                            var alvo = texto + ' ' + contexto;
                            var score = 0;
                            if (/^vincular\\s+conta$|^conectar\\s+conta$/.test(texto)) score += 260;
                            if (/vincular\\s+(?:conta|agora)|conectar\\s+conta/.test(alvo)) score += 180;
                            if (/avant\\s*pro|avantpro|speed-dial|menu/.test(alvo)) score += 60;
                            if (rect.width >= 90 && rect.width <= 260 && rect.height >= 28 && rect.height <= 90) score += 60;
                            if (rect.top > 60 && rect.top < vh - 80) score += 20;
                            return {
                                index: index,
                                score: score,
                                x: rect.left + rect.width / 2,
                                y: rect.top + rect.height / 2,
                                width: rect.width,
                                height: rect.height,
                                label: String(textoNode(node) || '').replace(/\\s+/g, ' ').trim().slice(0, 120)
                            };
                        })
                        .filter(function (item) { return item.score > 0 && item.width >= 40 && item.height >= 20; })
                        .sort(function (a, b) { return b.score - a.score || a.index - b.index; });
                    if (candidatos.length) {
                        var c = candidatos[0];
                        return {
                            success: true,
                            source: 'vincular_conta_avant_dom',
                            x: c.x,
                            y: c.y,
                            width: c.width,
                            height: c.height,
                            score: c.score,
                            label: c.label || 'Vincular conta',
                            url: location.href
                        };
                    }
                    return { success: false, reason: 'vincular_conta_avant_nao_localizado', url: location.href };
                })();
            `;
        }

        async function clicarVincularContaAvantProPorCoordenada() {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') {
                return { success: false, reason: 'navegador_indisponivel' };
            }
            const alvo = await mlWebviewEl.executeJavaScript(montarScriptLocalizarVincularContaAvantPro(), true).catch((err) => ({
                success: false,
                reason: 'erro_ao_localizar_vincular_conta_avant',
                error: err && err.message ? err.message : String(err)
            }));
            if (!(alvo && alvo.success)) return alvo || { success: false, reason: 'vincular_conta_avant_nao_localizado' };
            const click = await clicarNavegadorMlPorCoordenada(alvo).catch((err) => ({
                success: false,
                reason: 'erro_no_clique_real_vincular_conta_avant',
                error: err && err.message ? err.message : String(err)
            }));
            return {
                success: !!(click && click.success !== false),
                target: alvo,
                click
            };
        }

        async function diagnosticarEntradaAvantProNoWebview() {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') {
                return { ok: false, reason: 'navegador_indisponivel' };
            }
            return await mlWebviewEl.executeJavaScript(`
                (function () {
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var visivel = function (node) {
                        if (!node || !node.getBoundingClientRect) return false;
                        var rect = node.getBoundingClientRect();
                        var style = window.getComputedStyle ? window.getComputedStyle(node) : null;
                        return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0
                            && rect.left < (window.innerWidth || document.documentElement.clientWidth || 0)
                            && rect.top < (window.innerHeight || document.documentElement.clientHeight || 0)
                            && (!style || (style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) !== 0));
                    };
                    var queryAllDeep = function (selector, root) {
                        var found = [];
                        var seen = [];
                        var walk = function (base) {
                            if (!base || seen.indexOf(base) >= 0) return;
                            seen.push(base);
                            try {
                                if (base.querySelectorAll) {
                                    found = found.concat(Array.prototype.slice.call(base.querySelectorAll(selector)));
                                }
                            } catch (_err) {}
                            var nodes = [];
                            try {
                                nodes = base.querySelectorAll ? Array.prototype.slice.call(base.querySelectorAll('*')) : [];
                            } catch (_err2) {}
                            for (var i = 0; i < nodes.length; i += 1) {
                                if (nodes[i] && nodes[i].shadowRoot) walk(nodes[i].shadowRoot);
                            }
                        };
                        walk(root || document);
                        return found.filter(function (node, index) { return found.indexOf(node) === index; });
                    };
                    var textoNode = function (node) {
                        if (!node) return '';
                        return [
                            node.innerText,
                            node.textContent,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title'),
                            node.getAttribute && node.getAttribute('placeholder'),
                            node.getAttribute && node.getAttribute('class'),
                            node.getAttribute && node.getAttribute('id')
                        ].filter(Boolean).join(' ');
                    };
                    var contextoNode = function (node) {
                        var root = null;
                        try {
                            root = node && node.closest && node.closest('form, [role="dialog"], [aria-modal="true"], [class*="avant"], [id*="avant"], [class*="login"], [class*="auth"], [class*="modal"], [class*="popup"], [class*="drawer"], [class*="speed"], [class*="menu"]');
                        } catch (_err) {}
                        var host = null;
                        try {
                            var nodeRoot = node && node.getRootNode ? node.getRootNode() : null;
                            host = nodeRoot && nodeRoot.host ? nodeRoot.host : null;
                        } catch (_err2) {}
                        root = root || (node && node.parentElement) || document.body;
                        return normalizar([
                            textoNode(node),
                            root && textoNode(root),
                            root && (root.innerText || root.textContent),
                            host && textoNode(host),
                            host && (host.innerText || host.textContent)
                        ].filter(Boolean).join(' '));
                    };
                    var bodyText = String(document.body && (document.body.innerText || document.body.textContent) || '');
                    var bodyBusca = normalizar(bodyText);
                    var confirmado = /obrigado\\s+por\\s+usar\\s+nossa\\s+extensao|aguarde[,\\s]+a\\s+pagina\\s+sera\\s+recarregada/.test(bodyBusca);
                    var ferramentasPainelAberto = /ferramentas\\s+avantpro|ferramentas\\s+avant\\s*pro/.test(bodyBusca);
                    var sessaoExpiradaAvant = /session\\s+expired|please\\s+log\\s+in\\s+again|statuscode\\s*[:=]?\\s*401|nao\\s+foi\\s+possivel\\s+carregar/.test(bodyBusca);
                    var contaMercadoLivreNecessaria = /conecte\\s+sua\\s+conta\\s+do\\s+mercado\\s+livre|vincule\\s+sua\\s+conta\\s+para\\s+liberar/.test(bodyBusca);
                    var ferramentasMenuItens = [
                        /calculadora\\s+de\\s+contribuicao/.test(bodyBusca),
                        /metricas\\s+do\\s+anuncio/.test(bodyBusca),
                        /gerador\\s+de\\s+eans/.test(bodyBusca),
                        /teste\\s+a\\/?b/.test(bodyBusca),
                        /rastreio\\s+de\\s+ads/.test(bodyBusca),
                        /tendencias/.test(bodyBusca),
                        /publicar\\s+com\\s+ia/.test(bodyBusca),
                        /gerador\\s+de\\s+titulos/.test(bodyBusca),
                        /gerador\\s+de\\s+descricao/.test(bodyBusca)
                    ].filter(Boolean).length;
                    var ferramentasMenuLogado = ferramentasMenuItens >= 3 && !sessaoExpiradaAvant;
                    var emailInputs = queryAllDeep('input:not([type="hidden"])').filter(function (input) {
                        if (!visivel(input) || input.disabled || input.readOnly) return false;
                        var attrs = normalizar([
                            input.type,
                            input.name,
                            input.id,
                            input.className,
                            input.placeholder,
                            input.getAttribute && input.getAttribute('aria-label'),
                            input.getAttribute && input.getAttribute('autocomplete')
                        ].join(' '));
                        var contexto = contextoNode(input);
                        var pareceEmail = input.type === 'email' || /email|e-?mail|mail/.test(attrs + ' ' + contexto);
                        var pareceBuscaMl = /search|buscar|pesquisar|as_word|\\bq\\b/.test(attrs);
                        var contextoAvant = /avant\\s*pro|avantpro|avantprocloud|iniciar\\s+sessao|insira\\s+suas\\s+credenciais|seu\\s+e-?mail|seu\\s+email/.test(contexto + ' ' + bodyBusca);
                        return pareceEmail && contextoAvant && !pareceBuscaMl;
                    });
                    var botoesLogin = queryAllDeep('button, a, input[type="button"], input[type="submit"], [role="button"], [tabindex], [aria-label], [title], div, span').filter(function (node) {
                        if (!visivel(node)) return false;
                        var texto = normalizar(textoNode(node));
                        var contexto = contextoNode(node);
                        var alvo = texto + ' ' + contexto;
                        if (/assine\\s+ja|assinar|suporte|compras|favoritos|categorias|ofertas/.test(texto)) return false;
                        return /vincular\\s+(?:conta|agora)|conectar\\s+conta|fazer\\s+login|entrar\\s+no\\s+avant|login\\s+avant|^login$|iniciar\\s+sessao|continuar/.test(alvo)
                            && /avant\\s*pro|avantpro|avantprocloud|extensao|conta|credenciais/.test(alvo + ' ' + bodyBusca);
                    });
                    var ferramentasVisiveis = queryAllDeep('button, a, [role="button"], [aria-label], [title], div, span').some(function (node) {
                        if (!visivel(node)) return false;
                        return /\\bferramentas\\b|\\btools\\b/.test(normalizar(textoNode(node)));
                    });
                    var authFrames = queryAllDeep('iframe, frame').filter(function (node) {
                        if (!visivel(node)) return false;
                        var src = normalizar([
                            node.getAttribute && node.getAttribute('src'),
                            node.getAttribute && node.getAttribute('name'),
                            node.getAttribute && node.getAttribute('id'),
                            node.getAttribute && node.getAttribute('title')
                        ].filter(Boolean).join(' '));
                        return /avantprocloud|auth\\.avantpro|avant.*auth|login.*avant/.test(src);
                    });
                    var pronto = confirmado || ferramentasMenuLogado || emailInputs.length > 0 || authFrames.length > 0;
                    return {
                        ok: pronto,
                        prontoParaLogin: pronto,
                        confirmado: confirmado,
                        confirmed: confirmado || ferramentasMenuLogado,
                        ferramentasMenuLogado: ferramentasMenuLogado,
                        ferramentasMenuItens: ferramentasMenuItens,
                        emailInputs: emailInputs.length,
                        authFrames: authFrames.length,
                        botoesLogin: botoesLogin.length,
                        ferramentasVisiveis: ferramentasVisiveis,
                        ferramentasPainelAberto: ferramentasPainelAberto,
                        sessaoExpiradaAvant: sessaoExpiradaAvant,
                        contaMercadoLivreNecessaria: contaMercadoLivreNecessaria,
                        avantText: /avant\\s*pro|avantpro|avantprocloud/.test(bodyBusca),
                        reason: pronto ? 'entrada_avant_pronta'
                            : sessaoExpiradaAvant ? 'avantpro_sessao_expirada_sem_email'
                            : contaMercadoLivreNecessaria ? 'avantpro_conta_mercado_livre_necessaria'
                            : ferramentasPainelAberto ? 'avantpro_ferramentas_abriu_sem_email'
                            : 'entrada_avant_nao_visivel',
                        url: location.href,
                        title: document.title || ''
                    };
                })();
            `, true).catch((err) => ({
                ok: false,
                reason: 'erro_ao_diagnosticar_entrada_avant',
                error: err && err.message ? err.message : String(err)
            }));
        }

        async function aguardarEntradaAvantProAposFerramentas(opcoes = {}) {
            const timeoutMs = Math.max(800, Number(opcoes.timeoutMs) || 2600);
            const pollMs = Math.max(160, Number(opcoes.pollMs) || 320);
            const inicio = Date.now();
            let ultimo = null;
            while (Date.now() - inicio < timeoutMs) {
                ultimo = await diagnosticarEntradaAvantProNoWebview().catch(() => null);
                if (ultimo && ultimo.prontoParaLogin) {
                    return { ...ultimo, ok: true, elapsedMs: Date.now() - inicio };
                }
                await esperar(pollMs);
            }
            return ultimo ? { ...ultimo, ok: false, timeout: true } : { ok: false, timeout: true };
        }

        function mostrarStatusConexaoAvantPro(etapa, opcoes = {}) {
            const termo = String(opcoes.termo || '').trim();
            const contexto = termo ? ` para "${termo}"` : '';
            const tentativa = Number(opcoes.tentativa || 0);
            const sufixoTentativa = tentativa > 0 ? ` (tentativa ${tentativa})` : '';
            mostrarBalaoFavoritosStatus(`Conectando Avant Pro${contexto}: ${etapa}${sufixoTentativa}...`, {
                manterNavegadorVisivel: true,
                larga: true,
                titulo: 'Conectar Avant Pro'
            });
        }

        async function abrirFerramentasAvantProAntesLogin(opcoes = {}) {
            const maxTentativas = Math.max(1, Math.min(4, Number(opcoes.maxTentativas) || 3));
            const termo = String(opcoes.termo || '').trim();
            const permitirLoginGenerico = opcoes.permitirLoginGenerico !== false;
            const tentativas = [];
            for (let tentativa = 0; tentativa < maxTentativas; tentativa += 1) {
                mostrarStatusConexaoAvantPro('clicando na bolinha do Avant Pro', {
                    termo,
                    tentativa: tentativa + 1
                });
                const bolinha = await abrirBolinhaAvantProSeNecessario().catch((err) => ({
                    success: false,
                    reason: 'erro_ao_abrir_bolinha_avant',
                    error: err && err.message ? err.message : String(err)
                }));
                tentativas.push({ via: 'bolinha', tentativa: tentativa + 1, result: bolinha });
                await esperar(tentativa === 0 ? 420 : 560);
                mostrarStatusConexaoAvantPro('clicando em Ferramentas', {
                    termo,
                    tentativa: tentativa + 1
                });
                const clicksDom = await acionarControlesAvantProNoWebview({
                    forceClick: true,
                    permitirFerramentas: true,
                    somenteFerramentas: true,
                    maxClicks: 2
                }).catch(() => 0);
                tentativas.push({ via: 'dom', tentativa: tentativa + 1, clicks: clicksDom || 0 });
                await esperar(tentativa === 0 ? 520 : 760);
                const entradaDom = await aguardarEntradaAvantProAposFerramentas({
                    timeoutMs: tentativa === 0 ? 1400 : 1900,
                    pollMs: 260
                }).catch(() => null);
                tentativas.push({ via: 'diagnostico_entrada_dom', tentativa: tentativa + 1, result: entradaDom });
                if (entradaDom && entradaDom.prontoParaLogin) {
                    mostrarStatusConexaoAvantPro('Ferramentas abriu; procurando o campo de e-mail', { termo });
                    return { success: true, tentativas, entrada: entradaDom };
                }
                if (entradaDom && entradaDom.ferramentasPainelAberto) {
                    mostrarStatusConexaoAvantPro('Ferramentas abriu, mas o e-mail ainda nao apareceu', { termo });
                    return {
                        success: false,
                        reason: entradaDom.reason || 'avantpro_ferramentas_abriu_sem_email',
                        tentativas,
                        entrada: entradaDom
                    };
                }
                const clickFerramentas = await clicarFerramentasAvantProPorCoordenada().catch((err) => ({
                    success: false,
                    reason: 'erro_no_clique_ferramentas',
                    error: err && err.message ? err.message : String(err)
                }));
                tentativas.push({ via: 'coordenada', tentativa: tentativa + 1, result: clickFerramentas });
                if (clickFerramentas && clickFerramentas.success) {
                    const entrada = await aguardarEntradaAvantProAposFerramentas({
                        timeoutMs: tentativa === 0 ? 2200 : 3200,
                        pollMs: 280
                    }).catch(() => null);
                    tentativas.push({ via: 'diagnostico_entrada', tentativa: tentativa + 1, result: entrada });
                    if (entrada && entrada.prontoParaLogin) {
                        mostrarStatusConexaoAvantPro('Ferramentas abriu; procurando o campo de e-mail', { termo });
                        return { success: true, tentativas, entrada };
                    }
                    if (entrada && entrada.ferramentasPainelAberto) {
                        mostrarStatusConexaoAvantPro('Ferramentas abriu, mas o e-mail ainda nao apareceu', { termo });
                        return {
                            success: false,
                            reason: entrada.reason || 'avantpro_ferramentas_abriu_sem_email',
                            tentativas,
                            entrada
                        };
                    }
                    if (permitirLoginGenerico && typeof tentarLoginAvantProNoWebview === 'function') {
                        const tentativaLogin = await tentarLoginAvantProNoWebview(mlWebviewEl, {
                            timeoutMs: 2600,
                            atrasos: [0, 200, 520, 1000, 1700, 2400]
                        }).catch(() => null);
                        tentativas.push({ via: 'autologin_apos_ferramentas', tentativa: tentativa + 1, result: tentativaLogin });
                        const entradaDepoisLogin = await aguardarEntradaAvantProAposFerramentas({
                            timeoutMs: (tentativaLogin && tentativaLogin.clickedLoginButton) ? 2600 : 1200,
                            pollMs: 240
                        }).catch(() => null);
                        tentativas.push({ via: 'diagnostico_entrada_pos_autologin', tentativa: tentativa + 1, result: entradaDepoisLogin });
                        if (
                            (entradaDepoisLogin && entradaDepoisLogin.prontoParaLogin)
                            || (tentativaLogin && tentativaLogin.success && (
                                tentativaLogin.emailVisible
                                || tentativaLogin.reason === 'campo_email_avant_visivel'
                            ))
                        ) {
                            return { success: true, tentativas, entrada: entradaDepoisLogin || entrada };
                        }
                    }
                }
                await esperar(360);
            }
            return { success: false, tentativas };
        }

        function montarScriptLocalizarLoginAvantPro() {
            return `
                (function () {
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var visivel = function (node) {
                        if (!node || !node.getBoundingClientRect) return false;
                        var rect = node.getBoundingClientRect();
                        var style = window.getComputedStyle ? window.getComputedStyle(node) : null;
                        return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0
                            && rect.left < (window.innerWidth || document.documentElement.clientWidth || 0)
                            && rect.top < (window.innerHeight || document.documentElement.clientHeight || 0)
                            && (!style || (style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) !== 0));
                    };
                    var queryAllDeep = function (selector, root) {
                        var found = [];
                        var seen = [];
                        var walk = function (base) {
                            if (!base || seen.indexOf(base) >= 0) return;
                            seen.push(base);
                            try {
                                if (base.querySelectorAll) {
                                    found = found.concat(Array.prototype.slice.call(base.querySelectorAll(selector)));
                                }
                            } catch (_err) {}
                            var nodes = [];
                            try {
                                nodes = base.querySelectorAll ? Array.prototype.slice.call(base.querySelectorAll('*')) : [];
                            } catch (_err2) {}
                            for (var i = 0; i < nodes.length; i += 1) {
                                if (nodes[i] && nodes[i].shadowRoot) walk(nodes[i].shadowRoot);
                            }
                        };
                        walk(root || document);
                        return found.filter(function (node, index) { return found.indexOf(node) === index; });
                    };
                    var composedHost = function (node) {
                        try {
                            var root = node && node.getRootNode ? node.getRootNode() : null;
                            return root && root.host ? root.host : null;
                        } catch (_err) {
                            return null;
                        }
                    };
                    var estaDentroDeCardProduto = function (node) {
                        try {
                            return !!(node && node.closest && node.closest('li.ui-search-layout__item, .poly-card, [class*="poly-card"], [class*="ui-search-result"], [data-testid*="result"], [data-testid*="card"]'));
                        } catch (_err) {
                            return false;
                        }
                    };
                    var textoBotao = function (node) {
                        if (!node) return '';
                        return [
                            node.innerText,
                            node.textContent,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title')
                        ].filter(Boolean).join(' ');
                    };
                    var contextoBotao = function (node) {
                        var root = null;
                        try {
                            root = node && node.closest && node.closest('form, article, section, aside, li, [role="dialog"], [class*="avant"], [id*="avant"], [class*="login"], [class*="auth"], [class*="poly-card"], [class*="ui-search-result"]');
                        } catch (_err) {}
                        var host = composedHost(node);
                        root = root || (node && node.parentElement) || node;
                        return normalizar([
                            textoBotao(node),
                            root && (root.innerText || root.textContent),
                            root && root.getAttribute && root.getAttribute('class'),
                            root && root.getAttribute && root.getAttribute('id'),
                            host && textoBotao(host),
                            host && (host.innerText || host.textContent)
                        ].filter(Boolean).join(' '));
                    };
                    var candidatos = queryAllDeep('button, a, input[type="button"], input[type="submit"], [role="button"], [tabindex], [class*="andes-button"], div, span')
                        .filter(function (node) {
                            if (!visivel(node)) return false;
                            var busca = normalizar(textoBotao(node));
                            var contexto = contextoBotao(node);
                            var alvo = busca + ' ' + contexto;
                            if (!busca) return false;
                            if (/sidemenu(login)?|avantpro-logged-out-combo-cta|btnloginmenu/.test(alvo)) return true;
                            if (estaDentroDeCardProduto(node) && !/abrir\\s+menu\\s+avantpro|avantpro-floating-button|avantpro-speed-dial-fab|speed-dial/.test(alvo)) return false;
                            if (/abrir\\s+menu\\s+avantpro|avantpro-floating-button|avantpro-speed-dial-fab/.test(alvo)) return true;
                            if (/\\bferramentas\\b|\\btools\\b/.test(alvo)
                                && /avant\\s*pro|avantpro|speed-dial|ferramentas/.test(alvo)) return true;
                            if (/vincular\\s+(?:conta|agora)|conectar\\s+conta|autorizar\\s+(?:mercado\\s+livre|conta)|permitir\\s+acesso/.test(alvo)
                                && /avant\\s*pro|avantpro|mercado\\s+livre|conta|vincul/.test(alvo)) return false;
                            if (/^login$|\\blogin\\b/.test(busca) && /avant\\s*pro|avantpro|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos|extensao/.test(contexto)) return true;
                            if (/fazer\\s+login|entrar\\s+no\\s+avant|login\\s+avant/.test(busca)) return true;
                            return false;
                        })
                        .map(function (node, index) {
                            var busca = normalizar(textoBotao(node));
                            var contexto = contextoBotao(node);
                            var rect = node.getBoundingClientRect();
                            var alvo = busca + ' ' + contexto;
                            var score = 0;
                            if (/sidemenu(login)?|avantpro-logged-out-combo-cta/.test(alvo)) score += 240;
                            if (/btnloginmenu|fazer\\s+login/.test(alvo) && /avant\\s*pro|avantpro/.test(alvo)) score += 220;
                            if (/abrir\\s+menu\\s+avantpro|avantpro-floating-button|avantpro-speed-dial-fab/.test(alvo)) score += 150;
                            if (/\\bferramentas\\b|\\btools\\b/.test(alvo)
                                && /avant\\s*pro|avantpro|speed-dial|ferramentas/.test(alvo)) {
                                score += 160;
                            }
                            if (/vincular\\s+(?:conta|agora)|conectar\\s+conta|autorizar\\s+(?:mercado\\s+livre|conta)|permitir\\s+acesso/.test(alvo)
                                && /avant\\s*pro|avantpro|mercado\\s+livre|conta|vincul/.test(alvo)) {
                                score += /vincular\\s+agora|autorizar|permitir\\s+acesso/.test(alvo) ? 8 : 6;
                            }
                            if (/^login$|\\blogin\\b/.test(busca) && /avant\\s*pro|avantpro|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos|extensao/.test(contexto)) score += 120;
                            if (/fazer\\s+login|entrar\\s+no\\s+avant|login\\s+avant/.test(busca)) score += 90;
                            if (/avant/.test(busca + ' ' + contexto)) score += 10;
                            if (rect.left < 320) score += 8;
                            return {
                                node: node,
                                index: index,
                                score: score,
                                x: rect.left + rect.width / 2,
                                y: rect.top + rect.height / 2,
                                width: rect.width,
                                height: rect.height,
                                label: textoBotao(node).replace(/\\s+/g, ' ').trim().slice(0, 120)
                            };
                        })
                        .sort(function (a, b) { return b.score - a.score || a.index - b.index; });
                    if (candidatos.length) {
                        var c = candidatos[0];
                        return {
                            success: true,
                            source: 'dom',
                            x: c.x,
                            y: c.y,
                            width: c.width,
                            height: c.height,
                            score: c.score,
                            label: c.label,
                            url: location.href
                        };
                    }
                    var pageText = normalizar(document.body && (document.body.innerText || document.body.textContent) || '');
                    var pareceLoginAvant = /comece\\s+a\\s+usar\\s+o\\s+avant\\s*pro|comece\\s+a\\s+usar\\s+o\\s+avantpro|liberar\\s+os\\s+recursos\\s+da\\s+extensao|nao\\s+possui\\s+uma\\s+conta|avantpro/.test(pageText)
                        && /\\blogin\\b/.test(pageText);
                    if (pareceLoginAvant) {
                        var loginLateral = queryAllDeep('#sideMenuLogin, .avantpro-logged-out-combo-cta, .avantpro-logged-out-modern-cta')
                            .filter(function (node) { return visivel(node) && !estaDentroDeCardProduto(node); })
                            .map(function (node, index) {
                                var rect = node.getBoundingClientRect();
                                return {
                                    node: node,
                                    index: index,
                                    x: rect.left + rect.width / 2,
                                    y: rect.top + rect.height / 2,
                                    width: rect.width,
                                    height: rect.height,
                                    label: textoBotao(node).replace(/\\s+/g, ' ').trim().slice(0, 120)
                                };
                            })[0];
                        if (loginLateral) {
                            return {
                                success: true,
                                source: 'login_lateral_avantpro',
                                x: loginLateral.x,
                                y: loginLateral.y,
                                width: loginLateral.width,
                                height: loginLateral.height,
                                score: 260,
                                label: loginLateral.label || 'Login Avant Pro',
                                url: location.href
                            };
                        }
                    }
                    return {
                        success: false,
                        reason: 'login_avant_nao_localizado_para_clique_real',
                        hasAvantText: pareceLoginAvant,
                        url: location.href
                    };
                })();
            `;
        }

        async function clicarNavegadorMlPorCoordenada(point) {
            if (!point) return { success: false, reason: 'coordenada_ausente' };
            const x = Number(point.x ?? point.left);
            const y = Number(point.y ?? point.top);
            if (!Number.isFinite(x) || !Number.isFinite(y)) {
                return { success: false, reason: 'coordenada_invalida' };
            }
            const payload = {
                x,
                y,
                clickCount: 1,
                source: point.source || '',
                label: point.label || ''
            };
            if (mlWebviewEl && mlWebviewEl.__isShellBrowserProxy && typeof mlWebviewEl.clickAt === 'function') {
                const result = await mlWebviewEl.clickAt(payload);
                return result || { success: true, x, y };
            }
            if (mlWebviewEl && typeof mlWebviewEl.sendInputEvent === 'function') {
                mlWebviewEl.sendInputEvent({ type: 'mouseMove', x, y, movementX: 0, movementY: 0 });
                mlWebviewEl.sendInputEvent({ type: 'mouseDown', x, y, button: 'left', clickCount: 1 });
                await esperar(45);
                mlWebviewEl.sendInputEvent({ type: 'mouseUp', x, y, button: 'left', clickCount: 1 });
                return { success: true, x, y };
            }
            const api = obterElectronApiFavoritosMlBrowser();
            if (api && typeof api.clickEmbeddedMlBrowser === 'function') {
                return await api.clickEmbeddedMlBrowser(payload);
            }
            return { success: false, reason: 'clique_real_indisponivel' };
        }

        function montarScriptLocalizarCampoEmailAvantProParaDigitacao() {
            return `
                (function () {
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var visivel = function (node) {
                        if (!node || !node.getBoundingClientRect) return false;
                        var rect = node.getBoundingClientRect();
                        var style = window.getComputedStyle ? window.getComputedStyle(node) : null;
                        return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0
                            && rect.left < (window.innerWidth || document.documentElement.clientWidth || 0)
                            && rect.top < (window.innerHeight || document.documentElement.clientHeight || 0)
                            && (!style || (style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) !== 0));
                    };
                    var queryAllDeep = function (selector, root) {
                        var found = [];
                        var seen = [];
                        var walk = function (base) {
                            if (!base || seen.indexOf(base) >= 0) return;
                            seen.push(base);
                            try {
                                if (base.querySelectorAll) {
                                    found = found.concat(Array.prototype.slice.call(base.querySelectorAll(selector)));
                                }
                            } catch (_err) {}
                            var nodes = [];
                            try {
                                nodes = base.querySelectorAll ? Array.prototype.slice.call(base.querySelectorAll('*')) : [];
                            } catch (_err2) {}
                            for (var i = 0; i < nodes.length; i += 1) {
                                if (nodes[i] && nodes[i].shadowRoot) walk(nodes[i].shadowRoot);
                            }
                        };
                        walk(root || document);
                        return found.filter(function (node, index) { return found.indexOf(node) === index; });
                    };
                    var textoNode = function (node) {
                        if (!node) return '';
                        return [
                            node.innerText,
                            node.textContent,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title'),
                            node.getAttribute && node.getAttribute('placeholder'),
                            node.getAttribute && node.getAttribute('class'),
                            node.getAttribute && node.getAttribute('id'),
                            node.getAttribute && node.getAttribute('src'),
                            node.getAttribute && node.getAttribute('href')
                        ].filter(Boolean).join(' ');
                    };
                    var contextoNode = function (node) {
                        var root = null;
                        try {
                            root = node && node.closest && node.closest('form, [role="dialog"], [aria-modal="true"], [class*="avant"], [id*="avant"], [class*="login"], [class*="auth"], [class*="modal"], [class*="popup"], [class*="drawer"], [class*="speed"], [class*="menu"]');
                        } catch (_err) {}
                        var host = null;
                        try {
                            var nodeRoot = node && node.getRootNode ? node.getRootNode() : null;
                            host = nodeRoot && nodeRoot.host ? nodeRoot.host : null;
                        } catch (_err2) {}
                        root = root || (node && node.parentElement) || document.body;
                        return normalizar([
                            textoNode(node),
                            root && textoNode(root),
                            root && (root.innerText || root.textContent),
                            host && textoNode(host),
                            host && (host.innerText || host.textContent)
                        ].filter(Boolean).join(' '));
                    };
                    var centro = function (node, source, yRatio) {
                        var rect = node.getBoundingClientRect();
                        var x = rect.left + rect.width / 2;
                        var y = rect.top + rect.height * (Number.isFinite(yRatio) ? yRatio : 0.5);
                        var out = {
                            success: true,
                            source: source,
                            x: Math.max(1, Math.min(Math.round(x), (window.innerWidth || document.documentElement.clientWidth || 1) - 1)),
                            y: Math.max(1, Math.min(Math.round(y), (window.innerHeight || document.documentElement.clientHeight || 1) - 1)),
                            width: rect.width,
                            height: rect.height,
                            url: location.href
                        };
                        try { window.__JK_AVANT_EMAIL_NATIVE_TARGET = out; } catch (_err) {}
                        return out;
                    };
                    var bodyText = String(document.body && (document.body.innerText || document.body.textContent) || '');
                    var bodyBusca = normalizar(bodyText);
                    if (/obrigado\\s+por\\s+usar\\s+nossa\\s+extensao|aguarde[,\\s]+a\\s+pagina\\s+sera\\s+recarregada/.test(bodyBusca)) {
                        return { success: false, confirmed: true, reason: 'login_avant_ja_confirmado', url: location.href };
                    }
                    var inputs = queryAllDeep('input:not([type="hidden"]), textarea, [contenteditable="true"], [role="textbox"]').map(function (node, index) {
                        if (!visivel(node) || node.disabled || node.readOnly) return null;
                        var attrs = normalizar([
                            node.type,
                            node.name,
                            node.id,
                            node.className,
                            node.placeholder,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('autocomplete')
                        ].join(' '));
                        var contexto = contextoNode(node);
                        var busca = attrs + ' ' + contexto + ' ' + bodyBusca;
                        if (/search|buscar|pesquisar|as_word|\\bq\\b/.test(attrs)) return null;
                        var score = 0;
                        if (node.type === 'email' || /email|e-?mail|mail/.test(busca)) score += 120;
                        if (/avant\\s*pro|avantpro|avantprocloud|extensao|credenciais|iniciar\\s+sessao/.test(busca)) score += 120;
                        if (/mercado\\s*livre|mercadolivre|mercadolibre/.test(contexto) && !/avant/.test(contexto)) score -= 120;
                        if (score < 120) return null;
                        return { node: node, score: score, index: index };
                    }).filter(Boolean).sort(function (a, b) { return b.score - a.score || a.index - b.index; });
                    if (inputs.length) return centro(inputs[0].node, 'email_dom_avantpro', 0.5);

                    var frames = queryAllDeep('iframe, webview').map(function (node, index) {
                        if (!visivel(node)) return null;
                        var contexto = contextoNode(node);
                        var score = 0;
                        if (/avant\\s*pro|avantpro|avantprocloud|auth|login|credential|extension|jdefnfmbnchmnjkcknaadaddgjbgephh/.test(contexto)) score += 160;
                        if (/ferramentas|seu\\s+e-?mail|email|credenciais|iniciar\\s+sessao/.test(bodyBusca + ' ' + contexto)) score += 80;
                        if (score < 160) return null;
                        return { node: node, score: score, index: index };
                    }).filter(Boolean).sort(function (a, b) { return b.score - a.score || a.index - b.index; });
                    if (frames.length) return centro(frames[0].node, 'email_frame_avantpro_estimado', 0.48);

                    var containers = queryAllDeep('form, [role="dialog"], [aria-modal="true"], section, aside, div[class*="avant"], div[id*="avant"], div[class*="login"], div[class*="auth"], div[class*="modal"], div[class*="popup"], div[class*="drawer"]').map(function (node, index) {
                        if (!visivel(node)) return null;
                        var rect = node.getBoundingClientRect();
                        if (rect.width < 180 || rect.height < 80) return null;
                        var contexto = contextoNode(node);
                        var score = 0;
                        if (/avant\\s*pro|avantpro|avantprocloud/.test(contexto + ' ' + bodyBusca)) score += 90;
                        if (/seu\\s+e-?mail|email|credenciais|iniciar\\s+sessao|ferramentas/.test(contexto + ' ' + bodyBusca)) score += 80;
                        if (/assine\\s+ja|suporte/.test(contexto) && !/email|credenciais|iniciar/.test(contexto)) score -= 80;
                        if (score < 120) return null;
                        return { node: node, score: score, index: index };
                    }).filter(Boolean).sort(function (a, b) { return b.score - a.score || a.index - b.index; });
                    if (containers.length) return centro(containers[0].node, 'email_container_avantpro_estimado', 0.48);

                    return {
                        success: false,
                        reason: 'campo_email_avant_nao_localizado_para_digitacao',
                        avantText: /avant\\s*pro|avantpro|avantprocloud/.test(bodyBusca),
                        ferramentasText: /\\bferramentas\\b/.test(bodyBusca),
                        url: location.href
                    };
                })();
            `;
        }

        function montarScriptLocalizarBotaoConfirmarAvantProParaClique() {
            return `
                (function () {
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var visivel = function (node) {
                        if (!node || !node.getBoundingClientRect) return false;
                        var rect = node.getBoundingClientRect();
                        var style = window.getComputedStyle ? window.getComputedStyle(node) : null;
                        return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0
                            && rect.left < (window.innerWidth || document.documentElement.clientWidth || 0)
                            && rect.top < (window.innerHeight || document.documentElement.clientHeight || 0)
                            && (!style || (style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) !== 0));
                    };
                    var queryAllDeep = function (selector, root) {
                        var found = [];
                        var seen = [];
                        var walk = function (base) {
                            if (!base || seen.indexOf(base) >= 0) return;
                            seen.push(base);
                            try {
                                if (base.querySelectorAll) {
                                    found = found.concat(Array.prototype.slice.call(base.querySelectorAll(selector)));
                                }
                            } catch (_err) {}
                            var nodes = [];
                            try {
                                nodes = base.querySelectorAll ? Array.prototype.slice.call(base.querySelectorAll('*')) : [];
                            } catch (_err2) {}
                            for (var i = 0; i < nodes.length; i += 1) {
                                if (nodes[i] && nodes[i].shadowRoot) walk(nodes[i].shadowRoot);
                            }
                        };
                        walk(root || document);
                        return found.filter(function (node, index) { return found.indexOf(node) === index; });
                    };
                    var textoNode = function (node) {
                        if (!node) return '';
                        return [
                            node.innerText,
                            node.textContent,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title'),
                            node.getAttribute && node.getAttribute('class'),
                            node.getAttribute && node.getAttribute('id')
                        ].filter(Boolean).join(' ');
                    };
                    var contextoNode = function (node) {
                        var root = null;
                        try {
                            root = node && node.closest && node.closest('form, [role="dialog"], [aria-modal="true"], [class*="avant"], [id*="avant"], [class*="login"], [class*="auth"], [class*="modal"], [class*="popup"], [class*="drawer"]');
                        } catch (_err) {}
                        root = root || (node && node.parentElement) || document.body;
                        return normalizar([textoNode(node), root && textoNode(root), root && (root.innerText || root.textContent)].filter(Boolean).join(' '));
                    };
                    var bodyBusca = normalizar(String(document.body && (document.body.innerText || document.body.textContent) || ''));
                    var botoes = queryAllDeep('button, input[type="button"], input[type="submit"], [role="button"], [tabindex], a').map(function (node, index) {
                        if (!visivel(node)) return null;
                        var texto = normalizar(textoNode(node));
                        var contexto = contextoNode(node);
                        var alvo = texto + ' ' + contexto + ' ' + bodyBusca;
                        if (/assine\\s+ja|assinar|suporte|compras|favoritos|categorias|ofertas/.test(texto)) return null;
                        var score = 0;
                        if (/confirmar|entrar|acessar|login|iniciar|continuar|enviar|comecar|começar/.test(texto)) score += 120;
                        if (/avant\\s*pro|avantpro|avantprocloud|credenciais|email|e-?mail|extensao/.test(alvo)) score += 80;
                        if (score < 120) return null;
                        return { node: node, score: score, index: index };
                    }).filter(Boolean).sort(function (a, b) { return b.score - a.score || a.index - b.index; });
                    if (botoes.length) {
                        var rect = botoes[0].node.getBoundingClientRect();
                        return {
                            success: true,
                            source: 'confirmar_dom_avantpro',
                            x: Math.max(1, Math.min(Math.round(rect.left + rect.width / 2), (window.innerWidth || document.documentElement.clientWidth || 1) - 1)),
                            y: Math.max(1, Math.min(Math.round(rect.top + rect.height / 2), (window.innerHeight || document.documentElement.clientHeight || 1) - 1)),
                            width: rect.width,
                            height: rect.height,
                            url: location.href
                        };
                    }
                    var alvoEmail = null;
                    try { alvoEmail = window.__JK_AVANT_EMAIL_NATIVE_TARGET || null; } catch (_err3) {}
                    if (alvoEmail && Number.isFinite(Number(alvoEmail.x)) && Number.isFinite(Number(alvoEmail.y))) {
                        return {
                            success: true,
                            source: 'confirmar_estimado_apos_email',
                            x: Math.max(1, Math.min(Math.round(Number(alvoEmail.x)), (window.innerWidth || document.documentElement.clientWidth || 1) - 1)),
                            y: Math.max(1, Math.min(Math.round(Number(alvoEmail.y) + 72), (window.innerHeight || document.documentElement.clientHeight || 1) - 1)),
                            url: location.href
                        };
                    }
                    return { success: false, reason: 'botao_confirmar_avant_nao_localizado', url: location.href };
                })();
            `;
        }

        async function digitarTextoNativoNoNavegadorMl(text, opcoes = {}) {
            const payload = {
                text: String(text || ''),
                clearFirst: opcoes.clearFirst !== false,
                pressEnter: opcoes.pressEnter === true
            };
            if (mlWebviewEl && mlWebviewEl.__isShellBrowserProxy && typeof mlWebviewEl.typeText === 'function') {
                return await mlWebviewEl.typeText(payload);
            }
            if (mlWebviewEl && typeof mlWebviewEl.sendInputEvent === 'function') {
                const tapKey = async (keyCode, modifiers = []) => {
                    mlWebviewEl.sendInputEvent({ type: 'keyDown', keyCode, modifiers });
                    await esperar(25);
                    mlWebviewEl.sendInputEvent({ type: 'keyUp', keyCode, modifiers });
                };
                try { if (typeof mlWebviewEl.focus === 'function') mlWebviewEl.focus(); } catch (_err) {}
                if (payload.clearFirst) {
                    await tapKey('A', ['control']);
                    await esperar(20);
                    await tapKey('Backspace');
                }
                if (payload.text) {
                    if (typeof mlWebviewEl.insertText === 'function') {
                        await Promise.resolve(mlWebviewEl.insertText(payload.text));
                    } else {
                        for (const ch of payload.text) {
                            mlWebviewEl.sendInputEvent({ type: 'char', keyCode: ch });
                            await esperar(2);
                        }
                    }
                }
                if (payload.pressEnter) {
                    await esperar(80);
                    await tapKey('Enter');
                }
                return { success: true, typed: payload.text.length, enter: payload.pressEnter };
            }
            const api = obterElectronApiFavoritosMlBrowser();
            if (api && typeof api.typeEmbeddedMlBrowser === 'function') {
                return await api.typeEmbeddedMlBrowser(payload);
            }
            return { success: false, reason: 'digitacao_real_indisponivel' };
        }

        async function tentarLoginAvantProPorElectronNativo(opcoes = {}) {
            const email = typeof AVANT_PRO_LOGIN_EMAIL !== 'undefined' ? String(AVANT_PRO_LOGIN_EMAIL || '').trim() : '';
            const termo = String(opcoes.termo || '').trim();
            if (!email) return { success: false, reason: 'email_avant_indisponivel' };
            const api = obterElectronApiFavoritosMlBrowser();
            if (!api || typeof api.loginAvantProEmbeddedBrowser !== 'function') {
                return { success: false, reason: 'login_avant_electron_indisponivel' };
            }
            mostrarStatusConexaoAvantPro('preenchendo o e-mail pelo Electron', { termo });
            const resultado = await api.loginAvantProEmbeddedBrowser(email).catch((err) => ({
                success: false,
                reason: 'erro_no_login_avant_electron',
                error: err && err.message ? err.message : String(err)
            }));
            await esperar(Number(opcoes.esperaAposConfirmarMs) || 900);
            mostrarStatusConexaoAvantPro('aguardando a mensagem de agradecimento', { termo });
            const confirmado = typeof aguardarConfirmacaoLoginAvantProNoWebview === 'function'
                ? await aguardarConfirmacaoLoginAvantProNoWebview({
                    timeoutMs: Number(opcoes.timeoutConfirmacaoMs) || 6500,
                    pollMs: Number(opcoes.pollMs) || 350,
                    tentarPreencherEmail: false
                }).catch(() => null)
                : await detectarConfirmacaoLoginAvantProNoWebview().catch(() => null);
            if (confirmado && (confirmado.confirmado || confirmado.confirmed)) {
                registrarLoginAvantProConfirmadoFavoritos();
            }
            return {
                ...(resultado || {}),
                success: !!(resultado && resultado.success),
                confirmed: !!(confirmado && (confirmado.confirmado || confirmado.confirmed)),
                confirmacao: confirmado || null,
                via: 'electron_native_login'
            };
        }

        async function tentarLoginAvantProPorDigitacaoNativa(opcoes = {}) {
            const email = typeof AVANT_PRO_LOGIN_EMAIL !== 'undefined' ? String(AVANT_PRO_LOGIN_EMAIL || '').trim() : '';
            if (!email) return { success: false, reason: 'email_avant_indisponivel' };
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') {
                return { success: false, reason: 'navegador_indisponivel' };
            }
            const termo = String(opcoes.termo || '').trim();
            mostrarStatusConexaoAvantPro('localizando o campo de e-mail', { termo });
            const alvo = await mlWebviewEl.executeJavaScript(montarScriptLocalizarCampoEmailAvantProParaDigitacao(), true).catch((err) => ({
                success: false,
                reason: 'erro_ao_localizar_campo_avant_para_digitacao',
                error: err && err.message ? err.message : String(err)
            }));
            if (alvo && alvo.confirmed) {
                registrarLoginAvantProConfirmadoFavoritos();
                return { success: true, confirmed: true, alvo };
            }
            if (!(alvo && alvo.success)) return alvo || { success: false, reason: 'campo_email_avant_nao_localizado_para_digitacao' };
            const clickCampo = await clicarNavegadorMlPorCoordenada(alvo).catch((err) => ({
                success: false,
                reason: 'erro_no_clique_real_campo_email_avant',
                error: err && err.message ? err.message : String(err)
            }));
            if (!(clickCampo && clickCampo.success !== false)) {
                return { success: false, reason: 'falha_ao_focar_campo_email_avant', alvo, clickCampo };
            }
            await esperar(160);
            mostrarStatusConexaoAvantPro('digitando o e-mail do Avant Pro', { termo });
            const digitacao = await digitarTextoNativoNoNavegadorMl(email, {
                clearFirst: true,
                pressEnter: false
            }).catch((err) => ({
                success: false,
                reason: 'erro_ao_digitar_email_avant',
                error: err && err.message ? err.message : String(err)
            }));
            if (!(digitacao && digitacao.success !== false)) {
                return { success: false, reason: 'falha_ao_digitar_email_avant', alvo, clickCampo, digitacao };
            }
            await esperar(Number(opcoes.esperaAntesConfirmarMs) || 220);
            mostrarStatusConexaoAvantPro('confirmando o e-mail', { termo });
            const botao = await mlWebviewEl.executeJavaScript(montarScriptLocalizarBotaoConfirmarAvantProParaClique(), true).catch((err) => ({
                success: false,
                reason: 'erro_ao_localizar_confirmacao_avant',
                error: err && err.message ? err.message : String(err)
            }));
            let confirmar = null;
            if (botao && botao.success) {
                confirmar = await clicarNavegadorMlPorCoordenada(botao).catch((err) => ({
                    success: false,
                    reason: 'erro_no_clique_real_confirmacao_avant',
                    error: err && err.message ? err.message : String(err)
                }));
            }
            if (!(confirmar && confirmar.success !== false)) {
                confirmar = await digitarTextoNativoNoNavegadorMl('', {
                    clearFirst: false,
                    pressEnter: true
                }).catch((err) => ({
                    success: false,
                    reason: 'erro_ao_confirmar_avant_por_enter',
                    error: err && err.message ? err.message : String(err)
                }));
            }
            await esperar(Number(opcoes.esperaAposConfirmarMs) || 600);
            mostrarStatusConexaoAvantPro('aguardando a mensagem de agradecimento', { termo });
            const confirmado = await detectarConfirmacaoLoginAvantProNoWebview().catch(() => null);
            if (confirmado && confirmado.confirmado) {
                registrarLoginAvantProConfirmadoFavoritos();
            }
            return {
                success: true,
                alvo,
                clickCampo,
                digitacao,
                botao,
                confirmar,
                confirmed: !!(confirmado && confirmado.confirmado),
                confirmacao: confirmado || null
            };
        }

        async function clicarLoginAvantProPorCoordenada() {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') {
                return { success: false, reason: 'navegador_indisponivel' };
            }
            const alvo = await mlWebviewEl.executeJavaScript(montarScriptLocalizarLoginAvantPro(), true).catch((err) => ({
                success: false,
                reason: 'erro_ao_localizar_login_avant',
                error: err && err.message ? err.message : String(err)
            }));
            if (!(alvo && alvo.success)) return alvo || { success: false, reason: 'login_avant_nao_localizado' };
            const click = await clicarNavegadorMlPorCoordenada(alvo).catch((err) => ({
                success: false,
                reason: 'erro_no_clique_real',
                error: err && err.message ? err.message : String(err)
            }));
            return {
                success: !!(click && click.success !== false),
                target: alvo,
                click
            };
        }

        async function fecharModalBloqueanteAvantProNoWebview(opcoes = {}) {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') {
                return { success: false, closed: false, reason: 'navegador_indisponivel' };
            }
            const fecharLoginReal = opcoes.fecharLoginReal === true;
            return await mlWebviewEl.executeJavaScript(`
                (async function () {
                    var sleep = function (ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); };
                    var fecharLoginReal = ${fecharLoginReal ? 'true' : 'false'};
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var visivel = function (node) {
                        if (!node || !node.getBoundingClientRect) return false;
                        var rect = node.getBoundingClientRect();
                        var style = window.getComputedStyle ? window.getComputedStyle(node) : null;
                        return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0
                            && rect.left < (window.innerWidth || document.documentElement.clientWidth || 0)
                            && rect.top < (window.innerHeight || document.documentElement.clientHeight || 0)
                            && (!style || (style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) !== 0));
                    };
                    var queryAllDeep = function (selector, root) {
                        var found = [];
                        var seen = [];
                        var walk = function (base) {
                            if (!base || seen.indexOf(base) >= 0) return;
                            seen.push(base);
                            try {
                                if (base.querySelectorAll) {
                                    found = found.concat(Array.prototype.slice.call(base.querySelectorAll(selector)));
                                }
                            } catch (_err) {}
                            var nodes = [];
                            try {
                                nodes = base.querySelectorAll ? Array.prototype.slice.call(base.querySelectorAll('*')) : [];
                            } catch (_err2) {}
                            for (var i = 0; i < nodes.length; i += 1) {
                                if (nodes[i] && nodes[i].shadowRoot) walk(nodes[i].shadowRoot);
                            }
                        };
                        walk(root || document);
                        return found.filter(function (node, index) { return found.indexOf(node) === index; });
                    };
                    var textoNode = function (node) {
                        if (!node) return '';
                        return [
                            node.innerText,
                            node.textContent,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title'),
                            node.getAttribute && node.getAttribute('class'),
                            node.getAttribute && node.getAttribute('id')
                        ].filter(Boolean).join(' ');
                    };
                    var raizContexto = function (node) {
                        var root = null;
                        try {
                            root = node && node.closest && node.closest('form, article, section, aside, li, [role="dialog"], [aria-modal="true"], [class*="modal"], [class*="popup"], [class*="drawer"], [class*="avant"], [id*="avant"], [class*="login"], [class*="auth"]');
                        } catch (_err) {}
                        return root || (node && node.parentElement) || document.body;
                    };
                    var contextoNode = function (node) {
                        var root = raizContexto(node);
                        var host = null;
                        try {
                            var nodeRoot = node && node.getRootNode ? node.getRootNode() : null;
                            host = nodeRoot && nodeRoot.host ? nodeRoot.host : null;
                        } catch (_err) {}
                        return normalizar([
                            textoNode(node),
                            root && textoNode(root),
                            root && (root.innerText || root.textContent),
                            host && textoNode(host),
                            host && (host.innerText || host.textContent)
                        ].filter(Boolean).join(' '));
                    };
                    var contarRotulosDadosAvant = function (value) {
                        var busca = normalizar(value);
                        var padroes = [
                            /informacoes?\\s+avant(?:\\s*pro|pro)?/,
                            /vendas?\\s+do\\s+(?:produto|anuncio|item)/,
                            /vendas?\\s+estimad/,
                            /ritmo\\s+atual/,
                            /visitas\\s+do\\s+anuncio/,
                            /participacao\\b/,
                            /\\bmarca\\b/,
                            /faturamento\\s+do\\s+produto/,
                            /nome\\s+do\\s+vendedor/,
                            /localizacao\\s+do\\s+vendedor/,
                            /anuncio\\s+(?:ganhador\\s+)?criado\\s+em/,
                            /comissao\\b/
                        ];
                        return padroes.reduce(function (total, regex) {
                            return total + (regex.test(busca) ? 1 : 0);
                        }, 0);
                    };
                    var temDadosAvant = function (value) {
                        return contarRotulosDadosAvant(value) >= 2;
                    };
                    var bodyText = String(document.body && (document.body.innerText || document.body.textContent) || '');
                    var bodyBusca = normalizar(bodyText);
                    var cardSelectorsAvant = 'li.ui-search-layout__item, .poly-card, [class*="poly-card"], [class*="ui-search-result"], [data-testid*="result"], [data-testid*="card"], [class*="shops__layout-item"]';
                    var cardCountAvant = 0;
                    try { cardCountAvant = queryAllDeep(cardSelectorsAvant).length; } catch (_cardCountErr) {}
                    var textoEscopoGlobalAvant = function () {
                        var partes = [];
                        var vistos = [];
                        queryAllDeep('body, main, header, aside, section, div, form, [role="dialog"], [aria-modal="true"], [class*="modal"], [class*="popup"], [class*="drawer"], [class*="avant"], [id*="avant"]').slice(0, 900).forEach(function (node) {
                            try {
                                if (node !== document.body && node.closest && node.closest(cardSelectorsAvant)) return;
                                var text = textoNode(node) || node.innerText || node.textContent || '';
                                text = String(text || '').replace(/\\s+/g, ' ').trim();
                                if (!text || vistos.indexOf(text) >= 0) return;
                                vistos.push(text);
                                partes.push(text);
                            } catch (_err) {}
                        });
                        return partes.join(' ');
                    };
                    var globalBusca = normalizar(textoEscopoGlobalAvant());
                    var escopoAvant = cardCountAvant > 0 ? globalBusca : bodyBusca;
                    var modalAvantPromocional = /avant\\s*pro|avantpro|avantprocloud/.test(escopoAvant)
                        && /vincule\\s+o\\s+avantpro|vincular\\s+agora|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos|use\\s+gratis|usar\\s+gratis|dica\\s+avantpro|tutoriais/.test(escopoAvant);
                    var loginRealVisivel = /avant\\s*pro|avantpro|avantprocloud/.test(escopoAvant)
                        && /iniciar\\s+sessao|insira\\s+suas\\s+credenciais|seu\\s+e-?mail|seu\\s+email/.test(escopoAvant);
                    if (loginRealVisivel && !modalAvantPromocional && !fecharLoginReal) {
                        return { success: true, closed: false, reason: 'login_real_visivel', url: location.href };
                    }
                    if (!modalAvantPromocional && !(fecharLoginReal && loginRealVisivel)) {
                        return { success: true, closed: false, reason: 'modal_avant_bloqueante_nao_detectado', url: location.href };
                    }
                    var modalRaizes = queryAllDeep('div, section, article, aside, [role="dialog"], [aria-modal="true"], [class*="modal"], [class*="popup"], [class*="drawer"], [class*="avant"], [id*="avant"]').filter(function (node) {
                        if (!visivel(node)) return false;
                        var texto = normalizar(node.innerText || node.textContent || '');
                        if (!/avant\\s*pro|avantpro|avantprocloud|vincule\\s+o\\s+avantpro/.test(texto)) return false;
                        if (!/vincule\\s+o\\s+avantpro|vincular\\s+agora|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos|use\\s+gratis|usar\\s+gratis/.test(texto)) return false;
                        var rect = node.getBoundingClientRect();
                        return rect.width >= 220 && rect.height >= 140;
                    }).map(function (node, index) {
                        var rect = node.getBoundingClientRect();
                        return { node: node, index: index, area: rect.width * rect.height, rect: rect };
                    }).sort(function (a, b) {
                        var aDialog = a.node && a.node.matches && a.node.matches('[role="dialog"], [aria-modal="true"], [class*="modal"], [class*="popup"], [class*="drawer"]') ? 1 : 0;
                        var bDialog = b.node && b.node.matches && b.node.matches('[role="dialog"], [aria-modal="true"], [class*="modal"], [class*="popup"], [class*="drawer"]') ? 1 : 0;
                        return bDialog - aDialog || b.area - a.area || a.index - b.index;
                    });
                    var modalPrincipal = modalRaizes.length ? modalRaizes[0].node : document.body;
                    var modalPrincipalRect = modalRaizes.length ? modalRaizes[0].rect : null;
                    var candidatos = [];
                    queryAllDeep('button, a, [role="button"], [aria-label], [title], [class*="close"], [class*="Close"], [class*="fechar"]').forEach(function (node, index) {
                        var alvo = node;
                        try {
                            alvo = node.closest && node.closest('button, a, [role="button"]') || node;
                        } catch (_err) {}
                        if (!alvo || !visivel(alvo)) return;
                        var root = raizContexto(alvo);
                        var contexto = contextoNode(alvo);
                        var label = normalizar(textoNode(alvo));
                        var rect = alvo.getBoundingClientRect();
                        var rootRect = root && root.getBoundingClientRect ? root.getBoundingClientRect() : null;
                        var refRect = modalPrincipalRect || rootRect;
                        var centroX = rect.left + rect.width / 2;
                        var centroY = rect.top + rect.height / 2;
                        var dentroModalPrincipal = !!(modalPrincipal === document.body || (modalPrincipal.contains && modalPrincipal.contains(alvo)) || (refRect
                            && centroX >= refRect.left
                            && centroX <= refRect.right
                            && centroY >= refRect.top
                            && centroY <= refRect.bottom));
                        if (!dentroModalPrincipal && !/avant\\s*pro|avantpro|avantprocloud|vincule\\s+o\\s+avantpro|comece\\s+a\\s+usar/.test(contexto)) return;
                        var labelFecha = /(^|\\b)(fechar|close|dismiss|cancelar|agora\\s+nao|depois)(\\b|$)|^(x|×)$/.test(label);
                        if (!labelFecha && label.charCodeAt(0) === 215) labelFecha = true;
                        var geometriaFecha = !!(refRect && rect.width <= 80 && rect.height <= 80
                            && rect.left >= refRect.right - 120
                            && rect.top <= refRect.top + 120);
                        if (!labelFecha && !geometriaFecha) return;
                        var score = 0;
                        if (labelFecha) score += 40;
                        if (geometriaFecha) score += 25;
                        if (dentroModalPrincipal) score += 20;
                        if (/close|fechar/.test(label)) score += 15;
                        if (/vincule\\s+o\\s+avantpro|vincular\\s+agora|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos/.test(contexto)) score += 15;
                        candidatos.push({
                            node: alvo,
                            index: index,
                            score: score,
                            label: String(alvo.innerText || alvo.textContent || alvo.getAttribute && (alvo.getAttribute('aria-label') || alvo.getAttribute('title')) || '').replace(/\\s+/g, ' ').trim().slice(0, 120)
                        });
                    });
                    candidatos.sort(function (a, b) { return b.score - a.score || a.index - b.index; });
                    if (!candidatos.length) {
                        if (modalPrincipalRect) {
                            var x = Math.max(0, Math.min((window.innerWidth || document.documentElement.clientWidth || 0) - 1, modalPrincipalRect.right - 42));
                            var y = Math.max(0, Math.min((window.innerHeight || document.documentElement.clientHeight || 0) - 1, modalPrincipalRect.top + 46));
                            var alvoPonto = document.elementFromPoint ? document.elementFromPoint(x, y) : null;
                            if (alvoPonto) {
                                var alvoClique = alvoPonto.closest && alvoPonto.closest('button, a, [role="button"], [aria-label], [title]') || alvoPonto;
                                var optsPonto = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y };
                                try { alvoClique.dispatchEvent(new MouseEvent('mousedown', optsPonto)); } catch (_pDownErr) {}
                                try { alvoClique.dispatchEvent(new MouseEvent('mouseup', optsPonto)); } catch (_pUpErr) {}
                                try { alvoClique.dispatchEvent(new MouseEvent('click', optsPonto)); } catch (_pClickErr) {}
                                try { alvoClique.click(); } catch (_pDirectErr) {}
                                await sleep(120);
                                window.__JK_AVANT_PRO_CLOSED_MODAL_AT = Date.now();
                                return { success: true, closed: true, via: 'top_right_point', reason: 'botao_fechar_avant_por_geometria', modalAvantPromocional: modalAvantPromocional, loginRealVisivel: loginRealVisivel, url: location.href };
                            }
                        }
                        try { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true, cancelable: true })); } catch (_escDownErr) {}
                        try { document.dispatchEvent(new KeyboardEvent('keyup', { key: 'Escape', code: 'Escape', bubbles: true, cancelable: true })); } catch (_escUpErr) {}
                        try { window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true, cancelable: true })); } catch (_winEscErr) {}
                        window.__JK_AVANT_PRO_CLOSED_MODAL_AT = Date.now();
                        return { success: true, closed: true, via: 'escape', reason: 'botao_fechar_avant_nao_encontrado', modalAvantPromocional: modalAvantPromocional, loginRealVisivel: loginRealVisivel, url: location.href };
                    }
                    var escolhido = candidatos[0].node;
                    try { escolhido.scrollIntoView({ block: 'center', inline: 'center' }); } catch (_scrollErr) {}
                    await sleep(80);
                    var rect = escolhido.getBoundingClientRect ? escolhido.getBoundingClientRect() : null;
                    var opts = rect ? { bubbles: true, cancelable: true, view: window, clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 } : { bubbles: true, cancelable: true, view: window };
                    try { escolhido.dispatchEvent(new MouseEvent('mousedown', opts)); } catch (_downErr) {}
                    try { escolhido.dispatchEvent(new MouseEvent('mouseup', opts)); } catch (_upErr) {}
                    try { escolhido.dispatchEvent(new MouseEvent('click', opts)); } catch (_clickErr) {}
                    try { escolhido.click(); } catch (_directErr) {}
                    window.__JK_AVANT_PRO_CLOSED_MODAL_AT = Date.now();
                    return { success: true, closed: true, label: candidatos[0].label, url: location.href };
                })();
            `, true).catch((err) => ({
                success: false,
                closed: false,
                reason: 'erro_ao_fechar_modal_avant',
                error: err && err.message ? err.message : String(err)
            }));
        }

        async function abrirLoginAvantProNoWebview() {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') {
                return { success: false, reason: 'navegador_indisponivel' };
            }
            if (typeof autorizarLoginAvantProTemporario === 'function') {
                autorizarLoginAvantProTemporario(180000);
            }
            await mlWebviewEl.executeJavaScript(`
                (function () {
                    window.__JK_AVANT_PRO_LOGIN_CLICKED_AT = Date.now();
                    return true;
                })();
            `, true).catch(() => false);
            await fecharModalBloqueanteAvantProNoWebview({ fecharLoginReal: false }).catch(() => null);
            const resultado = await mlWebviewEl.executeJavaScript(`
                (async function () {
                    var sleep = function (ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); };
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var visivel = function (node) {
                        if (!node || !node.getBoundingClientRect) return false;
                        var rect = node.getBoundingClientRect();
                        var style = window.getComputedStyle ? window.getComputedStyle(node) : null;
                        return rect.width > 0 && rect.height > 0 && (!style || (style.display !== 'none' && style.visibility !== 'hidden'));
                    };
                    var queryAllDeep = function (selector, root) {
                        var found = [];
                        var seen = [];
                        var walk = function (base) {
                            if (!base || seen.indexOf(base) >= 0) return;
                            seen.push(base);
                            try {
                                if (base.querySelectorAll) {
                                    found = found.concat(Array.prototype.slice.call(base.querySelectorAll(selector)));
                                }
                            } catch (_err) {}
                            var nodes = [];
                            try {
                                nodes = base.querySelectorAll ? Array.prototype.slice.call(base.querySelectorAll('*')) : [];
                            } catch (_err2) {}
                            for (var i = 0; i < nodes.length; i += 1) {
                                if (nodes[i] && nodes[i].shadowRoot) walk(nodes[i].shadowRoot);
                            }
                        };
                        walk(root || document);
                        return found.filter(function (node, index) { return found.indexOf(node) === index; });
                    };
                    var composedHost = function (node) {
                        try {
                            var root = node && node.getRootNode ? node.getRootNode() : null;
                            return root && root.host ? root.host : null;
                        } catch (_err) {
                            return null;
                        }
                    };
                    var estaDentroDeCardProduto = function (node) {
                        try {
                            return !!(node && node.closest && node.closest('li.ui-search-layout__item, .poly-card, [class*="poly-card"], [class*="ui-search-result"], [data-testid*="result"], [data-testid*="card"]'));
                        } catch (_err) {
                            return false;
                        }
                    };
                    var textoBotao = function (node) {
                        if (!node) return '';
                        return [
                            node.innerText,
                            node.textContent,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title'),
                            node.getAttribute && node.getAttribute('href')
                        ].filter(Boolean).join(' ');
                    };
                    var contextoBotao = function (node) {
                        var root = null;
                        try {
                            root = node && node.closest && node.closest('form, article, section, aside, li, [role="dialog"], [class*="avant"], [id*="avant"], [class*="poly-card"], [class*="ui-search-result"], [class*="modal"], [class*="login"], [class*="auth"]');
                        } catch (_err) {}
                        var host = composedHost(node);
                        root = root || (node && node.parentElement) || node;
                        return normalizar([
                            textoBotao(node),
                            root && (root.innerText || root.textContent),
                            root && root.getAttribute && root.getAttribute('class'),
                            root && root.getAttribute && root.getAttribute('id'),
                            host && textoBotao(host),
                            host && (host.innerText || host.textContent)
                        ].filter(Boolean).join(' '));
                    };
                    var emailAvantVisivelAtual = function () {
                        return queryAllDeep('input:not([type="hidden"])').some(function (input) {
                            if (!visivel(input) || input.disabled || input.readOnly) return false;
                            var attrs = normalizar([
                                input.type,
                                input.name,
                                input.id,
                                input.className,
                                input.placeholder,
                                input.getAttribute && input.getAttribute('aria-label'),
                                input.getAttribute && input.getAttribute('autocomplete')
                            ].join(' '));
                            var contexto = contextoBotao(input);
                            return /email|e-?mail|mail/.test(attrs + ' ' + contexto)
                                && /avant\\s*pro|avantpro|iniciar\\s+sessao|credenciais|seu\\s+e-?mail/.test(contexto);
                        });
                    };
                    if (emailAvantVisivelAtual()) {
                        return { success: true, clicked: false, reason: 'campo_email_avant_visivel', url: location.href };
                    }
                    var clicarElemento = async function (node) {
                        if (!node) return false;
                        try { node.scrollIntoView({ block: 'center', inline: 'center' }); } catch (_scrollErr) {}
                        await sleep(120);
                        var rect = node.getBoundingClientRect ? node.getBoundingClientRect() : null;
                        var opts = rect ? { bubbles: true, cancelable: true, view: window, clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 } : { bubbles: true, cancelable: true, view: window };
                        try { node.dispatchEvent(new MouseEvent('mouseover', opts)); } catch (_overErr) {}
                        try { node.dispatchEvent(new MouseEvent('mousedown', opts)); } catch (_downErr) {}
                        try { node.dispatchEvent(new MouseEvent('mouseup', opts)); } catch (_upErr) {}
                        try { node.dispatchEvent(new MouseEvent('click', opts)); } catch (_clickErr) {}
                        try { node.click(); } catch (_directErr) {}
                        return true;
                    };
                    var botaoMenuAvant = queryAllDeep('.avantpro-menu, .avantpro-menu-surface, .avantpro-menu-icon, .avantpro-menu-icon-html, button, a, [role="button"], [tabindex], [aria-label], [title]')
                        .filter(function (node) {
                            if (!visivel(node)) return false;
                            if (estaDentroDeCardProduto(node)) return false;
                            var busca = normalizar(textoBotao(node) + ' ' + (node.getAttribute && (node.getAttribute('class') || '') || '') + ' ' + contextoBotao(node));
                            return /avantpro-menu|abrir\\s+menu\\s+avantpro|avantpro-floating-button|avantpro-speed-dial-fab/.test(busca);
                        })
                        .map(function (node, index) {
                            var rect = node.getBoundingClientRect ? node.getBoundingClientRect() : null;
                            return { node: node, index: index, area: rect ? rect.width * rect.height : 0 };
                        })
                        .sort(function (a, b) { return b.area - a.area || a.index - b.index; })[0];
                    if (botaoMenuAvant) {
                        await clicarElemento(botaoMenuAvant.node);
                        await sleep(500);
                    }
                    var botaoLoginGlobalAvant = queryAllDeep('#sideMenuLogin, #btnLoginMenu, .avantpro-logged-out-combo-cta, .avantpro-logged-out-modern-cta, button, a, input[type="button"], input[type="submit"], [role="button"], [tabindex], [aria-label], [title], [class*="andes-button"], div, span')
                        .filter(function (node) {
                            if (!visivel(node)) return false;
                            if (estaDentroDeCardProduto(node)) return false;
                            var busca = normalizar(textoBotao(node));
                            var contexto = contextoBotao(node);
                            var alvo = busca + ' ' + contexto + ' ' + normalizar(node.getAttribute && (node.getAttribute('class') || '') || '') + ' ' + normalizar(node.id || '');
                            if (/sidemenu(login)?|avantpro-logged-out-combo-cta|btnloginmenu/.test(alvo)) return true;
                            if (/^login$|\\blogin\\b|fazer\\s+login|entrar\\s+no\\s+avant|login\\s+avant/.test(busca)
                                && /avant\\s*pro|avantpro|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos|extensao/.test(alvo)) return true;
                            return false;
                        })
                        .map(function (node, index) {
                            var rect = node.getBoundingClientRect ? node.getBoundingClientRect() : null;
                            var alvo = normalizar(textoBotao(node) + ' ' + contextoBotao(node) + ' ' + (node.id || '') + ' ' + (node.getAttribute && (node.getAttribute('class') || '') || ''));
                            var score = 0;
                            if (/sidemenu(login)?|avantpro-logged-out-combo-cta/.test(alvo)) score += 240;
                            if (/btnloginmenu|fazer\\s+login/.test(alvo)) score += 220;
                            if (rect && rect.left < 320) score += 20;
                            return { node: node, index: index, score: score, area: rect ? rect.width * rect.height : 0, text: textoBotao(node).replace(/\\s+/g, ' ').trim().slice(0, 120) };
                        })
                        .sort(function (a, b) { return b.score - a.score || b.area - a.area || a.index - b.index; })[0];
                    if (botaoLoginGlobalAvant) {
                        window.__JK_AVANT_PRO_CLICKED_AT = Date.now();
                        await clicarElemento(botaoLoginGlobalAvant.node);
                        await sleep(1300);
                        return { success: true, clicked: true, label: botaoLoginGlobalAvant.text || 'Login Avant Pro', reason: 'login_global_avant_clicado', emailVisible: emailAvantVisivelAtual(), url: location.href };
                    }
                    var botaoFerramentasAvant = queryAllDeep('button, a, input[type="button"], input[type="submit"], [role="button"], [tabindex], [aria-label], [title], [class*="andes-button"], div, span')
                        .filter(function (node) {
                            if (!visivel(node)) return false;
                            if (estaDentroDeCardProduto(node)) return false;
                            var busca = normalizar(textoBotao(node));
                            var contexto = contextoBotao(node);
                            var alvo = busca + ' ' + contexto + ' ' + normalizar(node.getAttribute && (node.getAttribute('class') || '') || '');
                            return /\\bferramentas\\b|\\btools\\b/.test(alvo)
                                && /avant\\s*pro|avantpro|speed-dial|ferramentas/.test(alvo);
                        })
                        .map(function (node, index) {
                            var rect = node.getBoundingClientRect ? node.getBoundingClientRect() : null;
                            return { node: node, index: index, area: rect ? rect.width * rect.height : 0, text: textoBotao(node).replace(/\\s+/g, ' ').trim().slice(0, 120) };
                        })
                        .sort(function (a, b) { return b.area - a.area || a.index - b.index; })[0];
                    if (botaoFerramentasAvant) {
                        window.__JK_AVANT_PRO_CLICKED_AT = Date.now();
                        await clicarElemento(botaoFerramentasAvant.node);
                        await sleep(1200);
                        return { success: true, clicked: true, label: botaoFerramentasAvant.text || 'Ferramentas', reason: 'ferramentas_avant_clicada', emailVisible: emailAvantVisivelAtual(), url: location.href };
                    }
                    var candidatos = queryAllDeep('button, a, input[type="button"], input[type="submit"], [role="button"], [tabindex], [class*="andes-button"], div, span')
                        .filter(function (node) {
                            if (!visivel(node)) return false;
                            if (estaDentroDeCardProduto(node)) return false;
                            var busca = normalizar(textoBotao(node));
                            var contexto = contextoBotao(node);
                            var alvo = busca + ' ' + contexto + ' ' + normalizar(node.id || '') + ' ' + normalizar(node.getAttribute && (node.getAttribute('class') || '') || '');
                            if (!busca) return false;
                            if (/sidemenu(login)?|avantpro-logged-out-combo-cta|btnloginmenu/.test(alvo)) return true;
                            if (/\\bferramentas\\b|\\btools\\b/.test(alvo)
                                && /avant\\s*pro|avantpro|speed-dial|ferramentas/.test(alvo)) return true;
                            if (/^login$|\\blogin\\b/.test(busca) && /avant\\s*pro|avantpro|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos|extensao/.test(contexto)) return true;
                            if (/fazer\\s+login|entrar\\s+no\\s+avant|login\\s+avant/.test(busca)) return true;
                            return false;
                        })
                        .map(function (node, index) {
                            var busca = normalizar(textoBotao(node));
                            var contexto = contextoBotao(node);
                            var alvo = busca + ' ' + contexto + ' ' + normalizar(node.id || '') + ' ' + normalizar(node.getAttribute && (node.getAttribute('class') || '') || '');
                            var score = 0;
                            if (/sidemenu(login)?|avantpro-logged-out-combo-cta/.test(alvo)) score += 240;
                            if (/btnloginmenu|fazer\\s+login/.test(alvo) && /avant\\s*pro|avantpro/.test(alvo)) score += 220;
                            if (/\\bferramentas\\b|\\btools\\b/.test(alvo)
                                && /avant\\s*pro|avantpro|speed-dial|ferramentas/.test(alvo)) {
                                score += 180;
                            }
                            if (/^login$|\\blogin\\b/.test(busca) && /avant\\s*pro|avantpro|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos|extensao/.test(contexto)) score += 120;
                            if (/fazer\\s+login|entrar\\s+no\\s+avant|login\\s+avant/.test(busca)) score += 90;
                            if (/avant/.test(busca + ' ' + contexto)) score += 8;
                            return { node: node, index: index, score: score, text: textoBotao(node).replace(/\\s+/g, ' ').trim().slice(0, 120) };
                        })
                        .sort(function (a, b) { return b.score - a.score || a.index - b.index; });
                    if (!candidatos.length) {
                        return { success: false, reason: 'botao_login_avant_nao_encontrado', url: location.href };
                    }
                    var escolhido = candidatos[0];
                    window.__JK_AVANT_PRO_CLICKED_AT = Date.now();
                    await clicarElemento(escolhido.node);
                    await sleep(900);
                    return { success: true, clicked: true, label: escolhido.text, emailVisible: emailAvantVisivelAtual(), url: location.href };
                })();
            `, true).catch((err) => ({
                success: false,
                reason: 'erro_ao_clicar_login_avant',
                error: err && err.message ? err.message : String(err)
            }));
            let autoLogin = await promiseComTimeout(
                tentarLoginAvantProNoWebview(mlWebviewEl, {
                    timeoutMs: 3200,
                    atrasos: [0, 140, 320, 650, 1100, 1800, 2800]
                }),
                3600,
                'Aguardando abertura do login Avant Pro.'
            ).catch(() => null);
            let fallbackClick = null;
            let ferramentasClick = null;
            if (!(autoLogin && autoLogin.success)) {
                ferramentasClick = await clicarFerramentasAvantProPorCoordenada().catch((err) => ({
                    success: false,
                    reason: 'erro_no_fallback_clique_ferramentas_avant',
                    error: err && err.message ? err.message : String(err)
                }));
                if (ferramentasClick && ferramentasClick.success) {
                    await esperar(900);
                    autoLogin = await promiseComTimeout(
                        tentarLoginAvantProNoWebview(mlWebviewEl, {
                            timeoutMs: 4200,
                            atrasos: [0, 180, 420, 850, 1500, 2600, 3800]
                        }),
                        4600,
                        'Aguardando formulario do Avant Pro apos clicar em Ferramentas.'
                    ).catch(() => autoLogin);
                }
            }
            if (!(autoLogin && autoLogin.success)) {
                fallbackClick = await clicarLoginAvantProPorCoordenada().catch((err) => ({
                    success: false,
                    reason: 'erro_no_fallback_clique_login_avant',
                    error: err && err.message ? err.message : String(err)
                }));
                if (fallbackClick && fallbackClick.success) {
                    await esperar(280);
                    autoLogin = await promiseComTimeout(
                        tentarLoginAvantProNoWebview(mlWebviewEl, {
                            timeoutMs: 3600,
                            atrasos: [0, 180, 420, 850, 1500, 2400, 3400]
                        }),
                        4000,
                        'Aguardando iframe de login Avant Pro.'
                    ).catch(() => autoLogin);
                }
            }
            return {
                ...(resultado || {}),
                success: !!((resultado && resultado.success) || (ferramentasClick && ferramentasClick.success) || (fallbackClick && fallbackClick.success) || (autoLogin && autoLogin.success)),
                autoLogin,
                ferramentasClick,
                fallbackClick
            };
        }

        function mostrarAcaoConectarAvantPro(status = {}) {
            if (!mlFavoritosBalloonActionsEl) {
                mostrarBalaoFavoritosStatus('Avant Pro nao retornou dados coletaveis. Confirme manualmente o login no navegador interno.', {
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
                mostrarBalaoFavoritosStatus('Abrindo login do Avant Pro no navegador interno...', {
                    manterAcoes: true,
                    manterNavegadorVisivel: true,
                    larga: true,
                    titulo: 'Conectar Avant Pro'
                });
                const resultado = await abrirLoginAvantProNoWebview();
                const mensagem = resultado && resultado.success
                    ? 'Conclua o login do Avant Pro no navegador interno. Depois clique em Tentar novamente.'
                    : 'Nao encontrei o botao de login do Avant Pro. Abra Ferramentas > Fazer Login no navegador interno e depois tente novamente.';
                mostrarBalaoFavoritosStatus(mensagem, {
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
                mostrarBalaoFavoritosStatus('Verificando dados do Avant Pro...', {
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
                    esconderBalaoFavoritosStatus();
                } else {
                    mostrarAcaoConectarAvantPro(novoStatus || status);
                }
                tentarNovamente.disabled = false;
            });

            const fechar = document.createElement('button');
            fechar.type = 'button';
            fechar.textContent = 'Fechar';
            fechar.addEventListener('click', esconderBalaoFavoritosStatus);

            mlFavoritosBalloonActionsEl.appendChild(conectar);
            mlFavoritosBalloonActionsEl.appendChild(tentarNovamente);
            mlFavoritosBalloonActionsEl.appendChild(fechar);
            mostrarBalaoFavoritosStatus('Avant Pro nao retornou dados coletaveis. Confirme manualmente se o login esta pronto no navegador interno.', {
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
            return await mlWebviewEl.executeJavaScript(`
                (function () {
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var texto = normalizar([
                        document.title,
                        location.href,
                        document.body && (document.body.innerText || document.body.textContent)
                    ].filter(Boolean).join(' '));
                    var confirmado = /obrigado\\s+por\\s+usar\\s+nossa\\s+extensao|aguarde[,\\s]+a\\s+pagina\\s+sera\\s+recarregada/.test(texto);
                    return {
                        confirmado: confirmado,
                        url: location.href,
                        title: document.title || '',
                        texto: confirmado ? texto.slice(0, 260) : ''
                    };
                })();
            `, true).catch((err) => ({
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

        async function entrarAvantProPorFerramentasAntesPesquisa(opcoes = {}) {
            const termo = String(opcoes.termo || '').trim();
            const contexto = termo ? ` para "${termo}"` : '';
            mostrarBalaoFavoritosStatus(`Conectando Avant Pro${contexto}: abrindo bolinha e Ferramentas...`, {
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
            const digitacaoNativaTentativas = [];

            mostrarBalaoFavoritosStatus(
                ferramentas && ferramentas.success
                    ? `Conectando Avant Pro${contexto}: preenchendo e-mail e confirmando...`
                    : `Conectando Avant Pro${contexto}: Ferramentas nao abriu; procurando a tela de login...`,
                {
                manterNavegadorVisivel: true,
                larga: true,
                titulo: 'Conectar Avant Pro'
                }
            );
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
                mostrarBalaoFavoritosStatus(`Conectando Avant Pro${contexto}: tentando preencher pela tela visivel...`, {
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
                mostrarBalaoFavoritosStatus(`Avant Pro confirmou o login${contexto}. Iniciando a pesquisa...`, {
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
            mostrarBalaoFavoritosStatus(`Preparando Avant Pro${contexto} antes da pesquisa...`, {
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
                mostrarBalaoFavoritosStatus(`Avant Pro nao retornou dados coletaveis${contexto}. Confirme manualmente o login no navegador interno.`, {
                    larga: true,
                    titulo: 'Avant Pro sem dados'
                });
                status = await tentarAguardarSemReload();
                status = await recarregarAposLoginSePreciso(status);
                if (statusAvantProTemDadosColetaveis(status)) return normalizarStatusAvantProPronto(status);
            }

            if (!statusAvantProTemDadosColetaveis(status)) {
                mostrarBalaoFavoritosStatus(`Verificando Avant Pro${contexto} antes de coletar os dados...`, {
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
                    mostrarBalaoFavoritosStatus(`Avant Pro nao retornou dados coletaveis${contexto}. Confirme manualmente o login no navegador interno.`, {
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

        async function extrairAnunciosWebviewVisivel(opcoes = {}) {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return null;
            if (opcoes.clicarAvant) {
                const ignorarLoginAvant = opcoes.ignorarLoginAvant === true;
                let statusAvant = await diagnosticarAvantProNoWebview().catch(() => null);
                const fechamentoAvant = await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
                if (fechamentoAvant && fechamentoAvant.closed) {
                    await esperar(360);
                    statusAvant = await diagnosticarAvantProNoWebview().catch(() => statusAvant);
                }
                const temDadosAvant = statusAvantProTemDadosColetaveis(statusAvant);
                const deveAcionarAvant = !temDadosAvant;
                if (deveAcionarAvant) {
                    const loginAvantNecessario = !!(statusAvant && (
                        statusAvant.needsAccountLink
                        || statusAvant.accountActionRequired
                        || statusAvant.avantLoginDialog
                        || statusAvant.avantLoginEmailInputs > 0
                    ));
                    let autoLogin = null;
                    if (loginAvantNecessario) {
                        if (statusAvant && statusAvant.modalAvantPromocional) {
                            await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
                        } else if (!ignorarLoginAvant) {
                            const resultadoLogin = await abrirLoginAvantProNoWebview().catch(() => null);
                            autoLogin = resultadoLogin && resultadoLogin.autoLogin;
                        }
                    } else {
                        await acionarControlesAvantProNoWebview({
                            forceClick: false,
                            permitirFerramentas: opcoes.permitirFerramentasAvant !== false,
                            clicarCardsSemDados: opcoes.clicarCardsSemDados !== false,
                            maxClicks: opcoes.maxCliquesAvant || 12
                        }).catch(() => 0);
                        await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
                        if (opcoes.permitirAutoLoginAvant !== false) {
                            autoLogin = await tentarLoginAvantProNoWebview(mlWebviewEl).catch(() => null);
                        }
                    }
                    const esperaClique = Number(opcoes.aguardarAposCliqueAvant);
                    const esperaPadrao = autoLogin && autoLogin.success ? 360 : 260;
                    await esperar(Number.isFinite(esperaClique) ? Math.max(esperaPadrao, esperaClique) : esperaPadrao);
                }
                if (opcoes.aguardarEstabilidadeAvant && (deveAcionarAvant || temDadosAvant)) {
                    const estabilidade = opcoes.aguardarEstabilidadeAvant === true ? {} : opcoes.aguardarEstabilidadeAvant;
                    await aguardarDadosAvantProEstaveisWebview(estabilidade).catch(() => null);
                }
                await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
            }
            await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
            await mlWebviewEl.executeJavaScript(`window.__JK_ML_FAST_LINKS = ${opcoes.fastLinks ? 'true' : 'false'};`, true).catch(() => null);
            const maxFastDom = Math.max(20, Math.min(Number(opcoes.maxFastDom) || Number(opcoes.maxAnuncios) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80, 100));
            await mlWebviewEl.executeJavaScript(`window.__JK_ML_FAST_DOM_MAX = ${JSON.stringify(maxFastDom)};`, true).catch(() => null);
            const resultadoCompleto = await promiseComTimeout(
                mlWebviewEl.executeJavaScript(ML_WEBVIEW_EXTRACT_SCRIPT, true),
                opcoes.timeoutMs || 6000,
                'Tempo limite ao extrair os links do quadro interno.'
            );
            if (!resultadoCompleto || opcoes.fastLinks === true) return resultadoCompleto;
            const avantDom = await extrairAnunciosAvantProDomWebview({ limite: maxFastDom }).catch(() => null);
            if (avantDom && Array.isArray(avantDom.anuncios) && avantDom.anuncios.length) {
                resultadoCompleto.anuncios = mesclarAnunciosAvant(
                    Array.isArray(resultadoCompleto.anuncios) ? resultadoCompleto.anuncios : [],
                    avantDom.anuncios
                );
                resultadoCompleto.debug = {
                    ...(resultadoCompleto.debug || {}),
                    avantDom: avantDom.debug || null,
                    mode: 'complete_avant_dom_merged'
                };
            }
            const anunciosCompletos = Array.isArray(resultadoCompleto.anuncios) ? resultadoCompleto.anuncios : [];
            const totalComVendas = anunciosCompletos.filter(item => {
                const vendas = Number(item && item.vendas);
                return Number.isFinite(vendas) && vendas >= 0;
            }).length;
            if (anunciosCompletos.length && totalComVendas === 0) {
                console.info('Extracao completa sem vendas Avant; tentando leitura rapida dos cards visiveis');
                const rapido = await extrairAnunciosWebviewFastDom({ maxFastDom }).catch(() => null);
                if (rapido && Array.isArray(rapido.anuncios) && rapido.anuncios.length) {
                    return {
                        ...resultadoCompleto,
                        anuncios: mesclarAnunciosAvant(resultadoCompleto.anuncios, rapido.anuncios),
                        debug: { ...(resultadoCompleto.debug || {}), fastDom: rapido.debug || null, mode: 'complete_fast_dom_merged' }
                    };
                }
            } else if (anunciosCompletos.length && totalComVendas < Math.min(anunciosCompletos.length, Math.ceil(maxFastDom * 0.7))) {
                console.info('Extracao completa parcial; tentando leitura rapida dos cards visiveis');
                const rapido = await extrairAnunciosWebviewFastDom({ maxFastDom }).catch(() => null);
                if (rapido && Array.isArray(rapido.anuncios) && rapido.anuncios.length) {
                    return {
                        ...resultadoCompleto,
                        anuncios: mesclarAnunciosAvant(resultadoCompleto.anuncios, rapido.anuncios),
                        debug: { ...(resultadoCompleto.debug || {}), fastDom: rapido.debug || null, mode: 'complete_fast_dom_merged' }
                    };
                }
            }
            return resultadoCompleto;
        }

        function resolverWebviewFavoritosColeta(opcoes = {}) {
            const informado = opcoes && typeof opcoes === 'object' ? opcoes.webview : null;
            return informado || mlWebviewEl || null;
        }

        async function extrairAnunciosWebviewFastDom(opcoes = {}) {
            const webview = resolverWebviewFavoritosColeta(opcoes);
            if (!webview || typeof webview.executeJavaScript !== 'function') return { success: false, total: 0, anuncios: [] };
            const maxFastDom = Math.max(20, Math.min(Number(opcoes.maxFastDom) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80, 100));
            return await webview.executeJavaScript(`
                (function () {
                    var maxFastDom = Number(window.__JK_ML_FAST_DOM_MAX || ${JSON.stringify(maxFastDom)}) || ${JSON.stringify(maxFastDom)};
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var parseHumanNumber = function (value, suffix) {
                        var raw = String(value || '').replace(/\\s+/g, '').replace(/\\./g, '').replace(',', '.');
                        var numero = Number(raw);
                        if (!Number.isFinite(numero)) return null;
                        suffix = String(suffix || '').toLowerCase();
                        if (suffix === 'mil' || suffix === 'k') numero *= 1000;
                        return Math.round(numero);
                    };
                    var valorNoTexto = function (card, regex) {
                        var text = String(card && (card.innerText || card.textContent) || '').replace(/\\s+/g, ' ').trim();
                        var match = text.match(regex);
                        return match && match[1] ? parseHumanNumber(match[1], match[2]) : null;
                    };
                    var cleanUrl = function (href) {
                        href = String(href || '').split('#')[0].trim();
                        if (/^\\/\\//.test(href)) return 'https:' + href;
                        if (/^\\//.test(href)) return 'https://www.mercadolivre.com.br' + href;
                        return href;
                    };
                    var extrairId = function (value) {
                        var text = String(value || '');
                        try { text = decodeURIComponent(text); } catch (_err) {}
                        var match = text.match(/\\bMLB-?(\\d{6,})\\b/i) || text.match(/[?&](?:wid|item_id)=(MLB\\d{6,})/i);
                        if (!match) return '';
                        var raw = String(match[1] || '').replace('-', '').toUpperCase();
                        return raw.indexOf('MLB') === 0 ? raw : ('MLB' + raw);
                    };
                    var isProductUrl = function (href) {
                        var url = cleanUrl(href);
                        return /(?:produto\\.mercadolivre\\.com\\.br\\/MLB-\\d+|\\/MLB-\\d+|\\/p\\/MLB\\d+|\\/up\\/MLB|item_id(?:=|%3A)MLB\\d+|wid=MLB\\d+)/i.test(url);
                    };
                    var tituloDe = function (card) {
                        var el = card && card.querySelector && card.querySelector('a.poly-component__title, .poly-component__title, .ui-search-item__title, h2, h3, a[href]');
                        return String((el && (el.innerText || el.textContent || el.getAttribute && (el.getAttribute('title') || el.getAttribute('aria-label')))) || card && (card.innerText || card.textContent) || '')
                            .replace(/\\s+/g, ' ')
                            .trim()
                            .slice(0, 240);
                    };
                    var hrefDe = function (card) {
                        var links = Array.prototype.slice.call(card.querySelectorAll ? card.querySelectorAll('a[href]') : []);
                        for (var i = 0; i < links.length; i += 1) {
                            var rawHref = links[i].href || links[i].getAttribute('href') || '';
                            if (isProductUrl(rawHref)) return rawHref;
                        }
                        return '';
                    };
                    var selectors = [
                        'li.ui-search-layout__item',
                        'div.ui-search-result__wrapper',
                        'div.ui-search-result',
                        'div.poly-card',
                        'section.poly-card',
                        'article.poly-card',
                        'article.ui-search-result',
                        '[data-testid="product-card"]',
                        '[data-testid="item-card"]',
                        '[class*="product-card"]',
                        '[class*="poly-card"]',
                        '[class*="ui-search-layout__item"]',
                        '[class*="shops__layout-item"]',
                        'main ol > li',
                        'main ul > li'
                    ].join(',');
                    var cards = Array.prototype.slice.call(document.querySelectorAll(selectors));
                    var vistos = {};
                    var anuncios = [];
                    for (var i = 0; i < cards.length && anuncios.length < maxFastDom; i += 1) {
                        var card = cards[i];
                        var href = hrefDe(card);
                        var id = extrairId(href || card.outerHTML || '');
                        var titulo = tituloDe(card);
                        if (!href && !id) continue;
                        var key = id || href || normalizar(titulo);
                        if (!key || vistos[key]) continue;
                        vistos[key] = true;
                        var vendasProduto = valorNoTexto(card, /vendas?\\s+do\\s+produto\\s+[:\\-]?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i);
                        var vendasEstimadas = valorNoTexto(card, /vendas?\\s+estimad[ao]s?\\s*[:\\-]?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i);
                        var ritmoAtual = valorNoTexto(card, /ritmo\\s+atual(?:\\s*\\(vendas\\/mes\\))?\\s*[:\\-]?\\s*(?:\\+\\s*)?([\\d\\.,]+)\\s*(mil|k)?/i);
                        var vendas = vendasProduto !== null && vendasProduto !== undefined ? vendasProduto : (vendasEstimadas !== null && vendasEstimadas !== undefined ? vendasEstimadas : ritmoAtual);
                        anuncios.push({
                            posicao: anuncios.length + 1,
                            id: id,
                            url: href,
                            titulo: titulo,
                            vendas: vendas,
                            vendasFonte: vendas !== null && vendas !== undefined ? 'avantpro_fast_dom' : '',
                            vendas_fonte: vendas !== null && vendas !== undefined ? 'avantpro_fast_dom' : '',
                            origem_dados: 'avantpro_fast_dom'
                        });
                    }
                    return {
                        success: true,
                        total: anuncios.length,
                        anuncios: anuncios,
                        debug: { mode: 'fast_dom', maxFastDom: maxFastDom, url: location.href, title: document.title || '' }
                    };
                })();
            `, true).catch((err) => ({
                success: false,
                total: 0,
                anuncios: [],
                error: err && err.message ? err.message : String(err)
            }));
        }

        async function extrairAnunciosAvantProDomWebview(opcoes = {}) {
            const webview = resolverWebviewFavoritosColeta(opcoes);
            if (!webview || typeof webview.executeJavaScript !== 'function') return { success: false, total: 0, anuncios: [] };
            const limite = Math.max(1, Math.min(Number(opcoes.limite) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80, 120));
            return await webview.executeJavaScript(`
                (function () {
                    var limite = ${JSON.stringify(limite)};
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var queryAllDeep = function (selector, root) {
                        var found = [];
                        var visited = [];
                        var visit = function (base) {
                            if (!base || visited.indexOf(base) >= 0) return;
                            visited.push(base);
                            try {
                                if (base.querySelectorAll) {
                                    Array.prototype.slice.call(base.querySelectorAll(selector)).forEach(function (node) {
                                        if (found.indexOf(node) < 0) found.push(node);
                                    });
                                    Array.prototype.slice.call(base.querySelectorAll('*')).forEach(function (node) {
                                        if (node && node.shadowRoot) visit(node.shadowRoot);
                                    });
                                }
                            } catch (_err) {}
                        };
                        visit(root || document);
                        return found;
                    };
                    var cleanUrl = function (href) {
                        href = String(href || '').split('#')[0].trim();
                        if (!href) return '';
                        if (/^\\/\\//.test(href)) return 'https:' + href;
                        if (/^\\//.test(href)) return 'https://www.mercadolivre.com.br' + href;
                        return href;
                    };
                    var extrairId = function (value) {
                        var text = String(value || '');
                        try { text = decodeURIComponent(text); } catch (_err) {}
                        var match = text.match(/\\bMLB-?(\\d{6,})\\b/i) || text.match(/[?&](?:wid|item_id)=(MLB\\d{6,})/i);
                        if (!match) return '';
                        var raw = String(match[1] || '').replace('-', '').toUpperCase();
                        return raw.indexOf('MLB') === 0 ? raw : ('MLB' + raw);
                    };
                    var cleanProductUrl = function (href, id) {
                        href = cleanUrl(href);
                        if (!href && id) return 'https://produto.mercadolivre.com.br/' + String(id).replace('MLB', 'MLB-');
                        if (!href) return '';
                        try {
                            var parsed = new URL(href, 'https://www.mercadolivre.com.br');
                            parsed.hash = '';
                            [
                                'tracking_id',
                                'position',
                                'polycard_client',
                                'sid',
                                'searchVariation',
                                'backend_model',
                                'backend_type',
                                'client',
                                'reco_item_pos',
                                'reco_backend',
                                'reco_backend_type',
                                'reco_client',
                                'reco_id',
                                'c_id',
                                'pdp_filters',
                                'picker_url',
                                'quantity',
                                'variation',
                                'loader',
                                'noIndex'
                            ].forEach(function (param) { parsed.searchParams.delete(param); });
                            parsed.pathname = parsed.pathname.replace(/\\/+$/, '');
                            return parsed.toString().replace(/[?&]$/, '');
                        } catch (_err) {
                            return href;
                        }
                    };
                    var isProductUrl = function (href) {
                        var url = cleanUrl(href);
                        if (!url || url.toLowerCase().indexOf('mercadolivre.com.br') < 0) return false;
                        var hasExplicitItemSignal = /\\bMLB-?\\d{6,}\\b/i.test(url)
                            || /[?&](?:wid|item_id)=MLB\\d{6,}/i.test(url)
                            || /\\/p\\/MLB/i.test(url)
                            || /\\/up\\/MLB[A-Z0-9]*/i.test(url)
                            || /produto\\.mercadolivre\\.com\\.br/i.test(url);
                        if (/https?:\\/\\/lista\\.mercadolivre\\.com\\.br\\//i.test(url)) return hasExplicitItemSignal;
                        if (/\\/(?:ajuda|ofertas|cupons|categorias|supermercado|moda|mercado-play|vender|contato|compras|favoritos|login|registration|cart|publicidade|navigation|perfil|stores?|loja|post-purchase)\\b/i.test(url)) return false;
                        return hasExplicitItemSignal;
                    };
                    var tituloDoHref = function (href) {
                        var text = String(href || '');
                        try { text = decodeURIComponent(text); } catch (_err) {}
                        var match = text.match(/\\/MLB-?\\d+-([^?#]+?)(?:-_?JM|_JM|$)/i)
                            || text.match(/mercadolivre\\.com\\.br\\/([^/?#]+?)\\/up\\/MLB[A-Z0-9]+/i)
                            || text.match(/\\/([^/?#]+?)\\/up\\/MLB[A-Z0-9]+/i);
                        if (!match || !match[1]) return '';
                        return String(match[1])
                            .replace(/[-_]+/g, ' ')
                            .replace(/\\bJM\\b/ig, ' ')
                            .replace(/\\s+/g, ' ')
                            .trim()
                            .slice(0, 240);
                    };
                    var tituloFraco = function (value) {
                        var text = normalizar(value);
                        if (!text) return true;
                        if (/^(jm|novo|usado|patrocinado|mais vendido|r\\$|frete|chegar|vendid|mercado livre|favoritos|compras|produto relacionado|opcoes de compra)$/i.test(text)) return true;
                        if (/^mlb\\d+$/i.test(text.replace(/-/g, ''))) return true;
                        return text.length <= 3;
                    };
                    var parseHumanNumber = function (value, suffix, decimal) {
                        if (value === null || value === undefined) return null;
                        var raw = String(value).trim().toLowerCase();
                        if (!raw) return null;
                        var normalized = raw.replace(/\\s+/g, '');
                        if (normalized.indexOf('.') >= 0 && normalized.indexOf(',') >= 0) {
                            normalized = normalized.lastIndexOf('.') > normalized.lastIndexOf(',')
                                ? normalized.replace(/,/g, '')
                                : normalized.replace(/\\./g, '').replace(/,/g, '.');
                        } else if (normalized.indexOf(',') >= 0) {
                            normalized = normalized.replace(/\\./g, '').replace(/,/g, '.');
                        }
                        var parsed = parseFloat(normalized);
                        if (!isFinite(parsed)) return null;
                        suffix = String(suffix || '').toLowerCase();
                        if (suffix === 'mil' || suffix === 'k') parsed *= 1000;
                        return decimal ? parsed : Math.round(parsed);
                    };
                    var numeroNoTexto = function (text, regex, decimal) {
                        var match = String(text || '').match(regex);
                        return match && match[1] ? parseHumanNumber(match[1], match[2], decimal) : null;
                    };
                    var parsePrecoTexto = function (valor) {
                        var match = String(valor || '').match(/R\\$\\s*([0-9.]+)(?:\\s*,\\s*([0-9]{1,2}))?/);
                        if (!match) return null;
                        var inteiro = String(match[1] || '').replace(/\\./g, '');
                        var cents = String(match[2] || '0').padEnd(2, '0').slice(0, 2);
                        var value = Number(inteiro + '.' + cents);
                        return Number.isFinite(value) ? value : null;
                    };
                    var nodeDentroAvant = function (node) {
                        var atual = node;
                        for (var i = 0; atual && i < 8; i += 1) {
                            var cls = String(atual.className || '');
                            var id = String(atual.id || '');
                            if (/avant|created-time-card|product-info-row|faturamento|comissao|frete/i.test(cls + ' ' + id)) return true;
                            atual = atual.parentElement;
                        }
                        return false;
                    };
                    var textoPrecoIndesejado = function (node) {
                        var texto = String(node && (node.innerText || node.textContent) || '').replace(/\\s+/g, ' ');
                        var parent = node && node.parentElement ? String(node.parentElement.innerText || node.parentElement.textContent || '').replace(/\\s+/g, ' ') : texto;
                        return /frete|comiss[aã]o|faturamento|taxa|categoria|total\\s+de\\s+vendas|vendas\\s+do\\s+produto|ritmo|visitas/i.test(texto + ' ' + parent);
                    };
                    var parsePrecoNode = function (node) {
                        if (!node || nodeDentroAvant(node) || textoPrecoIndesejado(node)) return null;
                        var fractionNode = node.querySelector && node.querySelector('.andes-money-amount__fraction, .price-tag-fraction, [class*="fraction"]');
                        var centsNode = node.querySelector && node.querySelector('.andes-money-amount__cents, .price-tag-cents, [class*="cents"], [class*="decimal"]');
                        var fraction = fractionNode ? String(fractionNode.innerText || fractionNode.textContent || '').replace(/[^0-9.]/g, '') : '';
                        var textNode = String(node.innerText || node.textContent || '');
                        var cents = centsNode ? String(centsNode.innerText || centsNode.textContent || '').replace(/[^0-9]/g, '') : '';
                        if (!cents) {
                            var centsMatch = textNode.match(/[,.]\\s*(\\d{1,2})\\s*$/);
                            cents = centsMatch && centsMatch[1] ? centsMatch[1] : '';
                        }
                        if (fraction) {
                            var inteiro = fraction.replace(/\\./g, '');
                            var centavos = cents ? cents.padEnd(2, '0').slice(0, 2) : '00';
                            var parsed = Number(inteiro + '.' + centavos);
                            return Number.isFinite(parsed) ? parsed : null;
                        }
                        return parsePrecoTexto(textNode);
                    };
                    var isPrecoOriginalNode = function (node) {
                        var atual = node;
                        for (var i = 0; atual && i < 5; i += 1) {
                            var info = String(atual.className || '') + ' ' + String(atual.getAttribute && (atual.getAttribute('aria-label') || atual.getAttribute('role') || '') || '');
                            if (/previous|original|old|strikethrough|discount|antes|tachado/i.test(info)) return true;
                            atual = atual.parentElement;
                        }
                        return false;
                    };
                    var isPrecoPrincipalNode = function (node) {
                        var atual = node;
                        for (var i = 0; atual && i < 5; i += 1) {
                            var cls = String(atual.className || '');
                            if (/poly-price__current|ui-search-price__second-line/i.test(cls)) return true;
                            atual = atual.parentElement;
                        }
                        return false;
                    };
                    var isPrecoSecundarioNode = function (node) {
                        var atual = node;
                        for (var i = 0; atual && i < 5; i += 1) {
                            var info = String(atual.className || '') + ' ' + String(atual.innerText || atual.textContent || '');
                            if (/installments|parcel|per[_-]?quantity|price-per-quantity|levando\\s+\\d+\\s+ou\\s+mais|\\b\\d+x\\s*r\\$/i.test(info)) return true;
                            atual = atual.parentElement;
                        }
                        return false;
                    };
                    var precosDe = function (root) {
                        var selectorsPreco = [
                            '.poly-price__current .andes-money-amount',
                            '.poly-component__price .andes-money-amount',
                            '.ui-search-price__second-line .andes-money-amount',
                            '[class*="price"] .andes-money-amount',
                            '.andes-money-amount',
                            '.price-tag'
                        ].join(',');
                        var precoPrincipalDireto = Array.prototype.slice.call(root && root.querySelectorAll ? root.querySelectorAll('.poly-price__current .andes-money-amount, .ui-search-price__second-line .andes-money-amount') : [])
                            .map(function (node) { return parsePrecoTexto(node && (node.innerText || node.textContent)); })
                            .filter(function (value) { return value !== null && value > 0; });
                        var nodes = Array.prototype.slice.call(root && root.querySelectorAll ? root.querySelectorAll(selectorsPreco) : [])
                            .filter(function (node) { return !nodeDentroAvant(node) && !textoPrecoIndesejado(node); });
                        var atuaisPrincipais = [];
                        var atuais = [];
                        var originais = [];
                        nodes.forEach(function (node) {
                            var value = parsePrecoNode(node);
                            if (value === null || value <= 0) return;
                            if (isPrecoOriginalNode(node)) originais.push(value);
                            else if (isPrecoPrincipalNode(node)) atuaisPrincipais.push(value);
                            else if (isPrecoSecundarioNode(node)) return;
                            else atuais.push(value);
                        });
                        var precoAtual = precoPrincipalDireto.length ? precoPrincipalDireto[0] : (atuaisPrincipais.length ? atuaisPrincipais[0] : (atuais.length ? atuais[0] : null));
                        var precoOriginal = originais.length ? originais[0] : '';
                        if (precoAtual === null) {
                            var direto = String(root && (root.innerText || root.textContent) || '').split(/frete|comiss[aã]o|faturamento|taxa|total\\s+de\\s+vendas/i)[0];
                            precoAtual = parsePrecoTexto(direto);
                        }
                        if (precoAtual === null) return { preco: null, preco_original: '', preco_promocional: '', moeda: '' };
                        if (precoOriginal && precoOriginal > precoAtual) {
                            return {
                                preco: precoAtual,
                                preco_original: precoOriginal,
                                preco_promocional: precoAtual,
                                moeda: 'BRL'
                            };
                        }
                        return { preco: precoAtual, preco_original: '', preco_promocional: '', moeda: 'BRL' };
                    };
                    var imagemValida = function (url) {
                        var text = String(url || '').trim();
                        if (!text || /^data:/i.test(text)) return false;
                        if (!/^https?:\\/\\//i.test(text) && !/^\\/\\//.test(text)) return false;
                        return !/(logo|avatar|sprite|icon|favicon|badge|medal|avantpro|meliplus)/i.test(text);
                    };
                    var imagemDe = function (root) {
                        var imgs = Array.prototype.slice.call(root && root.querySelectorAll ? root.querySelectorAll('img, source[srcset], source[data-srcset]') : []);
                        var candidatos = imgs.map(function (img, index) {
                            var srcset = img.getAttribute && (img.getAttribute('srcset') || img.getAttribute('data-srcset'));
                            var srcsetUrl = '';
                            if (srcset) {
                                srcsetUrl = String(srcset).split(',').map(function (part) {
                                    return part.trim().split(/\\s+/)[0] || '';
                                }).filter(imagemValida)[0] || '';
                            }
                            var url = srcsetUrl
                                || String(img.currentSrc || '')
                                || String(img.src || '')
                                || String(img.getAttribute && (img.getAttribute('src') || img.getAttribute('data-src') || img.getAttribute('data-original') || img.getAttribute('data-lazy') || '') || '');
                            var rect = null;
                            try { rect = img.getBoundingClientRect && img.getBoundingClientRect(); } catch (_err) {}
                            var area = rect ? Math.max(0, rect.width) * Math.max(0, rect.height) : 0;
                            return { url: url, area: area, index: index };
                        }).filter(function (item) { return imagemValida(item.url); });
                        candidatos.sort(function (a, b) { return b.area - a.area || a.index - b.index; });
                        return candidatos[0] && candidatos[0].url || '';
                    };
                    var cardSelectors = [
                        'li.ui-search-layout__item',
                        'div.ui-search-result__wrapper',
                        'div.ui-search-result',
                        'div.poly-card',
                        'section.poly-card',
                        'article.poly-card',
                        'article.ui-search-result',
                        '[data-testid="product-card"]',
                        '[data-testid="item-card"]',
                        '[class*="product-card"]',
                        '[class*="poly-card"]',
                        '[class*="shops__layout-item"]',
                        'main ol > li',
                        'main ul > li',
                        'li',
                        'article',
                        'section'
                    ].join(',');
                    var linksProdutoDe = function (root) {
                        var links = Array.prototype.slice.call(root && root.querySelectorAll ? root.querySelectorAll('a[href]') : []);
                        var out = [];
                        for (var i = 0; i < links.length; i += 1) {
                            var rawHref = links[i].href || links[i].getAttribute('href') || '';
                            if (isProductUrl(rawHref)) out.push(rawHref);
                        }
                        return out;
                    };
                    var linkProdutoDe = function (root) {
                        return linksProdutoDe(root)[0] || '';
                    };
                    var chavesProdutoDe = function (root) {
                        var vistos = {};
                        return linksProdutoDe(root).map(function (href) {
                            var id = extrairId(href);
                            return id ? ('mlb:' + id) : ('link:' + cleanProductUrl(href, '').toLowerCase());
                        }).filter(function (key) {
                            if (!key || vistos[key]) return false;
                            vistos[key] = true;
                            return true;
                        });
                    };
                    var rootGenericoDemais = function (node) {
                        var tag = String(node && node.tagName || '').toUpperCase();
                        return !node || node === document.body || node === document.documentElement || /^(HTML|BODY|MAIN|OL|UL)$/.test(tag);
                    };
                    var rootProdutoSeguro = function (root) {
                        return !!(root && !rootGenericoDemais(root) && chavesProdutoDe(root).length === 1);
                    };
                    var tituloDe = function (root, href) {
                        var candidatos = []
                            .concat(Array.prototype.slice.call(root && root.querySelectorAll ? root.querySelectorAll('h2, h3, [class*="title"], [class*="name"], [class*="poly-component__title"], [class*="ui-search-item__title"], [data-testid*="title"], a[href]') : []));
                        candidatos.push(root);
                        for (var i = 0; i < candidatos.length; i += 1) {
                            var el = candidatos[i];
                            var text = String((el && (
                                el.getAttribute && (el.getAttribute('title') || el.getAttribute('aria-label')) ||
                                el.innerText ||
                                el.textContent
                            )) || '').replace(/\\s+/g, ' ').trim();
                            if (text.length >= 8 && !tituloFraco(text)) return text.slice(0, 240);
                        }
                        return tituloDoHref(href);
                    };
                    var nearestProductLink = function (node) {
                        var links = queryAllDeep('a[href]').filter(function (link) {
                            return isProductUrl(link.href || link.getAttribute('href') || '');
                        });
                        var nr = null;
                        try { nr = node.getBoundingClientRect && node.getBoundingClientRect(); } catch (_err) {}
                        if (!nr) return '';
                        var best = null;
                        links.forEach(function (link) {
                            var lr = null;
                            try { lr = link.getBoundingClientRect && link.getBoundingClientRect(); } catch (_err) {}
                            if (!lr || !lr.width || !lr.height) return;
                            var dy = Math.abs(((lr.top + lr.bottom) / 2) - ((nr.top + nr.bottom) / 2));
                            var dx = Math.abs(((lr.left + lr.right) / 2) - ((nr.left + nr.right) / 2));
                            var score = dy + dx * 0.35;
                            if (!best || score < best.score) best = { href: link.href || link.getAttribute('href') || '', score: score };
                        });
                        return best && best.href || '';
                    };
                    var rootDe = function (node) {
                        var root = node && node.closest && node.closest(cardSelectors);
                        if (rootProdutoSeguro(root)) return root;
                        var atual = node;
                        for (var i = 0; atual && i < 6; i += 1) {
                            if (rootProdutoSeguro(atual)) return atual;
                            atual = atual.parentElement;
                        }
                        return root || node;
                    };
                    var visibleAvantNode = function (node) {
                        try {
                            var rect = node && node.getBoundingClientRect ? node.getBoundingClientRect() : null;
                            var style = node && window.getComputedStyle ? window.getComputedStyle(node) : null;
                            var vw = window.innerWidth || document.documentElement.clientWidth || 1280;
                            var vh = window.innerHeight || document.documentElement.clientHeight || 900;
                            return !!(rect && rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0 && rect.top < vh && rect.left < vw && (!style || (style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) !== 0)));
                        } catch (_err) {
                            return false;
                        }
                    };
                    var linhasTextoAvant = function (node) {
                        return String(node && (node.innerText || node.textContent) || '')
                            .split(/\\n+/)
                            .map(function (line) { return line.replace(/\\s+/g, ' ').trim(); })
                            .filter(Boolean);
                    };
                    var definicoesLabelsAvant = [
                        { label: 'Vendas do produto', tokens: ['vendas do produto', 'venda do produto', 'vendas do anuncio', 'venda do anuncio', 'vendas do item', 'venda do item'] },
                        { label: 'Vendas estimadas', tokens: ['vendas estimad', 'venda estimad'] },
                        { label: 'Ritmo atual', tokens: ['ritmo atual', 'media mensal', 'vendas/mes', 'vendas mes'] },
                        { label: 'Nome do vendedor', tokens: ['nome do vendedor'] },
                        { label: 'Localizacao do vendedor', tokens: ['localizacao do vendedor'] },
                        { label: 'Anuncio criado em', tokens: ['anuncio criado em', 'catalogo criado em', 'criado em'] },
                        { label: 'Visitas do anuncio', tokens: ['visitas do anuncio', 'visitas'] },
                        { label: 'Participacao', tokens: ['participacao'] },
                        { label: 'Comissao', tokens: ['comissao'] },
                        { label: 'Reputacao do vendedor', tokens: ['reputacao do vendedor'] },
                        { label: 'Frete', tokens: ['frete'] },
                        { label: 'Taxa da categoria', tokens: ['taxa da categoria'] },
                        { label: 'Marca', tokens: ['marca'] }
                    ];
                    var labelAvantPorLinha = function (line) {
                        var norm = normalizar(line);
                        for (var i = 0; i < definicoesLabelsAvant.length; i += 1) {
                            var def = definicoesLabelsAvant[i];
                            for (var j = 0; j < def.tokens.length; j += 1) {
                                var token = def.tokens[j];
                                if (norm === token || norm.indexOf(token + ' ') === 0 || norm.indexOf(token + ':') === 0) return def;
                            }
                        }
                        return null;
                    };
                    var linhaEhLabelAvant = function (line) {
                        return !!labelAvantPorLinha(line);
                    };
                    var textoValorNaMesmaLinhaAvant = function (line, def) {
                        var text = String(line || '').trim();
                        var norm = normalizar(text);
                        var melhor = '';
                        (def && def.tokens || []).forEach(function (token) {
                            if (norm.indexOf(token) !== 0) return;
                            var resto = text.slice(token.length).replace(/^[\\s:=-]+/, '').trim();
                            if (resto && resto.length > melhor.length) melhor = resto;
                        });
                        return melhor;
                    };
                    var cardProdutoMaisProximoDe = function (node) {
                        var candidatos = queryAllDeep(cardSelectors).filter(rootProdutoSeguro).filter(visibleAvantNode);
                        if (!candidatos.length) return null;
                        var nr = null;
                        try { nr = node && node.getBoundingClientRect && node.getBoundingClientRect(); } catch (_err) {}
                        if (!nr) return candidatos[0];
                        var ncx = (nr.left + nr.right) / 2;
                        var ncy = (nr.top + nr.bottom) / 2;
                        var best = null;
                        candidatos.forEach(function (card) {
                            var cr = null;
                            try { cr = card.getBoundingClientRect && card.getBoundingClientRect(); } catch (_err) {}
                            if (!cr) return;
                            var ccx = (cr.left + cr.right) / 2;
                            var ccy = (cr.top + cr.bottom) / 2;
                            var score = Math.abs(ccy - ncy) + Math.abs(ccx - ncx) * 0.25;
                            if (!best || score < best.score) best = { card: card, score: score };
                        });
                        return best && best.card || candidatos[0];
                    };
                    var extrairLinhasVirtuaisAvant = function (root) {
                        var linhas = linhasTextoAvant(root);
                        if (!linhas.length) return [];
                        var rootProduto = rootProdutoSeguro(root) ? root : cardProdutoMaisProximoDe(root);
                        var out = [];
                        for (var i = 0; i < linhas.length; i += 1) {
                            var line = linhas[i];
                            var def = labelAvantPorLinha(line);
                            if (!def) continue;
                            var value = textoValorNaMesmaLinhaAvant(line, def);
                            if (!value) {
                                for (var j = i + 1; j < Math.min(linhas.length, i + 5); j += 1) {
                                    var candidato = linhas[j];
                                    if (!candidato || linhaEhLabelAvant(candidato)) break;
                                    if (/^(informacoes?\\s+avant(?:pro)?|carregar\\s+dados\\s+avantpro?)$/i.test(normalizar(candidato))) continue;
                                    value = candidato;
                                    break;
                                }
                            }
                            out.push({
                                __jkLabel: def.label,
                                __jkValue: value || '',
                                __jkText: [def.label, value || ''].filter(Boolean).join(' '),
                                __jkRoot: rootProduto || root
                            });
                        }
                        return out;
                    };
                    var coletarLinhasVirtuaisAvant = function () {
                        var candidatos = queryAllDeep('[class*="avant"], [class*="Avant"], [role="dialog"], aside, section, div')
                            .filter(function (node) {
                                if (!visibleAvantNode(node) || rootGenericoDemais(node)) return false;
                                var text = normalizar(node.innerText || node.textContent || '');
                                if (!/avant|vendas?\\s+do\\s+produto|ritmo\\s+atual|nome\\s+do\\s+vendedor|anuncio\\s+criado\\s+em/.test(text)) return false;
                                var labels = 0;
                                definicoesLabelsAvant.forEach(function (def) {
                                    if (def.tokens.some(function (token) { return text.indexOf(token) >= 0; })) labels += 1;
                                });
                                return labels >= 2;
                            });
                        var usados = [];
                        var linhas = [];
                        candidatos.sort(function (a, b) {
                            var ar = null;
                            var br = null;
                            try { ar = a.getBoundingClientRect && a.getBoundingClientRect(); } catch (_err) {}
                            try { br = b.getBoundingClientRect && b.getBoundingClientRect(); } catch (_err) {}
                            var aa = ar ? ar.width * ar.height : 0;
                            var ba = br ? br.width * br.height : 0;
                            return aa - ba;
                        }).forEach(function (node) {
                            if (usados.some(function (parent) { return parent !== node && parent.contains && parent.contains(node); })) return;
                            var extraidas = extrairLinhasVirtuaisAvant(node);
                            if (extraidas.length < 2) return;
                            usados.push(node);
                            linhas = linhas.concat(extraidas);
                        });
                        return linhas;
                    };
                    var labelValue = function (row) {
                        if (row && row.__jkLabel) {
                            return {
                                label: row.__jkLabel || '',
                                value: row.__jkValue || '',
                                text: row.__jkText || [row.__jkLabel, row.__jkValue].filter(Boolean).join(' ')
                            };
                        }
                        var labelNode = row && row.querySelector && row.querySelector('.avantpro-product-info-row-label, [class*="label"]');
                        var valueNode = row && row.querySelector && row.querySelector('.avantpro-product-info-row-value, [class*="value"]');
                        var label = String(labelNode && (labelNode.innerText || labelNode.textContent) || '').replace(/\\s+/g, ' ').trim();
                        var value = String(valueNode && (valueNode.innerText || valueNode.textContent) || '').replace(/\\s+/g, ' ').trim();
                        var text = String(row && (row.innerText || row.textContent) || '').replace(/\\s+/g, ' ').trim();
                        return { label: label, value: value, text: text };
                    };
                    var labelTem = function (label, partes) {
                        var n = normalizar(label);
                        return partes.some(function (parte) { return n.indexOf(parte) >= 0; });
                    };
                    var preencherPorLinha = function (grupo, row) {
                        var lv = labelValue(row);
                        var label = lv.label;
                        var value = lv.value;
                        var text = lv.text;
                        var busca = normalizar(label + ' ' + text);
                        if (!value && label && text.indexOf(label) >= 0) {
                            value = text.slice(text.indexOf(label) + label.length).replace(/^\\s*[:\\-]?\\s*/, '').trim();
                        }
                        if (labelTem(label || busca, ['vendas do produto', 'venda do produto', 'vendas do anuncio', 'venda do anuncio', 'vendas do item', 'venda do item'])) {
                            var vendas = parseHumanNumber(value || text, '', false);
                            if (Number.isFinite(vendas)) grupo.vendas = vendas;
                        } else if (labelTem(label || busca, ['vendas estimad', 'venda estimad'])) {
                            var estimadas = parseHumanNumber(value || text, '', false);
                            if (Number.isFinite(estimadas) && !Number.isFinite(grupo.vendas)) grupo.vendas = estimadas;
                            if (Number.isFinite(estimadas)) grupo.vendas_estimadas = estimadas;
                        } else if (labelTem(label || busca, ['ritmo atual'])) {
                            var ritmo = parseHumanNumber(value || text, '', true);
                            if (Number.isFinite(ritmo)) {
                                grupo.media_mensal = ritmo;
                                grupo.ritmo_atual = ritmo;
                            }
                        } else if (labelTem(label || busca, ['reputacao do vendedor'])) {
                            grupo.reputacao_vendedor = value || '';
                        } else if (labelTem(label || busca, ['localizacao do vendedor'])) {
                            grupo.localizacao_vendedor = value || '';
                        } else if (labelTem(label || busca, ['nome do vendedor']) || (labelTem(label || busca, ['vendedor']) && !labelTem(label || busca, ['reputacao do vendedor', 'localizacao do vendedor']))) {
                            var vendedor = String(value || '').replace(/^\\s*[:\\-]?\\s*/, '').trim();
                            if (vendedor && vendedor.length <= 120 && !/^\\d+$/.test(vendedor)) grupo.vendedor = vendedor;
                        } else if (labelTem(label || busca, ['anuncio criado em', 'catalogo criado em', 'criado em'])) {
                            var data = String(value || text || '').match(/\\b\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}\\b|\\b20\\d{2}-\\d{2}-\\d{2}\\b/);
                            if (data && data[0]) grupo.data_criacao = data[0];
                        } else if (labelTem(label || busca, ['visitas do anuncio', 'visitas'])) {
                            var visitas = parseHumanNumber(value || text, '', false);
                            if (Number.isFinite(visitas)) grupo.visitas = visitas;
                        } else if (labelTem(label || busca, ['participacao'])) {
                            grupo.participacao = value || '';
                        } else if (labelTem(label || busca, ['comissao'])) {
                            grupo.comissao = value || '';
                        }
                    };
                    var rows = queryAllDeep('.avantpro-product-info-row, .created-time-card, [class*="avantpro-product-info"], [class*="created-time-card"], [class*="Avantpro"][class*="row"], [class*="avantpro"][class*="row"]');
                    rows = rows.concat(coletarLinhasVirtuaisAvant());
                    var mapa = {};
                    var ordem = [];
                    try {
                        var cachePainel = window.__JK_AVANT_CARD_DATA_CACHE || {};
                        Object.keys(cachePainel).forEach(function (cacheKey) {
                            var cached = Object.assign({}, cachePainel[cacheKey] || {});
                            var id = extrairId(cached.id || cached.mlb || cached.url || cached.link || cached.permalink || cacheKey);
                            var href = cleanProductUrl(cached.url || cached.permalink || cached.link || '', id);
                            var key = id ? ('mlb:' + id) : (href ? ('link:' + href.toLowerCase()) : (/^(?:mlb|link):/i.test(cacheKey) ? cacheKey.toLowerCase().replace(/^mlb:/, 'mlb:') : ''));
                            if (!key) return;
                            if (id) {
                                cached.id = id;
                                cached.mlb = id;
                            }
                            if (href) {
                                cached.url = href;
                                cached.permalink = href;
                                cached.link = href;
                            }
                            cached.origem_dados = cached.origem_dados || 'avantpro_card_panel_cache';
                            cached.chave_canonica = key;
                            cached.chaveCanonica = key;
                            if (!mapa[key]) {
                                mapa[key] = cached;
                                ordem.push(key);
                            } else {
                                mapa[key] = Object.assign({}, mapa[key], cached);
                            }
                        });
                    } catch (_cacheErr) {}
                    rows.forEach(function (row) {
                        var text = String(row && (row.__jkText || row.innerText || row.textContent) || '').replace(/\\s+/g, ' ').trim();
                        if (!/vendas?|ritmo|vendedor|criado|visitas|participa|comissao|reputacao/i.test(normalizar(text))) return;
                        var root = row && row.__jkRoot || rootDe(row);
                        var href = linkProdutoDe(root) || nearestProductLink(row);
                        var id = extrairId(href || (root && root.outerHTML) || (row && row.outerHTML) || '');
                        href = cleanProductUrl(href, id);
                        var key = id ? ('mlb:' + id) : (href ? ('link:' + href.toLowerCase()) : '');
                        if (!key) return;
                        if (!mapa[key]) {
                            var precos = precosDe(root);
                            mapa[key] = {
                                posicao: ordem.length + 1,
                                id: id,
                                mlb: id,
                                url: href,
                                permalink: href,
                                link: href,
                                titulo: tituloDe(root, href),
                                title: tituloDe(root, href),
                                imagem: imagemDe(root),
                                thumbnail: imagemDe(root),
                                foto: imagemDe(root),
                                preco: precos.preco,
                                price: precos.preco_promocional || precos.preco,
                                preco_original: precos.preco_original,
                                original_price: precos.preco_original,
                                preco_promocional: precos.preco_promocional,
                                promotional_price: precos.preco_promocional,
                                moeda: precos.moeda,
                                currency_id: precos.moeda,
                                tituloFonte: 'mercado_livre_dom_contexto_avant',
                                fotoFonte: imagemDe(root) ? 'mercado_livre_dom_contexto_avant' : '',
                                linkFonte: href ? 'mercado_livre_dom_contexto_avant' : '',
                                precoFonte: precos.preco !== null ? 'mercado_livre_dom_contexto_avant' : '',
                                fonte_preco: precos.preco !== null ? 'mercado_livre_dom_contexto_avant' : '',
                                origem_dados: 'avantpro_dom',
                                vendasFonte: '',
                                vendas_fonte: '',
                                vendedorFonte: '',
                                vendedor_fonte: ''
                            };
                            ordem.push(key);
                        }
                        preencherPorLinha(mapa[key], row);
                    });
                    var anuncios = ordem.map(function (key) {
                        var item = mapa[key];
                        var texto = [
                            item.vendas,
                            item.media_mensal,
                            item.vendedor,
                            item.data_criacao,
                            item.visitas,
                            item.participacao
                        ].join(' ');
                        if (item.vendas !== null && item.vendas !== undefined && item.vendas !== '') {
                            item.vendasFonte = 'avantpro_dom';
                            item.vendas_fonte = 'avantpro_dom';
                        }
                        if (item.vendedor) {
                            item.vendedorFonte = 'avantpro_dom';
                            item.vendedor_fonte = 'avantpro_dom';
                        }
                        if (item.data_criacao) {
                            item.dataCriacaoFonte = 'avantpro_dom';
                            item.data_criacao_fonte = 'avantpro_dom';
                        }
                        if (item.media_mensal !== null && item.media_mensal !== undefined && item.media_mensal !== '') {
                            item.media_mensal_fonte = 'avantpro_dom';
                        }
                        return item;
                    }).filter(function (item) {
                        return item && (item.vendasFonte || item.vendedorFonte || item.data_criacao || item.media_mensal || item.url || item.id);
                    }).slice(0, limite);
                    return {
                        success: true,
                        total: anuncios.length,
                        anuncios: anuncios,
                        debug: {
                            mode: 'avantpro_dom',
                            rows: rows.length,
                            url: location.href,
                            title: document.title || ''
                        }
                    };
                })();
            `, true).then((resultado) => {
                const anuncios = Array.isArray(resultado && resultado.anuncios)
                    ? resultado.anuncios.map(item => prepararAnuncioMercadoLivreCanonico(item, 'avantpro_dom')).filter(item => item.chave_canonica)
                    : [];
                return {
                    ...(resultado || {}),
                    success: true,
                    total: anuncios.length,
                    anuncios
                };
            }).catch((err) => ({
                success: false,
                total: 0,
                anuncios: [],
                error: err && err.message ? err.message : String(err)
            }));
        }

        async function extrairCardsMercadoLivreBasicoWebview(opcoes = {}) {
            const webview = resolverWebviewFavoritosColeta(opcoes);
            if (!webview || typeof webview.executeJavaScript !== 'function') return { success: false, total: 0, anuncios: [] };
            const limite = Math.max(1, Math.min(Number(opcoes.limite) || 100, 160));
            return await webview.executeJavaScript(`
                (function () {
                    var limite = ${JSON.stringify(limite)};
                    var cleanUrl = function (href) {
                        href = String(href || '').split('#')[0].trim();
                        if (!href) return '';
                        if (/^\\/\\//.test(href)) return 'https:' + href;
                        if (/^\\//.test(href)) return 'https://www.mercadolivre.com.br' + href;
                        return href;
                    };
                    var cleanProductUrl = function (href, id) {
                        href = cleanUrl(href);
                        if (!href && id) return 'https://produto.mercadolivre.com.br/' + String(id).replace('MLB', 'MLB-');
                        if (!href) return '';
                        try {
                            var parsed = new URL(href, 'https://www.mercadolivre.com.br');
                            parsed.hash = '';
                            [
                                'tracking_id',
                                'position',
                                'polycard_client',
                                'sid',
                                'searchVariation',
                                'backend_model',
                                'backend_type',
                                'client',
                                'reco_item_pos',
                                'reco_backend',
                                'reco_backend_type',
                                'reco_client',
                                'reco_id',
                                'c_id',
                                'pdp_filters',
                                'picker_url',
                                'quantity',
                                'variation',
                                'loader',
                                'noIndex'
                            ].forEach(function (param) { parsed.searchParams.delete(param); });
                            parsed.pathname = parsed.pathname.replace(/\\/+$/, '');
                            return parsed.toString().replace(/[?&]$/, '');
                        } catch (e) {
                            return href;
                        }
                    };
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (e) {}
                        return text.toLowerCase();
                    };
                    var queryAllDeep = function (selector, root) {
                        var found = [];
                        var visited = [];
                        var visit = function (base) {
                            if (!base || visited.indexOf(base) >= 0) return;
                            visited.push(base);
                            try {
                                if (base.querySelectorAll) {
                                    Array.prototype.slice.call(base.querySelectorAll(selector)).forEach(function (node) {
                                        if (found.indexOf(node) < 0) found.push(node);
                                    });
                                    Array.prototype.slice.call(base.querySelectorAll('*')).forEach(function (node) {
                                        if (node && node.shadowRoot) visit(node.shadowRoot);
                                    });
                                }
                            } catch (e) {}
                        };
                        visit(root || document);
                        return found;
                    };
                    var extrairId = function (value) {
                        var text = String(value || '');
                        try { text = decodeURIComponent(text); } catch (e) {}
                        var match = text.match(/\\bMLB-?(\\d{6,})\\b/i) || text.match(/[?&](?:wid|item_id)=(MLB\\d{6,})/i);
                        if (!match) return '';
                        var raw = String(match[1] || '').replace('-', '').toUpperCase();
                        return raw.indexOf('MLB') === 0 ? raw : ('MLB' + raw);
                    };
                    var isProductUrlBasico = function (href) {
                        var url = cleanUrl(href);
                        if (!url || url.toLowerCase().indexOf('mercadolivre.com.br') < 0) return false;
                        var urlLower = url.toLowerCase();
                        var hasExplicitItemSignal = (
                            /\\bMLB-?\\d{6,}\\b/i.test(url) ||
                            /\\/p\\/MLB/i.test(url) ||
                            /\\/up\\/MLB[A-Z0-9]*/i.test(url) ||
                            /[?&](?:wid|item_id)=MLB\\d{6,}/i.test(url) ||
                            /produto\\.mercadolivre\\.com\\.br/i.test(url)
                        );
                        if (urlLower.indexOf('https://lista.mercadolivre.com.br/') === 0 || urlLower.indexOf('http://lista.mercadolivre.com.br/') === 0) return hasExplicitItemSignal;
                        if (/\\/(?:ajuda|ofertas|cupons|categorias|supermercado|moda|mercado-play|vender|contato|compras|favoritos|gz|jms|login|registration|cart|publicidade|navigation|perfil|stores?|loja|post-purchase)\\b/i.test(url)) return false;
                        return hasExplicitItemSignal;
                    };
                    var isNavUrl = function (href) {
                        var url = cleanUrl(href).toLowerCase();
                        return !isProductUrlBasico(url);
                    };
                    var tituloFraco = function (value) {
                        var norm = normalizar(value);
                        if (!norm) return true;
                        if (/^(jm|novo|usado|patrocinado|mais vendido|r\\$|frete|chegar|vendid|mercado livre|favoritos|compras|produto relacionado|opcoes de compra)$/i.test(norm)) return true;
                        if (/^mlb\\d+$/i.test(norm.replace(/-/g, ''))) return true;
                        return norm.length <= 3;
                    };
                    var tituloDoHref = function (href) {
                        var text = String(href || '');
                        try { text = decodeURIComponent(text); } catch (e) {}
                        var match = text.match(/\\/MLB-?\\d+-([^?#]+?)(?:-_?JM|_JM|$)/i)
                            || text.match(/mercadolivre\\.com\\.br\\/([^/?#]+?)\\/up\\/MLB[A-Z0-9]+/i)
                            || text.match(/\\/([^/?#]+?)\\/up\\/MLB[A-Z0-9]+/i);
                        if (!match || !match[1]) return '';
                        var titulo = String(match[1])
                            .replace(/[-_]+/g, ' ')
                            .replace(/\\bJM\\b/ig, ' ')
                            .replace(/\\s+/g, ' ')
                            .trim();
                        return tituloFraco(titulo) ? '' : titulo.slice(0, 240);
                    };
                    var tituloDe = function (card, hrefProduto) {
                        var candidatos = []
                            .concat(Array.prototype.slice.call(card.querySelectorAll('h2, h3')))
                            .concat(Array.prototype.slice.call(card.querySelectorAll('[class*="title"], [class*="name"], [class*="poly-component__title"], [class*="ui-search-item__title"], [data-testid*="title"], [aria-label]')))
                            .concat(Array.prototype.slice.call(card.querySelectorAll('a[href]')));
                        candidatos.push(card);
                        for (var i = 0; i < candidatos.length; i += 1) {
                            var el = candidatos[i];
                            var text = String((el && (
                                el.getAttribute && (el.getAttribute('title') || el.getAttribute('aria-label')) ||
                                el.innerText ||
                                el.textContent
                            )) || '').replace(/\\s+/g, ' ').trim();
                            if (text.length >= 8 && !tituloFraco(text)) {
                                return text.slice(0, 240);
                            }
                        }
                        var tituloHref = tituloDoHref(hrefProduto);
                        if (tituloHref) return tituloHref;
                        return '';
                    };
                    var hrefDe = function (card) {
                        var links = Array.prototype.slice.call(card.querySelectorAll('a[href]'));
                        for (var i = 0; i < links.length; i += 1) {
                            var rawHref = links[i].href || links[i].getAttribute('href') || '';
                            if (!isNavUrl(rawHref)) return rawHref;
                        }
                        return '';
                    };
                    var linksProdutoDe = function (root) {
                        var links = [];
                        try {
                            if (root && root.matches && root.matches('a[href]')) links.push(root);
                        } catch (_selfLinkErr) {}
                        try {
                            links = links.concat(Array.prototype.slice.call(root && root.querySelectorAll ? root.querySelectorAll('a[href], [data-href], [data-url]') : []));
                        } catch (_linksErr) {}
                        var vistosLinks = {};
                        return links.map(function (node) {
                            return cleanUrl(node && (
                                node.href
                                || node.getAttribute && (node.getAttribute('href') || node.getAttribute('data-href') || node.getAttribute('data-url'))
                                || ''
                            ) || '');
                        }).filter(function (href) {
                            if (!href || isNavUrl(href)) return false;
                            var limpo = cleanProductUrl(href, extrairId(href)).toLowerCase();
                            if (!limpo || vistosLinks[limpo]) return false;
                            vistosLinks[limpo] = true;
                            return true;
                        });
                    };
                    var quantidadeLinksProduto = function (root) {
                        return linksProdutoDe(root).length;
                    };
                    var rootMuitoGenerico = function (root) {
                        if (!root || root === document || root === document.body || root === document.documentElement) return true;
                        var tag = String(root.tagName || '').toUpperCase();
                        if (/^(HTML|BODY|MAIN|OL|UL|NAV|HEADER|FOOTER|FORM)$/.test(tag)) return true;
                        try {
                            var rect = root.getBoundingClientRect && root.getBoundingClientRect();
                            var area = rect ? Math.max(0, rect.width) * Math.max(0, rect.height) : 0;
                            var viewportArea = Math.max(1, (window.innerWidth || 1280) * (window.innerHeight || 900));
                            if (area > viewportArea * 1.8 && quantidadeLinksProduto(root) > 1) return true;
                        } catch (_areaErr) {}
                        return false;
                    };
                    var temSinalProdutoVisual = function (root) {
                        if (!root || rootMuitoGenerico(root)) return false;
                        var texto = normalizar(root.innerText || root.textContent || '');
                        if (/categorias|ofertas|cupons|compras|favoritos|ordenar por|dados carregados|avantpro control/.test(texto) && quantidadeLinksProduto(root) !== 1) return false;
                        var temTitulo = !!tituloDe(root, linksProdutoDe(root)[0] || '');
                        var temImagem = !!imagemDe(root);
                        var precos = precosDe(root);
                        var temPreco = precos && precos.preco !== null && precos.preco !== undefined;
                        return quantidadeLinksProduto(root) === 1 && (temTitulo || temImagem || temPreco);
                    };
                    var imagemValida = function (url) {
                        var text = String(url || '').trim();
                        if (!text || /^data:/i.test(text)) return false;
                        if (text.indexOf('http://') !== 0 && text.indexOf('https://') !== 0 && text.indexOf('//') !== 0) return false;
                        return !/(logo|avatar|sprite|icon|favicon|badge|medal|avantpro|meliplus)/i.test(text);
                    };
                    var primeiraSrcset = function (value) {
                        var text = String(value || '').trim();
                        if (!text) return '';
                        var partes = text.split(',').map(function (item) {
                            var bits = item.trim().split(/\\s+/);
                            return {
                                url: bits[0] || '',
                                peso: parseFloat((bits[1] || '').replace(/[^\\d.]/g, '')) || 0
                            };
                        }).filter(function (item) { return imagemValida(item.url); });
                        partes.sort(function (a, b) { return b.peso - a.peso; });
                        return partes[0] && partes[0].url || '';
                    };
                    var imagemDe = function (card) {
                        var imgs = Array.prototype.slice.call(card.querySelectorAll('img, source[srcset], source[data-srcset]'));
                        var candidatos = imgs.map(function (img, index) {
                            var srcset = img.getAttribute && (img.getAttribute('srcset') || img.getAttribute('data-srcset'));
                            var url = primeiraSrcset(srcset)
                                || String(img.currentSrc || '')
                                || String(img.src || '')
                                || String(img.getAttribute && (img.getAttribute('src') || img.getAttribute('data-src') || img.getAttribute('data-original') || img.getAttribute('data-lazy') || '') || '');
                            var rect = null;
                            try { rect = img.getBoundingClientRect && img.getBoundingClientRect(); } catch (e) {}
                            var area = rect ? Math.max(0, rect.width) * Math.max(0, rect.height) : 0;
                            return { url: url, area: area, index: index };
                        }).filter(function (item) {
                            return imagemValida(item.url);
                        });
                        candidatos.sort(function (a, b) {
                            return b.area - a.area || a.index - b.index;
                        });
                        return candidatos[0] && candidatos[0].url || '';
                    };
                    var parsePrecoTexto = function (valor) {
                        var match = String(valor || '').match(/R\\$\\s*([0-9.]+)(?:\\s*,\\s*([0-9]{1,2}))?/);
                        if (!match) return null;
                        var inteiro = String(match[1] || '').replace(/\\./g, '');
                        var cents = String(match[2] || '0').padEnd(2, '0').slice(0, 2);
                        var value = Number(inteiro + '.' + cents);
                        return Number.isFinite(value) ? value : null;
                    };
                    var nodeDentroAvant = function (node) {
                        var atual = node;
                        for (var i = 0; atual && i < 8; i += 1) {
                            var cls = String(atual.className || '');
                            var id = String(atual.id || '');
                            if (/avant|created-time-card|product-info-row|faturamento|comissao|frete/i.test(cls + ' ' + id)) return true;
                            atual = atual.parentElement;
                        }
                        return false;
                    };
                    var textoPrecoIndesejado = function (node) {
                        var texto = String(node && (node.innerText || node.textContent) || '').replace(/\\s+/g, ' ');
                        var parent = node && node.parentElement ? String(node.parentElement.innerText || node.parentElement.textContent || '').replace(/\\s+/g, ' ') : texto;
                        return /frete|comiss[aã]o|faturamento|taxa|categoria|total\\s+de\\s+vendas|vendas\\s+do\\s+produto|ritmo|visitas/i.test(texto + ' ' + parent);
                    };
                    var parsePrecoNode = function (node) {
                        if (!node || nodeDentroAvant(node) || textoPrecoIndesejado(node)) return null;
                        var fractionNode = node.querySelector && node.querySelector('.andes-money-amount__fraction, .price-tag-fraction, [class*="fraction"]');
                        var centsNode = node.querySelector && node.querySelector('.andes-money-amount__cents, .price-tag-cents, [class*="cents"], [class*="decimal"]');
                        var fraction = fractionNode ? String(fractionNode.innerText || fractionNode.textContent || '').replace(/[^0-9.]/g, '') : '';
                        var textNode = String(node.innerText || node.textContent || '');
                        var cents = centsNode ? String(centsNode.innerText || centsNode.textContent || '').replace(/[^0-9]/g, '') : '';
                        if (!cents) {
                            var centsMatch = textNode.match(/[,.]\\s*(\\d{1,2})\\s*$/);
                            cents = centsMatch && centsMatch[1] ? centsMatch[1] : '';
                        }
                        if (fraction) {
                            var inteiro = fraction.replace(/\\./g, '');
                            var centavos = cents ? cents.padEnd(2, '0').slice(0, 2) : '00';
                            var parsed = Number(inteiro + '.' + centavos);
                            return Number.isFinite(parsed) ? parsed : null;
                        }
                        return parsePrecoTexto(textNode);
                    };
                    var isPrecoOriginalNode = function (node) {
                        var atual = node;
                        for (var i = 0; atual && i < 5; i += 1) {
                            var info = String(atual.className || '') + ' ' + String(atual.getAttribute && (atual.getAttribute('aria-label') || atual.getAttribute('role') || '') || '');
                            if (/previous|original|old|strikethrough|discount|antes|tachado/i.test(info)) return true;
                            atual = atual.parentElement;
                        }
                        return false;
                    };
                    var isPrecoPrincipalNode = function (node) {
                        var atual = node;
                        for (var i = 0; atual && i < 5; i += 1) {
                            var cls = String(atual.className || '');
                            if (/poly-price__current|ui-search-price__second-line/i.test(cls)) return true;
                            atual = atual.parentElement;
                        }
                        return false;
                    };
                    var isPrecoSecundarioNode = function (node) {
                        var atual = node;
                        for (var i = 0; atual && i < 5; i += 1) {
                            var info = String(atual.className || '') + ' ' + String(atual.innerText || atual.textContent || '');
                            if (/installments|parcel|per[_-]?quantity|price-per-quantity|levando\\s+\\d+\\s+ou\\s+mais|\\b\\d+x\\s*r\\$/i.test(info)) return true;
                            atual = atual.parentElement;
                        }
                        return false;
                    };
                    var precosDe = function (card) {
                        var selectorsPreco = [
                            '.poly-price__current .andes-money-amount',
                            '.poly-component__price .andes-money-amount',
                            '.ui-search-price__second-line .andes-money-amount',
                            '[class*="price"] .andes-money-amount',
                            '.andes-money-amount',
                            '.price-tag'
                        ].join(',');
                        var precoPrincipalDireto = Array.prototype.slice.call(card.querySelectorAll ? card.querySelectorAll('.poly-price__current .andes-money-amount, .ui-search-price__second-line .andes-money-amount') : [])
                            .map(function (node) { return parsePrecoTexto(node && (node.innerText || node.textContent)); })
                            .filter(function (value) { return value !== null && value > 0; });
                        var nodes = Array.prototype.slice.call(card.querySelectorAll ? card.querySelectorAll(selectorsPreco) : [])
                            .filter(function (node) { return !nodeDentroAvant(node) && !textoPrecoIndesejado(node); });
                        var atuaisPrincipais = [];
                        var atuais = [];
                        var originais = [];
                        nodes.forEach(function (node) {
                            var value = parsePrecoNode(node);
                            if (value === null || value <= 0) return;
                            if (isPrecoOriginalNode(node)) originais.push(value);
                            else if (isPrecoPrincipalNode(node)) atuaisPrincipais.push(value);
                            else if (isPrecoSecundarioNode(node)) return;
                            else atuais.push(value);
                        });
                        var precoAtual = precoPrincipalDireto.length ? precoPrincipalDireto[0] : (atuaisPrincipais.length ? atuaisPrincipais[0] : (atuais.length ? atuais[0] : null));
                        var precoOriginal = originais.length ? originais[0] : '';
                        if (precoAtual === null) {
                            var direto = String(card.innerText || card.textContent || '').split(/frete|comiss[aã]o|faturamento|taxa|total\\s+de\\s+vendas/i)[0];
                            precoAtual = parsePrecoTexto(direto);
                        }
                        if (precoAtual === null) return { preco: null, preco_original: '', preco_promocional: '', moeda: '' };
                        if (precoOriginal && precoOriginal > precoAtual) {
                            return {
                                preco: precoAtual,
                                preco_original: precoOriginal,
                                preco_promocional: precoAtual,
                                moeda: 'BRL'
                            };
                        }
                        return { preco: precoAtual, preco_original: '', preco_promocional: '', moeda: 'BRL' };
                    };
                    var selectors = [
                        'li.ui-search-layout__item',
                        'div.ui-search-result__wrapper',
                        'div.ui-search-result',
                        'div.poly-card',
                        'section.poly-card',
                        'article.poly-card',
                        'article.ui-search-result',
                        '[data-testid="product-card"]',
                        '[data-testid="item-card"]',
                        '[data-testid*="product"]',
                        '[data-testid*="item"]',
                        '[class*="product-card"]',
                        '[class*="poly-card"]',
                        '[class*="ui-search-layout__item"]',
                        '[class*="ui-search-layout"] > li',
                        '[class*="shops__layout-item"]',
                        '[data-testid*="result"]',
                        'main ol > li',
                        'main ul > li'
                    ].join(',');
                    var cardMaisProximo = function (anchor) {
                        if (!anchor || !anchor.closest) return anchor;
                        return anchor.closest([
                            'li.ui-search-layout__item',
                            'div.ui-search-result__wrapper',
                            'div.ui-search-result',
                            'div.poly-card',
                            'section.poly-card',
                            'article.poly-card',
                            'article.ui-search-result',
                            '[data-testid="product-card"]',
                            '[data-testid="item-card"]',
                            '[class*="product-card"]',
                            '[class*="poly-card"]',
                            '[class*="shops__layout-item"]',
                            'main ol > li',
                            'main ul > li',
                            'li',
                            'article',
                            'section'
                        ].join(',')) || anchor;
                    };
                    var cards = queryAllDeep(selectors);
                    queryAllDeep('a[href]').forEach(function (anchor) {
                        var href = cleanUrl(anchor.href || anchor.getAttribute('href') || '');
                        if (isNavUrl(href)) return;
                        var card = cardMaisProximo(anchor);
                        if (card && cards.indexOf(card) < 0) cards.push(card);
                    });
                    var vistos = {};
                    var out = [];
                    var adicionarCardAoResultado = function (card, hrefPreferencial, origem) {
                        if (!card || out.length >= limite) return false;
                        var href = hrefPreferencial || hrefDe(card);
                        var id = extrairId(href || card.outerHTML || '');
                        href = cleanProductUrl(href, id);
                        var titulo = tituloDe(card, href);
                        if (!href && !id) return false;
                        var key = id ? ('mlb:' + id) : (href ? ('link:' + href.toLowerCase()) : '');
                        if (!key || vistos[key]) return false;
                        vistos[key] = true;
                        var imagem = imagemDe(card);
                        var precos = precosDe(card);
                        out.push({
                            posicao: out.length + 1,
                            id: id,
                            mlb: id,
                            url: href,
                            permalink: href,
                            link: href,
                            titulo: titulo,
                            title: titulo,
                            imagem: imagem,
                            thumbnail: imagem,
                            foto: imagem,
                            preco: precos.preco,
                            price: precos.preco_promocional || precos.preco,
                            preco_original: precos.preco_original,
                            original_price: precos.preco_original,
                            preco_promocional: precos.preco_promocional,
                            promotional_price: precos.preco_promocional,
                            moeda: precos.moeda,
                            currency_id: precos.moeda,
                            tituloFonte: titulo ? origem : '',
                            fotoFonte: imagem ? origem : '',
                            linkFonte: href ? origem : '',
                            precoFonte: precos.preco !== null ? origem : '',
                            fonte_preco: precos.preco !== null ? origem : '',
                            chave_canonica: key,
                            link_normalizado: href,
                            origem_dados: origem
                        });
                        return true;
                    };
                    var adicionarItemDireto = function (item, origem) {
                        if (!item || out.length >= limite) return false;
                        var href = cleanProductUrl(item.url || item.permalink || item.link || '', item.id || item.mlb || '');
                        var id = extrairId(item.id || item.mlb || href || '');
                        href = cleanProductUrl(href, id);
                        if (!href && !id) return false;
                        var key = id ? ('mlb:' + id) : (href ? ('link:' + href.toLowerCase()) : '');
                        if (!key || vistos[key]) return false;
                        var titulo = String(item.titulo || item.title || item.name || '').replace(/\\s+/g, ' ').trim();
                        if (tituloFraco(titulo)) titulo = tituloDoHref(href);
                        var imagem = '';
                        if (Array.isArray(item.imagem || item.image)) {
                            imagem = (item.imagem || item.image).filter(imagemValida)[0] || '';
                        } else {
                            imagem = String(item.imagem || item.image || item.thumbnail || item.foto || '').trim();
                        }
                        if (!imagemValida(imagem)) imagem = '';
                        var preco = item.preco;
                        if (preco === null || preco === undefined || preco === '') preco = item.price;
                        if (preco === null || preco === undefined || preco === '') preco = item.offers && item.offers.price;
                        if (preco === null || preco === undefined || preco === '') preco = null;
                        preco = preco === null || preco === undefined || preco === '' ? null : Number(String(preco).replace(/\\./g, '').replace(',', '.').replace(/[^\\d.]/g, ''));
                        if (!Number.isFinite(preco) || preco <= 0) preco = null;
                        vistos[key] = true;
                        out.push({
                            posicao: out.length + 1,
                            id: id,
                            mlb: id,
                            url: href,
                            permalink: href,
                            link: href,
                            titulo: titulo,
                            title: titulo,
                            imagem: imagem,
                            thumbnail: imagem,
                            foto: imagem,
                            preco: preco,
                            price: preco,
                            preco_original: '',
                            original_price: '',
                            preco_promocional: '',
                            promotional_price: '',
                            moeda: preco ? 'BRL' : '',
                            currency_id: preco ? 'BRL' : '',
                            tituloFonte: titulo ? origem : '',
                            fotoFonte: imagem ? origem : '',
                            linkFonte: href ? origem : '',
                            precoFonte: preco !== null ? origem : '',
                            fonte_preco: preco !== null ? origem : '',
                            chave_canonica: key,
                            link_normalizado: href,
                            origem_dados: origem
                        });
                        return true;
                    };
                    for (var c = 0; c < cards.length && out.length < limite; c += 1) {
                        adicionarCardAoResultado(cards[c], '', 'mercado_livre_dom');
                    }
                    if (out.length < limite) {
                        queryAllDeep('article, section, li, div, [role="listitem"], [class*="result"], [class*="card"]').slice(0, 2500).forEach(function (root) {
                            if (out.length >= limite || !temSinalProdutoVisual(root)) return;
                            adicionarCardAoResultado(root, linksProdutoDe(root)[0] || '', 'mercado_livre_dom_bloco_visual');
                        });
                    }
                    if (out.length < limite) {
                        var caminharJson = function (value, depth) {
                            if (!value || depth > 7 || out.length >= limite) return;
                            if (Array.isArray(value)) {
                                value.forEach(function (item) { caminharJson(item, depth + 1); });
                                return;
                            }
                            if (typeof value !== 'object') return;
                            var candidato = value.item && typeof value.item === 'object' ? value.item : value;
                            var href = candidato.url || candidato.permalink || candidato.link || value.url || '';
                            var nome = candidato.name || candidato.title || value.name || value.title || '';
                            var image = candidato.image || candidato.thumbnail || value.image || value.thumbnail || '';
                            var offers = candidato.offers || value.offers || {};
                            var preco = candidato.price || value.price || offers.price || '';
                            if (href && isProductUrlBasico(href)) {
                                adicionarItemDireto({
                                    url: href,
                                    titulo: nome,
                                    title: nome,
                                    imagem: image,
                                    image: image,
                                    preco: preco,
                                    price: preco
                                }, 'mercado_livre_json_ld');
                            }
                            Object.keys(value).slice(0, 80).forEach(function (key) {
                                caminharJson(value[key], depth + 1);
                            });
                        };
                        queryAllDeep('script[type="application/ld+json"], script[type="application/json"]').slice(0, 80).forEach(function (script) {
                            if (out.length >= limite) return;
                            var text = String(script && script.textContent || '').trim();
                            if (!text || text.length > 120000 || !/(MLB|mercadolivre|offers|ItemList|Product)/i.test(text)) return;
                            try {
                                caminharJson(JSON.parse(text), 0);
                            } catch (_jsonErr) {}
                        });
                        [
                            '__PRELOADED_STATE__',
                            '__STATE__',
                            '__APOLLO_STATE__',
                            '__NEXT_DATA__',
                            '__MELI_STATE__'
                        ].forEach(function (globalName) {
                            if (out.length >= limite) return;
                            try {
                                if (window[globalName]) caminharJson(window[globalName], 0);
                            } catch (_globalJsonErr) {}
                        });
                    }
                    if (out.length < limite) {
                        var textoParaLinks = '';
                        try {
                            textoParaLinks = [
                                document.documentElement && document.documentElement.innerHTML,
                                queryAllDeep('script').slice(0, 120).map(function (script) {
                                    return String(script && script.textContent || '').slice(0, 220000);
                                }).join(' ')
                            ].filter(Boolean).join(' ');
                        } catch (_htmlErr) {
                            textoParaLinks = '';
                        }
                        textoParaLinks = String(textoParaLinks || '')
                            .replace(/\\u002F/g, '/')
                            .replace(/\\\\\\\//g, '/')
                            .replace(/&amp;/g, '&')
                            .replace(/\\u0026/g, '&');
                        var regexUrls = /https?:\\/\\/(?:www\\.|lista\\.)?mercadolivre\\.com\\.br\\/[^"'<>\\s]*?(?:MLB-?\\d{6,}|\\/p\\/MLB\\d+|\\/up\\/MLB[A-Z0-9]+|item_id(?:%3A|:|=)MLB\\d+|wid=MLB\\d+)[^"'<>\\s]*/ig;
                        var regexRelativas = /\\/[A-Za-z0-9][^"'<>\\s]{8,}?\\/up\\/MLB[A-Z0-9]+[^"'<>\\s]*/ig;
                        var coletarRegex = function (regex) {
                            var match = null;
                            var guard = 0;
                            while (out.length < limite && guard < 400 && (match = regex.exec(textoParaLinks))) {
                                guard += 1;
                                var href = match && match[0] ? match[0] : '';
                                href = cleanProductUrl(href, extrairId(href));
                                if (!href || !isProductUrlBasico(href)) continue;
                                adicionarItemDireto({
                                    url: href,
                                    titulo: tituloDoHref(href)
                                }, 'mercado_livre_html_links');
                            }
                        };
                        coletarRegex(regexUrls);
                        coletarRegex(regexRelativas);
                    }
                    return {
                        success: true,
                        total: out.length,
                        anuncios: out,
                        debug: {
                            cardCount: cards.length,
                            linkCount: document.links ? document.links.length : 0,
                            productLinkCount: queryAllDeep('a[href], [data-href], [data-url]').filter(function (node) {
                                return linksProdutoDe(node).length > 0;
                            }).length,
                            title: document.title || '',
                            url: location.href
                        }
                    };
                })();
            `, true).then((resultado) => {
                const anuncios = Array.isArray(resultado && resultado.anuncios)
                    ? resultado.anuncios.map(item => prepararAnuncioMercadoLivreCanonico(item, 'mercado_livre_dom')).filter(item => item.chave_canonica)
                    : [];
                return {
                    ...(resultado || {}),
                    success: true,
                    total: anuncios.length,
                    anuncios
                };
            }).catch((err) => ({
                success: false,
                total: 0,
                anuncios: [],
                error: err && err.message ? err.message : String(err)
            }));
        }

        async function extrairBaseMercadoLivreEmergencialWebview(opcoes = {}) {
            const webview = resolverWebviewFavoritosColeta(opcoes);
            if (!webview || typeof webview.executeJavaScript !== 'function') {
                return { success: false, total: 0, anuncios: [], error: 'webview_indisponivel' };
            }
            const limite = Math.max(1, Math.min(Number(opcoes.limite) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80, 160));
            let resultado = null;
            try {
                resultado = opcoes.webview
                    ? await extrairCardsMercadoLivreBasicoWebview({ limite, webview })
                    : await extrairAnunciosWebviewVisivel({
                        clicarAvant: false,
                        permitirFerramentasAvant: false,
                        permitirAutoLoginAvant: false,
                        clicarCardsSemDados: false,
                        ignorarLoginAvant: true,
                        fastLinks: false,
                        maxAnuncios: limite,
                        maxFastDom: limite,
                        timeoutMs: Number(opcoes.timeoutMs) || 9000
                    });
            } catch (err) {
                resultado = {
                    success: false,
                    total: 0,
                    anuncios: [],
                    error: err && err.message ? err.message : String(err)
                };
            }
            let anuncios = Array.isArray(resultado && resultado.anuncios)
                ? resultado.anuncios.map(item => prepararAnuncioMercadoLivreCanonico(item, 'mercado_livre_dom_emergencial')).filter(item => item.chave_canonica)
                : [];
            if (!anuncios.length) {
                const rapido = await extrairAnunciosWebviewFastDom({
                    maxFastDom: limite,
                    webview
                }).catch(() => null);
                anuncios = Array.isArray(rapido && rapido.anuncios)
                    ? rapido.anuncios.map(item => prepararAnuncioMercadoLivreCanonico(item, 'mercado_livre_dom_emergencial_links')).filter(item => item.chave_canonica)
                    : [];
                if (anuncios.length) {
                    resultado = {
                        ...(resultado || {}),
                        debug: {
                            ...(resultado && resultado.debug || {}),
                            emergenciaFastDom: rapido && rapido.debug || null
                        }
                    };
                }
            }
            return {
                ...(resultado || {}),
                success: anuncios.length > 0 || !!(resultado && resultado.success),
                total: anuncios.length,
                anuncios,
                debug: {
                    ...(resultado && resultado.debug || {}),
                    mode: 'mercado_livre_dom_emergencial',
                    emergenciaTotal: anuncios.length
                }
            };
        }

        async function aguardarBaseMercadoLivreColetavelFavoritos(opcoes = {}) {
            const webview = resolverWebviewFavoritosColeta(opcoes);
            const limite = Math.max(1, Math.min(Number(opcoes.limite) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80, 160));
            const timeoutMs = Math.max(4000, Math.min(Number(opcoes.timeoutMs) || 28000, 45000));
            const pollMs = Math.max(250, Math.min(Number(opcoes.pollMs) || 650, 1500));
            const onProgress = opcoes.onProgress;
            const inicio = Date.now();
            const deadline = inicio + timeoutMs;
            let ultimoBasico = null;
            let ultimoStatus = null;

            while (Date.now() < deadline) {
                const restante = Math.max(0, deadline - Date.now());
                ultimoBasico = await extrairCardsMercadoLivreBasicoWebview({ limite, webview }).catch(() => null);
                const anuncios = Array.isArray(ultimoBasico && ultimoBasico.anuncios) ? ultimoBasico.anuncios : [];
                if (anuncios.length) {
                    return {
                        ...(ultimoBasico || {}),
                        success: true,
                        ready: true,
                        total: anuncios.length,
                        anuncios,
                        status: ultimoStatus,
                        elapsedMs: Date.now() - inicio
                    };
                }

                if (typeof aguardarPrimeirosDadosAvantOuCardsWebview === 'function' && restante > 250) {
                    ultimoStatus = await aguardarPrimeirosDadosAvantOuCardsWebview({
                        timeoutMs: Math.min(2600, restante),
                        idleMs: 160,
                        acaoUsuario: true,
                        solicitadoPeloUsuario: true,
                        webview
                    }).catch(() => null);
                } else {
                    await esperar(Math.min(420, restante));
                }

                const statusVisiveis = Math.min(limite, Math.max(
                    Number(ultimoStatus && ultimoStatus.cardCount) || 0,
                    Number(ultimoStatus && ultimoStatus.productLinkCount) || 0
                ));
                const paginaProntaSemBase = !!(
                    ultimoStatus
                    && !ultimoStatus.loadingScreen
                    && !ultimoStatus.needsLogin
                    && !ultimoStatus.noResults
                    && (
                        statusVisiveis > 0
                        || ultimoStatus.hasAvantData
                        || Number(ultimoStatus.avantLabels || 0) >= 2
                    )
                );
                if (paginaProntaSemBase) {
                    const emergencia = await extrairBaseMercadoLivreEmergencialWebview({
                        limite,
                        timeoutMs: Math.min(5000, Math.max(1200, deadline - Date.now())),
                        webview
                    }).catch(() => null);
                    const emergenciaAnuncios = Array.isArray(emergencia && emergencia.anuncios) ? emergencia.anuncios : [];
                    if (emergenciaAnuncios.length) {
                        return {
                            ...(emergencia || {}),
                            success: true,
                            ready: true,
                            total: emergenciaAnuncios.length,
                            anuncios: emergenciaAnuncios,
                            status: ultimoStatus,
                            elapsedMs: Date.now() - inicio
                        };
                    }
                }

                emitirProgressoPrimeiraPaginaFavoritos(onProgress, {
                    etapa: 'aguardando_cards',
                    visiveis: statusVisiveis,
                    coletados: 0,
                    com_titulo: 0,
                    com_foto: 0,
                    com_preco: 0,
                    com_link: 0,
                    com_dados_avant: 0,
                    suspeitos: 0,
                    tempoRestanteMs: Math.max(0, deadline - Date.now()),
                    loadingScreen: !!(ultimoStatus && ultimoStatus.loadingScreen),
                    noResults: !!(ultimoStatus && ultimoStatus.noResults),
                    needsLogin: !!(ultimoStatus && ultimoStatus.needsLogin),
                    url: ultimoStatus && ultimoStatus.url || '',
                    diagnostico: {
                        cardCount: Number(ultimoStatus && ultimoStatus.cardCount) || 0,
                        productLinkCount: Number(ultimoStatus && ultimoStatus.productLinkCount) || 0,
                        avantLabels: Number(ultimoStatus && ultimoStatus.avantLabels) || 0,
                        basicoCardCount: Number(ultimoBasico && ultimoBasico.debug && ultimoBasico.debug.cardCount) || 0,
                        basicoProductLinkCount: Number(ultimoBasico && ultimoBasico.debug && ultimoBasico.debug.productLinkCount) || 0
                    }
                });

                if (ultimoStatus && (ultimoStatus.noResults || ultimoStatus.needsLogin)) {
                    break;
                }
                await esperar(Math.min(pollMs, Math.max(0, deadline - Date.now())));
            }

            return {
                success: false,
                ready: false,
                timeout: !(ultimoStatus && (ultimoStatus.noResults || ultimoStatus.needsLogin)),
                noResults: !!(ultimoStatus && ultimoStatus.noResults),
                needsLogin: !!(ultimoStatus && ultimoStatus.needsLogin),
                total: 0,
                anuncios: [],
                status: ultimoStatus,
                debug: ultimoBasico && ultimoBasico.debug || null,
                elapsedMs: Date.now() - inicio
            };
        }

        function chaveAnuncioPrimeiraPaginaFavoritos(anuncio) {
            return chaveCanonicaAnuncioFavoritos(anuncio);
        }

        function anuncioPrimeiraPaginaTemDadosAvant(anuncio) {
            if (!anuncio) return false;
            const fonte = normalizarFonte(anuncio.vendasFonte || anuncio.vendas_fonte || '');
            if (fonteVendasConfiavel(fonte) && hasNumeroVendas(parseNumeroVendas(anuncio.vendas))) return true;
            return !!(anuncio.vendedor || anuncio.data_criacao || anuncio.cacheAvant || anuncio.vendas_estimadas || anuncio.media_mensal);
        }

        function resumoPrimeiraPaginaFavoritos(totalVisiveis, anuncios, extra = {}) {
            const lista = Array.isArray(anuncios) ? anuncios.filter(Boolean) : [];
            const avantNaoVinculado = Array.isArray(anuncios && anuncios.__avantNaoVinculado)
                ? anuncios.__avantNaoVinculado.length
                : Number(extra.avant_nao_vinculado) || 0;
            const comTitulo = lista.filter(item => tituloValidoFavoritosCanonico(item && (item.titulo || item.title), item && (item.id || item.mlb))).length;
            const comFoto = lista.filter(imagemValidaFavoritosCanonico).length;
            const comPreco = lista.filter(precoValidoFavoritosCanonico).length;
            const comLink = lista.filter(item => !!limparLinkProdutoMercadoLivreFavoritos(item && (item.url || item.permalink || item.link), item && (item.id || item.mlb))).length;
            const comAvant = lista.filter(anuncioPrimeiraPaginaTemDadosAvant).length;
            lista.forEach(item => {
                if (item) item.estado_qualidade = classificarQualidadeAnuncioFavoritosCanonico(item);
            });
            const suspeitos = lista.filter(item => item && item.estado_qualidade === 'suspeito').length;
            return {
                visiveis: Number(totalVisiveis) || lista.length,
                coletados: lista.length,
                com_titulo: comTitulo,
                com_foto: comFoto,
                com_preco: comPreco,
                com_link: comLink,
                com_dados_avant: comAvant,
                incompletos: lista.filter(item => item && item.estado_qualidade === 'incompleto').length,
                suspeitos,
                avant_nao_vinculado: avantNaoVinculado,
                ...extra
            };
        }

        function assinaturaEstabilidadePrimeiraPaginaFavoritos(anuncios) {
            const lista = Array.isArray(anuncios) ? anuncios.filter(Boolean) : [];
            return lista.map(item => {
                const chave = typeof chaveCanonicaAnuncioFavoritos === 'function'
                    ? chaveCanonicaAnuncioFavoritos(item)
                    : String(item && (item.id || item.mlb || item.url || item.link) || '').trim().toLowerCase();
                return JSON.stringify([
                    chave,
                    item && (item.titulo || item.title) || '',
                    obterImagemAnuncioFavoritos(item),
                    item && (item.preco ?? item.price ?? ''),
                    item && (item.preco_original ?? item.original_price ?? ''),
                    item && (item.preco_promocional ?? item.promotional_price ?? ''),
                    item && item.vendedor || '',
                    item ? (item.vendas ?? '') : '',
                    item && (item.vendasFonte || item.vendas_fonte) || '',
                    item && item.data_criacao || '',
                    item && (item.tipo_anuncio || item.listing_type_id) || '',
                    item && (item.is_full ?? item.full ?? ''),
                    item && (item.condicao || item.condition || item.item_condition) || '',
                    item && (item.media_mensal ?? item.ritmo_atual ?? ''),
                    item ? (item.visitas ?? '') : ''
                ]);
            }).sort().join('|');
        }

        function deveEncerrarPlateauPrimeiraPaginaFavoritos(estado = {}) {
            return Number(estado.passada) >= 2
                && estado.passadaCompleta === true
                && !!String(estado.assinaturaAtual || '')
                && String(estado.assinaturaAtual) === String(estado.assinaturaAnterior || '')
                && Number(estado.pendentesAvant) === 0
                && (estado.mutationQuietMs === undefined || Number(estado.mutationQuietMs) >= 800);
        }

        function montarPosicoesVarreduraPrimeiraPaginaFavoritos(alturaPagina, alturaViewport, limiteAnuncios) {
            const viewport = Math.max(560, Number(alturaViewport) || 800);
            const altura = Math.max(viewport, Number(alturaPagina) || viewport);
            const finalPagina = Math.max(0, altura - viewport - 20);
            const passo = Math.max(540, Math.floor(viewport * 0.9));
            const posicoes = [];
            for (let y = 0; y <= finalPagina; y += passo) {
                posicoes.push(Math.max(0, Math.floor(y)));
            }
            posicoes.push(finalPagina);
            const completas = Array.from(new Set(posicoes)).sort((a, b) => a - b);
            const maxPosicoes = Math.max(6, Math.ceil((Number(limiteAnuncios) || 80) / 5) + 3);
            if (completas.length <= maxPosicoes) return completas;
            const amostradas = [];
            for (let indice = 0; indice < maxPosicoes; indice += 1) {
                const origem = Math.round((indice * (completas.length - 1)) / (maxPosicoes - 1));
                amostradas.push(completas[origem]);
            }
            return Array.from(new Set(amostradas)).sort((a, b) => a - b);
        }

        function alturaVarreduraPrimeiraPaginaFavoritos(alturaDocumento, fimResultados, alturaViewport) {
            const viewport = Math.max(560, Number(alturaViewport) || 800);
            const documento = Math.max(viewport, Number(alturaDocumento) || viewport);
            const resultados = Math.max(0, Number(fimResultados) || 0);
            if (resultados <= viewport) return documento;
            return Math.min(documento, Math.max(viewport, resultados + Math.floor(viewport * 0.35)));
        }

        function emitirProgressoPrimeiraPaginaFavoritos(onProgress, payload) {
            if (typeof onProgress !== 'function') return;
            try {
                onProgress(payload || {});
            } catch (_err) {}
        }

        async function executarFilaLimitadaFavoritos(items, limite, worker) {
            const lista = Array.isArray(items) ? items : [];
            const max = Math.max(1, Math.min(Number(limite) || 4, 4));
            let index = 0;
            const runners = Array.from({ length: Math.min(max, lista.length) }, async () => {
                while (index < lista.length) {
                    const atual = lista[index];
                    index += 1;
                    await worker(atual);
                }
            });
            await Promise.all(runners);
        }

        async function completarBaseMercadoLivreComApiFavoritos(anuncios, opcoes = {}) {
            const lista = Array.isArray(anuncios) ? anuncios : [];
            if (!lista.length || typeof consultarItemApiMercadoLivre !== 'function') return lista;
            const deadline = Number(opcoes.deadlineMs) || 0;
            const maxItens = Math.max(0, Number(opcoes.maxItens) || 0);
            const timeoutMs = Math.max(1200, Math.min(Number(opcoes.timeoutMs) || 3500, 8000));
            const pendentesTodos = lista.filter(item => {
                const id = normalizarMlbFavoritosCanonico(item && (item.id || item.mlb || item.url || item.link || item.permalink));
                if (!id) return false;
                return !tituloValidoFavoritosCanonico(item && (item.titulo || item.title), id)
                    || !imagemValidaFavoritosCanonico(item)
                    || !precoValidoFavoritosCanonico(item)
                    || !limparLinkProdutoMercadoLivreFavoritos(item && (item.url || item.permalink || item.link), id);
            });
            const pendentes = maxItens > 0 ? pendentesTodos.slice(0, maxItens) : pendentesTodos;
            if (!pendentes.length) return lista;
            await executarFilaLimitadaFavoritos(pendentes, opcoes.concorrencia || 4, async (alvo) => {
                if (deadline && Date.now() > deadline - 1500) return;
                const itemId = normalizarMlbFavoritosCanonico(alvo && (alvo.id || alvo.mlb || alvo.url || alvo.link || alvo.permalink));
                if (!itemId) return;
                try {
                    const apiInfo = await Promise.race([
                        consultarItemApiMercadoLivre(itemId),
                        esperar(timeoutMs).then(() => null)
                    ]);
                    if (!apiInfo) return;
                    const completo = prepararAnuncioMercadoLivreCanonico(apiInfo, 'mercado_livre_api');
                    const mescladoLista = mesclarAnunciosAvant([alvo], [completo]);
                    const chaveAlvo = chaveCanonicaAnuncioFavoritos(alvo);
                    const chaveApi = chaveCanonicaAnuncioFavoritos(completo);
                    const mesclado = mescladoLista.find(item => {
                        const chaveItem = chaveCanonicaAnuncioFavoritos(item);
                        return chaveItem && (chaveItem === chaveAlvo || chaveItem === chaveApi);
                    });
                    if (!mesclado) return;
                    Object.keys(alvo).forEach(key => delete alvo[key]);
                    Object.assign(alvo, mesclado);
                    alvo.estado_qualidade = classificarQualidadeAnuncioFavoritosCanonico(alvo);
                } catch (err) {
                    console.warn('Nao foi possivel completar base ML pela API:', itemId, err);
                }
            });
            return lista;
        }

        async function extrairCacheAvantProCardsWebview(opcoes = {}) {
            const webview = resolverWebviewFavoritosColeta(opcoes);
            if (!webview || typeof webview.executeJavaScript !== 'function') return { success: false, total: 0, anuncios: [] };
            const limite = Math.max(1, Math.min(Number(opcoes.limite) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80, 120));
            return await webview.executeJavaScript(`
                (function () {
                    var cache = window.__JK_AVANT_CARD_DATA_CACHE || {};
                    var anuncios = Object.keys(cache)
                        .map(function (key) {
                            var item = cache[key];
                            if (!item || typeof item !== 'object') return null;
                            return Object.assign({}, item, {
                                chave_canonica: item.chave_canonica || key,
                                chaveCanonica: item.chaveCanonica || item.chave_canonica || key,
                                origem_dados: item.origem_dados || 'avantpro_card_panel_cache'
                            });
                        })
                        .filter(Boolean)
                        .slice(0, ${JSON.stringify(limite)});
                    return {
                        success: true,
                        total: anuncios.length,
                        anuncios: anuncios
                    };
                })();
            `, true).catch((err) => ({
                success: false,
                total: 0,
                anuncios: [],
                error: err && err.message ? err.message : String(err)
            }));
        }

        async function capturarAvantProCardsVisiveisRapidoWebview(opcoes = {}) {
            const webview = resolverWebviewFavoritosColeta(opcoes);
            if (!webview || typeof webview.executeJavaScript !== 'function') return { success: false, total: 0, anuncios: [] };
            const limite = Math.max(1, Math.min(Number(opcoes.limite) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80, 120));
            return await webview.executeJavaScript(`
                (function () {
                    var limite = ${JSON.stringify(limite)};
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var visible = function (node) {
                        try {
                            var rect = node && node.getBoundingClientRect ? node.getBoundingClientRect() : null;
                            var style = node && window.getComputedStyle ? window.getComputedStyle(node) : null;
                            var vw = window.innerWidth || document.documentElement.clientWidth || 1280;
                            var vh = window.innerHeight || document.documentElement.clientHeight || 900;
                            return !!(rect && rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0 && rect.top < vh && rect.left < vw && (!style || (style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) !== 0)));
                        } catch (_err) {
                            return false;
                        }
                    };
                    var idDe = function (value) {
                        var text = String(value || '');
                        try { text = decodeURIComponent(text); } catch (_err) {}
                        var match = text.match(/\\bMLB-?(\\d{6,})\\b/i) || text.match(/[?&](?:wid|item_id)=(MLB\\d{6,})/i);
                        if (!match) return '';
                        var raw = String(match[1] || '').replace('-', '').toUpperCase();
                        return raw.indexOf('MLB') === 0 ? raw : ('MLB' + raw);
                    };
                    var cleanUrl = function (href, id) {
                        href = String(href || '').split('#')[0].trim();
                        if (/^\\/\\//.test(href)) href = 'https:' + href;
                        if (/^\\//.test(href)) href = 'https://www.mercadolivre.com.br' + href;
                        if (!href && id) return 'https://produto.mercadolivre.com.br/' + String(id).replace('MLB', 'MLB-');
                        return href;
                    };
                    var numeroHumano = function (value, decimal) {
                        var raw = String(value || '').trim().toLowerCase();
                        if (!raw) return null;
                        var suffix = /\\b(mil|k)\\b/i.test(raw) ? 'k' : '';
                        var n = raw.replace(/[^0-9,.-]/g, '');
                        if (n.indexOf('.') >= 0 && n.indexOf(',') >= 0) {
                            n = n.lastIndexOf('.') > n.lastIndexOf(',') ? n.replace(/,/g, '') : n.replace(/\\./g, '').replace(/,/g, '.');
                        } else if (n.indexOf(',') >= 0) {
                            n = n.replace(/\\./g, '').replace(/,/g, '.');
                        }
                        var parsed = parseFloat(n);
                        if (!isFinite(parsed)) return null;
                        if (suffix) parsed *= 1000;
                        return decimal ? parsed : Math.round(parsed);
                    };
                    var labelValue = function (row) {
                        var labelNode = row && row.querySelector && row.querySelector('.avantpro-product-info-row-label, [class*="label"]');
                        var valueNode = row && row.querySelector && row.querySelector('.avantpro-product-info-row-value, [class*="value"]');
                        var label = String(labelNode && (labelNode.innerText || labelNode.textContent) || '').replace(/\\s+/g, ' ').trim();
                        var value = String(valueNode && (valueNode.innerText || valueNode.textContent) || '').replace(/\\s+/g, ' ').trim();
                        var text = String(row && (row.innerText || row.textContent) || '').replace(/\\s+/g, ' ').trim();
                        if (!value && label && text.indexOf(label) >= 0) value = text.slice(text.indexOf(label) + label.length).replace(/^\\s*[:\\-]?\\s*/, '').trim();
                        return { label: label, value: value, text: text, busca: normalizar(label + ' ' + text) };
                    };
                    var preencher = function (item, row) {
                        var lv = labelValue(row);
                        var busca = lv.busca;
                        var value = lv.value || lv.text;
                        if (/vendas?\\s+do\\s+(produto|anuncio|item)|vendas?\\s+estimad/.test(busca)) {
                            var vendas = numeroHumano(value, false);
                            if (Number.isFinite(vendas)) item.vendas = vendas;
                        } else if (/ritmo\\s+atual|vendas?\\s*\\/\\s*mes|media\\s+mensal/.test(busca)) {
                            var ritmo = numeroHumano(value, true);
                            if (Number.isFinite(ritmo)) {
                                item.media_mensal = ritmo;
                                item.ritmo_atual = ritmo;
                            }
                        } else if (/nome\\s+do\\s+vendedor/.test(busca) || (/\\bvendedor\\b/.test(busca) && !/reputacao|localizacao/.test(busca))) {
                            var vendedor = String(lv.value || '').replace(/^\\s*[:\\-]?\\s*/, '').trim();
                            if (vendedor && vendedor.length <= 120 && !/^\\d+$/.test(vendedor)) item.vendedor = vendedor;
                        } else if (/anuncio\\s+criado\\s+em|catalogo\\s+criado\\s+em|criado\\s+em/.test(busca)) {
                            var data = String(value || '').match(/\\b\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}\\b|\\b20\\d{2}-\\d{2}-\\d{2}\\b/);
                            if (data && data[0]) item.data_criacao = data[0];
                        } else if (/visitas?\\s+do\\s+anuncio|\\bvisitas?\\b/.test(busca)) {
                            var visitas = numeroHumano(value, false);
                            if (Number.isFinite(visitas)) item.visitas = visitas;
                        } else if (/participacao/.test(busca)) {
                            item.participacao = lv.value || '';
                        } else if (/comissao/.test(busca)) {
                            item.comissao = lv.value || '';
                        } else if (/reputacao\\s+do\\s+vendedor/.test(busca)) {
                            item.reputacao_vendedor = lv.value || '';
                        } else if (/localizacao\\s+do\\s+vendedor/.test(busca)) {
                            item.localizacao_vendedor = lv.value || '';
                        }
                    };
                    var rowSelector = '.avantpro-product-info-row, .created-time-card, [class*="avantpro-product-info"], [class*="created-time-card"], [class*="Avantpro"][class*="row"], [class*="avantpro"][class*="row"]';
                    var primeiraLinhaAvant = null;
                    try { primeiraLinhaAvant = document.querySelector('.avantpro-product-info-row, .created-time-card'); } catch (_rowExactErr) {}
                    if (!primeiraLinhaAvant) {
                        try { primeiraLinhaAvant = document.querySelector('[class*="avantpro-product-info"], [class*="created-time-card"]'); } catch (_rowFallbackErr) {}
                    }
                    if (!primeiraLinhaAvant) return { success: true, total: 0, anuncios: [] };
                    var cardSelectors = [
                        'li.ui-search-layout__item',
                        'div.ui-search-result__wrapper',
                        'div.ui-search-result',
                        'div.poly-card',
                        'section.poly-card',
                        'article.poly-card',
                        'article.ui-search-result',
                        '[data-testid="product-card"]',
                        '[data-testid="item-card"]',
                        '[class*="product-card"]',
                        '[class*="poly-card"]',
                        '[class*="shops__layout-item"]',
                        'main ol > li',
                        'main ul > li'
                    ].join(',');
                    var cards = [];
                    try { cards = Array.prototype.slice.call(document.querySelectorAll(cardSelectors)); } catch (_cardsErr) {}
                    var anuncios = [];
                    cards.filter(visible).forEach(function (card) {
                        if (anuncios.length >= limite) return;
                        var rows = [];
                        try { rows = Array.prototype.slice.call(card.querySelectorAll(rowSelector)); } catch (_rowsErr) {}
                        rows = rows.filter(function (row) {
                            var text = normalizar(String(row && (row.innerText || row.textContent) || ''));
                            return /vendas?|ritmo|vendedor|criado|visitas|participa|comissao|reputacao/.test(text);
                        });
                        if (!rows.length) return;
                        var href = '';
                        try {
                            href = Array.prototype.slice.call(card.querySelectorAll('a[href]'))
                                .map(function (a) { return a.href || a.getAttribute('href') || ''; })
                                .filter(function (link) { return /\\bMLB-?\\d{6,}\\b|[?&](?:wid|item_id)=MLB\\d{6,}|\\/p\\/MLB|\\/up\\/MLB|produto\\.mercadolivre\\.com\\.br/i.test(link); })[0] || '';
                        } catch (_hrefErr) {}
                        var id = idDe(href);
                        href = cleanUrl(href, id);
                        var canonical = id ? ('mlb:' + id) : (href ? ('link:' + href.toLowerCase()) : '');
                        if (!canonical) return;
                        var item = {
                            id: id,
                            mlb: id,
                            url: href,
                            permalink: href,
                            link: href,
                            chave_canonica: canonical,
                            chaveCanonica: canonical,
                            origem_dados: 'avantpro_dom_visivel'
                        };
                        rows.forEach(function (row) { preencher(item, row); });
                        var temDados = item.vendas !== null && item.vendas !== undefined && item.vendas !== ''
                            || item.media_mensal !== null && item.media_mensal !== undefined && item.media_mensal !== ''
                            || item.vendedor
                            || item.data_criacao
                            || item.visitas !== null && item.visitas !== undefined && item.visitas !== '';
                        if (!temDados) return;
                        if (item.vendas !== null && item.vendas !== undefined && item.vendas !== '') {
                            item.vendasFonte = 'avantpro_dom';
                            item.vendas_fonte = 'avantpro_dom';
                        }
                        if (item.vendedor) {
                            item.vendedorFonte = 'avantpro_dom';
                            item.vendedor_fonte = 'avantpro_dom';
                        }
                        if (item.data_criacao) {
                            item.dataCriacaoFonte = 'avantpro_dom';
                            item.data_criacao_fonte = 'avantpro_dom';
                        }
                        if (item.media_mensal !== null && item.media_mensal !== undefined && item.media_mensal !== '') {
                            item.media_mensal_fonte = 'avantpro_dom';
                        }
                        anuncios.push(item);
                    });
                    var cache = window.__JK_AVANT_CARD_DATA_CACHE || {};
                    anuncios.forEach(function (item) {
                        cache[item.chave_canonica] = Object.assign({}, cache[item.chave_canonica] || {}, item);
                        var incremental = window.__JK_FAVORITOS_INCREMENTAL_COLLECTOR_V1;
                        if (incremental && incremental.resolvedKeys) {
                            incremental.resolvedKeys[item.chave_canonica] = Date.now();
                            if (incremental.pendingKeys) delete incremental.pendingKeys[item.chave_canonica];
                        }
                    });
                    window.__JK_AVANT_CARD_DATA_CACHE = cache;
                    return { success: true, total: anuncios.length, anuncios: anuncios };
                })();
            `, true).catch((err) => ({
                success: false,
                total: 0,
                anuncios: [],
                error: err && err.message ? err.message : String(err)
            }));
        }

        async function obterMetricaRolagemMercadoLivreFavoritos(webview = mlWebviewEl) {
            if (!webview || typeof webview.executeJavaScript !== 'function') return { y: 0, height: 0, view: 800, atBottom: true };
            return await webview.executeJavaScript(`
                (function () {
                    var candidatos = [document.scrollingElement, document.documentElement, document.body]
                        .concat(Array.prototype.slice.call(document.querySelectorAll('main, section, div, ol, ul')));
                    var melhor = null;
                    var melhorDelta = 0;
                    candidatos.forEach(function (node) {
                        if (!node) return;
                        var delta = Math.max(0, Number(node.scrollHeight || 0) - Number(node.clientHeight || 0));
                        if (delta > melhorDelta) {
                            melhor = node;
                            melhorDelta = delta;
                        }
                    });
                    var doc = melhor || document.scrollingElement || document.documentElement || document.body;
                    var y = Math.max(window.scrollY || 0, (doc && doc.scrollTop) || 0);
                    var height = Math.max(
                        (doc && doc.scrollHeight) || 0,
                        document.documentElement ? document.documentElement.scrollHeight || 0 : 0,
                        document.body ? document.body.scrollHeight || 0 : 0
                    );
                    var view = Math.max(
                        window.innerHeight || 0,
                        (doc && doc.clientHeight) || 0,
                        document.documentElement ? document.documentElement.clientHeight || 0 : 0,
                        800
                    );
                    var rootsResultados = Array.prototype.slice.call(document.querySelectorAll(
                        'ol.ui-search-layout, ul.ui-search-layout, main .ui-search-layout, [class*="ui-search-layout"][class*="results"]'
                    ));
                    var cardsResultados = Array.prototype.slice.call(document.querySelectorAll(
                        'li.ui-search-layout__item, div.ui-search-result__wrapper, article.ui-search-result, div.poly-card, article.poly-card'
                    ));
                    var alvosResultados = rootsResultados.length ? rootsResultados : cardsResultados;
                    var fimResultados = alvosResultados.reduce(function (maior, node) {
                        if (!node || !node.getBoundingClientRect) return maior;
                        var rect = node.getBoundingClientRect();
                        if (!rect || rect.height <= 0 || rect.width <= 0) return maior;
                        return Math.max(maior, y + rect.bottom);
                    }, 0);
                    return { y: y, height: height, view: view, resultsBottom: fimResultados, atBottom: y + view >= height - 48 };
                })();
            `, true).catch(() => ({ y: 0, height: 0, view: 800, atBottom: true }));
        }

        async function rolarMercadoLivreFavoritos(y, webview = mlWebviewEl) {
            if (!webview || typeof webview.executeJavaScript !== 'function') return null;
            const destino = Math.max(0, Math.floor(Number(y) || 0));
            return await webview.executeJavaScript(`
                (function () {
                    var doc = document.scrollingElement || document.documentElement || document.body;
                    window.scrollTo(0, ${destino});
                    if (doc) doc.scrollTop = ${destino};
                    try {
                        window.dispatchEvent(new WheelEvent('wheel', {
                            deltaY: ${destino},
                            bubbles: true,
                            cancelable: true
                        }));
                    } catch (_wheelErr) {}
                    return {
                        y: Math.max(window.scrollY || 0, (doc && doc.scrollTop) || 0),
                        height: Math.max(
                            (doc && doc.scrollHeight) || 0,
                            document.documentElement ? document.documentElement.scrollHeight || 0 : 0,
                            document.body ? document.body.scrollHeight || 0 : 0
                        ),
                        view: Math.max(window.innerHeight || 0, (doc && doc.clientHeight) || 0, 800)
                    };
                })();
            `, true).catch(() => null);
        }

        async function materializarCardsPrimeiraPaginaMercadoLivreFavoritos(opcoes = {}) {
            const webview = resolverWebviewFavoritosColeta(opcoes);
            const limite = Math.max(20, Math.min(Number(opcoes.limite) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80, 100));
            const deadline = Number(opcoes.deadlineMs) || (Date.now() + 25000);
            const onProgress = opcoes.onProgress;
            let anuncios = [];
            let ultimaAssinatura = '';
            let estaveis = 0;
            const metricaInicial = await obterMetricaRolagemMercadoLivreFavoritos(webview);
            const originalY = Math.max(0, Number(metricaInicial && metricaInicial.y) || 0);
            let viewport = Math.max(560, Number(metricaInicial && metricaInicial.view) || 800);
            let y = 0;
            let passos = 0;

            while (Date.now() < deadline) {
                passos += 1;
                await rolarMercadoLivreFavoritos(y, webview);
                await esperar(420);
                const basico = await extrairCardsMercadoLivreBasicoWebview({ limite, webview }).catch(() => null);
                anuncios = mesclarAnunciosAvant(anuncios, (basico && basico.anuncios) || []);
                const metricaAtual = await obterMetricaRolagemMercadoLivreFavoritos(webview);
                viewport = Math.max(560, Number(metricaAtual && metricaAtual.view) || viewport);
                const resumo = resumoPrimeiraPaginaFavoritos(anuncios.length, anuncios, {
                    etapa: 'materializando',
                    y: metricaAtual && metricaAtual.y,
                    height: metricaAtual && metricaAtual.height
                });
                emitirProgressoPrimeiraPaginaFavoritos(onProgress, resumo);

                const assinatura = anuncios.map(chaveAnuncioPrimeiraPaginaFavoritos).filter(Boolean).join('|');
                estaveis = assinatura && assinatura === ultimaAssinatura ? estaveis + 1 : 0;
                ultimaAssinatura = assinatura;
                if (anuncios.length >= limite) break;
                if (metricaAtual && metricaAtual.atBottom && estaveis >= 1 && passos >= 4) break;
                const proximoY = Math.max(y + Math.floor(viewport * 0.9), Number(metricaAtual && metricaAtual.y || 0) + Math.floor(viewport * 0.75));
                if (metricaAtual && metricaAtual.height && proximoY > metricaAtual.height + viewport) break;
                y = proximoY;
            }

            await rolarMercadoLivreFavoritos(originalY, webview);
            return {
                anuncios: anuncios.slice(0, limite),
                totalVisiveis: Math.min(limite, anuncios.length),
                originalY
            };
        }

        async function acionarCardsAvantProFilaWebview(opcoes = {}) {
            const webview = resolverWebviewFavoritosColeta(opcoes);
            if (!webview || typeof webview.executeJavaScript !== 'function') return { clicked: 0, totalCandidates: 0, keys: [] };
            const maxClicksValor = opcoes.maxClicks === undefined ? 0 : Number(opcoes.maxClicks);
            const maxClicks = Math.max(0, Math.min(8, Number.isFinite(maxClicksValor) ? maxClicksValor : 0));
            const maxTentativasPorCard = Math.max(1, Math.min(3, Number(opcoes.maxTentativasPorCard) || 3));
            const maxRuntimeMsValor = opcoes.maxRuntimeMs === undefined ? 4500 : Number(opcoes.maxRuntimeMs);
            const maxRuntimeMs = Math.max(1200, Math.min(8000, Number.isFinite(maxRuntimeMsValor) ? maxRuntimeMsValor : 4500));
            const deepScan = opcoes.deepScan !== false;
            const checkLogin = opcoes.checkLogin !== false;
            if (maxClicks <= 0) return { clicked: 0, totalCandidates: 0, keys: [], capturados: 0, skipped: true };
            return await webview.executeJavaScript(`
                (async function () {
                    var maxClicks = ${JSON.stringify(maxClicks)};
                    var maxTentativasPorCard = ${JSON.stringify(maxTentativasPorCard)};
                    var maxRuntimeMs = ${JSON.stringify(maxRuntimeMs)};
                    var deepScan = ${JSON.stringify(deepScan)};
                    var checkLogin = ${JSON.stringify(checkLogin)};
                    var startedAt = Date.now();
                    var endAt = startedAt + maxRuntimeMs;
                    var hasTime = function (bufferMs) { return Date.now() + (Number(bufferMs) || 0) < endAt; };
                    var sleep = function (ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); };
                    var normalizar = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (e) {}
                        return text.toLowerCase();
                    };
                    var cleanUrl = function (href) {
                        href = String(href || '').split('#')[0].trim();
                        if (/^\\/\\//.test(href)) return 'https:' + href;
                        if (/^\\//.test(href)) return 'https://www.mercadolivre.com.br' + href;
                        return href;
                    };
                    var queryAllDeep = function (selector, root) {
                        var found = [];
                        var visited = [];
                        var visit = function (base) {
                            if (!base || visited.indexOf(base) >= 0) return;
                            visited.push(base);
                            try {
                                if (base.querySelectorAll) {
                                    Array.prototype.slice.call(base.querySelectorAll(selector)).forEach(function (node) {
                                        if (found.indexOf(node) < 0) found.push(node);
                                    });
                                    Array.prototype.slice.call(base.querySelectorAll('*')).forEach(function (node) {
                                        if (node && node.shadowRoot) visit(node.shadowRoot);
                                    });
                                }
                            } catch (_err) {}
                        };
                        visit(root || document);
                        return found;
                    };
                    var visible = function (node) {
                        try {
                            var rect = node && node.getBoundingClientRect ? node.getBoundingClientRect() : null;
                            var style = node && window.getComputedStyle ? window.getComputedStyle(node) : null;
                            var vw = window.innerWidth || document.documentElement.clientWidth || 1280;
                            var vh = window.innerHeight || document.documentElement.clientHeight || 900;
                            return !!(rect && rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0 && rect.top < vh && rect.left < vw && (!style || (style.display !== 'none' && style.visibility !== 'hidden' && Number(style.opacity || 1) !== 0)));
                        } catch (_err) {
                            return false;
                        }
                    };
                    var textoNode = function (node) {
                        if (!node) return '';
                        return normalizar([
                            node.innerText,
                            node.textContent,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title'),
                            node.getAttribute && node.getAttribute('class'),
                            node.getAttribute && node.getAttribute('id')
                        ].filter(Boolean).join(' '));
                    };
                    var idDe = function (value) {
                        var text = String(value || '');
                        try { text = decodeURIComponent(text); } catch (_err) {}
                        var match = text.match(/\\bMLB-?(\\d{6,})\\b/i) || text.match(/[?&](?:wid|item_id)=(MLB\\d{6,})/i);
                        if (!match) return '';
                        var raw = String(match[1] || '').replace('-', '').toUpperCase();
                        return raw.indexOf('MLB') === 0 ? raw : ('MLB' + raw);
                    };
                    var cardSelectors = [
                        'li.ui-search-layout__item',
                        'div.ui-search-result__wrapper',
                        'div.ui-search-result',
                        'div.poly-card',
                        'section.poly-card',
                        'article.poly-card',
                        'article.ui-search-result',
                        '[data-testid="product-card"]',
                        '[data-testid="item-card"]',
                        '[class*="product-card"]',
                        '[class*="poly-card"]',
                        '[class*="shops__layout-item"]',
                        'main ol > li',
                        'main ul > li'
                    ].join(',');
                    var productHref = function (card) {
                        var links = [];
                        try { links = Array.prototype.slice.call(card && card.querySelectorAll ? card.querySelectorAll('a[href]') : []); } catch (_directHrefErr) {}
                        try {
                            if (card && card.matches && card.matches('a[href]') && links.indexOf(card) < 0) {
                                links.unshift(card);
                            }
                        } catch (_selfHrefErr) {}
                        if (!links.length) links = queryAllDeep('a[href]', card);
                        for (var i = 0; i < links.length; i += 1) {
                            var rawHref = links[i].href || links[i].getAttribute('href') || '';
                            if (/\\bMLB-?\\d{6,}\\b|[?&](?:wid|item_id)=MLB\\d{6,}|\\/p\\/MLB|\\/up\\/MLB|produto\\.mercadolivre\\.com\\.br/i.test(rawHref)) return rawHref;
                        }
                        return '';
                    };
                    var keyCard = function (card) {
                        var href = productHref(card);
                        var attrText = [
                            card && card.getAttribute && card.getAttribute('data-item-id'),
                            card && card.getAttribute && card.getAttribute('data-id'),
                            card && card.getAttribute && card.getAttribute('id'),
                            card && card.getAttribute && card.getAttribute('aria-label')
                        ].filter(Boolean).join(' ');
                        var id = idDe(href || attrText || '');
                        if (id) return 'id:' + id;
                        if (href) {
                            var linkLimpo = cleanProductUrl(href, '').toLowerCase();
                            return linkLimpo ? ('url:' + linkLimpo) : ('url:' + cleanUrl(href).split('#')[0].toLowerCase());
                        }
                        return '';
                    };
                    var cleanProductUrl = function (href, id) {
                        href = cleanUrl(href);
                        if (!href && id) return 'https://produto.mercadolivre.com.br/' + String(id).replace('MLB', 'MLB-');
                        if (!href) return '';
                        try {
                            var parsed = new URL(href, 'https://www.mercadolivre.com.br');
                            parsed.hash = '';
                            [
                                'tracking_id',
                                'position',
                                'polycard_client',
                                'sid',
                                'searchVariation',
                                'backend_model',
                                'backend_type',
                                'client',
                                'reco_item_pos',
                                'reco_backend',
                                'reco_backend_type',
                                'reco_client',
                                'reco_id',
                                'c_id',
                                'pdp_filters',
                                'picker_url',
                                'quantity',
                                'variation',
                                'loader',
                                'noIndex'
                            ].forEach(function (param) { parsed.searchParams.delete(param); });
                            parsed.pathname = parsed.pathname.replace(/\\/+$/, '');
                            return parsed.toString().replace(/[?&]$/, '');
                        } catch (_err) {
                            return href;
                        }
                    };
                    var tituloFracoCard = function (value) {
                        var text = normalizar(value);
                        if (!text) return true;
                        if (/^(jm|novo|usado|patrocinado|mais vendido|r\\$|frete|chegar|vendid|mercado livre|favoritos|compras|produto relacionado|opcoes de compra)$/i.test(text)) return true;
                        if (/^mlb\\d+$/i.test(text.replace(/-/g, ''))) return true;
                        return text.length <= 3;
                    };
                    var tituloDoCard = function (card) {
                        var candidatos = queryAllDeep('h2, h3, [class*="title"], [class*="name"], [class*="poly-component__title"], [class*="ui-search-item__title"], [data-testid*="title"], a[href]', card);
                        candidatos.push(card);
                        for (var i = 0; i < candidatos.length; i += 1) {
                            var node = candidatos[i];
                            var text = String((node && (
                                node.getAttribute && (node.getAttribute('title') || node.getAttribute('aria-label')) ||
                                node.innerText ||
                                node.textContent
                            )) || '').replace(/\\s+/g, ' ').trim();
                            if (text.length >= 8 && !tituloFracoCard(text)) return text.slice(0, 240);
                        }
                        return '';
                    };
                    var imagemValidaCard = function (url) {
                        var text = String(url || '').trim();
                        if (!text || /^data:/i.test(text)) return false;
                        if (!/^https?:\\/\\//i.test(text) && !/^\\/\\//.test(text)) return false;
                        return !/(logo|avatar|sprite|icon|favicon|badge|medal|avantpro|meliplus)/i.test(text);
                    };
                    var imagemDoCard = function (card) {
                        var imgs = queryAllDeep('img, source[srcset], source[data-srcset]', card);
                        var candidatos = imgs.map(function (img, index) {
                            var srcset = img.getAttribute && (img.getAttribute('srcset') || img.getAttribute('data-srcset'));
                            var srcsetUrl = '';
                            if (srcset) {
                                srcsetUrl = String(srcset).split(',').map(function (part) {
                                    return part.trim().split(/\\s+/)[0] || '';
                                }).filter(imagemValidaCard)[0] || '';
                            }
                            var url = srcsetUrl
                                || String(img.currentSrc || '')
                                || String(img.src || '')
                                || String(img.getAttribute && (img.getAttribute('src') || img.getAttribute('data-src') || img.getAttribute('data-original') || img.getAttribute('data-lazy') || '') || '');
                            var rect = null;
                            try { rect = img.getBoundingClientRect && img.getBoundingClientRect(); } catch (_rectErr) {}
                            return {
                                url: url,
                                area: rect ? Math.max(0, rect.width) * Math.max(0, rect.height) : 0,
                                index: index
                            };
                        }).filter(function (item) { return imagemValidaCard(item.url); });
                        candidatos.sort(function (a, b) { return b.area - a.area || a.index - b.index; });
                        return candidatos[0] && candidatos[0].url || '';
                    };
                    var parsePrecoTextoCard = function (valor) {
                        var match = String(valor || '').match(/R\\$\\s*([0-9.]+)(?:\\s*,\\s*([0-9]{1,2}))?/);
                        if (!match) return null;
                        var inteiro = String(match[1] || '').replace(/\\./g, '');
                        var cents = String(match[2] || '0').padEnd(2, '0').slice(0, 2);
                        var value = Number(inteiro + '.' + cents);
                        return Number.isFinite(value) ? value : null;
                    };
                    var nodeDentroAvantCard = function (node) {
                        var atual = node;
                        for (var i = 0; atual && i < 8; i += 1) {
                            var cls = String(atual.className || '');
                            var id = String(atual.id || '');
                            if (/avant|created-time-card|product-info-row|faturamento|comissao|frete/i.test(cls + ' ' + id)) return true;
                            atual = atual.parentElement;
                        }
                        return false;
                    };
                    var textoPrecoIndesejadoCard = function (node) {
                        var texto = String(node && (node.innerText || node.textContent) || '').replace(/\\s+/g, ' ');
                        var parent = node && node.parentElement ? String(node.parentElement.innerText || node.parentElement.textContent || '').replace(/\\s+/g, ' ') : texto;
                        return /frete|comiss[aã]o|faturamento|taxa|categoria|total\\s+de\\s+vendas|vendas\\s+do\\s+produto|ritmo|visitas/i.test(texto + ' ' + parent);
                    };
                    var parsePrecoNodeCard = function (node) {
                        if (!node || nodeDentroAvantCard(node) || textoPrecoIndesejadoCard(node)) return null;
                        var fractionNode = node.querySelector && node.querySelector('.andes-money-amount__fraction, .price-tag-fraction, [class*="fraction"]');
                        var centsNode = node.querySelector && node.querySelector('.andes-money-amount__cents, .price-tag-cents, [class*="cents"], [class*="decimal"]');
                        var fraction = fractionNode ? String(fractionNode.innerText || fractionNode.textContent || '').replace(/[^0-9.]/g, '') : '';
                        var textNode = String(node.innerText || node.textContent || '');
                        var cents = centsNode ? String(centsNode.innerText || centsNode.textContent || '').replace(/[^0-9]/g, '') : '';
                        if (!cents) {
                            var centsMatch = textNode.match(/[,.]\\s*(\\d{1,2})\\s*$/);
                            cents = centsMatch && centsMatch[1] ? centsMatch[1] : '';
                        }
                        if (fraction) {
                            var inteiro = fraction.replace(/\\./g, '');
                            var centavos = cents ? cents.padEnd(2, '0').slice(0, 2) : '00';
                            var parsed = Number(inteiro + '.' + centavos);
                            return Number.isFinite(parsed) ? parsed : null;
                        }
                        return parsePrecoTextoCard(textNode);
                    };
                    var isPrecoOriginalNodeCard = function (node) {
                        var atual = node;
                        for (var i = 0; atual && i < 5; i += 1) {
                            var info = String(atual.className || '') + ' ' + String(atual.getAttribute && (atual.getAttribute('aria-label') || atual.getAttribute('role') || '') || '');
                            if (/previous|original|old|strikethrough|discount|antes|tachado/i.test(info)) return true;
                            atual = atual.parentElement;
                        }
                        return false;
                    };
                    var isPrecoPrincipalNodeCard = function (node) {
                        var atual = node;
                        for (var i = 0; atual && i < 5; i += 1) {
                            var cls = String(atual.className || '');
                            if (/poly-price__current|ui-search-price__second-line/i.test(cls)) return true;
                            atual = atual.parentElement;
                        }
                        return false;
                    };
                    var isPrecoSecundarioNodeCard = function (node) {
                        var atual = node;
                        for (var i = 0; atual && i < 5; i += 1) {
                            var info = String(atual.className || '') + ' ' + String(atual.innerText || atual.textContent || '');
                            if (/installments|parcel|per[_-]?quantity|price-per-quantity|levando\\s+\\d+\\s+ou\\s+mais|\\b\\d+x\\s*r\\$/i.test(info)) return true;
                            atual = atual.parentElement;
                        }
                        return false;
                    };
                    var precosDoCard = function (card) {
                        var selectorsPreco = [
                            '.poly-price__current .andes-money-amount',
                            '.poly-component__price .andes-money-amount',
                            '.ui-search-price__second-line .andes-money-amount',
                            '[class*="price"] .andes-money-amount',
                            '.andes-money-amount',
                            '.price-tag'
                        ].join(',');
                        var precoPrincipalDireto = queryAllDeep('.poly-price__current .andes-money-amount, .ui-search-price__second-line .andes-money-amount', card)
                            .map(function (node) { return parsePrecoTextoCard(node && (node.innerText || node.textContent)); })
                            .filter(function (value) { return value !== null && value > 0; });
                        var nodes = queryAllDeep(selectorsPreco, card)
                            .filter(function (node) { return !nodeDentroAvantCard(node) && !textoPrecoIndesejadoCard(node); });
                        var atuaisPrincipais = [];
                        var atuais = [];
                        var originais = [];
                        nodes.forEach(function (node) {
                            var value = parsePrecoNodeCard(node);
                            if (value === null || value <= 0) return;
                            if (isPrecoOriginalNodeCard(node)) originais.push(value);
                            else if (isPrecoPrincipalNodeCard(node)) atuaisPrincipais.push(value);
                            else if (isPrecoSecundarioNodeCard(node)) return;
                            else atuais.push(value);
                        });
                        var precoAtual = precoPrincipalDireto.length ? precoPrincipalDireto[0] : (atuaisPrincipais.length ? atuaisPrincipais[0] : (atuais.length ? atuais[0] : null));
                        var precoOriginal = originais.length ? originais[0] : '';
                        if (precoAtual === null) {
                            var direto = String(card && (card.innerText || card.textContent) || '').split(/frete|comiss[aã]o|faturamento|taxa|total\\s+de\\s+vendas/i)[0];
                            precoAtual = parsePrecoTextoCard(direto);
                        }
                        if (precoAtual === null) return { preco: null, preco_original: '', preco_promocional: '', moeda: '' };
                        if (precoOriginal && precoOriginal > precoAtual) {
                            return { preco: precoAtual, preco_original: precoOriginal, preco_promocional: precoAtual, moeda: 'BRL' };
                        }
                        return { preco: precoAtual, preco_original: '', preco_promocional: '', moeda: 'BRL' };
                    };
                    var baseCardData = function (card) {
                        var href = productHref(card);
                        var id = idDe(href || (card && card.outerHTML) || '');
                        href = cleanProductUrl(href, id);
                        var precos = precosDoCard(card);
                        var imagem = imagemDoCard(card);
                        return {
                            id: id,
                            mlb: id,
                            url: href,
                            permalink: href,
                            link: href,
                            titulo: tituloDoCard(card),
                            title: tituloDoCard(card),
                            imagem: imagem,
                            thumbnail: imagem,
                            foto: imagem,
                            preco: precos.preco,
                            price: precos.preco_promocional || precos.preco,
                            preco_original: precos.preco_original,
                            original_price: precos.preco_original,
                            preco_promocional: precos.preco_promocional,
                            promotional_price: precos.preco_promocional,
                            moeda: precos.moeda,
                            currency_id: precos.moeda,
                            tituloFonte: 'mercado_livre_dom_card_clicado',
                            fotoFonte: imagem ? 'mercado_livre_dom_card_clicado' : '',
                            linkFonte: href ? 'mercado_livre_dom_card_clicado' : '',
                            precoFonte: precos.preco !== null ? 'mercado_livre_dom_card_clicado' : '',
                            fonte_preco: precos.preco !== null ? 'mercado_livre_dom_card_clicado' : '',
                            origem_dados: 'avantpro_card_panel_cache'
                        };
                    };
                    var baseCardDataLeve = function (card) {
                        var href = productHref(card);
                        var attrText = [
                            card && card.getAttribute && card.getAttribute('data-item-id'),
                            card && card.getAttribute && card.getAttribute('data-id'),
                            card && card.getAttribute && card.getAttribute('id'),
                            card && card.getAttribute && card.getAttribute('aria-label')
                        ].filter(Boolean).join(' ');
                        var id = idDe(href || attrText || '');
                        href = cleanProductUrl(href, id);
                        return {
                            id: id,
                            mlb: id,
                            url: href,
                            permalink: href,
                            link: href,
                            linkFonte: href ? 'mercado_livre_dom_card_clicado' : '',
                            origem_dados: 'avantpro_card_panel_cache'
                        };
                    };
                    var parseHumanNumberCard = function (value, suffix, decimal) {
                        if (value === null || value === undefined) return null;
                        var raw = String(value).trim().toLowerCase();
                        if (!raw) return null;
                        var normalized = raw.replace(/\\s+/g, '');
                        if (normalized.indexOf('.') >= 0 && normalized.indexOf(',') >= 0) {
                            normalized = normalized.lastIndexOf('.') > normalized.lastIndexOf(',')
                                ? normalized.replace(/,/g, '')
                                : normalized.replace(/\\./g, '').replace(/,/g, '.');
                        } else if (normalized.indexOf(',') >= 0) {
                            normalized = normalized.replace(/\\./g, '').replace(/,/g, '.');
                        }
                        var parsed = parseFloat(normalized);
                        if (!isFinite(parsed)) return null;
                        suffix = String(suffix || '').toLowerCase();
                        if (suffix === 'mil' || suffix === 'k') parsed *= 1000;
                        return decimal ? parsed : Math.round(parsed);
                    };
                    var labelValueAvantCard = function (row) {
                        if (row && row.__jkLabel) {
                            return {
                                label: row.__jkLabel || '',
                                value: row.__jkValue || '',
                                text: row.__jkText || [row.__jkLabel, row.__jkValue].filter(Boolean).join(' ')
                            };
                        }
                        var labelNode = row && row.querySelector && row.querySelector('.avantpro-product-info-row-label, [class*="label"]');
                        var valueNode = row && row.querySelector && row.querySelector('.avantpro-product-info-row-value, [class*="value"]');
                        var label = String(labelNode && (labelNode.innerText || labelNode.textContent) || '').replace(/\\s+/g, ' ').trim();
                        var value = String(valueNode && (valueNode.innerText || valueNode.textContent) || '').replace(/\\s+/g, ' ').trim();
                        var text = String(row && (row.innerText || row.textContent) || '').replace(/\\s+/g, ' ').trim();
                        if (!value && label && text.indexOf(label) >= 0) {
                            value = text.slice(text.indexOf(label) + label.length).replace(/^\\s*[:\\-]?\\s*/, '').trim();
                        }
                        return { label: label, value: value, text: text };
                    };
                    var labelTemAvantCard = function (label, partes) {
                        var n = normalizar(label);
                        return partes.some(function (parte) { return n.indexOf(parte) >= 0; });
                    };
                    var definicoesLabelsAvantCard = [
                        { label: 'Vendas do produto', tokens: ['vendas do produto', 'venda do produto', 'vendas do anuncio', 'venda do anuncio', 'vendas do item', 'venda do item'] },
                        { label: 'Vendas estimadas', tokens: ['vendas estimad', 'venda estimad'] },
                        { label: 'Ritmo atual', tokens: ['ritmo atual', 'media mensal', 'vendas/mes', 'vendas mes'] },
                        { label: 'Nome do vendedor', tokens: ['nome do vendedor'] },
                        { label: 'Localizacao do vendedor', tokens: ['localizacao do vendedor'] },
                        { label: 'Anuncio criado em', tokens: ['anuncio criado em', 'catalogo criado em', 'criado em'] },
                        { label: 'Visitas do anuncio', tokens: ['visitas do anuncio', 'visitas'] },
                        { label: 'Participacao', tokens: ['participacao'] },
                        { label: 'Comissao', tokens: ['comissao'] },
                        { label: 'Reputacao do vendedor', tokens: ['reputacao do vendedor'] }
                    ];
                    var labelAvantCardPorLinha = function (line) {
                        var norm = normalizar(line);
                        for (var i = 0; i < definicoesLabelsAvantCard.length; i += 1) {
                            var def = definicoesLabelsAvantCard[i];
                            for (var j = 0; j < def.tokens.length; j += 1) {
                                var token = def.tokens[j];
                                if (norm === token || norm.indexOf(token + ' ') === 0 || norm.indexOf(token + ':') === 0 || norm.indexOf(token + ' (') === 0) return def;
                            }
                        }
                        return null;
                    };
                    var linhaEhLabelAvantCard = function (line) {
                        return !!labelAvantCardPorLinha(line);
                    };
                    var textoValorNaMesmaLinhaAvantCard = function (line, def) {
                        var text = String(line || '').trim();
                        var norm = normalizar(text);
                        var melhor = '';
                        (def && def.tokens || []).forEach(function (token) {
                            if (norm.indexOf(token) !== 0) return;
                            var resto = text.slice(token.length).replace(/^[\\s:()\\-/=]+/, '').trim();
                            if (resto && resto.length > melhor.length) melhor = resto;
                        });
                        return melhor;
                    };
                    var linhasTextoAvantCard = function (node) {
                        return String(node && (node.innerText || node.textContent) || '')
                            .split(/\\n+/)
                            .map(function (line) { return line.replace(/\\s+/g, ' ').trim(); })
                            .filter(Boolean);
                    };
                    var extrairLinhasVirtuaisAvantCard = function (root) {
                        var linhas = linhasTextoAvantCard(root);
                        if (!linhas.length) return [];
                        var out = [];
                        for (var i = 0; i < linhas.length; i += 1) {
                            var line = linhas[i];
                            var def = labelAvantCardPorLinha(line);
                            if (!def) continue;
                            var value = textoValorNaMesmaLinhaAvantCard(line, def);
                            if (!value) {
                                for (var j = i + 1; j < Math.min(linhas.length, i + 5); j += 1) {
                                    var candidato = linhas[j];
                                    if (!candidato || linhaEhLabelAvantCard(candidato)) break;
                                    if (/^(informacoes?\\s+avant(?:pro)?|carregar\\s+dados\\s+avantpro?)$/i.test(normalizar(candidato))) continue;
                                    value = candidato;
                                    break;
                                }
                            }
                            out.push({
                                __jkLabel: def.label,
                                __jkValue: value || '',
                                __jkText: [def.label, value || ''].filter(Boolean).join(' ')
                            });
                        }
                        return out;
                    };
                    var preencherDadosAvantCard = function (item, row) {
                        var lv = labelValueAvantCard(row);
                        var label = lv.label;
                        var value = lv.value;
                        var text = lv.text;
                        var busca = normalizar(label + ' ' + text);
                        if (labelTemAvantCard(label || busca, ['vendas do produto', 'venda do produto', 'vendas do anuncio', 'venda do anuncio', 'vendas do item', 'venda do item'])) {
                            var vendas = parseHumanNumberCard(value || text, '', false);
                            if (Number.isFinite(vendas)) item.vendas = vendas;
                        } else if (labelTemAvantCard(label || busca, ['vendas estimad', 'venda estimad'])) {
                            var estimadas = parseHumanNumberCard(value || text, '', false);
                            if (Number.isFinite(estimadas) && !Number.isFinite(item.vendas)) item.vendas = estimadas;
                            if (Number.isFinite(estimadas)) item.vendas_estimadas = estimadas;
                        } else if (labelTemAvantCard(label || busca, ['ritmo atual'])) {
                            var ritmo = parseHumanNumberCard(value || text, '', true);
                            if (Number.isFinite(ritmo)) {
                                item.media_mensal = ritmo;
                                item.ritmo_atual = ritmo;
                            }
                        } else if (labelTemAvantCard(label || busca, ['reputacao do vendedor'])) {
                            item.reputacao_vendedor = value || '';
                        } else if (labelTemAvantCard(label || busca, ['localizacao do vendedor'])) {
                            item.localizacao_vendedor = value || '';
                        } else if (labelTemAvantCard(label || busca, ['nome do vendedor']) || (labelTemAvantCard(label || busca, ['vendedor']) && !labelTemAvantCard(label || busca, ['reputacao do vendedor', 'localizacao do vendedor']))) {
                            var vendedor = String(value || '').replace(/^\\s*[:\\-]?\\s*/, '').trim();
                            if (vendedor && vendedor.length <= 120 && !/^\\d+$/.test(vendedor)) item.vendedor = vendedor;
                        } else if (labelTemAvantCard(label || busca, ['anuncio criado em', 'catalogo criado em', 'criado em'])) {
                            var data = String(value || text || '').match(/\\b\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}\\b|\\b20\\d{2}-\\d{2}-\\d{2}\\b/);
                            if (data && data[0]) item.data_criacao = data[0];
                        } else if (labelTemAvantCard(label || busca, ['visitas do anuncio', 'visitas'])) {
                            var visitas = parseHumanNumberCard(value || text, '', false);
                            if (Number.isFinite(visitas)) item.visitas = visitas;
                        } else if (labelTemAvantCard(label || busca, ['participacao'])) {
                            item.participacao = value || '';
                        } else if (labelTemAvantCard(label || busca, ['comissao'])) {
                            item.comissao = value || '';
                        }
                    };
                    var linhasAvantPainel = function (root) {
                        var rows = queryAllDeep('.avantpro-product-info-row, .created-time-card, [class*="avantpro-product-info"], [class*="created-time-card"], [class*="Avantpro"][class*="row"], [class*="avantpro"][class*="row"]', root || document)
                            .filter(function (row) {
                                var text = String(row && (row.innerText || row.textContent) || '').replace(/\\s+/g, ' ').trim();
                                return /vendas?|ritmo|vendedor|criado|visitas|participa|comissao|reputacao/i.test(normalizar(text));
                            });
                        var virtuais = [];
                        if (root && root !== document) {
                            virtuais = extrairLinhasVirtuaisAvantCard(root);
                        } else {
                            var candidatos = queryAllDeep('[class*="avant"], [class*="Avant"], [role="dialog"], aside, section, div')
                                .filter(function (node) {
                                    if (!visible(node)) return false;
                                    var tag = String(node && node.tagName || '').toUpperCase();
                                    if (tag === 'HTML' || tag === 'BODY' || tag === 'MAIN') return false;
                                    var text = normalizar(node && (node.innerText || node.textContent) || '');
                                    if (!/avant|vendas?\\s+do\\s+produto|ritmo\\s+atual|nome\\s+do\\s+vendedor|anuncio\\s+criado\\s+em/.test(text)) return false;
                                    var labels = 0;
                                    definicoesLabelsAvantCard.forEach(function (def) {
                                        if (def.tokens.some(function (token) { return text.indexOf(token) >= 0; })) labels += 1;
                                    });
                                    return labels >= 2;
                                });
                            var usados = [];
                            candidatos.sort(function (a, b) {
                                var ar = null;
                                var br = null;
                                try { ar = a.getBoundingClientRect && a.getBoundingClientRect(); } catch (_arErr) {}
                                try { br = b.getBoundingClientRect && b.getBoundingClientRect(); } catch (_brErr) {}
                                var aa = ar ? ar.width * ar.height : 0;
                                var ba = br ? br.width * br.height : 0;
                                return aa - ba;
                            }).forEach(function (node) {
                                if (usados.some(function (parent) { return parent !== node && parent.contains && parent.contains(node); })) return;
                                var extraidas = extrairLinhasVirtuaisAvantCard(node);
                                if (extraidas.length < 2) return;
                                usados.push(node);
                                virtuais = virtuais.concat(extraidas);
                            });
                        }
                        return rows.concat(virtuais);
                    };
                    var snapshotAvantPainel = function (root) {
                        return linhasAvantPainel(root).map(function (row) {
                            return String(row && (row.innerText || row.textContent) || '').replace(/\\s+/g, ' ').trim();
                        }).filter(Boolean).join('|');
                    };
                    var capturarPainelAvantParaCard = async function (card, snapshotAntes, baseLeve) {
                        var baseMinima = baseLeve || baseCardDataLeve(card);
                        var base = null;
                        var canonical = baseMinima.mlb ? ('mlb:' + baseMinima.mlb) : (baseMinima.link ? ('link:' + String(baseMinima.link).toLowerCase()) : '');
                        if (!canonical) return null;
                        var finalSnapshot = '';
                        var rowsCaptura = [];
                        var origemCard = false;
                        for (var tentativa = 0; tentativa < 3 && hasTime(260); tentativa += 1) {
                            await sleep(tentativa === 0 ? 120 : 180);
                            rowsCaptura = linhasAvantPainel(card);
                            if (rowsCaptura.length) {
                                origemCard = true;
                                finalSnapshot = snapshotAvantPainel(card);
                                break;
                            }
                            finalSnapshot = snapshotAvantPainel();
                            if (finalSnapshot && finalSnapshot !== snapshotAntes) {
                                rowsCaptura = linhasAvantPainel();
                                break;
                            }
                        }
                        if (!origemCard) {
                            finalSnapshot = finalSnapshot || snapshotAvantPainel();
                        }
                        if (!origemCard && (!finalSnapshot || finalSnapshot === snapshotAntes)) return null;
                        if (!rowsCaptura.length) rowsCaptura = linhasAvantPainel();
                        if (!rowsCaptura.length) return null;
                        base = Object.assign({}, baseMinima, baseCardData(card));
                        var item = Object.assign({}, base);
                        rowsCaptura.forEach(function (row) { preencherDadosAvantCard(item, row); });
                        var temDados = item.vendas !== null && item.vendas !== undefined && item.vendas !== ''
                            || item.media_mensal !== null && item.media_mensal !== undefined && item.media_mensal !== ''
                            || item.vendedor
                            || item.data_criacao
                            || item.visitas !== null && item.visitas !== undefined && item.visitas !== '';
                        if (!temDados) return null;
                        if (item.vendas !== null && item.vendas !== undefined && item.vendas !== '') {
                            item.vendasFonte = 'avantpro_dom';
                            item.vendas_fonte = 'avantpro_dom';
                        }
                        if (item.vendedor) {
                            item.vendedorFonte = 'avantpro_dom';
                            item.vendedor_fonte = 'avantpro_dom';
                        }
                        if (item.data_criacao) {
                            item.dataCriacaoFonte = 'avantpro_dom';
                            item.data_criacao_fonte = 'avantpro_dom';
                        }
                        if (item.media_mensal !== null && item.media_mensal !== undefined && item.media_mensal !== '') {
                            item.media_mensal_fonte = 'avantpro_dom';
                        }
                        item.chave_canonica = canonical;
                        item.chaveCanonica = canonical;
                        item.__capturadoDoPainelAvant = true;
                        item.__capturadoEm = Date.now();
                        var cache = window.__JK_AVANT_CARD_DATA_CACHE || {};
                        cache[canonical] = Object.assign({}, cache[canonical] || {}, item);
                        window.__JK_AVANT_CARD_DATA_CACHE = cache;
                        window.__JK_AVANT_LAST_PANEL_SNAPSHOT = finalSnapshot;
                        return item;
                    };
                    var cardTemDadosAvant = function (card) {
                        return linhasAvantPainel(card).length > 0;
                    };
                    var ehLinkProduto = function (node) {
                        var href = node && node.getAttribute && node.getAttribute('href');
                        return !!(href && /\\bMLB-?\\d{6,}\\b|[?&](?:wid|item_id)=MLB\\d{6,}|produto\\.mercadolivre\\.com\\.br/i.test(cleanUrl(href)));
                    };
                    var hrefNode = function (node) {
                        if (!node || !node.getAttribute) return '';
                        return String(node.href || node.getAttribute('href') || node.getAttribute('data-href') || '').trim();
                    };
                    var textoAlvoClique = function (node) {
                        if (!node) return '';
                        return normalizar([
                            node.innerText,
                            node.textContent,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title'),
                            node.getAttribute && node.getAttribute('class'),
                            node.getAttribute && node.getAttribute('id'),
                            node.getAttribute && node.getAttribute('data-testid'),
                            node.getAttribute && node.getAttribute('download'),
                            hrefNode(node)
                        ].filter(Boolean).join(' '));
                    };
                    var textoExplicitoClique = function (node) {
                        if (!node) return '';
                        return normalizar([
                            node.innerText,
                            node.textContent,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title'),
                            node.getAttribute && node.getAttribute('data-testid')
                        ].filter(Boolean).join(' '));
                    };
                    var ehAncoraNavegavel = function (node) {
                        var tag = String(node && node.tagName || '').toUpperCase();
                        var href = hrefNode(node);
                        return tag === 'A' && !!href && !/^javascript:/i.test(href);
                    };
                    var ehDownloadOuMidiaAvant = function (node) {
                        if (!node) return false;
                        var href = hrefNode(node);
                        var alvo = textoAlvoClique(node);
                        var hrefNormalizado = normalizar(href);
                        try {
                            if (node.closest && node.closest('a[download], [download]')) return true;
                        } catch (_closestErr) {}
                        if (/^(blob|data):/i.test(href)) return true;
                        if (/\\.zip(?:$|[?#])|\\/download\\b|download=|filename=|imagens?\\.zip|images?\\.zip/i.test(href)) return true;
                        return /(^|[\\s_-])(baixar|download|imagens?|images?|fotos?|photos?|foto|photo|zip|exportar?|salvar|gallery|galeria)([\\s_-]|$)/.test(alvo + ' ' + hrefNormalizado);
                    };
                    var ehControleAvant = function (node, card) {
                        var alvo = textoAlvoClique(node);
                        var explicito = textoExplicitoClique(node);
                        var contexto = textoNode(card);
                        if (!alvo) return false;
                        if (!explicito) return false;
                        if (ehAncoraNavegavel(node)) return false;
                        if (ehDownloadOuMidiaAvant(node)) return false;
                        if (/\\blogin\\b|fazer\\s+login|entrar\\s+no\\s+avant|vincular\\s+(?:conta|agora)|ferramentas|tools|assine\\s+ja|assinar|suporte|comunidade/.test(alvo + ' ' + explicito)) return false;
                        if (/baixar|download|imagens?|images?|fotos?|photos?|zip|exportar?|salvar|gallery|galeria|rotulos\\s+visuais/.test(alvo + ' ' + explicito)) return false;
                        if (ehLinkProduto(node)) return false;
                        return /informacoes?\\s+avant|informacoes?\\s+avantpro|avantpro\\s+info|carregar\\s+dados\\s+avant|dados\\s+avantpro|dados\\s+avant\\s*pro/.test(explicito)
                            && /avant\\s*pro|avantpro|informacoes?\\s+avant/.test(alvo + ' ' + contexto);
                    };
                    var store = window.__JK_AVANT_CARD_QUEUE_CLICKED_KEYS || {};
                    window.__JK_AVANT_CARD_QUEUE_CLICKED_KEYS = store;
                    window.__JK_AVANT_CARD_DATA_CACHE = window.__JK_AVANT_CARD_DATA_CACHE || {};
                    var cardMaisProximoFila = function (anchor) {
                        if (!anchor || !anchor.closest) return anchor;
                        try {
                            return anchor.closest(cardSelectors + ', li, article, section') || anchor;
                        } catch (_closestErr) {
                            return anchor.closest('li, article, section, div') || anchor;
                        }
                    };
                    var cards = [];
                    var cardsPorKey = {};
                    var debugFila = {
                        selectorNodes: 0,
                        anchorNodes: 0,
                        includedBySelector: 0,
                        includedByAnchor: 0,
                        duplicateKeys: 0,
                        replacedDuplicate: 0,
                        skippedNoKey: 0,
                        sampleLinks: []
                    };
                    var pontuarCardFila = function (card) {
                        if (!card) return -1;
                        var score = 0;
                        if (visible(card)) score += 1000000;
                        var text = textoNode(card);
                        if (/carregar\s+dados\s+avant|informacoes?\s+avant|dados\s+avantpro/.test(text)) score += 200000;
                        try {
                            var rect = card.getBoundingClientRect ? card.getBoundingClientRect() : null;
                            if (rect) {
                                score += Math.min(120000, Math.max(0, rect.width) * Math.max(0, rect.height));
                                if (rect.top >= -80) score += Math.max(0, 20000 - Math.abs(rect.top));
                            }
                        } catch (_scoreRectErr) {}
                        try {
                            if (card.querySelector && card.querySelector('button, [role="button"], [aria-label], [title]')) score += 5000;
                        } catch (_scoreControlErr) {}
                        return score;
                    };
                    var incluirCard = function (card) {
                        if (!card) return false;
                        var key = keyCard(card);
                        if (!key) {
                            debugFila.skippedNoKey += 1;
                            return false;
                        }
                        var existente = cardsPorKey[key];
                        if (existente) {
                            debugFila.duplicateKeys += 1;
                            if (pontuarCardFila(card) > pontuarCardFila(existente)) {
                                var pos = cards.indexOf(existente);
                                if (pos >= 0) cards[pos] = card;
                                cardsPorKey[key] = card;
                                debugFila.replacedDuplicate += 1;
                            }
                            return false;
                        }
                        if (cards.indexOf(card) >= 0) return false;
                        cardsPorKey[key] = card;
                        cards.push(card);
                        return true;
                    };
                    try {
                        var cardsSelector = deepScan
                            ? queryAllDeep(cardSelectors)
                            : Array.prototype.slice.call(document.querySelectorAll(cardSelectors));
                        debugFila.selectorNodes = cardsSelector.length;
                        cardsSelector.forEach(function (card) {
                            if (incluirCard(card)) debugFila.includedBySelector += 1;
                        });
                    } catch (_cardsErr) {}
                    try {
                        var anchorsProduto = deepScan
                            ? queryAllDeep('a[href]')
                            : Array.prototype.slice.call(document.querySelectorAll('a[href]'));
                        debugFila.anchorNodes = anchorsProduto.length;
                        anchorsProduto.forEach(function (anchor) {
                            var href = anchor.href || anchor.getAttribute('href') || '';
                            if (!/\\bMLB-?\\d{6,}\\b|[?&](?:wid|item_id)=MLB\\d{6,}|\\/p\\/MLB|\\/up\\/MLB|produto\\.mercadolivre\\.com\\.br/i.test(href)) return;
                            if (debugFila.sampleLinks.length < 5) debugFila.sampleLinks.push(String(href).slice(0, 180));
                            if (incluirCard(cardMaisProximoFila(anchor))) debugFila.includedByAnchor += 1;
                        });
                    } catch (_anchorCardsErr) {}
                    cards.sort(function (left, right) {
                        var leftVisible = visible(left) ? 0 : 1;
                        var rightVisible = visible(right) ? 0 : 1;
                        if (leftVisible !== rightVisible) return leftVisible - rightVisible;
                        try {
                            var lr = left.getBoundingClientRect ? left.getBoundingClientRect() : null;
                            var rr = right.getBoundingClientRect ? right.getBoundingClientRect() : null;
                            return Math.abs((lr && lr.top) || 0) - Math.abs((rr && rr.top) || 0);
                        } catch (_sortErr) {
                            return 0;
                        }
                    });
                    window.__JK_AVANT_CARD_QUEUE_DEBUG = Object.assign({}, debugFila, {
                        totalCards: cards.length,
                        uniqueKeys: Object.keys(cardsPorKey).length,
                        candidateKeys: Object.keys(cardsPorKey).slice(0, 120)
                    });
                    var permitirClique = window.__JK_AVANT_CARD_QUEUE_CLICK_SLOW !== true;
                    var clicked = 0;
                    var eligiblePending = 0;
                    var pendingKeys = [];
                    var resolvedKeys = [];
                    var keys = [];
                    var capturados = [];
                    var controleSelector = 'button, [role="button"], input[type="button"], input[type="submit"], [aria-label], [title], [class*="andes-button"]';
                    for (var c = 0; c < cards.length && hasTime(550); c += 1) {
                        var card = cards[c];
                        var baseParaCache = baseCardDataLeve(card);
                        var key = baseParaCache.mlb ? ('id:' + baseParaCache.mlb) : (baseParaCache.link ? ('url:' + baseParaCache.link) : keyCard(card));
                        var tentativas = Number((store[key] && store[key].count) || store[key] || 0);
                        var canonicalCache = baseParaCache.mlb ? ('mlb:' + baseParaCache.mlb) : (baseParaCache.link ? ('link:' + String(baseParaCache.link).toLowerCase()) : '');
                        var cacheAtual = canonicalCache && window.__JK_AVANT_CARD_DATA_CACHE && window.__JK_AVANT_CARD_DATA_CACHE[canonicalCache];
                        var cacheTemDados = !!(cacheAtual && (cacheAtual.vendasFonte || cacheAtual.vendedorFonte || cacheAtual.data_criacao || cacheAtual.media_mensal || cacheAtual.visitas));
                        if (!key || tentativas >= maxTentativasPorCard) continue;
                        if (cacheTemDados) {
                            resolvedKeys.push(key);
                            continue;
                        }
                        if (cardTemDadosAvant(card)) {
                            var capturadoExistente = await capturarPainelAvantParaCard(card, '', baseParaCache);
                            if (capturadoExistente) {
                                store[key] = { count: tentativas + 1, at: Date.now(), semClique: true };
                                keys.push(key);
                                capturados.push(capturadoExistente);
                                resolvedKeys.push(key);
                                continue;
                            }
                        }
                        if (!permitirClique || clicked >= maxClicks) {
                            eligiblePending += 1;
                            pendingKeys.push(key);
                            continue;
                        }
                        var controles = [];
                        try { controles = Array.prototype.slice.call(card && card.querySelectorAll ? card.querySelectorAll(controleSelector) : []); } catch (_directControlErr) {}
                        if (!controles.length && deepScan) controles = queryAllDeep(controleSelector, card);
                        controles = controles
                            .filter(function (node) { return visible(node) && ehControleAvant(node, card); });
                        if (!controles.length) continue;
                        var alvo = controles[0];
                        var snapshotAntes = window.__JK_AVANT_LAST_PANEL_SNAPSHOT || '';
                        store[key] = { count: tentativas + 1, at: Date.now() };
                        try { alvo.scrollIntoView && alvo.scrollIntoView({ block: 'center', inline: 'center' }); } catch (_scrollErr) {}
                        await sleep(80);
                        try {
                            var rect = alvo.getBoundingClientRect ? alvo.getBoundingClientRect() : null;
                            var opts = rect ? { bubbles: true, cancelable: true, view: window, clientX: rect.left + rect.width / 2, clientY: rect.top + rect.height / 2 } : { bubbles: true, cancelable: true, view: window };
                            if (typeof alvo.click === 'function') {
                                alvo.click();
                            } else {
                                try { alvo.dispatchEvent(new MouseEvent('click', opts)); } catch (_clickErr) {}
                            }
                            clicked += 1;
                            keys.push(key);
                            window.__JK_AVANT_CARD_CLICKED_AT = Date.now();
                            window.__JK_AVANT_LAST_CARD_CLICKED = Object.assign({}, baseParaCache, {
                                key: key,
                                chave_canonica: canonicalCache,
                                at: Date.now()
                            });
                        } catch (_clickOuterErr) {}
                        var capturado = await capturarPainelAvantParaCard(card, snapshotAntes, baseParaCache);
                        if (capturado) {
                            capturados.push(capturado);
                            resolvedKeys.push(key);
                        } else {
                            pendingKeys.push(key);
                        }
                        await sleep(80);
                    }
                    var textoPaginaAvant = checkLogin ? normalizar([
                        document.body && (document.body.innerText || document.body.textContent),
                        queryAllDeep('[role="dialog"], [aria-modal="true"], [class*="modal"], [class*="Modal"], [class*="avant"], [class*="login"], form, input, button, label, h1, h2, h3, p')
                            .slice(0, 360)
                            .map(function (node) {
                                return [
                                    node.innerText,
                                    node.textContent,
                                    node.value,
                                    node.getAttribute && node.getAttribute('placeholder'),
                                    node.getAttribute && node.getAttribute('aria-label'),
                                    node.getAttribute && node.getAttribute('title'),
                                    node.getAttribute && node.getAttribute('name'),
                                    node.getAttribute && node.getAttribute('type'),
                                    node.getAttribute && node.getAttribute('class'),
                                    node.getAttribute && node.getAttribute('id')
                                ].filter(Boolean).join(' ');
                            })
                            .join(' ')
                    ].filter(Boolean).join(' ')) : '';
                    var inputsLoginAvant = checkLogin && queryAllDeep('input, textarea').some(function (node) {
                        if (!visible(node)) return false;
                        var alvo = textoAlvoClique(node);
                        return /e\s*mail|email|senha|password|credential|credencial/.test(alvo);
                    });
                    var textoLoginAvant = checkLogin ? queryAllDeep('[role="dialog"], [aria-modal="true"], [class*="modal"], [class*="Modal"], [class*="avant"], [class*="login"], form')
                        .filter(visible)
                        .slice(0, 16)
                        .map(textoNode)
                        .join(' ') : '';
                    var marcaAvantLogin = /avant\s*pro|avantpro/.test(textoPaginaAvant + ' ' + textoLoginAvant);
                    var sinaisLoginAvant = /iniciar\s+sessao|insira\s+suas\s+credenciais|credenciais\s+para\s+acessar|seu\s+e\s*mail|seu\s+email|e-?mail|entrar\s+na\s+sua\s+conta|login\s+avant|avantpro\s+mercado\s+livre|ainda\s+nao\s+tem\s+um\s+cadastro/.test(textoPaginaAvant + ' ' + textoLoginAvant);
                    var loginAvantBloqueando = !!((marcaAvantLogin && sinaisLoginAvant) || (inputsLoginAvant && marcaAvantLogin));
                    var elapsed = Date.now() - startedAt;
                    if (clicked > 0 && capturados.length === 0 && loginAvantBloqueando) {
                        window.__JK_AVANT_CARD_QUEUE_CLICK_SLOW = true;
                    }
                    var incrementalState = window.__JK_FAVORITOS_INCREMENTAL_COLLECTOR_V1;
                    if (incrementalState) {
                        Array.from(new Set(pendingKeys)).forEach(function (key) {
                            incrementalState.pendingKeys[key] = Date.now();
                        });
                        Array.from(new Set(resolvedKeys)).forEach(function (key) {
                            incrementalState.resolvedKeys[key] = Date.now();
                            delete incrementalState.pendingKeys[key];
                        });
                    }
                    var resultadoFila = { clicked: clicked, totalCandidates: cards.length, eligiblePending: eligiblePending, pendingKeys: Array.from(new Set(pendingKeys)), resolvedKeys: Array.from(new Set(resolvedKeys)), keys: keys, capturados: capturados.length, elapsedMs: elapsed, timedOut: !hasTime(1), slowDisabled: window.__JK_AVANT_CARD_QUEUE_CLICK_SLOW === true, loginBlocked: loginAvantBloqueando, deepScan: deepScan, checkLogin: checkLogin, mutationVersion: incrementalState ? Number(incrementalState.mutationVersion) || 0 : 0, debug: window.__JK_AVANT_CARD_QUEUE_DEBUG || debugFila };
                    window.__JK_AVANT_LAST_QUEUE_RESULT = resultadoFila;
                    return resultadoFila;
                })();
            `, true).catch((err) => ({
                clicked: 0,
                totalCandidates: 0,
                keys: [],
                capturados: 0,
                error: err && err.message ? err.message : String(err)
            }));
        }

        async function coletarPrimeiraPaginaFavoritosControlada(opcoes = {}) {
            const webview = resolverWebviewFavoritosColeta(opcoes);
            if (!webview || typeof webview.executeJavaScript !== 'function') {
                return { success: false, totalVisiveis: 0, anuncios: [], error: 'webview_indisponivel' };
            }
            const signal = opcoes.signal || null;
            const erroCancelamento = () => {
                const err = new Error('Coleta de favoritos cancelada pelo usuario.');
                err.name = 'AbortError';
                err.canceladoFavoritos = true;
                return err;
            };
            const verificarCancelamento = () => {
                if (signal && signal.aborted) throw erroCancelamento();
            };
            const aguardarCancelavel = (promise) => {
                verificarCancelamento();
                if (!signal || typeof signal.addEventListener !== 'function') return Promise.resolve(promise);
                let onAbort = null;
                const cancelamento = new Promise((_resolve, reject) => {
                    onAbort = () => reject(erroCancelamento());
                    signal.addEventListener('abort', onAbort, { once: true });
                });
                return Promise.race([Promise.resolve(promise), cancelamento]).finally(() => {
                    if (onAbort) signal.removeEventListener('abort', onAbort);
                });
            };
            const esperarCancelavel = (ms) => aguardarCancelavel(esperar(ms));
            const limite = Math.max(20, Math.min(Number(opcoes.maxAnuncios) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80, 100));
            const tempoLimiteMs = Math.max(30000, Math.min(Number(opcoes.tempoLimiteMs) || 180000, 180000));
            const maxPassadas = Math.max(1, Math.min(Number(opcoes.maxPassadas) || 3, 3));
            const loteCliquesValor = opcoes.loteCliques === undefined ? 0 : Number(opcoes.loteCliques);
            const loteCliques = Math.max(0, Math.min(Number.isFinite(loteCliquesValor) ? loteCliquesValor : 0, 8));
            const inicio = Date.now();
            const deadline = inicio + tempoLimiteMs;
            const reservaAvantMs = loteCliques > 0
                ? Math.min(Math.max(15000, Math.floor(tempoLimiteMs * 0.55)), Math.max(12000, tempoLimiteMs - 12000))
                : 0;
            const deadlinePreparacao = reservaAvantMs > 0
                ? Math.max(inicio + 8000, deadline - reservaAvantMs)
                : deadline;
            const onProgress = opcoes.onProgress;
            const incrementalPreparado = await aguardarCancelavel(webview.executeJavaScript(`
                (function () {
                    window.__JK_AVANT_CARD_QUEUE_CLICKED_KEYS = {};
                    window.__JK_AVANT_CARD_DATA_CACHE = {};
                    window.__JK_AVANT_LAST_PANEL_SNAPSHOT = '';
                    window.__JK_AVANT_LAST_CARD_CLICKED = null;
                    window.__JK_AVANT_CARD_CLICKED_AT = 0;
                    window.__JK_AVANT_CARD_QUEUE_CLICK_SLOW = false;
                    try {
                        var anterior = window.__JK_FAVORITOS_INCREMENTAL_COLLECTOR_V1;
                        if (anterior && anterior.observer && typeof anterior.observer.disconnect === 'function') {
                            anterior.observer.disconnect();
                        }
                        var incremental = {
                            version: 1,
                            mutationVersion: 0,
                            lastMutationAt: Date.now(),
                            resolvedKeys: {},
                            pendingKeys: {},
                            observer: null
                        };
                        incremental.observer = new MutationObserver(function () {
                            incremental.mutationVersion += 1;
                            incremental.lastMutationAt = Date.now();
                        });
                        incremental.observer.observe(document.body || document.documentElement, {
                            subtree: true,
                            childList: true,
                            characterData: true
                        });
                        window.__JK_FAVORITOS_INCREMENTAL_COLLECTOR_V1 = incremental;
                    } catch (_incrementalErr) {
                        window.__JK_FAVORITOS_INCREMENTAL_COLLECTOR_V1 = null;
                    }
                    if (!window.__JK_FAVORITOS_DOWNLOAD_GUARD_REGISTERED) {
                        window.__JK_FAVORITOS_DOWNLOAD_GUARD_REGISTERED = true;
                        document.addEventListener('click', function (event) {
                            var node = event && event.target;
                            var alvo = node && node.closest ? node.closest('a, button, [role="button"], [download]') : null;
                            if (!alvo) return;
                            var href = String(alvo.href || (alvo.getAttribute && (alvo.getAttribute('href') || alvo.getAttribute('data-href') || '')) || '');
                            var texto = String([
                                alvo.innerText,
                                alvo.textContent,
                                alvo.getAttribute && alvo.getAttribute('aria-label'),
                                alvo.getAttribute && alvo.getAttribute('title'),
                                alvo.getAttribute && alvo.getAttribute('download'),
                                href
                            ].filter(Boolean).join(' ')).toLowerCase();
                            if (/^(blob|data):/i.test(href)
                                || /\\.zip(?:$|[?#])|\\/download\\b|download=|filename=|imagens?\\.zip|images?\\.zip/i.test(href)
                                || /baixar|download|imagens?|images?|fotos?|photos?|zip|exportar|salvar/.test(texto)) {
                                event.preventDefault();
                                event.stopPropagation();
                                event.stopImmediatePropagation();
                            }
                        }, true);
                    }
                    return !!window.__JK_FAVORITOS_INCREMENTAL_COLLECTOR_V1;
                })();
            `, true).catch(() => false));
            let incrementalAtivo = opcoes.incremental !== false && incrementalPreparado === true;
            verificarCancelamento();
            let materializado = null;
            let anuncios = [];
            let totalVisiveis = 0;
            if (loteCliques > 0) {
                verificarCancelamento();
                const metricaInicialRapida = await aguardarCancelavel(obterMetricaRolagemMercadoLivreFavoritos(webview));
                const originalYRapido = Math.max(0, Number(metricaInicialRapida && metricaInicialRapida.y) || 0);
                await aguardarCancelavel(rolarMercadoLivreFavoritos(0, webview));
                await esperarCancelavel(350);
                const basicoInicial = await aguardarCancelavel(aguardarBaseMercadoLivreColetavelFavoritos({
                    limite,
                    timeoutMs: Math.min(30000, Math.max(8000, deadlinePreparacao - Date.now())),
                    onProgress,
                    webview
                }).catch(() => null));
                anuncios = (basicoInicial && basicoInicial.anuncios) || [];
                totalVisiveis = Math.min(limite, Number(basicoInicial && basicoInicial.total) || anuncios.length);
                materializado = {
                    anuncios,
                    totalVisiveis,
                    originalY: originalYRapido
                };
                if (!anuncios.length && Date.now() < deadlinePreparacao) {
                    materializado = await aguardarCancelavel(materializarCardsPrimeiraPaginaMercadoLivreFavoritos({
                        limite,
                        deadlineMs: Math.min(deadlinePreparacao, Date.now() + 16000),
                        onProgress,
                        webview
                    }).catch(() => null));
                    anuncios = (materializado && materializado.anuncios) || [];
                    totalVisiveis = Math.min(limite, Number(materializado && materializado.totalVisiveis) || anuncios.length);
                    materializado = materializado || { anuncios, totalVisiveis };
                    materializado.originalY = originalYRapido;
                }
                if (!anuncios.length && Date.now() < deadlinePreparacao) {
                    const emergenciaInicial = await aguardarCancelavel(extrairBaseMercadoLivreEmergencialWebview({
                        limite,
                        timeoutMs: Math.min(9000, Math.max(3500, deadlinePreparacao - Date.now())),
                        webview
                    }).catch(() => null));
                    if (emergenciaInicial && Array.isArray(emergenciaInicial.anuncios) && emergenciaInicial.anuncios.length) {
                        anuncios = emergenciaInicial.anuncios;
                        totalVisiveis = Math.min(limite, Number(emergenciaInicial.total) || anuncios.length);
                        materializado = {
                            ...(materializado || {}),
                            anuncios,
                            totalVisiveis,
                            originalY: originalYRapido,
                            emergencia: true
                        };
                    }
                }
                emitirProgressoPrimeiraPaginaFavoritos(onProgress, resumoPrimeiraPaginaFavoritos(totalVisiveis, anuncios, {
                    etapa: 'materializando',
                    y: 0,
                    height: metricaInicialRapida && metricaInicialRapida.height
                }));
            } else {
                materializado = await aguardarCancelavel(materializarCardsPrimeiraPaginaMercadoLivreFavoritos({
                    limite,
                    deadlineMs: Math.min(deadlinePreparacao, Date.now() + 28000),
                    onProgress,
                    webview
                }));
                anuncios = (materializado && materializado.anuncios) || [];
                totalVisiveis = Math.min(limite, Number(materializado && materializado.totalVisiveis) || anuncios.length);
                if (!anuncios.length && Date.now() < deadlinePreparacao) {
                    const emergenciaMaterializacao = await aguardarCancelavel(extrairBaseMercadoLivreEmergencialWebview({
                        limite,
                        timeoutMs: Math.min(9000, Math.max(3500, deadlinePreparacao - Date.now())),
                        webview
                    }).catch(() => null));
                    if (emergenciaMaterializacao && Array.isArray(emergenciaMaterializacao.anuncios) && emergenciaMaterializacao.anuncios.length) {
                        anuncios = emergenciaMaterializacao.anuncios;
                        totalVisiveis = Math.min(limite, Number(emergenciaMaterializacao.total) || anuncios.length);
                        materializado = {
                            ...(materializado || {}),
                            anuncios,
                            totalVisiveis,
                            emergencia: true
                        };
                    }
                }
            }
            verificarCancelamento();
            anuncios = await aguardarCancelavel(completarBaseMercadoLivreComApiFavoritos(anuncios, {
                concorrencia: 4,
                deadlineMs: Math.min(deadlinePreparacao, Date.now() + (loteCliques > 0 ? 2500 : 18000)),
                maxItens: loteCliques > 0 ? 4 : 0,
                timeoutMs: loteCliques > 0 ? 1200 : 3500
            }));
            const fimMaterializacao = Date.now();
            let metrica = await aguardarCancelavel(obterMetricaRolagemMercadoLivreFavoritos(webview));
            let viewport = Math.max(560, Number(metrica && metrica.view) || 800);
            let altura = alturaVarreduraPrimeiraPaginaFavoritos(
                Number(metrica && metrica.height) || viewport,
                Number(metrica && metrica.resultsBottom) || 0,
                viewport
            );
            const posicoesUnicas = montarPosicoesVarreduraPrimeiraPaginaFavoritos(altura, viewport, limite);
            let cliquesAvantDesligados = false;
            let loginAvantBloqueado = false;
            let assinaturaPassadaAnterior = '';
            let motivoEncerramento = '';
            let passadasExecutadas = 0;
            let posicoesPercorridasTotal = 0;
            let totalCliquesAvant = 0;
            let totalCapturadosAvant = 0;
            const chavesCapturadasAvant = new Set();
            const normalizarChavePendenciaAvant = (chave) => String(chave || '')
                .replace(/^id:/i, 'mlb:')
                .replace(/^url:/i, 'link:')
                .toLowerCase();

            for (let passada = 1; passada <= maxPassadas && Date.now() < deadline && !loginAvantBloqueado; passada += 1) {
                let posicoesPercorridasPassada = 0;
                const pendentesAvantPassada = new Set();
                for (let index = 0; index < posicoesUnicas.length && Date.now() < deadline && !loginAvantBloqueado; index += 1) {
                    posicoesPercorridasPassada = index + 1;
                    posicoesPercorridasTotal += 1;
                    verificarCancelamento();
                    await aguardarCancelavel(rolarMercadoLivreFavoritos(posicoesUnicas[index], webview));
                    await esperarCancelavel(100);
                    const avantVisivelAntes = await aguardarCancelavel(capturarAvantProCardsVisiveisRapidoWebview({ limite, webview }).catch(() => null));
                    anuncios = mesclarAnunciosAvant(anuncios, (avantVisivelAntes && avantVisivelAntes.anuncios) || []);
                    ((avantVisivelAntes && avantVisivelAntes.anuncios) || []).forEach(item => {
                        const chave = normalizarChavePendenciaAvant(chaveCanonicaAnuncioFavoritos(item));
                        if (!chave) return;
                        pendentesAvantPassada.delete(chave);
                        chavesCapturadasAvant.add(chave);
                    });
                    const deveTentarCliqueAvant = loteCliques > 0 && !cliquesAvantDesligados;
                    const usarBuscaProfunda = !incrementalAtivo
                        || index === posicoesUnicas.length - 1
                        || !(avantVisivelAntes && Number(avantVisivelAntes.total) > 0);
                    const verificarLoginNestaPosicao = !incrementalAtivo
                        || index === 0
                        || index === posicoesUnicas.length - 1;
                    const clickInfo = deveTentarCliqueAvant
                        ? await aguardarCancelavel(acionarCardsAvantProFilaWebview({
                            maxClicks: loteCliques,
                            maxRuntimeMs: cliquesAvantDesligados
                                ? Math.min(1600, Math.max(1200, deadline - Date.now() - 500))
                                : Math.min(4000, Math.max(1500, deadline - Date.now() - 500)),
                            deepScan: usarBuscaProfunda,
                            checkLogin: verificarLoginNestaPosicao,
                            webview
                        }))
                        : { clicked: 0, totalCandidates: 0, keys: [], capturados: 0, skipped: true, slowDisabled: cliquesAvantDesligados };
                    if (clickInfo && clickInfo.slowDisabled) cliquesAvantDesligados = true;
                    if (clickInfo && clickInfo.error && incrementalAtivo) incrementalAtivo = false;
                    (Array.isArray(clickInfo && clickInfo.pendingKeys) ? clickInfo.pendingKeys : []).forEach(chave => {
                        const normalizada = normalizarChavePendenciaAvant(chave);
                        if (normalizada) pendentesAvantPassada.add(normalizada);
                    });
                    (Array.isArray(clickInfo && clickInfo.resolvedKeys) ? clickInfo.resolvedKeys : []).forEach(chave => {
                        const normalizada = normalizarChavePendenciaAvant(chave);
                        if (!normalizada) return;
                        pendentesAvantPassada.delete(normalizada);
                        chavesCapturadasAvant.add(normalizada);
                    });
                    if (loteCliques > 0 && cliquesAvantDesligados && pendentesAvantPassada.size === 0) {
                        pendentesAvantPassada.add('__avant_slow_disabled__');
                    }
                    totalCliquesAvant += Math.max(0, Number(clickInfo && clickInfo.clicked) || 0);
                    totalCapturadosAvant = Math.max(chavesCapturadasAvant.size, totalCapturadosAvant);
                    if (clickInfo && clickInfo.loginBlocked) {
                        cliquesAvantDesligados = true;
                        loginAvantBloqueado = true;
                    }
                    if (clickInfo && clickInfo.clicked) {
                        await esperarCancelavel(Math.min(1800, 500 + (clickInfo.clicked * 200)));
                        await aguardarCancelavel(aguardarDadosAvantProEstaveisWebview({
                            minWaitMs: 120,
                            stableMs: 300,
                            maxWaitMs: 1600,
                            webview
                        }).catch(() => null));
                    }
                    const avantVisivelDepois = clickInfo && clickInfo.clicked
                        ? await aguardarCancelavel(capturarAvantProCardsVisiveisRapidoWebview({ limite, webview }).catch(() => null))
                        : null;
                    anuncios = mesclarAnunciosAvant(anuncios, (avantVisivelDepois && avantVisivelDepois.anuncios) || []);
                    const avantDomDepois = (!incrementalAtivo || usarBuscaProfunda || (clickInfo && clickInfo.clicked))
                        ? await aguardarCancelavel(extrairAnunciosAvantProDomWebview({ limite, webview }).catch(() => null))
                        : null;
                    anuncios = mesclarAnunciosAvant(anuncios, (avantDomDepois && avantDomDepois.anuncios) || []);
                    const avantCache = await aguardarCancelavel(extrairCacheAvantProCardsWebview({ limite, webview }).catch(() => null));
                    anuncios = mesclarAnunciosAvant(anuncios, (avantCache && avantCache.anuncios) || []);
                    const deveAtualizarBaseMl = index === 0
                        || index === posicoesUnicas.length - 1
                        || index % 4 === 3
                        || Date.now() + 4500 >= deadline;
                    const basico = deveAtualizarBaseMl
                        ? await aguardarCancelavel(extrairCardsMercadoLivreBasicoWebview({ limite, webview }).catch(() => null))
                        : null;
                    if (basico && Array.isArray(basico.anuncios)) {
                        anuncios = mesclarAnunciosAvant(basico.anuncios, anuncios);
                    }
                    totalVisiveis = Math.max(
                        totalVisiveis,
                        Math.min(limite, Number(basico && basico.total) || ((basico && basico.anuncios && basico.anuncios.length) || 0)),
                        Math.min(limite, Number(avantCache && avantCache.total) || ((avantCache && avantCache.anuncios && avantCache.anuncios.length) || 0)),
                        anuncios.length
                    );
                    const resumo = resumoPrimeiraPaginaFavoritos(totalVisiveis, anuncios, {
                        etapa: 'avant',
                        passada,
                        maxPassadas,
                        posicao: index + 1,
                        posicoes: posicoesUnicas.length,
                        clicados: clickInfo && clickInfo.clicked || 0,
                        candidatosAvant: clickInfo && clickInfo.totalCandidates || 0,
                        capturadosAvant: clickInfo && clickInfo.capturados || 0,
                        erroCliqueAvant: clickInfo && clickInfo.error || '',
                        loginAvantBloqueado: !!(clickInfo && clickInfo.loginBlocked),
                        cliquesAvantDesligados: !!(clickInfo && clickInfo.slowDisabled),
                        tempoRestanteMs: Math.max(0, deadline - Date.now())
                    });
                    verificarCancelamento();
                    emitirProgressoPrimeiraPaginaFavoritos(onProgress, resumo);
                    if (loginAvantBloqueado && anuncios.length) break;
                    if (totalVisiveis > 0 && resumo.com_dados_avant >= totalVisiveis) break;
                    await esperarCancelavel(20);
                }
                passadasExecutadas = passada;
                if (incrementalAtivo) await esperarCancelavel(800);
                const estadoIncremental = incrementalAtivo
                    ? await aguardarCancelavel(webview.executeJavaScript(`
                        (function () {
                            var state = window.__JK_FAVORITOS_INCREMENTAL_COLLECTOR_V1;
                            return state ? {
                                mutationVersion: Number(state.mutationVersion) || 0,
                                lastMutationAt: Number(state.lastMutationAt) || 0,
                                quietMs: Math.max(0, Date.now() - (Number(state.lastMutationAt) || Date.now()))
                            } : null;
                        })();
                    `, true).catch(() => null))
                    : null;
                const assinaturaPassadaAtual = assinaturaEstabilidadePrimeiraPaginaFavoritos(anuncios);
                const passadaCompleta = posicoesPercorridasPassada >= posicoesUnicas.length;
                const plateauEstavel = deveEncerrarPlateauPrimeiraPaginaFavoritos({
                    passada,
                    passadaCompleta,
                    assinaturaAtual: assinaturaPassadaAtual,
                    assinaturaAnterior: assinaturaPassadaAnterior,
                    pendentesAvant: pendentesAvantPassada.size,
                    mutationQuietMs: estadoIncremental ? estadoIncremental.quietMs : undefined
                });
                const resumoPassada = resumoPrimeiraPaginaFavoritos(totalVisiveis, anuncios, {
                    etapa: 'passada',
                    passada,
                    maxPassadas,
                    loginAvantBloqueado,
                    login_avant_bloqueado: loginAvantBloqueado,
                    passada_completa: passadaCompleta,
                    pendentes_avant: pendentesAvantPassada.size,
                    mutation_quiet_ms: estadoIncremental ? estadoIncremental.quietMs : 0,
                    incremental: incrementalAtivo,
                    plateau_estavel: plateauEstavel,
                    tempoRestanteMs: Math.max(0, deadline - Date.now())
                });
                emitirProgressoPrimeiraPaginaFavoritos(onProgress, resumoPassada);
                if (loginAvantBloqueado && anuncios.length) {
                    motivoEncerramento = 'login_avant';
                    break;
                }
                if (totalVisiveis > 0 && resumoPassada.com_dados_avant >= totalVisiveis) {
                    motivoEncerramento = 'completude_avant';
                    break;
                }
                if (plateauEstavel) {
                    motivoEncerramento = 'stable_plateau';
                    break;
                }
                assinaturaPassadaAnterior = assinaturaPassadaAtual;
            }
            const fimAvant = Date.now();
            if (!motivoEncerramento) {
                motivoEncerramento = Date.now() >= deadline ? 'tempo_limite' : 'max_passadas';
            }

            verificarCancelamento();
            const basicoFinal = await aguardarCancelavel(extrairCardsMercadoLivreBasicoWebview({ limite, webview }).catch(() => null));
            const avantCacheFinal = Date.now() < deadline
                ? await aguardarCancelavel(extrairCacheAvantProCardsWebview({ limite, webview }).catch(() => null))
                : null;
            const avantDomFinal = Date.now() < deadline
                ? await aguardarCancelavel(extrairAnunciosAvantProDomWebview({ limite, webview }).catch(() => null))
                : null;
            anuncios = mesclarAnunciosAvant((basicoFinal && basicoFinal.anuncios) || [], anuncios);
            anuncios = mesclarAnunciosAvant(anuncios, (avantCacheFinal && avantCacheFinal.anuncios) || []);
            anuncios = mesclarAnunciosAvant(anuncios, (avantDomFinal && avantDomFinal.anuncios) || []);
            if (!anuncios.length) {
                const emergenciaFinal = await aguardarCancelavel(extrairBaseMercadoLivreEmergencialWebview({
                    limite,
                    timeoutMs: Math.min(12000, Math.max(4500, deadline - Date.now())),
                    webview
                }).catch(() => null));
                if (emergenciaFinal && Array.isArray(emergenciaFinal.anuncios) && emergenciaFinal.anuncios.length) {
                    anuncios = mesclarAnunciosAvant(emergenciaFinal.anuncios, anuncios);
                }
            }
            anuncios = await aguardarCancelavel(completarBaseMercadoLivreComApiFavoritos(anuncios, {
                concorrencia: 4,
                deadlineMs: deadline
            }));
            const fimFinalizacao = Date.now();
            totalVisiveis = Math.max(
                totalVisiveis,
                Math.min(limite, Number(basicoFinal && basicoFinal.total) || ((basicoFinal && basicoFinal.anuncios && basicoFinal.anuncios.length) || 0)),
                Math.min(limite, Number(avantCacheFinal && avantCacheFinal.total) || ((avantCacheFinal && avantCacheFinal.anuncios && avantCacheFinal.anuncios.length) || 0)),
                Math.min(limite, Number(avantDomFinal && avantDomFinal.total) || ((avantDomFinal && avantDomFinal.anuncios && avantDomFinal.anuncios.length) || 0)),
                anuncios.length
            );
            await aguardarCancelavel(rolarMercadoLivreFavoritos(Number(materializado && materializado.originalY) || 0, webview));
            verificarCancelamento();
            const resumoFinal = resumoPrimeiraPaginaFavoritos(totalVisiveis, anuncios, {
                etapa: 'final',
                loginAvantBloqueado,
                login_avant_bloqueado: loginAvantBloqueado,
                tempo_esgotado: Date.now() >= deadline,
                motivo_encerramento: motivoEncerramento,
                passadas: passadasExecutadas,
                posicoes_percorridas: posicoesPercorridasTotal,
                cliques_avant: totalCliquesAvant,
                capturados_avant: totalCapturadosAvant,
                tempo_materializacao_ms: Math.max(0, fimMaterializacao - inicio),
                tempo_avant_ms: Math.max(0, fimAvant - fimMaterializacao),
                tempo_finalizacao_ms: Math.max(0, fimFinalizacao - fimAvant),
                elapsedMs: Date.now() - inicio
            });
            emitirProgressoPrimeiraPaginaFavoritos(onProgress, resumoFinal);
            const anunciosSaida = anuncios.slice(0, limite);
            try {
                Object.defineProperty(anunciosSaida, '__avantNaoVinculado', {
                    value: Array.isArray(anuncios.__avantNaoVinculado) ? anuncios.__avantNaoVinculado : [],
                    enumerable: false
                });
            } catch (_err) {
                anunciosSaida.__avantNaoVinculado = Array.isArray(anuncios.__avantNaoVinculado) ? anuncios.__avantNaoVinculado : [];
            }
            return {
                success: true,
                totalVisiveis,
                anuncios: anunciosSaida,
                resumo: resumoFinal,
                loginAvantBloqueado,
                tempoEsgotado: !!resumoFinal.tempo_esgotado,
                elapsedMs: resumoFinal.elapsedMs,
                motivoEncerramento,
                passadasExecutadas,
                posicoesPercorridas: posicoesPercorridasTotal,
                totalCliquesAvant,
                totalCapturadosAvant
            };
        }

        async function aguardarDadosAvantProEstaveisWebview(opcoes = {}) {
            const webview = resolverWebviewFavoritosColeta(opcoes);
            if (!webview || typeof webview.executeJavaScript !== 'function') return null;
            const minWaitMs = Math.max(0, Number(opcoes.minWaitMs) || AVANT_PRO_ESTABILIDADE_MIN_MS);
            const stableMs = Math.max(250, Number(opcoes.stableMs) || AVANT_PRO_ESTABILIDADE_MS);
            const maxWaitMs = Math.max(minWaitMs + stableMs, Number(opcoes.maxWaitMs) || AVANT_PRO_ESTABILIDADE_MAX_MS);
            const pollMs = Math.max(120, Number(opcoes.pollMs) || 220);
            return await webview.executeJavaScript(`
                (async function () {
                    var minWaitMs = ${JSON.stringify(minWaitMs)};
                    var stableMs = ${JSON.stringify(stableMs)};
                    var maxWaitMs = ${JSON.stringify(maxWaitMs)};
                    var pollMs = ${JSON.stringify(pollMs)};
                    var sleep = function (ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); };
                    var queryAllDeepLocal = function (selector, root) {
                        var found = [];
                        var visited = [];
                        var visit = function (base) {
                            if (!base || visited.indexOf(base) >= 0) return;
                            visited.push(base);
                            try {
                                if (base.querySelectorAll) {
                                    var nodes = Array.prototype.slice.call(base.querySelectorAll(selector));
                                    nodes.forEach(function (node) {
                                        if (found.indexOf(node) < 0) found.push(node);
                                    });
                                    Array.prototype.slice.call(base.querySelectorAll('*')).forEach(function (node) {
                                        if (node && node.shadowRoot) visit(node.shadowRoot);
                                    });
                                }
                            } catch (e) {}
                        };
                        visit(root || document);
                        return found;
                    };
                    var queryOneDeepLocal = function (selector, root) {
                        var nodes = queryAllDeepLocal(selector, root);
                        return nodes.length ? nodes[0] : null;
                    };
                    var snapshot = function () {
                        var labelsAvant = /informacoes?\\s+avant(?:\\s*pro|pro)?|vendas?\\s+do\\s+produto|vendas?\\s+estimad|ritmo\\s+atual|faturamento\\s+do\\s+produto|nome\\s+do\\s+vendedor|localizacao\\s+do\\s+vendedor|participacao|visitas\\s+do\\s+anuncio|comissao|reputacao\\s+do\\s+vendedor|anuncio\\s+(?:ganhador\\s+)?criado\\s+em/i;
                        var normalizarBuscaLocal = function (value) {
                            var text = String(value || '').replace(/\\s+/g, ' ').trim();
                            try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (e) {}
                            return text.toLowerCase();
                        };
                        var rows = queryAllDeepLocal('.avantpro-product-info-row');
                        if (rows.length) {
                            return rows.map(function (row) {
                                var label = queryOneDeepLocal('.avantpro-product-info-row-label', row) || row.querySelector('.avantpro-product-info-row-label');
                                var value = queryOneDeepLocal('.avantpro-product-info-row-value', row) || row.querySelector('.avantpro-product-info-row-value');
                                var labelText = String(label && label.textContent || '').replace(/\\s+/g, ' ').trim();
                                var valueText = String(value && value.textContent || '').replace(/\\s+/g, ' ').trim();
                                return labelText || valueText ? [labelText, valueText].join('=') : '';
                            }).filter(Boolean).join('|');
                        }
                        var parts = [String(document.body && (document.body.innerText || document.body.textContent) || '')];
                        queryAllDeepLocal('*').slice(0, 1500).forEach(function (node) {
                            var nodeText = String(node && (node.innerText || node.textContent) || '').replace(/\\s+/g, ' ').trim();
                            if (nodeText) parts.push(nodeText);
                        });
                        return parts.join('\\n')
                            .split(/\\n+/)
                            .map(function (line) { return line.replace(/\\s+/g, ' ').trim(); })
                            .filter(function (line) { return line && labelsAvant.test(normalizarBuscaLocal(line)); })
                            .slice(0, 120)
                            .join('|');
                    };
                    var inicio = Date.now();
                    var ultimo = snapshot();
                    var ultimaMudanca = Date.now();
                    while (Date.now() - inicio < maxWaitMs) {
                        await sleep(pollMs);
                        var atual = snapshot();
                        if (atual !== ultimo) {
                            ultimo = atual;
                            ultimaMudanca = Date.now();
                        }
                        var decorrido = Date.now() - inicio;
                        if (ultimo && decorrido >= minWaitMs && Date.now() - ultimaMudanca >= stableMs) {
                            return { stable: true, elapsedMs: decorrido, rows: ultimo.split('|').filter(Boolean).length };
                        }
                    }
                    return { stable: false, elapsedMs: Date.now() - inicio, rows: ultimo ? ultimo.split('|').filter(Boolean).length : 0 };
                })();
            `, true);
        }

        async function diagnosticarAvantProNoWebview(webview = mlWebviewEl) {
            if (!webview || typeof webview.executeJavaScript !== 'function') return null;
            return await webview.executeJavaScript(`
                (function () {
                    var AVANT_ID = 'jdefnfmbnchmnjkcknaadaddgjbgephh';
                    var normalizar = function (value) {
                        return String(value || '').replace(/\\s+/g, ' ').trim();
                    };
                    var normalizarBusca = function (value) {
                        var text = normalizar(value);
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (e) {}
                        return text.toLowerCase();
                    };
                    var queryAllDeep = function (selector, root) {
                        var found = [];
                        var seen = [];
                        var walk = function (base) {
                            if (!base || seen.indexOf(base) >= 0) return;
                            seen.push(base);
                            try {
                                if (base.querySelectorAll) {
                                    found = found.concat(Array.prototype.slice.call(base.querySelectorAll(selector)));
                                }
                            } catch (_err) {}
                            var nodes = [];
                            try {
                                nodes = base.querySelectorAll ? Array.prototype.slice.call(base.querySelectorAll('*')) : [];
                            } catch (_err2) {}
                            for (var i = 0; i < nodes.length; i += 1) {
                                if (nodes[i] && nodes[i].shadowRoot) walk(nodes[i].shadowRoot);
                            }
                        };
                        walk(root || document);
                        return found.filter(function (node, index) { return found.indexOf(node) === index; });
                    };
                    var textoDeep = function () {
                        var partes = [];
                        var vistos = [];
                        var incluir = function (value) {
                            var text = normalizar(value);
                            if (!text || vistos.indexOf(text) >= 0) return;
                            vistos.push(text);
                            partes.push(text);
                        };
                        incluir(document.body && (document.body.innerText || document.body.textContent));
                        queryAllDeep('*').slice(0, 1800).forEach(function (node) {
                            try {
                                incluir(node.innerText || node.textContent || node.value || '');
                                if (node.getAttribute) {
                                    incluir(node.getAttribute('aria-label'));
                                    incluir(node.getAttribute('title'));
                                    incluir(node.getAttribute('placeholder'));
                                    incluir(node.getAttribute('class'));
                                    incluir(node.getAttribute('id'));
                                }
                            } catch (_err) {}
                        });
                        return partes.join(' ');
                    };
                    var contextoNode = function (node) {
                        var root = null;
                        try {
                            root = node && node.closest && node.closest('form, article, section, aside, li, [role="dialog"], [class*="avant"], [id*="avant"], [class*="login"], [class*="auth"], [class*="poly-card"], [class*="ui-search-result"]');
                        } catch (_err) {}
                        var host = null;
                        try {
                            var nodeRoot = node && node.getRootNode ? node.getRootNode() : null;
                            host = nodeRoot && nodeRoot.host ? nodeRoot.host : null;
                        } catch (_err2) {}
                        return normalizarBusca([
                            node && (node.innerText || node.textContent || node.value),
                            node && node.getAttribute && node.getAttribute('aria-label'),
                            node && node.getAttribute && node.getAttribute('title'),
                            root && (root.innerText || root.textContent),
                            root && root.getAttribute && root.getAttribute('class'),
                            root && root.getAttribute && root.getAttribute('id'),
                            host && (host.innerText || host.textContent),
                            host && host.getAttribute && host.getAttribute('class'),
                            host && host.getAttribute && host.getAttribute('id')
                        ].filter(Boolean).join(' '));
                    };
                    var contemAvant = function (value) {
                        return /avant\\s*pro|avantpro|carregar\\s+dado?s?\\s+avant|informacoes?\\s+avant|ferramentas|vincular\\s+(?:conta|agora)|conectar\\s+conta|use\\s+gratis|usar\\s+gratis|rotulos\\s+visuais|atualizar\\s+dado?s?|extrair\\s+dado?s?/i.test(normalizarBusca(value));
                    };
                    var estaDentroDeCardProduto = function (node) {
                        try {
                            return !!(node && node.closest && node.closest('li.ui-search-layout__item, .poly-card, [class*="poly-card"], [class*="ui-search-result"], [data-testid*="result"], [data-testid*="card"], [class*="shops__layout-item"]'));
                        } catch (_err) {
                            return false;
                        }
                    };
                    var ehControleDadosAvant = function (value) {
                        return /carregar\\s+dado?s?\\s+avant|informacoes?\\s+avant|informacoes?\\s+avantpro|rotulos\\s+visuais|atualizar\\s+dado?s?\\s+avant|extrair\\s+dado?s?\\s+avant/i.test(normalizarBusca(value));
                    };
                    var contemDadosAnuncioAvant = function (value) {
                        return /vendas?\\s+do\\s+(?:produto|anuncio|item)|vendas?\\s+estimad|ritmo\\s+atual|faturamento\\s+do\\s+produto|nome\\s+do\\s+vendedor|visitas\\s+do\\s+anuncio|participacao|anuncio\\s+(?:ganhador\\s+)?criado\\s+em|reputacao\\s+do\\s+vendedor/i.test(normalizarBusca(value));
                    };
                    var contarRotulosAvantNoTexto = function (value) {
                        var busca = normalizarBusca(value);
                        var padroes = [
                            /vendas?\\s+do\\s+produto/,
                            /vendas?\\s+estimad/,
                            /ritmo\\s+atual/,
                            /visitas\\s+do\\s+anuncio/,
                            /participacao\\b/,
                            /\\bmarca\\b/,
                            /faturamento\\s+do\\s+produto/,
                            /nome\\s+do\\s+vendedor/,
                            /localizacao\\s+do\\s+vendedor/,
                            /\\bmarca\\b/,
                            /participacao\\b/,
                            /visitas\\s+do\\s+anuncio/,
                            /comissao\\b/,
                            /reputacao\\s+do\\s+vendedor/,
                            /anuncio\\s+(?:ganhador\\s+)?criado\\s+em/
                        ];
                        return padroes.reduce(function (total, regex) {
                            return total + (regex.test(busca) ? 1 : 0);
                        }, 0);
                    };
                    var deepText = textoDeep();
                    var bodyBusca = normalizarBusca(deepText);
                    var bodyHasAvantInfo = /informacoes?\\s+avant(?:\\s*pro|pro)?/.test(bodyBusca);
                    var loginRealRegex = /iniciar\\s+sessao|insira\\s+suas\\s+credenciais|seu\\s+e-?mail|seu\\s+email|senha|password/;
                    var conviteAvantRegex = /vincule\\s+o\\s+avantpro|vincular\\s+agora|comece\\s+a\\s+usar|use\\s+gratis|usar\\s+gratis|liberar\\s+os\\s+recursos|dica\\s+avantpro|tutoriais/;
                    var modalAvantPromocional = /avant\\s*pro|avantpro|avantprocloud/.test(bodyBusca)
                        && conviteAvantRegex.test(bodyBusca)
                        && !loginRealRegex.test(bodyBusca);
                    var avantLoginDialog = /avant\\s*pro|avantpro|avantprocloud/.test(bodyBusca)
                        && !modalAvantPromocional
                        && /vincular\\s+conta|entre\\s+na\\s+sua\\s+conta|iniciar\\s+sessao|insira\\s+suas\\s+credenciais|seu\\s+e-?mail|seu\\s+email|chame\\s+o\\s+suporte|nao\\s+possui\\s+uma\\s+conta|crie\\s+uma\\s+aqui/.test(bodyBusca);
                    var avantLoginEmailInputs = Array.prototype.slice.call(queryAllDeep('input:not([type="hidden"])')).filter(function (input) {
                        var attrs = normalizarBusca([
                            input.type,
                            input.name,
                            input.id,
                            input.className,
                            input.placeholder,
                            input.getAttribute && input.getAttribute('aria-label')
                        ].join(' '));
                        var root = input.closest && input.closest('form, [role="dialog"], [class*="avant"], [id*="avant"], [class*="modal"], [class*="login"], [class*="auth"]');
                        var ctx = normalizarBusca((root && root.innerText) || '');
                        return /email|e-?mail|mail/.test(attrs + ' ' + ctx)
                            && /avant\\s*pro|avantpro|iniciar\\s+sessao|credenciais/.test(ctx + ' ' + bodyBusca);
                    }).length;
                    var bodyDataLabels = contarRotulosAvantNoTexto(bodyBusca);
                    var cardSelectors = [
                        'li.ui-search-layout__item',
                        'div.ui-search-result__wrapper',
                        'div.ui-search-result',
                        'div.poly-card',
                        'section.poly-card',
                        '[class*="poly-card"]',
                        '[class*="ui-search-result"]',
                        '[class*="ui-search-layout__item"]',
                        '[class*="shops__layout-item"]',
                        '[class*="item__info"]',
                        '[class*="ui-search-gallery"]',
                        '[data-testid*="item"]',
                        '[data-testid*="card"]',
                        '[data-testid*="result"]',
                        'main ol > li',
                        'main ul > li'
                    ].join(',');
                    var productLinkSelectors = [
                        'a[href*="produto.mercadolivre.com.br/MLB-"]',
                        'a[href*="/MLB-"]',
                        'a[href*="/p/MLB"]',
                        'a[href*="/up/MLB"]',
                        'a[href*="item_id=MLB"]',
                        'a[href*="item_id%3AMLB"]',
                        'a[href*="wid=MLB"]'
                    ].join(',');
                    var productLinks = [];
                    try {
                        productLinks = Array.prototype.slice.call(document.querySelectorAll(productLinkSelectors)).filter(function (node) {
                            var href = String(node && node.href || node && node.getAttribute && node.getAttribute('href') || '');
                            return /(?:produto\\.mercadolivre\\.com\\.br\\/MLB-\\d+|\\/MLB-\\d+|\\/p\\/MLB\\d+|\\/up\\/MLB|item_id(?:=|%3A)MLB\\d+|wid=MLB\\d+)/i.test(href);
                        });
                    } catch (_linkErr) {}
                    var cardCount = 0;
                    try {
                        cardCount = document.querySelectorAll(cardSelectors).length;
                    } catch (_cardErr) {}
                    cardCount = Math.max(cardCount, productLinks.length);
                    var loadingScreen = false;
                    try {
                        loadingScreen = document.querySelectorAll('.ui-search-loading-screen, [class*="ui-search-loading-screen"], [class*="loading-screen"], .andes-progress-indicator-circular').length > 0 && cardCount <= 0;
                    } catch (_loadingErr) {}
                    var rows = queryAllDeep('.avantpro-product-info-row').length;
                    var widgets = queryAllDeep('[class*="avantpro"], [id*="avantpro"], [data-testid*="avantpro"], [class*="Avant"], [id*="Avant"]').length;
                    var actionButtons = 0;
                    var infoButtons = 0;
                    var accountLinkButtons = 0;
                    var accountLinkButtonsCards = 0;
                    var accountLinkButtonsGlobais = 0;
                    var botoesLoginSemSeparacao = 0;
                    var toolsButtons = 0;
                    Array.prototype.slice.call(queryAllDeep('button, a, input[type="button"], input[type="submit"], [role="button"]')).forEach(function (node) {
                        var text = [
                            node.innerText,
                            node.textContent,
                            node.value,
                            node.getAttribute && node.getAttribute('aria-label'),
                            node.getAttribute && node.getAttribute('title')
                        ].map(normalizar).join(' ');
                        var contexto = contextoNode(node);
                        if (contemAvant(text)) actionButtons += 1;
                        if (ehControleDadosAvant(text)) infoButtons += 1;
                        var busca = normalizarBusca(text);
                        var ehBotaoContaAvant = /vincular\\s+(?:conta|agora)|conectar\\s+conta|fazer\\s+login|entrar\\s+no\\s+avant|use\\s+gratis|usar\\s+gratis/.test(busca);
                        if (/^login$|\\blogin\\b/.test(busca)
                            && (/avant\\s*pro|avantpro|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos|extensao/.test(contexto)
                                || (/avant\\s*pro|avantpro/.test(bodyBusca) && /comece\\s+a\\s+usar|entre\\s+na\\s+sua\\s+conta|liberar\\s+os\\s+recursos/.test(bodyBusca)))) ehBotaoContaAvant = true;
                        if (ehBotaoContaAvant) {
                            accountLinkButtons += 1;
                            if (estaDentroDeCardProduto(node)) {
                                accountLinkButtonsCards += 1;
                            } else if (contexto) {
                                accountLinkButtonsGlobais += 1;
                            } else {
                                botoesLoginSemSeparacao += 1;
                            }
                        }
                        if (/ferramentas|rotulos\\s+visuais|avant\\s*pro|avantpro/.test(busca)) toolsButtons += 1;
                    });
                    var taggedNodes = 0;
                    queryAllDeep('[class], [id], script').forEach(function (node) {
                        var text = [
                            node.id,
                            node.className,
                            node.getAttribute && node.getAttribute('src')
                        ].map(normalizar).join(' ');
                        if (contemAvant(text)) taggedNodes += 1;
                    });
                    var dataTextNodes = 0;
                    queryAllDeep('[class*="avant"], [id*="avant"], [class*="Avant"], [id*="Avant"], .created-time-card, .avantpro-product-info-row, .avantpro-product-info-row *').forEach(function (node) {
                        var text = normalizar(node.innerText || node.textContent || '');
                        if (text && contemDadosAnuncioAvant(text)) dataTextNodes += 1;
                    });
                    var extensionResources = 0;
                    try {
                        extensionResources = performance.getEntriesByType('resource').filter(function (entry) {
                            var name = String(entry && entry.name || '').toLowerCase();
                            return name.indexOf('chrome-extension://' + AVANT_ID) >= 0 || name.indexOf('avantpro') >= 0;
                        }).length;
                    } catch (_err) {}
                    var hasRealAvantData = rows > 0 || dataTextNodes > 0 || (!avantLoginDialog && bodyDataLabels >= 2);
                    var ok = hasRealAvantData || (!avantLoginDialog && infoButtons > 0);
                    var paginaComCards = cardCount > 0;
                    var avantLoginBloqueante = !hasRealAvantData && avantLoginDialog;
                    var avantLoginEmailInputsBloqueantes = hasRealAvantData ? 0 : avantLoginEmailInputs;
                    var bodySugereLoginAvant = !hasRealAvantData
                        && !modalAvantPromocional
                        && /avant\\s*pro|avantpro|avantprocloud/.test(bodyBusca)
                        && /login|entrar|comece\\s+a\\s+usar|use\\s+gratis|usar\\s+gratis|liberar\\s+os\\s+recursos|nao\\s+possui\\s+uma\\s+conta|crie\\s+uma\\s+aqui/.test(bodyBusca);
                    var extensionDetected = widgets > 0 || actionButtons > 0 || taggedNodes > 0 || extensionResources > 0 || bodyHasAvantInfo || bodyDataLabels > 0;
                    var accountLinkBloqueante = accountLinkButtons > 0 && !modalAvantPromocional;
                    var accountLinkButtonsGlobaisEfetivos = conviteAvantRegex.test(bodyBusca) && !loginRealRegex.test(bodyBusca)
                        ? 0
                        : accountLinkButtonsGlobais;
                    var loginClickAt = Number(window.__JK_AVANT_PRO_LOGIN_CLICKED_AT || 0);
                    var cardClickAt = Number(window.jkAvantCardClickedAt || window.__JK_AVANT_CARD_CLICKED_AT || 0);
                    var legacyPareceLogin = avantLoginBloqueante || bodySugereLoginAvant || accountLinkBloqueante;
                    var loginAvantClicadoRecentemente = loginClickAt > 0 && Date.now() - loginClickAt < ${ML_FAVORITOS_AVANT_LOGIN_RECENTE_MS};
                    var reloadAposLoginAvantRecomendado = loginAvantClicadoRecentemente && (!hasRealAvantData && !avantLoginEmailInputsBloqueantes);
                    var loginGlobalPendente = !!(accountLinkButtonsGlobaisEfetivos > 0 || botoesLoginSemSeparacao > 0 || avantLoginBloqueante || avantLoginEmailInputsBloqueantes > 0 || bodySugereLoginAvant);
                    var apenasCardsPedemLogin = accountLinkButtonsCards > 0 && !loginGlobalPendente;
                    var loginAvantPendenteVisual = loginGlobalPendente;
                    return {
                        ok: !!ok,
                        rows: rows,
                        cardCount: cardCount,
                        productLinkCount: productLinks.length,
                        loadingScreen: !!loadingScreen,
                        widgets: widgets,
                        actionButtons: actionButtons,
                        infoButtons: infoButtons,
                        accountLinkButtons: accountLinkButtons,
                        accountLinkButtonsCards: accountLinkButtonsCards,
                        accountLinkButtonsGlobais: accountLinkButtonsGlobais,
                        botoesLoginSemSeparacao: botoesLoginSemSeparacao,
                        toolsButtons: toolsButtons,
                        needsAccountLink: !reloadAposLoginAvantRecomendado && loginAvantPendenteVisual && !hasRealAvantData,
                        accountActionRequired: !reloadAposLoginAvantRecomendado && loginAvantPendenteVisual && !hasRealAvantData,
                        hasAvantData: !!hasRealAvantData,
                        modalAvantPromocional: !!modalAvantPromocional,
                        avantLoginDialog: !!avantLoginBloqueante,
                        avantLoginEmailInputs: avantLoginEmailInputsBloqueantes,
                        bodySugereLoginAvant: !!bodySugereLoginAvant,
                        apenasCardsPedemLogin: !!apenasCardsPedemLogin,
                        reloadAposLoginAvantRecomendado: !!reloadAposLoginAvantRecomendado,
                        loginAvantClicadoRecentemente: !!loginAvantClicadoRecentemente,
                        legacyPareceLogin: !!legacyPareceLogin,
                        loginClickAt: loginClickAt,
                        cardClickAt: cardClickAt,
                        shellOnly: !ok && extensionDetected,
                        extensionDetected: !!extensionDetected,
                        dataTextNodes: dataTextNodes,
                        bodyHasAvantInfo: !!bodyHasAvantInfo,
                        bodyDataLabels: bodyDataLabels,
                        taggedNodes: taggedNodes,
                        extensionResources: extensionResources,
                        url: location.href,
                        title: document.title || ''
                    };
                })();
            `, true).catch(() => null);
        }

        async function aguardarPrimeirosDadosAvantOuCardsWebview(opcoes = {}) {
            const webview = resolverWebviewFavoritosColeta(opcoes);
            if (!webview || typeof webview.executeJavaScript !== 'function') return null;
            if (!monitoramentoPaginaFavoritosAutomaticoAtivo() && !acaoUsuarioFavoritosPermiteLeituraPagina(opcoes)) {
                return statusMonitoramentoPaginaFavoritosDesativado({
                    etapa: 'aguardar_primeiros_dados'
                });
            }
            const timeoutMs = Math.max(800, Number(opcoes.timeoutMs) || 3200);
            const idleMs = Math.max(120, Number(opcoes.idleMs) || 260);
            return await webview.executeJavaScript(`
                (function () {
                    var timeoutMs = ${JSON.stringify(timeoutMs)};
                    var idleMs = ${JSON.stringify(idleMs)};
                    var pollMs = Math.max(120, Math.min(400, idleMs));
                    var startedAt = Date.now();
                    var timer = null;
                    var normalizarBusca = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var queryAllDeepLocal = function (selector, root) {
                        var found = [];
                        var seen = [];
                        var walk = function (base) {
                            if (!base || seen.indexOf(base) >= 0) return;
                            seen.push(base);
                            try {
                                if (base.querySelectorAll) {
                                    found = found.concat(Array.prototype.slice.call(base.querySelectorAll(selector)));
                                }
                            } catch (_err) {}
                            var nodes = [];
                            try {
                                nodes = base.querySelectorAll ? Array.prototype.slice.call(base.querySelectorAll('*')) : [];
                            } catch (_err2) {}
                            for (var i = 0; i < nodes.length; i += 1) {
                                if (nodes[i] && nodes[i].shadowRoot) walk(nodes[i].shadowRoot);
                            }
                        };
                        walk(root || document);
                        return found.filter(function (node, index) { return found.indexOf(node) === index; });
                    };
                    var textoDeepLocal = function () {
                        var partes = [];
                        var vistos = [];
                        var incluir = function (value) {
                            var text = String(value || '').replace(/\\s+/g, ' ').trim();
                            if (!text || vistos.indexOf(text) >= 0) return;
                            vistos.push(text);
                            partes.push(text);
                        };
                        incluir(document.body && (document.body.innerText || document.body.textContent));
                        queryAllDeepLocal('*').slice(0, 1500).forEach(function (node) {
                            try {
                                incluir(node.innerText || node.textContent || node.value || '');
                                if (node.getAttribute) {
                                    incluir(node.getAttribute('aria-label'));
                                    incluir(node.getAttribute('title'));
                                    incluir(node.getAttribute('class'));
                                    incluir(node.getAttribute('id'));
                                }
                            } catch (_err) {}
                        });
                        return partes.join(' ');
                    };
                    var contarRotulosAvant = function (value) {
                        var busca = normalizarBusca(value);
                        var padroes = [
                            /informacoes?\\s+avant(?:\\s*pro|pro)?/,
                            /vendas?\\s+do\\s+produto/,
                            /vendas?\\s+estimad/,
                            /ritmo\\s+atual/,
                            /visitas\\s+do\\s+anuncio/,
                            /participacao\\b/,
                            /\\bmarca\\b/,
                            /faturamento\\s+do\\s+produto/,
                            /nome\\s+do\\s+vendedor/,
                            /localizacao\\s+do\\s+vendedor/,
                            /anuncio\\s+(?:ganhador\\s+)?criado\\s+em/,
                            /comissao\\b/
                        ];
                        return padroes.reduce(function (total, regex) {
                            return total + (regex.test(busca) ? 1 : 0);
                        }, 0);
                    };
                    var snapshot = function () {
                        var bodyText = textoDeepLocal();
                        var busca = normalizarBusca(bodyText);
                        var cardSelectors = [
                            'li.ui-search-layout__item',
                            'div.ui-search-result__wrapper',
                            'div.ui-search-result',
                            'div.poly-card',
                            'section.poly-card',
                            'article.poly-card',
                            'article.ui-search-result',
                            '.poly-card__content',
                            '.poly-component__title',
                            '[class*="poly-card__content"]',
                            '[class*="poly-component__title"]',
                            '[class*="ui-search-result__content"]',
                            '[class*="ui-search-item__title"]',
                            '[class*="product-card"]',
                            '[class*="andes-card"]',
                            '[class*="poly-card"]',
                            '[class*="ui-search-result"]',
                            '[class*="ui-search-layout__item"]',
                            '[class*="shops__layout-item"]',
                            '[class*="item__info"]',
                            '[class*="ui-search-gallery"]',
                            '[data-testid*="item"]',
                            '[data-testid*="card"]',
                            '[data-testid*="result"]',
                            'main ol > li',
                            'main ul > li'
                        ].join(',');
                        var productLinkSelectors = [
                            'a[href*="produto.mercadolivre.com.br/MLB-"]',
                            'a[href*="/MLB-"]',
                            'a[href*="/p/MLB"]',
                            'a[href*="/up/MLB"]',
                            'a[href*="item_id=MLB"]',
                            'a[href*="item_id%3AMLB"]',
                            'a[href*="wid=MLB"]'
                        ].join(',');
                        var productLinks = [];
                        try {
                            productLinks = Array.prototype.slice.call(document.querySelectorAll(productLinkSelectors)).filter(function (node) {
                                var href = String(node && node.href || node && node.getAttribute && node.getAttribute('href') || '');
                                return /(?:produto\\.mercadolivre\\.com\\.br\\/MLB-\\d+|\\/MLB-\\d+|\\/p\\/MLB\\d+|\\/up\\/MLB|item_id(?:=|%3A)MLB\\d+|wid=MLB\\d+)/i.test(href);
                            });
                        } catch (_linkErr) {}
                        var cardCount = 0;
                        try {
                            cardCount = document.querySelectorAll(cardSelectors).length;
                        } catch (_cardErr) {}
                        cardCount = Math.max(cardCount, productLinks.length);
                        var resultadoVisual = /\b\d+\s+resultados?\b/.test(busca)
                            || queryAllDeepLocal('h1, [class*="quantity-results"], [class*="ui-search-search-result"], [class*="breadcrumb__title"]').some(function (node) {
                                return /\b\d+\s+resultados?\b/.test(normalizarBusca(node && (node.innerText || node.textContent) || ''));
                            });
                        if (resultadoVisual && cardCount <= 0) cardCount = 1;
                        var cardLoginPanelsAvant = 0;
                        try {
                            Array.prototype.slice.call(document.querySelectorAll(cardSelectors)).forEach(function (card) {
                                var cardBusca = normalizarBusca(card && (card.innerText || card.textContent) || '');
                                if (/avant\\s*pro|avantpro|avantprocloud/.test(cardBusca)
                                    && /vincular\\s+(?:conta|agora)|conectar\\s+conta|fazer\\s+login|entrar\\s+no\\s+avant|comece\\s+a\\s+usar|liberar\\s+os\\s+recursos/.test(cardBusca)) {
                                    cardLoginPanelsAvant += 1;
                                }
                            });
                        } catch (_cardLoginErr) {}
                        var loadingScreen = false;
                        try {
                            loadingScreen = document.querySelectorAll('.ui-search-loading-screen, [class*="ui-search-loading-screen"], [class*="loading-screen"], .andes-progress-indicator-circular').length > 0 && cardCount <= 0 && !resultadoVisual;
                        } catch (_loadingErr) {}
                        var avantLabels = contarRotulosAvant(bodyText);
                        var rows = queryAllDeepLocal('.avantpro-product-info-row').length;
                        var needsLogin =
                            location.href.indexOf('/gz/account-verification') >= 0 ||
                            location.href.indexOf('/jms/mlb/lgz/login') >= 0 ||
                            busca.indexOf('para continuar, acesse sua conta') >= 0;
                        var avantLoginCandidate =
                            /avant\\s*pro|avantpro|avantprocloud/.test(busca) &&
                            /vincule\\s+o\\s+avantpro|vincular\\s+agora|vincular\\s+conta|comece\\s+a\\s+usar|entre\\s+na\\s+sua\\s+conta|liberar\\s+os\\s+recursos|iniciar\\s+sessao|insira\\s+suas\\s+credenciais|seu\\s+e-?mail|seu\\s+email|chame\\s+o\\s+suporte/.test(busca);
                        var hasAvantData = rows > 0 || (!avantLoginCandidate && avantLabels >= 2);
                        var needsAvantLoginGlobal = avantLoginCandidate && !hasAvantData;
                        var needsAvantLoginCards = cardLoginPanelsAvant > 0 && !hasAvantData;
                        var needsAvantLogin = needsAvantLoginGlobal || needsAvantLoginCards;
                        var resultadoMlVisivel = cardCount > 0 || productLinks.length > 0 || !!resultadoVisual;
                        var noResults = !loadingScreen && !resultadoMlVisivel && !hasAvantData && (
                            busca.indexOf('nao encontramos resultados') >= 0 ||
                            busca.indexOf('nao ha resultados') >= 0 ||
                            busca.indexOf('sem resultados') >= 0 ||
                            busca.indexOf('verifique a ortografia') >= 0
                        );
                        return {
                            ready: cardCount > 0 || avantLabels > 0 || rows > 0 || needsLogin || needsAvantLogin || noResults,
                            hasCards: cardCount > 0,
                            hasAvantData: hasAvantData,
                            cardCount: cardCount,
                            productLinkCount: productLinks.length,
                            loadingScreen: !!loadingScreen,
                            avantLabels: avantLabels,
                            rows: rows,
                            needsLogin: needsLogin,
                            needsAvantLogin: needsAvantLogin,
                            needsAvantLoginCards: needsAvantLoginCards,
                            cardLoginPanelsAvant: cardLoginPanelsAvant,
                            noResults: noResults,
                            elapsedMs: Date.now() - startedAt,
                            url: location.href,
                            title: document.title || ''
                        };
                    };
                    return new Promise(function (resolve) {
                        var done = false;
                        var readyAt = 0;
                        var finish = function (result) {
                            if (done) return;
                            done = true;
                            try { clearTimeout(timer); } catch (_err) {}
                            resolve(result || snapshot());
                        };
                        var check = function () {
                            if (done) return;
                            var atual = snapshot();
                            if (atual.ready) {
                                if (!readyAt) readyAt = Date.now();
                                if (Date.now() - readyAt >= idleMs) {
                                    finish(atual);
                                    return;
                                }
                            } else {
                                readyAt = 0;
                            }
                            var elapsedMs = Date.now() - startedAt;
                            if (elapsedMs >= timeoutMs) {
                                atual.timeout = true;
                                finish(atual);
                                return;
                            }
                            timer = setTimeout(check, Math.min(pollMs, Math.max(1, timeoutMs - elapsedMs)));
                        };
                        check();
                    });
                })();
            `, true).catch(() => null);
        }

        async function diagnosticarResultadosMercadoLivreWebview() {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return null;
            return await mlWebviewEl.executeJavaScript(`
                (function () {
                    var normalizarBusca = function (value) {
                        var text = String(value || '').replace(/\\s+/g, ' ').trim();
                        try { text = text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, ''); } catch (_err) {}
                        return text.toLowerCase();
                    };
                    var busca = normalizarBusca(document.body && (document.body.innerText || document.body.textContent) || '');
                    var cardSelectors = [
                        'li.ui-search-layout__item',
                        'div.ui-search-result__wrapper',
                        'div.ui-search-result',
                        'div.poly-card',
                        'section.poly-card',
                        'article.poly-card',
                        'article.ui-search-result',
                        '.poly-card__content',
                        '.poly-component__title',
                        '[class*="poly-card__content"]',
                        '[class*="poly-component__title"]',
                        '[class*="ui-search-result__content"]',
                        '[class*="ui-search-item__title"]',
                        '[class*="product-card"]',
                        '[class*="andes-card"]',
                        '[data-testid="product-card"]',
                        '[data-testid="item-card"]',
                        '[class*="poly-card"]',
                        '[class*="ui-search-result"]',
                        '[class*="ui-search-layout__item"]',
                        '[class*="shops__layout-item"]',
                        '[class*="item__info"]',
                        '[class*="ui-search-gallery"]',
                        '[data-testid*="item"]',
                        '[data-testid*="card"]',
                        '[data-testid*="result"]',
                        'main ol > li',
                        'main ul > li'
                    ].join(',');
                    var productLinkSelectors = [
                        'a[href*="produto.mercadolivre.com.br/MLB-"]',
                        'a[href*="/MLB-"]',
                        'a[href*="/p/MLB"]',
                        'a[href*="/up/MLB"]',
                        'a[href*="item_id=MLB"]',
                        'a[href*="item_id%3AMLB"]',
                        'a[href*="wid=MLB"]'
                    ].join(',');
                    var cardCount = 0;
                    try { cardCount = document.querySelectorAll(cardSelectors).length; } catch (_cardErr) {}
                    var productLinks = [];
                    try {
                        productLinks = Array.prototype.slice.call(document.querySelectorAll(productLinkSelectors)).filter(function (node) {
                            var href = String(node && node.href || node && node.getAttribute && node.getAttribute('href') || '');
                            return /(?:produto\\.mercadolivre\\.com\\.br\\/MLB-\\d+|\\/MLB-\\d+|\\/p\\/MLB\\d+|\\/up\\/MLB|item_id(?:=|%3A)MLB\\d+|wid=MLB\\d+)/i.test(href);
                        });
                    } catch (_linkErr) {}
                    cardCount = Math.max(cardCount, productLinks.length);
                    var resultadoVisual = /\b\d+\s+resultados?\b/.test(busca)
                        || Array.prototype.slice.call(document.querySelectorAll('h1, [class*="quantity-results"], [class*="ui-search-search-result"], [class*="breadcrumb__title"]')).some(function (node) {
                            return /\b\d+\s+resultados?\b/.test(normalizarBusca(node && (node.innerText || node.textContent) || ''));
                        });
                    if (resultadoVisual && cardCount <= 0) cardCount = 1;
                    var loadingScreen = false;
                    try {
                        loadingScreen = document.querySelectorAll('.ui-search-loading-screen, [class*="ui-search-loading-screen"], [class*="loading-screen"], .andes-progress-indicator-circular').length > 0 && cardCount <= 0 && !resultadoVisual;
                    } catch (_loadingErr) {}
                    var avantLabels = [
                        /vendas?\\s+do\\s+produto/,
                        /vendas?\\s+estimad/,
                        /ritmo\\s+atual/,
                        /visitas\\s+do\\s+anuncio/,
                        /nome\\s+do\\s+vendedor/,
                        /anuncio\\s+(?:ganhador\\s+)?criado\\s+em/
                    ].reduce(function (total, regex) {
                        return total + (regex.test(busca) ? 1 : 0);
                    }, 0);
                    var needsLogin =
                        location.href.indexOf('/gz/account-verification') >= 0 ||
                        location.href.indexOf('/jms/mlb/lgz/login') >= 0 ||
                        busca.indexOf('para continuar, acesse sua conta') >= 0;
                    var avantLoginCandidate =
                        /avant\\s*pro|avantpro|avantprocloud/.test(busca) &&
                        /vincule\\s+o\\s+avantpro|vincular\\s+agora|vincular\\s+conta|comece\\s+a\\s+usar|entre\\s+na\\s+sua\\s+conta|liberar\\s+os\\s+recursos|iniciar\\s+sessao|insira\\s+suas\\s+credenciais|seu\\s+e-?mail|seu\\s+email|chame\\s+o\\s+suporte/.test(busca);
                    var hasRealAvantData = avantLabels >= 2 && !avantLoginCandidate;
                    var resultadoMlVisivel = cardCount > 0 || productLinks.length > 0 || !!resultadoVisual;
                    var noResults = !loadingScreen && !resultadoMlVisivel && !hasRealAvantData && (
                        busca.indexOf('nao encontramos resultados') >= 0 ||
                        busca.indexOf('nao ha resultados') >= 0 ||
                        busca.indexOf('sem resultados') >= 0 ||
                        busca.indexOf('verifique a ortografia') >= 0
                    );
                    return {
                        url: location.href,
                        title: document.title || '',
                        cardCount: cardCount,
                        productLinkCount: productLinks.length,
                        resultadoVisual: !!resultadoVisual,
                        hasCards: cardCount > 0,
                        hasAvantData: hasRealAvantData,
                        avantLabels: avantLabels,
                        loadingScreen: !!loadingScreen,
                        noResults: !!noResults,
                        needsLogin: !!needsLogin
                    };
                })();
            `, true).catch(() => null);
        }

        async function aguardarResultadosMercadoLivreWebview(opcoes = {}) {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return null;
            const timeoutMs = Math.max(2500, Number(opcoes.timeoutMs) || 22000);
            const pollMs = Math.max(250, Number(opcoes.pollMs) || 600);
            const reloadAfterMs = Math.max(3000, Number(opcoes.reloadAfterMs) || 9000);
            const inicio = Date.now();
            let recarregou = false;
            let ultimo = null;
            while (Date.now() - inicio < timeoutMs) {
                ultimo = await diagnosticarResultadosMercadoLivreWebview().catch(() => null);
                if (ultimo && (ultimo.hasCards || ultimo.hasAvantData || ultimo.noResults || ultimo.needsLogin)) {
                    return { ...ultimo, elapsedMs: Date.now() - inicio, reloaded: recarregou };
                }
                if (
                    !recarregou
                    && opcoes.recarregarSeTravado !== false
                    && !mlFavoritosEmExecucao
                    && ultimo
                    && ultimo.loadingScreen
                    && Date.now() - inicio >= reloadAfterMs
                ) {
                    mostrarBalaoFavoritosStatus(opcoes.mensagemRecarregando || 'Mercado Livre ainda esta carregando. Recarregando a pesquisa uma vez...', {
                        larga: true,
                        titulo: 'Aguardando Mercado Livre'
                    });
                    recarregou = true;
                    await mlWebviewEl.executeJavaScript(`
                        (function () {
                            if (/^https?:\\/\\//i.test(location.href)) {
                                location.reload();
                                return true;
                            }
                            return false;
                        })();
                    `, true).catch(() => false);
                    await esperar(2800);
                    continue;
                }
                await esperar(pollMs);
            }
            return ultimo ? { ...ultimo, elapsedMs: Date.now() - inicio, reloaded: recarregou, timeout: true } : null;
        }

        async function recarregarNavegadorMlParaAvantPro() {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return false;
            if (mlFavoritosEmExecucao) return false;
            await garantirExtensoesNavegadorMl().catch(() => []);
            const recarregou = await mlWebviewEl.executeJavaScript(`
                (function () {
                    if (window.__JK_ML_FAVORITOS_EM_EXECUCAO) return false;
                    if (!/^https?:\\/\\//i.test(location.href)) return false;
                    location.reload();
                    return true;
                })();
            `, true).catch(() => false);
            if (!recarregou) return false;
            await esperar(3400);
            tentarLoginAvantProNoWebview(mlWebviewEl);
            return true;
        }

        async function aguardarAvantProNoWebview(opcoes = {}) {
            if (!monitoramentoPaginaFavoritosAutomaticoAtivo() && !acaoUsuarioFavoritosPermiteLeituraPagina(opcoes)) {
                return statusMonitoramentoPaginaFavoritosDesativado({
                    etapa: 'aguardar_avant_pro'
                });
            }
            const timeoutMs = Math.max(800, Number(opcoes.timeoutMs) || 14000);
            const pollMs = Math.max(150, Number(opcoes.pollMs) || 350);
            const inicio = Date.now();
            let ultimo = null;
            let tentouAcionar = false;
            let tentouLoginAvant = false;
            while (Date.now() - inicio < timeoutMs) {
                ultimo = await diagnosticarAvantProNoWebview().catch(() => null);
                if (ultimo && ultimo.modalAvantPromocional) {
                    const fechamentoAvant = await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
                    if (fechamentoAvant && fechamentoAvant.closed) {
                        await esperar(350);
                        continue;
                    }
                }
                if (statusAvantProTemDadosColetaveis(ultimo)) {
                    return {
                        ...normalizarStatusAvantProPronto(ultimo),
                        elapsedMs: Date.now() - inicio
                    };
                }
                const cardsComExtensao = !!(ultimo && Number(ultimo.cardCount || 0) > 0 && (
                    ultimo.extensionDetected
                    || Number(ultimo.widgets || 0) > 0
                    || Number(ultimo.actionButtons || 0) > 0
                    || Number(ultimo.toolsButtons || 0) > 0
                ));
                if (statusAvantProPedeLoginOuVinculo(ultimo)) {
                    const fechamentoAvant = await fecharModalBloqueanteAvantProNoWebview().catch(() => null);
                    if (fechamentoAvant && fechamentoAvant.closed) {
                        await esperar(450);
                        continue;
                    }
                    mostrarAcaoConectarAvantPro(ultimo);
                    return {
                        ...ultimo,
                        ok: false,
                        accountActionRequired: true,
                        message: 'Avant Pro nao retornou dados coletaveis. Confirme manualmente o login no navegador interno.'
                    };
                }
                if (!tentouAcionar && ultimo && !statusAvantProPedeLoginOuVinculo(ultimo) && (ultimo.infoButtons > 0 || ultimo.toolsButtons > 0 || ultimo.shellOnly)) {
                    tentouAcionar = true;
                    const clicados = await acionarControlesAvantProNoWebview({
                        forceClick: true,
                        permitirFerramentas: true,
                        maxClicks: 10
                    }).catch(() => 0);
                    if (clicados) await esperar(900);
                }
                if (tentouAcionar && !statusAvantProPedeLoginOuVinculo(ultimo) && statusAvantProShellSemDados(ultimo)) {
                    return {
                        ...normalizarStatusAvantProCarregadoParaColeta(ultimo),
                        elapsedMs: Date.now() - inicio
                    };
                }
                if (tentouAcionar && !tentouLoginAvant && opcoes.autoLoginAvant !== false && statusAvantProShellSemDados(ultimo) && !cardsComExtensao) {
                    tentouLoginAvant = true;
                    mostrarBalaoFavoritosStatus('Avant Pro apareceu sem dados. Tentando abrir login automaticamente...', {
                        larga: true,
                        titulo: 'Conectar Avant Pro'
                    });
                    const resultadoLogin = await abrirLoginAvantProNoWebview().catch(() => null);
                    await esperar((resultadoLogin && resultadoLogin.success) ? 650 : 420);
                    continue;
                }
                await esperar(pollMs);
            }

            const paginaPronta = await aguardarPrimeirosDadosAvantOuCardsWebview({
                timeoutMs: 650,
                idleMs: 120,
                acaoUsuario: acaoUsuarioFavoritosPermiteLeituraPagina(opcoes)
            }).catch(() => null);
            if (paginaPronta && paginaPronta.hasAvantData) {
                return {
                    ...(ultimo || {}),
                    ok: true,
                    hasCards: !!paginaPronta.hasCards,
                    hasAvantData: true,
                    cardCount: paginaPronta.cardCount || 0,
                    elapsedMs: Date.now() - inicio
                };
            }

            if (opcoes.recarregarSeAusente && mlFavoritosEmExecucao) {
                return ultimo ? {
                    ...ultimo,
                    ok: false,
                    reloadBlockedDuringFavoritos: true
                } : {
                    ok: false,
                    unavailable: true,
                    reloadBlockedDuringFavoritos: true
                };
            }

            if (opcoes.recarregarSeAusente && !(ultimo && ultimo.needsAccountLink)) {
                mostrarBalaoFavoritosStatus(opcoes.mensagemRecarregando || 'Avant Pro nao carregou de primeira. Recarregando Mercado Livre...');
                const recarregou = await recarregarNavegadorMlParaAvantPro().catch(() => false);
                if (recarregou) {
                    const depoisReload = await aguardarAvantProNoWebview({
                        ...opcoes,
                        recarregarSeAusente: false,
                        timeoutMs: Number(opcoes.timeoutAposReloadMs) || timeoutMs
                    }).catch(() => null);
                    if (depoisReload) return { ...depoisReload, reloaded: true };
                }
            }

            return ultimo ? { ...ultimo, ok: false } : { ok: false, unavailable: true };
        }

        async function recarregarNavegadorMlAposLoginAvantProFavoritos(status = {}, opcoes = {}) {
            if (!monitoramentoPaginaFavoritosAutomaticoAtivo() && !acaoUsuarioFavoritosPermiteLeituraPagina(opcoes)) {
                return {
                    ...(status || {}),
                    ...statusMonitoramentoPaginaFavoritosDesativado({
                        etapa: 'retomada_pos_login_avant'
                    })
                };
            }
            const termo = String(opcoes.termo || '').trim();
            const statusInicial = status || {};
            const motivoPendente = 'login_ou_vinculo_ainda_pendente';
            const chavesRetomada = ML_FAVORITOS_AVANT_RELOAD_APOS_LOGIN_KEYS;
            if (statusAvantProPedeLoginOuVinculo(statusInicial)) {
                return {
                    ...statusInicial,
                    ok: false,
                    accountActionRequired: true,
                    reason: motivoPendente
                };
            }
            chavesRetomada.forEach((key, index) => {
                try {
                    localStorage.setItem(key, index === 0 ? '1' : String(Date.now()));
                } catch (_err) {}
            });
            if (termo) {
                mostrarBalaoFavoritosStatus(`Avant Pro liberado para "${termo}". Retomando favoritos sem atualizar a pagina...`, {
                    larga: true,
                    titulo: 'Retomando Favoritos'
                });
            }
            await acionarControlesAvantProNoWebview({
                forceClick: true,
                permitirFerramentas: true,
                maxClicks: 12
            }).catch(() => 0);
            const aguardado = await aguardarAvantProNoWebview({
                recarregarSeAusente: false,
                autoLoginAvant: false,
                timeoutMs: Number(opcoes.timeoutMs) || 6500,
                pollMs: Number(opcoes.pollMs) || 350,
                acaoUsuario: acaoUsuarioFavoritosPermiteLeituraPagina(opcoes)
            }).catch(() => null);
            let finalStatus = aguardado || await diagnosticarAvantProNoWebview().catch(() => statusInicial);
            if (statusAvantProTemDadosColetaveis(finalStatus)) {
                finalStatus = normalizarStatusAvantProPronto(finalStatus);
            } else if (!statusAvantProPedeLoginOuVinculo(finalStatus) && statusAvantProShellSemDados(finalStatus)) {
                finalStatus = normalizarStatusAvantProCarregadoParaColeta(finalStatus);
            }
            return {
                ...(finalStatus || {}),
                resumedAfterAvantLogin: true,
                reloadedAfterAvantLogin: false,
                reason: 'retomada_pos_login_sem_reload'
            };
        }
        function mesclarAnunciosAvant(destino, origem) {
            const mapa = new Map();
            const avantNaoVinculado = []
                .concat(Array.isArray(destino && destino.__avantNaoVinculado) ? destino.__avantNaoVinculado : [])
                .concat(Array.isArray(origem && origem.__avantNaoVinculado) ? origem.__avantNaoVinculado : []);

            const fonteMl = (item) => /mercado_livre|api_item|api|ml_dom|card_visivel/i.test(String(
                item && (item.origem_dados || item.source || item.tituloFonte || item.titulo_fonte || item.precoFonte || item.fonte_preco || '')
            ));
            const normalizarItem = (item, padraoFonte = '') => {
                const fontePadrao = padraoFonte || (fonteMl(item) ? 'mercado_livre_dom' : '');
                const normalizado = prepararAnuncioMercadoLivreCanonico(item || {}, fontePadrao || 'coleta_mesmo_mlb');
                normalizado.chave_canonica = chaveCanonicaAnuncioFavoritos(normalizado);
                normalizado.chaveCanonica = normalizado.chave_canonica;
                return normalizado;
            };
            const copiarCampoSeVazio = (dest, src, campo, alias, fonteCampo, fonteValor) => {
                const atual = String(dest[campo] || dest[alias] || '').trim();
                const novo = String(src[campo] || src[alias] || '').trim();
                const id = dest.id || src.id || '';
                if (!novo) return;
                if (campo === 'titulo' && !tituloValidoFavoritosCanonico(novo, id)) return;
                if (atual && (campo !== 'titulo' || tituloValidoFavoritosCanonico(atual, id))) return;
                dest[campo] = novo;
                if (alias) dest[alias] = novo;
                if (fonteCampo) {
                    dest[fonteCampo] = src[fonteCampo] || src[fonteCampo.replace(/[A-Z]/g, m => `_${m.toLowerCase()}`)] || fonteValor;
                    dest[fonteCampo.replace(/[A-Z]/g, m => `_${m.toLowerCase()}`)] = dest[fonteCampo];
                }
            };
            const copiarPrecoSeguro = (dest, src, permitirSobrescrever) => {
                if (!precoValidoFavoritosCanonico(src)) return;
                const fonteAtual = typeof fontePrecoFavoritos === 'function' ? fontePrecoFavoritos(dest) : (dest.precoFonte || dest.fonte_preco || '');
                const fonteNova = src.precoFonte || src.preco_fonte || src.fonte_preco || src.source || src.origem_dados || 'mercado_livre_dom';
                const prioridadeAtual = typeof prioridadeFontePrecoFavoritos === 'function' ? prioridadeFontePrecoFavoritos(fonteAtual) : (fonteAtual ? 10 : 0);
                const prioridadeNova = typeof prioridadeFontePrecoFavoritos === 'function' ? prioridadeFontePrecoFavoritos(fonteNova) : 20;
                if (precoValidoFavoritosCanonico(dest) && !(permitirSobrescrever && prioridadeNova >= prioridadeAtual)) return;
                [
                    'preco',
                    'price',
                    'preco_original',
                    'original_price',
                    'standard_price',
                    'preco_promocional',
                    'promotional_price',
                    'promotion_price',
                    'sale_price',
                    'discount_pct',
                    'moeda',
                    'currency_id'
                ].forEach(campo => {
                    if (src[campo] !== null && src[campo] !== undefined && src[campo] !== '') dest[campo] = src[campo];
                });
                dest.precoFonte = fonteNova;
                dest.preco_fonte = fonteNova;
                dest.fonte_preco = fonteNova;
            };
            const aplicarDadosComplementares = (atual, item) => {
                const combinado = { ...atual };
                const idAtual = normalizarMlbFavoritosCanonico(atual.id || atual.mlb || atual.url || atual.link);
                const idItem = normalizarMlbFavoritosCanonico(item.id || item.mlb || item.url || item.link);
                const linkAtual = limparLinkProdutoMercadoLivreFavoritos(atual.url || atual.permalink || atual.link, idAtual);
                const linkItem = limparLinkProdutoMercadoLivreFavoritos(item.url || item.permalink || item.link, idItem);
                const mesmoId = !!(idAtual && idItem && idAtual === idItem);
                const mesmoLink = !!(linkAtual && linkItem && linkAtual.toLowerCase() === linkItem.toLowerCase());
                if (!mesmoId && !mesmoLink) {
                    avantNaoVinculado.push({ ...item, motivo: 'sem_match_mlb_link' });
                    return combinado;
                }
                if (idAtual || idItem) {
                    combinado.id = idAtual || idItem;
                    combinado.mlb = combinado.id;
                }
                if (linkAtual || linkItem) {
                    const link = linkAtual || linkItem;
                    combinado.url = link;
                    combinado.permalink = link;
                    combinado.link = link;
                    combinado.link_normalizado = link;
                    combinado.linkFonte = combinado.linkFonte || atual.linkFonte || item.linkFonte || 'mercado_livre_dom';
                    combinado.link_fonte = combinado.linkFonte;
                }
                copiarCampoSeVazio(combinado, item, 'titulo', 'title', 'tituloFonte', item.tituloFonte || item.titulo_fonte || 'coleta_mesmo_mlb');
                if (!imagemValidaFavoritosCanonico(combinado) && imagemValidaFavoritosCanonico(item)) {
                    const imagem = typeof obterImagemAnuncioFavoritos === 'function'
                        ? obterImagemAnuncioFavoritos(item)
                        : String(item.imagem || item.thumbnail || item.foto || '').trim();
                    combinado.imagem = imagem;
                    combinado.thumbnail = imagem;
                    combinado.foto = combinado.foto || imagem;
                    combinado.fotoFonte = item.fotoFonte || item.foto_fonte || 'coleta_mesmo_mlb';
                    combinado.foto_fonte = combinado.fotoFonte;
                }
                copiarPrecoSeguro(combinado, item, fonteMl(item));

                const tituloAtual = String(combinado.titulo || combinado.title || '').trim();
                const tituloItem = String(item.titulo || item.title || '').trim();
                if (tituloValidoFavoritosCanonico(tituloAtual, combinado.id) && tituloValidoFavoritosCanonico(tituloItem, combinado.id)) {
                    const similaridade = similaridadeTitulosFavoritosCanonico(tituloAtual, tituloItem);
                    if (similaridade < 0.22) {
                        combinado.suspeito = true;
                        combinado.motivo_suspeito = 'titulo_divergente_mesmo_mlb_link';
                    }
                }

                const vendedorOrigem = normalizarNomeVendedor(item && item.vendedor || '');
                const vendedorAtual = normalizarNomeVendedor(combinado && combinado.vendedor || '');
                const fonteOrigemVendedor = item && (item.vendedorFonte || item.vendedor_fonte || item.fonte_vendedor || '');
                const fonteAtualVendedor = combinado && (combinado.vendedorFonte || combinado.vendedor_fonte || combinado.fonte_vendedor || '');
                if (deveAtualizarVendedor(vendedorAtual, fonteAtualVendedor, vendedorOrigem, fonteOrigemVendedor)) {
                    combinado.vendedor = vendedorOrigem;
                    combinado.vendedorFonte = fonteOrigemVendedor || 'avantpro';
                    combinado.vendedor_fonte = combinado.vendedorFonte;
                    combinado.vendedorFonte = combinado.vendedorFonte;
                }
                const fonteItemVendas = item && (item.vendasFonte || item.vendas_fonte || item.fonte_vendas || '');
                const fonteAtualVendas = combinado && (combinado.vendasFonte || combinado.vendas_fonte || combinado.fonte_vendas || '');
                const vendasItem = parseNumeroVendas(item && item.vendas);
                const vendasAtual = parseNumeroVendas(combinado && combinado.vendas);
                if (deveAtualizarVendas(vendasAtual, fonteAtualVendas, vendasItem, fonteItemVendas)) {
                    combinado.vendas = vendasItem;
                    combinado.vendasFonte = normalizarFonte(fonteItemVendas || 'avantpro');
                    combinado.vendas_fonte = combinado.vendasFonte;
                }
                if (!combinado.data_criacao && item.data_criacao) {
                    combinado.data_criacao = item.data_criacao;
                    combinado.dataCriacaoFonte = item.dataCriacaoFonte || item.data_criacao_fonte || item.source || 'avantpro';
                    combinado.data_criacao_fonte = combinado.dataCriacaoFonte;
                }
                ['media_mensal', 'vendas_estimadas', 'visitas', 'participacao', 'taxa_categoria', 'comissao', 'reputacao_vendedor'].forEach(campo => {
                    if ((combinado[campo] === null || combinado[campo] === undefined || combinado[campo] === '') && item[campo] !== null && item[campo] !== undefined && item[campo] !== '') {
                        combinado[campo] = item[campo];
                    }
                });
                combinado.chave_canonica = chaveCanonicaAnuncioFavoritos(combinado);
                combinado.chaveCanonica = combinado.chave_canonica;
                combinado.estado_qualidade = classificarQualidadeAnuncioFavoritosCanonico(combinado);
                return combinado;
            };

            (destino || []).forEach(item => {
                const normalizado = normalizarItem(item, fonteMl(item) ? 'mercado_livre_dom' : '');
                const key = chaveCanonicaAnuncioFavoritos(normalizado);
                if (key) mapa.set(key, normalizado);
            });

            (origem || []).forEach(item => {
                const normalizado = normalizarItem(item, fonteMl(item) ? 'mercado_livre_dom' : '');
                const key = chaveCanonicaAnuncioFavoritos(normalizado);
                if (!key) {
                    avantNaoVinculado.push({ ...(item || {}), motivo: 'sem_chave_canonica' });
                    return;
                }
                const atual = mapa.get(key);
                if (!atual) {
                    if (fonteMl(normalizado)) {
                        mapa.set(key, normalizado);
                    } else {
                        avantNaoVinculado.push({ ...normalizado, motivo: 'sem_base_mercado_livre' });
                    }
                    return;
                }
                mapa.set(key, aplicarDadosComplementares(atual, normalizado));
            });

            const saida = Array.from(mapa.values()).map(item => {
                item.estado_qualidade = classificarQualidadeAnuncioFavoritosCanonico(item);
                return item;
            });
            try {
                Object.defineProperty(saida, '__avantNaoVinculado', {
                    value: avantNaoVinculado,
                    enumerable: false
                });
            } catch (_err) {
                saida.__avantNaoVinculado = avantNaoVinculado;
            }
            return saida;
        }

        async function coletarDadosAvantComRolagem(opcoes = {}) {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return [];
            if (typeof coletarPrimeiraPaginaFavoritosControlada !== 'function') return [];
            const resultadoNovo = await coletarPrimeiraPaginaFavoritosControlada({
                maxAnuncios: Math.max(20, Math.min(Number(opcoes.maxAnuncios) || Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80, 100)),
                tempoLimiteMs: Math.max(15000, Math.min(Number(opcoes.tempoLimiteMs) || 90000, 180000)),
                maxPassadas: Math.max(1, Math.min(Number(opcoes.maxPassadas) || 2, 3)),
                loteCliques: opcoes.clicarAvant === false ? 0 : Math.max(0, Math.min(Number(opcoes.loteCliques) || 6, 8)),
                onProgress: opcoes.onProgress
            }).catch(() => null);
            return Array.isArray(resultadoNovo && resultadoNovo.anuncios) ? resultadoNovo.anuncios : [];
        }

        async function extrairDadosAvantDoWebviewVisivel(anuncio) {
            if (!mlWebviewEl || typeof mlWebviewEl.executeJavaScript !== 'function') return null;
            const resultado = typeof extrairAnunciosAvantProDomWebview === 'function'
                ? await extrairAnunciosAvantProDomWebview({ limite: Number(ML_FAVORITOS_COLETA_ANUNCIOS_MAX) || 80 })
                : null;
            const anuncios = (resultado && resultado.anuncios) || [];
            return encontrarAnuncioAvantCorrespondente(anuncio, anuncios);
        }

        function normalizarTextoMl(value) {
            return String(value || '')
                .normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '')
                .toLowerCase()
                .replace(/[^a-z0-9]+/g, ' ')
                .replace(/\s+/g, ' ')
                .trim();
        }

        function normalizarSkuBuscaMl(value) {
            return String(value || '')
                .normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '')
                .replace(/[^a-zA-Z0-9]+/g, '')
                .toUpperCase()
                .trim();
        }

        function normalizarNomeVendedor(value) {
            const texto = String(value || '')
                .normalize('NFD')
                .replace(/[\u0300-\u036f]/g, '')
                .replace(/^(vendido\s+por|loja\s+oficial|oficial\s+loja)\s*/i, '')
                .replace(/&quot;|\\\"/g, '"')
                .replace(/\s+/g, ' ')
                .trim();
            return texto;
        }
