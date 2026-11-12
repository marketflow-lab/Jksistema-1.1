(function installJKMediasTabelaFotos(global) {
    'use strict';

    global.JKMedias.defineModule('tabela-fotos', ['core', 'state', 'api', 'resultados', 'visao-carregamento'], (context) => {
        const { api, resultados } = context.modules;
        const seletorCelulaFoto = '[data-jk-medias-foto]';
        const placeholderFoto = '<span class="foto-empty foto-reserva">—</span>';
        const limiteLoteFotos = 64;
        const prazoLoteFotosMs = 60000;
        const atrasosRetentativaFotosMs = [2000, 5000, 10000];
        let fotosVisao = null;
        let revisaoCadastroFotos = 0;

        function cancelarFotosVisao() {
            if (!fotosVisao) return;
            const anterior = fotosVisao;
            fotosVisao = null;
            if (anterior.frame !== null) global.cancelAnimationFrame(anterior.frame);
            global.clearTimeout(anterior.prazo);
            global.clearTimeout(anterior.retentativa);
            anterior.controller?.abort();
            for (const celula of anterior.tbody.querySelectorAll(seletorCelulaFoto)) {
                delete celula.dataset.jkMediasFotoPronta;
                celula.innerHTML = placeholderFoto;
            }
        }

        function prepararFotosVisao(tbody, meta) {
            if (!meta) {
                cancelarFotosVisao();
                return null;
            }
            const headers = { ...api.authHeaders() };
            const sessao = resultados.identidadeSessao(headers);
            const loja = lojaSelecionada || '__todas';
            const storeId = loja === '__todas' ? '' : String(storeIdSelecionado || '');
            const politica = meta.politica_fotos || '';
            const revisao = JSON.stringify([meta.versao ?? '', revisaoCadastroFotos]);
            const chave = JSON.stringify([sessao, tenantKey, loja, storeId, periodoAtual, politica, revisao]);
            if (fotosVisao?.chave === chave) return fotosVisao;
            cancelarFotosVisao();
            fotosVisao = {
                chave, tbody, headers, sessao, loja, storeId, politica, revisao,
                fotos: new Map(), tentativas: new Map(), frame: null, controller: null, retentativa: null,
            };
            return fotosVisao;
        }

        function fotosVigentes(estado) {
            return fotosVisao === estado && abaAtual !== 'listas_pedidos'
                && estado.sessao === resultados.identidadeSessao();
        }

        function agendarFotosMontadas() {
            const estado = fotosVisao;
            if (!estado || estado.frame !== null || estado.controller) return;
            global.clearTimeout(estado.retentativa);
            estado.retentativa = null;
            // Aguarda o navegador apresentar as linhas antes de consultar as fotos.
            estado.frame = global.requestAnimationFrame(() => {
                estado.frame = global.requestAnimationFrame(() => {
                    estado.frame = null;
                    if (fotosVigentes(estado)) void preencherFotosMontadas(estado);
                });
            });
        }

        function preencherCelulaFoto(celula, foto, revisao) {
            if (celula.dataset.jkMediasFotoPronta) return;
            celula.dataset.jkMediasFotoPronta = '1';
            const url = obterUrlFoto(foto);
            celula.innerHTML = url
                ? '<img class="foto-sku" ' + atributoSrcFotoCadastro(url)
                    + ' data-jk-photo-revision="' + escaparHtml(revisao)
                    + '" alt="Foto ' + escaparHtml(celula.dataset.jkMediasFoto) + '" loading="lazy" decoding="async">'
                : '<span class="foto-empty foto-reserva">Sem foto</span>';
        }

        async function preencherFotosMontadas(estado) {
            const skus = new Set();
            const agora = Date.now();
            let proximaTentativa = Infinity;
            for (const celula of estado.tbody.querySelectorAll(seletorCelulaFoto)) {
                const sku = celula.dataset.jkMediasFoto;
                if (estado.fotos.has(sku)) {
                    preencherCelulaFoto(celula, estado.fotos.get(sku), estado.revisao);
                } else {
                    const tentativa = estado.tentativas.get(sku);
                    const disponivelEm = tentativa?.proximaEm ?? 0;
                    if (disponivelEm <= agora && (skus.size < limiteLoteFotos || skus.has(sku))) {
                        skus.add(sku);
                    } else if (disponivelEm > agora) {
                        proximaTentativa = Math.min(proximaTentativa, disponivelEm);
                    }
                }
            }
            if (!skus.size) {
                if (Number.isFinite(proximaTentativa)) {
                    estado.retentativa = global.setTimeout(() => {
                        estado.retentativa = null;
                        if (fotosVigentes(estado)) agendarFotosMontadas();
                    }, Math.max(1, proximaTentativa - agora));
                }
                return;
            }

            for (const sku of skus) {
                const quantidade = (estado.tentativas.get(sku)?.quantidade || 0) + 1;
                estado.tentativas.set(sku, { quantidade, proximaEm: Infinity });
            }

            const controller = new global.AbortController();
            estado.controller = controller;
            // O lote pode esperar pela admissão enquanto o cálculo da tabela termina.
            estado.prazo = global.setTimeout(() => controller.abort(), prazoLoteFotosMs);
            let tentarNovamente = false;
            try {
                const resposta = await api.request('/api/medias-compras/visao/fotos', {
                    method: 'POST',
                    headers: { ...estado.headers, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ loja: estado.loja, store_id: estado.storeId, skus: [...skus] }),
                    signal: controller.signal,
                    cache: 'no-store',
                });
                if (!fotosVigentes(estado)) return;
                if (controller.signal.aborted) { tentarNovamente = true; return; }
                if ([401, 403].includes(resposta.status)) {
                    cancelarFotosVisao();
                    return;
                }
                if (!resposta.ok) {
                    tentarNovamente = [408, 409, 425, 429].includes(resposta.status) || resposta.status >= 500;
                    return;
                }
                const dados = await resposta.json();
                if (!fotosVigentes(estado)) return;
                if (controller.signal.aborted) { tentarNovamente = true; return; }
                if (typeof dados?.politica_fotos !== 'string' || !dados.politica_fotos) return;
                if (estado.politica && estado.politica !== dados.politica_fotos) {
                    cancelarFotosVisao();
                    return;
                }
                if (!dados.fotos || typeof dados.fotos !== 'object' || Array.isArray(dados.fotos)) return;
                estado.politica = dados.politica_fotos;
                global.JKAuthenticatedMedia?.atualizarPoliticaRaiz?.(document, estado.politica);
                for (const sku of skus) {
                    const foto = Object.hasOwn(dados.fotos, sku) ? dados.fotos[sku] : '';
                    estado.fotos.set(sku, typeof foto === 'string' ? foto : '');
                }
            } catch (_erro) {
                tentarNovamente = true;
            } finally {
                global.clearTimeout(estado.prazo);
                estado.controller = null;
                if (tentarNovamente && fotosVigentes(estado)) {
                    for (const sku of skus) {
                        const tentativa = estado.tentativas.get(sku);
                        const atraso = atrasosRetentativaFotosMs[tentativa.quantidade - 1];
                        if (atraso !== undefined) tentativa.proximaEm = Date.now() + atraso;
                    }
                }
                if (fotosVigentes(estado)) agendarFotosMontadas();
            }
        }

        resultados.aoMudarSessao(cancelarFotosVisao);
        for (const evento of ['pagehide', 'jk-tenant-changed', 'jk-photo-policy-changed']) {
            global.addEventListener(evento, cancelarFotosVisao);
        }
        global.addEventListener('jk:cadastro-operation-finished', () => {
            revisaoCadastroFotos += 1;
            const tbody = fotosVisao?.tbody;
            cancelarFotosVisao();
            if (!tbody || abaAtual === 'listas_pedidos') return;
            prepararFotosVisao(tbody, context.modules['visao-carregamento'].obterMetaFotosVisao());
            agendarFotosMontadas();
        });

        return { prepararFotosVisao, agendarFotosMontadas, cancelarFotosVisao, placeholderFoto };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
