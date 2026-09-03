(function installJKMediasListas(global) {
    'use strict';

    const app = global.JKMedias;
    app.defineModule('listas', ['core', 'state', 'api', 'editor', 'importacao'], (context) => {
        const fetch = context.modules.api.request;
        const obterAuthHeaders = context.modules.api.authHeaders;

        function setStatusListaPedido(msg) {
            const el = document.getElementById('statusListaPedido');
            if (el) el.textContent = msg || '';
        }

        function renderStatusListaPedidoVisual(status) {
            const el = document.getElementById('statusListaPedidoEdit');
            if (!el) return;
            const statusNormalizado = normalizarStatusListaPedido(status);
            el.innerHTML = '<span class="status-badge ' + classeStatusListaPedido(statusNormalizado) + '">' + escaparHtml(statusNormalizado) + '</span>';
        }

        function exibirOverlayDownload(ativo) {
            const el = document.getElementById('downloadOverlay');
            if (!el) return;
            el.classList.toggle('open', !!ativo);
            el.setAttribute('aria-hidden', ativo ? 'false' : 'true');
        }

        function obterMapaStatusCanceladoLocal() {
            const chave = 'jk_lista_status_cancelado_override';
            try {
                const mapa = JSON.parse(localStorage.getItem(chave) || '{}');
                return mapa && typeof mapa === 'object' ? mapa : {};
            } catch (_e) {
                return {};
            }
        }

        function forcarStatusCanceladoLocal(listaId) {
            if (!listaId) return false;
            const mapa = obterMapaStatusCanceladoLocal();
            return !!mapa[String(listaId)];
        }

        function limparStatusCanceladoLocal(listaId) {
            if (!listaId) return;
            const chave = 'jk_lista_status_cancelado_override';
            const mapa = obterMapaStatusCanceladoLocal();
            if (Object.prototype.hasOwnProperty.call(mapa, String(listaId))) {
                delete mapa[String(listaId)];
                localStorage.setItem(chave, JSON.stringify(mapa));
            }
        }

        async function carregarListasPedidos() {
            try {
                setStatusListaPedido('Carregando listas de pedidos...');
                const params = new URLSearchParams();
                if (lojaSelecionada && lojaSelecionada !== '__todas') {
                    params.set('loja', lojaSelecionada);
                    params.set('store_id', String(storeIdSelecionado || ''));
                }
                const resp = await fetch('/api/medias-compras/listas-pedidos' + (params.toString() ? '?' + params.toString() : ''), {
                    method: 'GET',
                    headers: { ...obterAuthHeaders() }
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    throw new Error(err.detail || 'Falha ao carregar listas de pedidos.');
                }
                const data = await resp.json();
                listasPedidosResumo = Array.isArray(data.listas) ? data.listas : [];
                renderListaPedidosResumo();
                if (!listasPedidosResumo.length) {
                    listaPedidoAtual = null;
                    renderEditorListaPedido();
                    setStatusListaPedido('Nenhuma lista salva ainda.');
                } else if (!listaPedidoAtual) {
                    renderEditorListaPedido();
                    setStatusListaPedido('Clique em uma lista para visualizar e editar.');
                }
            } catch (e) {
                setStatusListaPedido(e.message || 'Erro ao carregar listas de pedidos.');
            }
        }

        function renderListaPedidosResumo() {
            const box = document.getElementById('listasPedidosLista');
            if (!box) return;
            box.innerHTML = '';
            const listasFiltradas = (listasPedidosResumo || []).filter(listaPedidoResumoContemPesquisaSku);
            const pesquisaAtiva = Boolean(obterPesquisaSkuNormalizada());

            if (!listasPedidosResumo.length) {
                box.innerHTML = '<div class="lista-item-meta" style="padding:10px;">Nenhuma lista de pedidos.</div>';
                return;
            }

            if (!listasFiltradas.length) {
                box.innerHTML = '<div class="lista-item-meta" style="padding:10px;">Nenhuma lista contém o SKU pesquisado.</div>';
                return;
            }

            listasFiltradas.forEach(l => {
                const ativo = listaPedidoAtual && String(listaPedidoAtual.id) === String(l.id);
                const statusOriginal = normalizarStatusListaPedido(l.status);
                const status = forcarStatusCanceladoLocal(l.id) ? 'Pedido cancelado' : statusOriginal;
                if (statusOriginal === 'Pedido cancelado') {
                    limparStatusCanceladoLocal(l.id);
                }
                const lojaMeta = (l.loja && l.loja !== '__todas') ? ' | Loja: ' + escaparHtml(l.loja) : '';
                const podeExcluirCancelada = status === 'Pedido cancelado';
                const emImportacoes = listaPedidoEstaEmImportacoes(status);
                const tituloExcluir = emImportacoes
                    ? 'Esta lista ja esta em Importacoes. Exclua pelo modulo Importacoes.'
                    : (podeExcluirCancelada ? 'Excluir lista cancelada' : 'Excluir lista');
                const avisoCancelada = podeExcluirCancelada
                    ? '<div class="lista-item-meta" style="color:#ffbdbd;">Aviso: lista cancelada. A exclusão é permanente.</div>'
                    : '';
                const avisoImportacoes = emImportacoes
                    ? '<div class="lista-item-meta" style="color:#ffd2a8;">Lista em Importacoes. Para excluir definitivamente, use o modulo Importacoes.</div>'
                    : '';
                const div = document.createElement('div');
                div.className = 'lista-item' + (ativo ? ' active' : '');
                div.innerHTML =
                    '<div class="lista-item-header">' +
                        '<div>' +
                            '<span class="status-badge ' + classeStatusListaPedido(status) + '">' + escaparHtml(status) + '</span>' +
                            '<div class="lista-item-titulo">' + escaparHtml(l.nome_lista || 'Sem nome') + '</div>' +
                        '</div>' +
                        '<div class="lista-item-acoes">' +
                            '<button type="button" class="btn-acao-lista" data-action="baixar" title="Baixar Excel" aria-label="Baixar Excel">' +
                                '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">' +
                                    '<path d="M12 3V14" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>' +
                                    '<path d="M8 10L12 14L16 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>' +
                                    '<path d="M4 17V19C4 20.1 4.9 21 6 21H18C19.1 21 20 20.1 20 19V17" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>' +
                                '</svg>' +
                            '</button>' +
                            '<button type="button" class="btn-acao-lista" data-action="anexar" title="Anexar Excel para atualizar preços" aria-label="Anexar Excel para atualizar preços">' +
                                '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">' +
                                    '<path d="M21.44 11.05L12.25 20.24C10.3 22.19 7.13 22.19 5.18 20.24C3.23 18.29 3.23 15.12 5.18 13.17L14.37 3.98C15.67 2.68 17.78 2.68 19.08 3.98C20.38 5.28 20.38 7.39 19.08 8.69L9.89 17.88C9.24 18.53 8.18 18.53 7.53 17.88C6.88 17.23 6.88 16.17 7.53 15.52L15.66 7.39" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>' +
                                '</svg>' +
                            '</button>' +
                            '<button type="button" class="btn-lixeira" data-action="excluir" title="' + tituloExcluir + '" aria-label="' + tituloExcluir + '"' + (emImportacoes ? ' disabled' : '') + '>' +
                                '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">' +
                                    '<path d="M3 6H21" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>' +
                                    '<path d="M8 6V4C8 2.9 8.9 2 10 2H14C15.1 2 16 2.9 16 4V6" stroke="currentColor" stroke-width="2"/>' +
                                    '<path d="M19 6L18 20C17.9 21.1 17 22 15.9 22H8.1C7 22 6.1 21.1 6 20L5 6" stroke="currentColor" stroke-width="2"/>' +
                                    '<path d="M10 11V17" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>' +
                                    '<path d="M14 11V17" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>' +
                                '</svg>' +
                            '</button>' +
                        '</div>' +
                    '</div>' +
                    '<div class="lista-item-meta">Itens: ' + numero(l.total_itens || 0) + lojaMeta + ' | Atualizada: ' + escaparHtml(formatarDataHoraLocal(l.updated_at || l.created_at || '')) + (pesquisaAtiva ? ' | Contém SKU pesquisado' : '') + '</div>' +
                    avisoCancelada +
                    avisoImportacoes;
                div.addEventListener('click', () => abrirListaPedidoPorId(l.id));
                const btnExcluir = div.querySelector('button[data-action="excluir"]');
                const btnBaixar = div.querySelector('button[data-action="baixar"]');
                const btnAnexar = div.querySelector('button[data-action="anexar"]');
                if (btnBaixar) {
                    btnBaixar.addEventListener('click', (ev) => {
                        ev.preventDefault();
                        ev.stopPropagation();
                        baixarListaPedidoExcel(l.id);
                    });
                }
                if (btnAnexar) {
                    btnAnexar.addEventListener('click', (ev) => {
                        ev.preventDefault();
                        ev.stopPropagation();
                        anexarExcelAtualizarPrecosLista(l.id, l.nome_lista || 'Sem nome');
                    });
                }
                if (btnExcluir) {
                    btnExcluir.addEventListener('click', (ev) => {
                        ev.preventDefault();
                        ev.stopPropagation();
                        if (emImportacoes) {
                            setStatusListaPedido('Esta lista ja esta em Importacoes. Exclua definitivamente pelo modulo Importacoes.');
                            return;
                        }
                        excluirListaPedido(l.id, l.nome_lista || 'Sem nome', status);
                    });
                }
                box.appendChild(div);
            });
        }

        async function baixarListaPedidoExcel(listaId) {
            if (!listaId) return;
            const MIN_OVERLAY_MS = 1000;
            const inicioAnimacao = Date.now();
            try {
                exibirOverlayDownload(true);
                setStatusListaPedido('Gerando Excel da lista...');
                const resp = await fetch('/api/medias-compras/listas-pedidos/' + encodeURIComponent(String(listaId)) + '/gerar-download', {
                    method: 'POST',
                    headers: { ...obterAuthHeaders() }
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    throw new Error(err.detail || 'Falha ao gerar Excel da lista.');
                }

                const data = await resp.json();
                const nomeArquivo = data.filename || ('lista_pedido_' + String(listaId) + '.xlsx');
                const downloadUrl = '/api/medias-compras/listas-pedidos/' + encodeURIComponent(String(listaId)) + '/download?ts=' + Date.now();
                const respDownload = await fetch(downloadUrl, {
                    method: 'GET',
                    headers: { ...obterAuthHeaders() },
                    cache: 'no-store'
                });
                if (!respDownload.ok) {
                    const err = await respDownload.json().catch(() => ({}));
                    throw new Error(err.detail || 'Arquivo não estava disponível para download.');
                }

                const blob = await respDownload.blob();
                if (!blob || !blob.size) {
                    throw new Error('Arquivo de download vazio.');
                }

                const objectUrl = URL.createObjectURL(blob);
                const link = document.createElement('a');
                link.href = objectUrl;
                link.download = nomeArquivo;
                document.body.appendChild(link);
                link.click();
                link.remove();
                URL.revokeObjectURL(objectUrl);

                try {
                    await atualizarStatusListaPedido(listaId, 'Em Orçamento');
                } catch (_e) {
                    // Não bloqueia download se atualização de status falhar.
                }

                setStatusListaPedido('Excel gerado e download iniciado.');
            } catch (e) {
                setStatusListaPedido(e.message || 'Erro ao baixar Excel da lista.');
            } finally {
                const tempoDecorrido = Date.now() - inicioAnimacao;
                const restante = Math.max(0, MIN_OVERLAY_MS - tempoDecorrido);
                if (restante > 0) {
                    await new Promise(resolve => setTimeout(resolve, restante));
                }
                exibirOverlayDownload(false);
            }
        }

        async function atualizarStatusListaPedido(listaId, novoStatus) {
            if (!listaId || !novoStatus) return;
            const resp = await fetch('/api/medias-compras/listas-pedidos/' + encodeURIComponent(String(listaId)) + '/status', {
                method: 'PATCH',
                headers: {
                    'Content-Type': 'application/json',
                    ...obterAuthHeaders()
                },
                body: JSON.stringify({ status: novoStatus })
            });
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                throw new Error(err.detail || 'Falha ao atualizar status da lista.');
            }
            const data = await resp.json();
            const listaAtualizada = data.lista || null;
            if (listaAtualizada && listaAtualizada.id) {
                listasPedidosResumo = (listasPedidosResumo || []).map(l => {
                    if (String(l.id) !== String(listaAtualizada.id)) return l;
                    return { ...l, ...listaAtualizada };
                });
                if (listaPedidoAtual && String(listaPedidoAtual.id) === String(listaAtualizada.id)) {
                    listaPedidoAtual = { ...listaPedidoAtual, ...listaAtualizada };
                }
                renderListaPedidosResumo();
                renderEditorListaPedido();
            }
        }

        async function excluirListaPedido(listaId, nomeLista, statusLista) {
            if (!listaId) return;
            if (listaPedidoEstaEmImportacoes(statusLista)) {
                setStatusListaPedido('Esta lista ja esta em Importacoes. Exclua definitivamente pelo modulo Importacoes.');
                return;
            }
            const cancelada = String(statusLista || '').trim() === 'Pedido cancelado';
            const mensagem = cancelada
                ? 'Aviso: esta lista cancelada será excluída permanentemente e não poderá ser recuperada.\n\nLista: "' + String(nomeLista || '') + '"\n\nDeseja continuar?'
                : 'Tem certeza que deseja apagar a lista "' + String(nomeLista || '') + '"?\n\nEssa ação não pode ser desfeita.';
            const confirma = window.confirm(mensagem);
            if (!confirma) return;
            try {
                setStatusListaPedido(cancelada ? 'Excluindo lista cancelada...' : 'Apagando lista...');
                const resp = await fetch('/api/medias-compras/listas-pedidos/' + encodeURIComponent(String(listaId)), {
                    method: 'DELETE',
                    headers: { ...obterAuthHeaders() }
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    throw new Error(err.detail || 'Falha ao apagar lista.');
                }

                listasPedidosResumo = listasPedidosResumo.filter(l => String(l.id) !== String(listaId));
                if (listaPedidoAtual && String(listaPedidoAtual.id) === String(listaId)) {
                    listaPedidoAtual = null;
                }
                renderListaPedidosResumo();
                renderEditorListaPedido();
                setStatusListaPedido('Lista apagada com sucesso.');
            } catch (e) {
                setStatusListaPedido(e.message || 'Erro ao apagar lista.');
            }
        }

        async function abrirListaPedidoPorId(listaId) {
            if (!listaId) return;
            try {
                setStatusListaPedido('Abrindo lista...');
                const resp = await fetch('/api/medias-compras/listas-pedidos/' + encodeURIComponent(String(listaId)), {
                    method: 'GET',
                    headers: { ...obterAuthHeaders() }
                });
                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    throw new Error(err.detail || 'Falha ao abrir lista de pedidos.');
                }
                const data = await resp.json();
                listaPedidoAtual = data.lista || null;
                if (listaPedidoAtual && forcarStatusCanceladoLocal(listaId)) {
                    listaPedidoAtual.status = 'Pedido cancelado';
                }
                renderListaPedidosResumo();
                renderEditorListaPedido();
                setStatusListaPedido('Lista carregada.');
            } catch (e) {
                setStatusListaPedido(e.message || 'Erro ao abrir lista.');
            }
        }

        function preencherSelectLojaListaPedido(select, lista) {
            if (!select) return;
            const lojaAtual = String((lista && lista.loja) || '').trim();
            let valorAtual = String((lista && lista.store_id) || '').trim();
            select.innerHTML = '';

            const placeholder = document.createElement('option');
            placeholder.value = '';
            placeholder.textContent = 'Selecionar loja';
            placeholder.disabled = true;
            placeholder.selected = !valorAtual;
            select.appendChild(placeholder);

            const lojas = (Array.isArray(lojasDisponiveis) ? lojasDisponiveis : []).filter((loja) => (
                String((loja && loja.store_id) || '').trim()
                && String((loja && loja.nome) || '').trim()
            ));
            if (!valorAtual && lojaAtual && lojaAtual !== '__todas') {
                const normalizarNome = (valor) => String(valor || '').trim().toLocaleLowerCase();
                const chaveAtual = normalizarNome(lojaAtual);
                const candidatas = lojas.filter((loja) => {
                    const aliases = [loja.nome, ...(Array.isArray(loja.nomes_anteriores) ? loja.nomes_anteriores : [])];
                    return aliases.some((alias) => normalizarNome(alias) === chaveAtual);
                });
                if (candidatas.length === 1) valorAtual = String(candidatas[0].store_id || '').trim();
            }

            lojas.forEach((loja) => {
                const nome = String((loja && loja.nome) || '').trim();
                const storeId = String((loja && loja.store_id) || '').trim();
                const opt = document.createElement('option');
                opt.value = storeId;
                opt.dataset.nomeLoja = nome;
                opt.textContent = nome;
                opt.selected = storeId === valorAtual;
                select.appendChild(opt);
            });
        }

        function obterLojaListaPedidoSelecionada() {
            const select = document.getElementById('lojaListaPedidoEdit');
            const option = select && select.selectedOptions && select.selectedOptions[0];
            return String((option && option.dataset && option.dataset.nomeLoja) || '').trim();
        }

        function obterStoreIdListaPedidoSelecionado() {
            const select = document.getElementById('lojaListaPedidoEdit');
            return String((select && select.value) || '').trim();
        }

        return {
            setStatusListaPedido,
            renderStatusListaPedidoVisual,
            exibirOverlayDownload,
            obterMapaStatusCanceladoLocal,
            forcarStatusCanceladoLocal,
            limparStatusCanceladoLocal,
            carregarListasPedidos,
            renderListaPedidosResumo,
            baixarListaPedidoExcel,
            atualizarStatusListaPedido,
            excluirListaPedido,
            abrirListaPedidoPorId,
            preencherSelectLojaListaPedido,
            obterLojaListaPedidoSelecionada,
            obterStoreIdListaPedidoSelecionado
        };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
