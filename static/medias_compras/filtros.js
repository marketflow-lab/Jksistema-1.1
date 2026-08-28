(function installJKMediasFiltros(global) {
    'use strict';

    const app = global.JKMedias;
    app.defineModule('filtros', ['core', 'state', 'api', 'tabela', 'listas'], (context) => {
        const fetch = context.modules.api.request;
        const obterAuthHeaders = context.modules.api.authHeaders;

        function atualizarBotoesPeriodo() {
            [3, 6, 12].forEach(m => {
                const btn = document.getElementById('btnPeriodo' + m);
                if (!btn) return;
                if (periodoAtual === m) btn.classList.add('active');
                else btn.classList.remove('active');
            });
        }

        function renderBotoesLojas(lojas) {
            const box = document.getElementById('lojaBotoes');
            if (!box) return;
            box.innerHTML = '';

            const criarBotao = (texto, valor, indice = 0) => {
                const btn = document.createElement('button');
                btn.type = 'button';
                const selecionada = lojaSelecionada === valor;
                const cor = obterCorLoja(valor, indice);
                btn.className = 'loja-btn' + (selecionada ? ' active' : '');
                btn.style.setProperty('--loja-accent-rgb', cor.accent);
                btn.style.setProperty('--loja-soft-rgb', cor.soft);
                const subtitulo = valor === '__todas' ? 'Visão consolidada do sistema' : 'Filtrar médias e pedidos desta loja';
                const chip = valor === '__todas' ? '🌐 Geral' : '🏬 Loja';
                const status = selecionada ? 'Ativa' : 'Selecionar';
                btn.setAttribute('aria-pressed', selecionada ? 'true' : 'false');
                btn.setAttribute('aria-label', texto + '. ' + subtitulo);
                btn.title = subtitulo;
                btn.innerHTML =
                    '<span class="loja-head">' +
                        '<span class="loja-chip">' + chip + '</span>' +
                        '<span class="loja-check">' + status + '</span>' +
                    '</span>' +
                    '<span class="loja-titulo">' + escaparHtml(texto) + '</span>' +
                    '<span class="loja-arrow">Ver dados →</span>';
                btn.onclick = () => {
                    const lojaAnterior = lojaSelecionada;
                    lojaSelecionada = valor;
                    if (lojaAnterior !== lojaSelecionada) {
                        listaPedidoAtual = null;
                    }
                    renderBotoesLojas(lojasDisponiveis || []);
                    carregarVisao(periodoAtual);
                    if (abaAtual === 'listas_pedidos') {
                        carregarListasPedidos();
                    }
                };
                box.appendChild(btn);
            };

            criarBotao('Todas as lojas', '__todas', 0);
            (Array.isArray(lojas) ? lojas : []).forEach((loja, indice) => {
                const nome = String((loja && loja.nome) || '').trim();
                if (!nome) return;
                criarBotao(nome, nome, indice + 1);
            });
        }

        async function carregarLojas() {
            try {
                const resp = await fetch('/api/lojas', {
                    method: 'GET',
                    headers: { ...obterAuthHeaders() }
                });
                if (!resp.ok) {
                    throw new Error('Não foi possível carregar as lojas.');
                }
                const lojas = await resp.json();
                lojasDisponiveis = Array.isArray(lojas) ? lojas : [];
                if (lojaSelecionada !== '__todas' && !lojasDisponiveis.some(l => String((l && l.nome) || '').trim() === lojaSelecionada)) {
                    lojaSelecionada = '__todas';
                }
                renderBotoesLojas(lojasDisponiveis);
            } catch (_e) {
                lojasDisponiveis = [];
                lojaSelecionada = '__todas';
                renderBotoesLojas([]);
            }
        }

        function setStatus(msg) {
            document.getElementById('status').textContent = msg;
        }

        function lojaEspecificaSelecionada() {
            const loja = String(lojaSelecionada || '').trim();
            return !!loja && loja !== '__todas';
        }

        function exigirLojaEspecificaParaLista(statusFn) {
            if (lojaEspecificaSelecionada()) return true;
            const msg = 'Selecione uma loja especifica antes de criar a lista.';
            if (typeof statusFn === 'function') statusFn(msg);
            else setStatus(msg);
            return false;
        }

        function obterPesquisaSkuNormalizada() {
            return normalizarSkuComparacaoLocal(filtroSkuAtual);
        }

        function filtrarItensPorPesquisaSku(itens) {
            const termo = obterPesquisaSkuNormalizada();
            const lista = Array.isArray(itens) ? itens : [];
            if (!termo) return lista;
            return lista.filter((item) => normalizarSkuComparacaoLocal(item && item.sku).includes(termo));
        }

        function atualizarControlePesquisaSku() {
            const input = document.getElementById('pesquisaSkuInput');
            const btnLimpar = document.getElementById('btnLimparPesquisaSku');
            const box = document.getElementById('skuSearchBox');
            if (input && input.value !== filtroSkuAtual) {
                input.value = filtroSkuAtual;
            }
            if (input) {
                input.placeholder = abaAtual === 'listas_pedidos' ? 'Pesquisar SKU nas listas' : 'Pesquisar SKU';
            }
            if (btnLimpar) {
                btnLimpar.classList.toggle('hidden', !filtroSkuAtual);
            }
            if (box) box.classList.remove('hidden');
        }

        function atualizarPesquisaSku(valor) {
            filtroSkuAtual = String(valor || '').trim();
            atualizarControlePesquisaSku();
            renderAbaAtual();
            if (abaAtual === 'listas_pedidos') {
                carregarSkusListasPedidosParaPesquisa();
            }
        }

        function limparPesquisaSku() {
            filtroSkuAtual = '';
            atualizarControlePesquisaSku();
            renderAbaAtual();
            const input = document.getElementById('pesquisaSkuInput');
            if (input) input.focus();
        }

        function obterSkuItemListaPedido(item) {
            if (typeof item === 'string' || typeof item === 'number') {
                return normalizarSkuComparacaoLocal(item);
            }
            return normalizarSkuComparacaoLocal(item && (item.SKU || item.sku || ''));
        }

        function extrairSkusListaPedido(lista) {
            if (!lista) return [];
            if (Array.isArray(lista.skus)) return lista.skus.map(obterSkuItemListaPedido).filter(Boolean);
            if (Array.isArray(lista.itens)) return lista.itens.map(obterSkuItemListaPedido).filter(Boolean);
            return [];
        }

        function listaPedidoResumoContemPesquisaSku(lista) {
            const termo = obterPesquisaSkuNormalizada();
            if (!termo) return true;
            const skus = extrairSkusListaPedido(lista);
            if (skus.length) return skus.some((sku) => sku.includes(termo));
            if (listaPedidoAtual && String(listaPedidoAtual.id) === String(lista && lista.id)) {
                return extrairSkusListaPedido(listaPedidoAtual).some((sku) => sku.includes(termo));
            }
            return true;
        }

        async function carregarSkusListasPedidosParaPesquisa() {
            if (carregandoSkusPesquisaListas || abaAtual !== 'listas_pedidos' || !obterPesquisaSkuNormalizada()) return;
            const faltantes = (listasPedidosResumo || []).filter((lista) => !lista._skusPesquisaCarregados && !extrairSkusListaPedido(lista).length);
            if (!faltantes.length) return;
            carregandoSkusPesquisaListas = true;
            try {
                for (const resumo of faltantes) {
                    if (!resumo || !resumo.id) continue;
                    if (listaPedidoAtual && String(listaPedidoAtual.id) === String(resumo.id) && Array.isArray(listaPedidoAtual.itens)) {
                        resumo.skus = extrairSkusListaPedido(listaPedidoAtual);
                        resumo._skusPesquisaCarregados = true;
                        continue;
                    }
                    const resp = await fetch('/api/medias-compras/listas-pedidos/' + encodeURIComponent(String(resumo.id)), {
                        method: 'GET',
                        headers: { ...obterAuthHeaders() }
                    });
                    if (!resp.ok) continue;
                    const data = await resp.json().catch(() => ({}));
                    resumo.skus = extrairSkusListaPedido(data.lista || {});
                    resumo._skusPesquisaCarregados = true;
                }
                if (abaAtual === 'listas_pedidos' && obterPesquisaSkuNormalizada()) {
                    renderListaPedidosResumo();
                    renderEditorListaPedido();
                }
            } finally {
                carregandoSkusPesquisaListas = false;
            }
        }

        function normalizarListaSkusOcultos(lista) {
            const saida = [];
            const vistos = new Set();
            (Array.isArray(lista) ? lista : []).forEach((item) => {
                const sku = normalizarSku(item);
                if (!sku || vistos.has(sku)) return;
                vistos.add(sku);
                saida.push(sku);
            });
            return saida;
        }

        async function salvarSkusOcultosServidor() {
            try {
                await fetch('/api/medias-compras/preferencias-skus-ocultos', {
                    method: 'PUT',
                    headers: {
                        'Content-Type': 'application/json',
                        ...obterAuthHeaders()
                    },
                    body: JSON.stringify({
                        skus_ocultos: Array.from(skusOcultosSet)
                    })
                });
            } catch (_e) {
                // Mantém persistência local mesmo em falha momentânea de rede.
            }
        }

        async function carregarSkusOcultosServidor() {
            try {
                const resp = await fetch('/api/medias-compras/preferencias-skus-ocultos', {
                    method: 'GET',
                    headers: { ...obterAuthHeaders() }
                });
                if (!resp.ok) return;

                const data = await resp.json();
                const skusServidor = normalizarListaSkusOcultos(data && data.skus_ocultos);
                skusOcultosSet = new Set(skusServidor);

                try {
                    localStorage.setItem(LS_HIDDEN_SKUS_KEY, JSON.stringify(Array.from(skusOcultosSet)));
                } catch (_e) {
                    // Persistência local complementar.
                }
            } catch (_e) {
                // Se falhar no servidor, segue com fallback local.
            }
        }

        function salvarSkusOcultos() {
            skusOcultosSet = new Set(normalizarListaSkusOcultos(Array.from(skusOcultosSet)));
            try {
                localStorage.setItem(LS_HIDDEN_SKUS_KEY, JSON.stringify(Array.from(skusOcultosSet)));
            } catch (_e) {
                // Persistência local complementar.
            }
            salvarSkusOcultosServidor();
        }

        function ocultarSkuPorCodigo(skuCodificado) {
            const sku = normalizarSku(decodeURIComponent(String(skuCodificado || '')));
            if (!sku) return;
            skusOcultosSet.add(sku);
            salvarSkusOcultos();
            renderAbaAtual();
            setStatus('SKU ocultado com sucesso.');
        }

        function reexibirSkuPorCodigo(skuCodificado) {
            const sku = normalizarSku(decodeURIComponent(String(skuCodificado || '')));
            if (!sku) return;
            skusOcultosSet.delete(sku);
            salvarSkusOcultos();
            renderAbaAtual();
            setStatus('SKU removido da aba de ocultados.');
        }

        function selecionarAba(aba) {
            abaAtual = (aba === 'ocultados' || aba === 'listas_pedidos') ? aba : 'lista';
            const btnLista = document.getElementById('btnAbaLista');
            const btnOcultados = document.getElementById('btnAbaOcultados');
            const btnListasPedidos = document.getElementById('btnAbaListasPedidos');
            if (btnLista) btnLista.classList.toggle('active', abaAtual === 'lista');
            if (btnOcultados) btnOcultados.classList.toggle('active', abaAtual === 'ocultados');
            if (btnListasPedidos) btnListasPedidos.classList.toggle('active', abaAtual === 'listas_pedidos');

            if (abaAtual === 'listas_pedidos') {
                carregarListasPedidos();
            }
            renderAbaAtual();
        }

        return {
            atualizarBotoesPeriodo,
            renderBotoesLojas,
            carregarLojas,
            setStatus,
            lojaEspecificaSelecionada,
            exigirLojaEspecificaParaLista,
            obterPesquisaSkuNormalizada,
            filtrarItensPorPesquisaSku,
            atualizarControlePesquisaSku,
            atualizarPesquisaSku,
            limparPesquisaSku,
            obterSkuItemListaPedido,
            extrairSkusListaPedido,
            listaPedidoResumoContemPesquisaSku,
            carregarSkusListasPedidosParaPesquisa,
            normalizarListaSkusOcultos,
            salvarSkusOcultosServidor,
            carregarSkusOcultosServidor,
            salvarSkusOcultos,
            ocultarSkuPorCodigo,
            reexibirSkuPorCodigo,
            selecionarAba
        };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
