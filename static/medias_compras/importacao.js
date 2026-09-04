(function installJKMediasImportacao(global) {
    'use strict';

    const app = global.JKMedias;
    app.defineModule('importacao', ['core', 'state', 'api', 'listas', 'editor', 'tabela'], (context) => {
        const fetch = context.modules.api.request;
        const obterAuthHeaders = context.modules.api.authHeaders;

        function anexarExcelAtualizarPrecosLista(listaId, nomeLista) {
            if (!listaId) return;
            const input = document.createElement('input');
            input.type = 'file';
            input.accept = '.xlsx,.xlsm,.xltx,.xltm';
            input.addEventListener('change', async () => {
                const file = input.files && input.files[0] ? input.files[0] : null;
                if (!file) return;
                try {
                    const enviarArquivo = async (confirmarInclusoes) => {
                        const fd = new FormData();
                        fd.append('file', file);
                        fd.append('confirmar_inclusoes', confirmarInclusoes ? '1' : '0');
                        const resp = await fetch('/api/medias-compras/listas-pedidos/' + encodeURIComponent(String(listaId)) + '/importar-excel-precos', {
                            method: 'POST',
                            headers: { ...obterAuthHeaders() },
                            body: fd
                        });
                        if (!resp.ok) {
                            const err = await resp.json().catch(() => ({}));
                            throw new Error(err.detail || 'Falha ao importar Excel.');
                        }
                        return resp.json();
                    };

                    setStatusListaPedido('Atualizando preços e quantidades da lista "' + String(nomeLista || '') + '"...');
                    let data = await enviarArquivo(false);

                    if (data && data.requer_confirmacao) {
                        const resumo = data.resumo_pendencias || {};
                        const pendencias = Array.isArray(data.pendencias) ? data.pendencias : [];
                        const exemplos = pendencias.slice(0, 8).map((p) => {
                            const skuTxt = String(p.sku || '');
                            if (p.tipo === 'sku_novo') {
                                return '- SKU novo: ' + skuTxt;
                            }
                            if (p.tipo === 'titulo_diferente') {
                                return '- Título diferente no SKU ' + skuTxt;
                            }
                            return '- Divergência no SKU ' + skuTxt;
                        }).join('\n');

                        const mensagemConfirmacao =
                            'O arquivo possui divergências e precisa da sua confirmação para incluir:\n\n' +
                            '- SKUs novos: ' + numero(resumo.total_sku_novo || 0) + '\n' +
                            '- Títulos diferentes: ' + numero(resumo.total_titulo_diferente || 0) + '\n' +
                            (exemplos ? ('\nExemplos:\n' + exemplos + '\n') : '\n') +
                            '\nDeseja continuar e incluir mesmo assim?';

                        const confirmar = window.confirm(mensagemConfirmacao);
                        if (!confirmar) {
                            setStatusListaPedido('Importação cancelada pelo usuário após validação de divergências.');
                            return;
                        }

                        data = await enviarArquivo(true);
                    }

                    try {
                        await atualizarStatusListaPedido(listaId, 'Analisando orçamento');
                    } catch (_e) {
                        // Não bloqueia atualização de preços se status falhar.
                    }
                    await carregarListasPedidos();
                    if (listaPedidoAtual && String(listaPedidoAtual.id) === String(listaId)) {
                        await abrirListaPedidoPorId(listaId);
                    }
                    const skusIncluidos = Array.isArray(data.skus_incluidos) ? data.skus_incluidos.filter(Boolean) : [];
                    const sufixoSkusIncluidos = skusIncluidos.length
                        ? ' | SKUs incluídos: ' + skusIncluidos.join(', ')
                        : '';
                    const skusIgnorados = Array.isArray(data.skus_ignorados) ? data.skus_ignorados.filter(Boolean) : [];
                    const sufixoSkusIgnorados = skusIgnorados.length
                        ? ' | SKUs ignorados: ' + numero(data.total_skus_ignorados || skusIgnorados.length)
                        : '';
                    setStatusListaPedido(
                        'Importação concluída. SKUs atualizados: ' + numero(data.total_skus_atualizados || 0) +
                        ' | Quantidades atualizadas: ' + numero(data.total_quantidades_atualizadas || 0) +
                        ' | SKUs incluídos: ' + numero(data.total_skus_incluidos || 0) + '.' +
                        sufixoSkusIncluidos +
                        sufixoSkusIgnorados
                    );
                } catch (e) {
                    setStatusListaPedido(e.message || 'Erro ao atualizar preços com Excel.');
                }
            });
            input.click();
        }

        function importarListaPedidoPorExcel() {
            if (!exigirLojaEspecificaParaLista(setStatusListaPedido)) return;

            const btn = document.getElementById('btnImportarListaPedidoExcel');
            const input = document.createElement('input');
            input.type = 'file';
            input.accept = '.xlsx,.xlsm,.xltx,.xltm';

            input.addEventListener('change', async () => {
                const file = input.files && input.files[0] ? input.files[0] : null;
                if (!file) return;

                const textoOriginal = btn ? btn.textContent : '';
                try {
                    if (!exigirLojaEspecificaParaLista(setStatusListaPedido)) return;

                    if (btn) {
                        btn.disabled = true;
                        btn.textContent = 'Importando Excel...';
                    }

                    const fd = new FormData();
                    fd.append('file', file);
                    fd.append('loja', lojaSelecionada || '__todas');
                    fd.append('store_id', storeIdSelecionado || '');

                    setStatusListaPedido('Importando lista por Excel...');
                    const resp = await fetch('/api/medias-compras/listas-pedidos/importar-excel', {
                        method: 'POST',
                        headers: { ...obterAuthHeaders() },
                        body: fd
                    });
                    if (!resp.ok) {
                        const err = await resp.json().catch(() => ({}));
                        throw new Error(err.detail || 'Falha ao importar Excel e criar lista.');
                    }

                    const data = await resp.json();
                    const listaCriada = data.lista || {};
                    const listaId = String(listaCriada.id || data.lista_id || '').trim();
                    await carregarListasPedidos();
                    if (listaId) {
                        await abrirListaPedidoPorId(listaId);
                    }
                    setStatusListaPedido('Lista adicionada por Excel com sucesso.');
                } catch (e) {
                    setStatusListaPedido(e.message || 'Erro ao adicionar lista por Excel.');
                } finally {
                    if (btn) {
                        btn.disabled = false;
                        btn.textContent = textoOriginal || 'Adicionar lista por Excel';
                    }
                }
            });

            input.click();
        }

        function setStatusModalListaCompra(msg) {
            const el = document.getElementById('modalListaCompraStatus');
            if (el) el.textContent = msg || '';
        }

        function abrirModalListaCompra() {
            if (!exigirLojaEspecificaParaLista(setStatus)) return;

            const modal = document.getElementById('modalListaCompra');
            const inputNome = document.getElementById('nomeListaPedido');
            if (!modal) return;
            setStatusModalListaCompra('');
            modal.classList.add('open');
            modal.setAttribute('aria-hidden', 'false');
            if (inputNome) {
                const agora = new Date();
                const mm = String(agora.getMonth() + 1).padStart(2, '0');
                const yyyy = String(agora.getFullYear());
                if (!String(inputNome.value || '').trim()) {
                    const sufixoLoja = (lojaSelecionada && lojaSelecionada !== '__todas') ? ' - ' + lojaSelecionada : '';
                    inputNome.value = 'Pedido' + sufixoLoja + ' ' + mm + '/' + yyyy;
                }
                setTimeout(() => inputNome.focus(), 40);
            }
        }

        function fecharModalListaCompra() {
            const modal = document.getElementById('modalListaCompra');
            if (!modal) return;
            modal.classList.remove('open');
            modal.setAttribute('aria-hidden', 'true');
        }

        function obterOpcaoListaCompraSelecionada() {
            const opcao = document.querySelector('input[name="opcaoCompra"]:checked');
            if (!opcao) {
                return { opcao: 'media_6m', crescimento_percent: 0 };
            }

            if (opcao.value === 'media_6m') {
                return { opcao: 'media_6m', crescimento_percent: 0 };
            }

            const perc = Number(opcao.value || 0);
            return {
                opcao: 'crescimento',
                crescimento_percent: Number.isFinite(perc) ? perc : 0
            };
        }

        async function gerarListaCompraExcel() {
            const btn = document.getElementById('btnConfirmarListaCompra');
            const inputNome = document.getElementById('nomeListaPedido');
            if (!exigirLojaEspecificaParaLista(setStatusModalListaCompra)) return;

            if (btn) btn.disabled = true;
            exibirOverlayDownload(true);
            setStatusModalListaCompra('Gerando arquivo Excel...');

            try {
                const payload = obterOpcaoListaCompraSelecionada();
                payload.nome_lista = String((inputNome && inputNome.value) || '').trim();
                if (!payload.nome_lista) {
                    throw new Error('Informe o nome da lista para continuar.');
                }
                payload.hidden_skus = Array.from(skusOcultosSet);
                payload.loja = lojaSelecionada || '__todas';
                payload.store_id = storeIdSelecionado || '';
                payload.periodo_meses = Number(periodoAtual || 12);
                payload.quantidades_sugeridas = obterQuantidadesSugeridasEditadas();
                let resp = await fetch('/api/medias-compras/gerar-lista-compra', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        ...obterAuthHeaders()
                    },
                    body: JSON.stringify(payload)
                });

                if (resp.status === 405) {
                    const params = new URLSearchParams();
                    params.set('opcao', String(payload.opcao || 'media_6m'));
                    if (payload.opcao === 'crescimento') {
                        params.set('crescimento_percent', String(Number(payload.crescimento_percent || 0)));
                    }
                    if (Array.isArray(payload.hidden_skus) && payload.hidden_skus.length) {
                        params.set('hidden_skus', payload.hidden_skus.join(','));
                    }
                    params.set('nome_lista', payload.nome_lista);
                    params.set('loja', String(payload.loja || '__todas'));
                    params.set('store_id', String(payload.store_id || ''));
                    params.set('periodo_meses', String(Number(payload.periodo_meses || 12)));
                    if (Object.keys(payload.quantidades_sugeridas || {}).length) {
                        params.set('quantidades_sugeridas', JSON.stringify(payload.quantidades_sugeridas));
                    }
                    resp = await fetch('/api/medias-compras/gerar-lista-compra?' + params.toString(), {
                        method: 'GET',
                        headers: {
                            ...obterAuthHeaders()
                        }
                    });
                }

                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    throw new Error(err.detail || 'Falha ao gerar lista de compra.');
                }

                const data = await resp.json();
                if (!data.lista_id) {
                    throw new Error('Lista nao foi gerada corretamente.');
                }

                setStatusModalListaCompra('Lista gerada com sucesso.');
                setStatus('Lista de compra gerada com sucesso. Itens: ' + numero(data.total_itens || 0) + '. SKUs ocultados ignorados: ' + numero(data.total_skus_ocultados || 0));
                setTimeout(() => {
                    fecharModalListaCompra();
                    selecionarAba('listas_pedidos');
                }, 500);
            } catch (e) {
                setStatusModalListaCompra(e.message || 'Erro ao gerar lista de compra.');
            } finally {
                exibirOverlayDownload(false);
                if (btn) btn.disabled = false;
            }
        }

        function abrirPaginaProdutosSemVenda() {
            const params = new URLSearchParams();
            if (lojaSelecionada && lojaSelecionada !== '__todas') {
                params.set('loja', lojaSelecionada);
                params.set('store_id', String(storeIdSelecionado || ''));
            }
            window.location.href = '/produtos_sem_venda.html' + (params.toString() ? '?' + params.toString() : '');
        }

        async function baixarListaSugestaoExcel() {
            if (!exigirLojaEspecificaParaLista(setStatus)) return;

            exibirOverlayDownload(true);
            setStatus('Gerando lista de sugestão de compra...');
            try {
                const params = new URLSearchParams();
                params.set('meses', String(Number(periodoAtual || 12)));
                params.set('loja', String(lojaSelecionada || '__todas'));
                params.set('store_id', String(storeIdSelecionado || ''));
                const quantidadesSugeridas = obterQuantidadesSugeridasEditadas();
                if (Object.keys(quantidadesSugeridas).length) {
                    params.set('quantidades_sugeridas', JSON.stringify(quantidadesSugeridas));
                }

                const resp = await fetch('/api/medias-compras/gerar-lista-sugestao?' + params.toString(), {
                    method: 'GET',
                    headers: { ...obterAuthHeaders() }
                });

                if (!resp.ok) {
                    const err = await resp.json().catch(() => ({}));
                    throw new Error(err.detail || 'Falha ao gerar lista de sugestão.');
                }

                const data = await resp.json();
                if (!data.file_id) {
                    throw new Error('Arquivo da sugestão não foi gerado.');
                }

                const downloadUrl = '/api/medias-compras/download/' + encodeURIComponent(String(data.file_id)) + '?ts=' + Date.now();
                const respDownload = await fetch(downloadUrl, {
                    method: 'GET',
                    headers: { ...obterAuthHeaders() }
                });
                if (!respDownload.ok) {
                    const err = await respDownload.json().catch(() => ({}));
                    throw new Error(err.detail || 'Arquivo de sugestão indisponível para download.');
                }

                const blob = await respDownload.blob();
                const link = document.createElement('a');
                link.href = URL.createObjectURL(blob);
                link.download = String(data.filename || 'sugestao_compra.xlsx');
                document.body.appendChild(link);
                link.click();
                link.remove();
                URL.revokeObjectURL(link.href);
                setStatus('Lista de sugestão gerada com sucesso. Itens: ' + numero(data.total_itens || 0));
            } catch (e) {
                setStatus(e.message || 'Erro ao gerar lista de sugestão.');
            } finally {
                exibirOverlayDownload(false);
            }
        }

        return {
            anexarExcelAtualizarPrecosLista,
            importarListaPedidoPorExcel,
            setStatusModalListaCompra,
            abrirModalListaCompra,
            fecharModalListaCompra,
            obterOpcaoListaCompraSelecionada,
            gerarListaCompraExcel,
            abrirPaginaProdutosSemVenda,
            baixarListaSugestaoExcel
        };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
