(function installJKMediasListasCarregamento(global) {
    'use strict';

    const app = global.JKMedias;
    app.defineModule('listas-carregamento', ['core', 'state', 'api', 'resultados', 'listas-cache', 'editor-salvamento'], ({ modules }) => {
        const cache = modules['listas-cache'].cacheListasPedidos;
        let aberturaListaPedido = null;
        let listaPedidoEmAbertura = '';

        function cancelarAberturaListaPedido() {
            invalidarContextoEdicaoListaPedido();
            const abertura = aberturaListaPedido;
            abertura?.controller.abort();
            if (abertura) {
                global.clearTimeout(abertura.timer);
                abertura.removerEscuta?.();
            }
            aberturaListaPedido = null;
            listaPedidoEmAbertura = '';
            const painel = document.getElementById('editorListaPedido');
            if (painel) painel.inert = false;
        }

        function listaEmAbertura() { return listaPedidoEmAbertura; }

        function aindaSemEdicao(abertura) {
            return abertura === aberturaListaPedido && !abertura.editada &&
                !abertura.controller.signal.aborted && (!abertura.leitura || cache.vigente(abertura.leitura)) &&
                listaPedidoAtual === abertura.lista && JSON.stringify(listaPedidoAtual) === abertura.assinatura;
        }

        function persistirLista(abertura) {
            if (!aindaSemEdicao(abertura) || !abertura.confirmada) return;
            void cache.salvar(abertura.leitura, { lista: abertura.confirmada, meta: abertura.meta, baseSalva: abertura.baseSalva || null });
        }

        function aplicarLista(abertura, lista, meta) {
            if (!lista || String(lista.id) !== abertura.id || !Array.isArray(lista.itens)) throw new Error('A resposta não contém a lista solicitada.');
            abertura.confirmada = modules.resultados.copiarResultado(lista);
            listaPedidoAtual = modules.resultados.copiarResultado(abertura.confirmada);
            if (forcarStatusCanceladoLocal(abertura.id)) listaPedidoAtual.status = 'Pedido cancelado';
            listaPedidoEmAbertura = '';
            abertura.lista = listaPedidoAtual;
            abertura.assinatura = JSON.stringify(listaPedidoAtual);
            abertura.meta = meta || null;
            abertura.persistente = false;
            renderListaPedidosResumo();
            renderEditorListaPedido();
        }

        function invalidarSnapshotIndisponivel(abertura, meta) {
            if (abertura !== aberturaListaPedido || (abertura.leitura && !cache.vigente(abertura.leitura))) return;
            const autoridadeAnterior = abertura.meta?.autoridade;
            const geradoEm = abertura.meta?.gerado_em ? new Date(abertura.meta.gerado_em).getTime() : NaN;
            const expirado = Number.isFinite(geradoEm) && Date.now() - geradoEm > 15 * 60 * 1000;
            const politicaAnterior = abertura.meta?.politica_fotos;
            if (!meta?.pendente_fontes && (!expirado || abertura.persistente)
                && (!autoridadeAnterior || autoridadeAnterior === meta?.autoridade)
                && (!politicaAnterior || !meta?.politica_fotos || politicaAnterior === meta.politica_fotos)) return;
            const leitura = abertura.leitura;
            void cache.invalidar(leitura).then(() => {
                if (abertura === aberturaListaPedido && abertura.sessao === modules.resultados.identidadeSessao()) {
                    abertura.leitura = cache.capturarDetalhe(abertura.id);
                }
            });
            // O conteúdo deixou de ser válido, mas o ciclo atual pode receber a próxima versão.
            invalidarContextoEdicaoListaPedido();
            listaPedidoAtual = null;
            abertura.lista = null;
            abertura.assinatura = JSON.stringify(null);
            abertura.meta = null;
            abertura.atual = false;
            listaPedidoEmAbertura = abertura.id;
            renderListaPedidosResumo();
            renderEditorListaPedido();
        }

        function limparListaSemAcesso(abertura, erro) {
            if (abertura !== aberturaListaPedido || ![401, 403, 404].includes(erro.status)) return false;
            if (abertura.leitura && !cache.vigente(abertura.leitura) && erro.name !== 'SessionChangedError') return false;
            void cache.invalidar(abertura.leitura);
            cancelarAberturaListaPedido();
            listaPedidoAtual = null;
            listasPedidosResumo = [];
            renderListaPedidosResumo();
            renderEditorListaPedido();
            return true;
        }

        async function atualizarComplementosListaPedido(abertura) {
            if (abertura !== aberturaListaPedido) return;
            try {
                modules.resultados.exigirSessao(abertura.sessao);
                if (!aindaSemEdicao(abertura)) return;
                const resultado = await modules.resultados.acompanharResultado(abertura.url, {
                    headers: abertura.headers, signal: abertura.controller.signal, sessao: abertura.sessao,
                    ativa: () => aindaSemEdicao(abertura),
                    receber: (data, meta) => {
                        if (aindaSemEdicao(abertura)) {
                            aplicarLista(abertura, data.lista, meta);
                            persistirLista(abertura);
                        }
                    },
                    indisponivel: meta => invalidarSnapshotIndisponivel(abertura, meta),
                    estado: (meta, pendente) => {
                        if (aindaSemEdicao(abertura)) setStatusListaPedido(modules.resultados.textoEstadoResultado(meta, pendente) || 'Lista carregada.');
                    },
                });
                modules.resultados.exigirSessao(abertura.sessao);
                if (aindaSemEdicao(abertura)) abertura.atual = !!resultado.atual;
            } catch (erro) {
                if (limparListaSemAcesso(abertura, erro) || aindaSemEdicao(abertura)) {
                    setStatusListaPedido(abertura.salvo && listaPedidoAtual ? cache.texto(abertura.salvo, true) : 'Atualização pendente: ' + erro.message);
                }
            }
        }

        async function confirmarListaAtualizada(listaId) {
            const abertura = aberturaListaPedido?.id === String(listaId) ? aberturaListaPedido : null;
            const url = '/api/medias-compras/listas-pedidos/' + encodeURIComponent(String(listaId));
            const resultado = await modules.resultados.acompanharResultado(url, {
                headers: { ...modules.api.authHeaders() },
                indisponivel: meta => { if (abertura) invalidarSnapshotIndisponivel(abertura, meta); },
                estado: (meta, pendente) => setStatusListaPedido(modules.resultados.textoEstadoResultado(meta, pendente)),
            });
            if (!resultado.atual) throw new Error('A atualização da lista ainda está pendente. Aguarde antes de prosseguir.');
            return resultado.dados.lista;
        }

        async function abrirListaPedidoPorId(listaId, { preservarConteudo = false, aguardarSalvamento = true } = {}) {
            if (!listaId) return;
            if (aberturaListaPedido) {
                try {
                    modules.resultados.exigirSessao(aberturaListaPedido.sessao);
                } catch (erro) {
                    limparListaSemAcesso(aberturaListaPedido, erro);
                    setStatusListaPedido(erro.message);
                    return false;
                }
            }
            const mesmaLista = listaPedidoAtual && String(listaPedidoAtual.id) === String(listaId);
            // Clicar novamente para acompanhar uma atualização não apaga um formulário alterado.
            const edicaoLocal = aberturaListaPedido && (aberturaListaPedido.editada ||
                (aberturaListaPedido.assinatura && JSON.stringify(listaPedidoAtual) !== aberturaListaPedido.assinatura));
            if (mesmaLista && edicaoLocal && !preservarConteudo && !edicaoListaPedidoConfirmada()) {
                setStatusListaPedido('Há edições em andamento. Salve a lista antes de atualizar os dados.');
                return false;
            }
            const manterConteudo = preservarConteudo && mesmaLista;
            const metaAnterior = manterConteudo ? aberturaListaPedido?.meta : null;
            const baseAnterior = manterConteudo ? aberturaListaPedido?.baseSalva : null;
            cancelarAberturaListaPedido();
            const abertura = { id: String(listaId), controller: new global.AbortController(), editada: false,
                url: '/api/medias-compras/listas-pedidos/' + encodeURIComponent(String(listaId)),
                headers: { ...modules.api.authHeaders() } };
            abertura.sessao = modules.resultados.identidadeSessao(abertura.headers);
            aberturaListaPedido = abertura;
            listaPedidoEmAbertura = abertura.id;
            if (!manterConteudo) listaPedidoAtual = null;
            abertura.lista = listaPedidoAtual;
            abertura.assinatura = JSON.stringify(listaPedidoAtual);
            abertura.meta = metaAnterior || null;
            abertura.baseSalva = baseAnterior || null;
            renderListaPedidosResumo();
            if (!manterConteudo) renderEditorListaPedido();
            const painel = document.getElementById('editorListaPedido');
            if (painel) { painel.classList.remove('hidden'); painel.inert = true; }
            if (painel) {
                const marcarEdicao = () => {
                    abertura.editada = true;
                    abertura.atual = false;
                    global.clearTimeout(abertura.timer);
                    abertura.controller.abort();
                };
                for (const evento of ['input', 'change', 'dragstart']) painel.addEventListener(evento, marcarEdicao, true);
                abertura.removerEscuta = () => { for (const evento of ['input', 'change', 'dragstart']) painel.removeEventListener(evento, marcarEdicao, true); };
            }
            setStatusListaPedido('Abrindo lista...');
            try {
                if (aguardarSalvamento) await aguardarSalvamentoListaPedido(listaId);
                modules.resultados.exigirSessao(abertura.sessao);
                if (abertura !== aberturaListaPedido) return false;
                abertura.leitura = cache.capturarDetalhe(abertura.id);
                if (!manterConteudo) {
                    const salvo = await cache.ler(abertura.leitura);
                    modules.resultados.exigirSessao(abertura.sessao);
                    if (!aindaSemEdicao(abertura)) return false;
                    if (String(salvo?.dados?.lista?.id) === abertura.id && Array.isArray(salvo?.dados?.lista?.itens)) {
                        aplicarLista(abertura, salvo.dados.lista, salvo.dados.meta);
                        abertura.baseSalva = typeof salvo.dados.baseSalva === 'string' ? salvo.dados.baseSalva : null;
                        abertura.salvo = salvo;
                        abertura.persistente = true;
                        if (painel) painel.inert = false;
                        setStatusListaPedido(cache.texto(salvo));
                    }
                }
                const { resp, data } = await modules.resultados.consultarResultado(
                    abertura.url + '?somente_salvos=true', abertura.headers, abertura.controller.signal, 5000);
                modules.resultados.exigirSessao(abertura.sessao);
                if (abertura !== aberturaListaPedido) return false;
                if (!resp.ok) {
                    const erro = new Error(typeof data.detail === 'string' ? data.detail : data.detail?.message || 'Falha ao abrir lista.');
                    erro.status = resp.status;
                    throw erro;
                }
                if (!aindaSemEdicao(abertura)) return false;
                if (resp.status === 202 || data.dados === null) {
                    invalidarSnapshotIndisponivel(abertura, data._cache);
                    setStatusListaPedido(modules.resultados.textoEstadoResultado(data._cache) || 'Atualizando dados...');
                } else {
                    const baseSalva = JSON.stringify(data.lista);
                    if (!abertura.salvo || abertura.baseSalva !== baseSalva) {
                        aplicarLista(abertura, data.lista, data._cache);
                        abertura.baseSalva = baseSalva;
                        persistirLista(abertura);
                    }
                    setStatusListaPedido('Dados salvos exibidos. Atualizando dados...');
                }
                abertura.timer = global.setTimeout(() => atualizarComplementosListaPedido(abertura), 300);
                return true;
            } catch (erro) {
                if (abertura === aberturaListaPedido) {
                    if (limparListaSemAcesso(abertura, erro)) {
                        setStatusListaPedido(erro.message);
                        return false;
                    }
                    if (abertura.editada || erro.name === 'AbortError' || (abertura.leitura && !cache.vigente(abertura.leitura))) return false;
                    if (abertura.salvo && listaPedidoAtual) {
                        setStatusListaPedido(cache.texto(abertura.salvo, true));
                        return true;
                    }
                    setStatusListaPedido(erro.name === 'TimeoutError'
                        ? 'A lista demorou para responder. Clique nela para tentar novamente.' : erro.message || 'Erro ao abrir lista.');
                }
                return false;
            } finally {
                if (abertura === aberturaListaPedido && painel) painel.inert = false;
            }
        }

        global.addEventListener('pagehide', cancelarAberturaListaPedido);
        const limparSessao = () => {
            cancelarAberturaListaPedido();
            modules.api.cancelarTarefa('listas-resumo');
            listaPedidoAtual = null;
            listasPedidosResumo = [];
            renderListaPedidosResumo();
            renderEditorListaPedido();
        };
        modules.resultados.aoMudarSessao(limparSessao);
        global.addEventListener('jk-tenant-changed', limparSessao);
        global.addEventListener('jk:cache-persistente-auth-negado', limparSessao);
        return { cancelarAberturaListaPedido, abrirListaPedidoPorId, listaEmAbertura, confirmarListaAtualizada };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
