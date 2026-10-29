(function installJKMediasVisaoCarregamento(global) {
    'use strict';

    const app = global.JKMedias;
    app.defineModule('visao-carregamento', ['core', 'state', 'api', 'resultados'], ({ modules }) => {
        let escopoVisaoExibida = '';
        let escopoConsulta = '';
        let atualizacaoAdiada = null;
        let metaVisaoExibida = null;
        let ultimoStatus = '';
        let situacaoVisao = 'inicial';
        const edicoesPorEscopo = new Map();

        function obterMensagemVisaoVazia() {
            if (situacaoVisao === 'pronto') return '';
            if (situacaoVisao === 'erro') return 'Não foi possível carregar as médias. Selecione o período ou a loja para tentar novamente.';
            if (situacaoVisao === 'pendente') return 'O carregamento continua pendente. Selecione o período ou a loja para acompanhar novamente.';
            return situacaoVisao === 'demorando' ? 'Calculando as médias. Continuamos acompanhando o resultado...' : 'Carregando médias de vendas e estoque...';
        }

        function atualizarSituacaoVisao(situacao) {
            if (situacaoVisao === situacao) return;
            situacaoVisao = situacao;
            if (!itensVisaoAtual.length) renderAbaAtual();
        }

        function cancelarCarregamentoVisao() {
            geracaoCarregamentoVisao++;
            modules.api.cancelarTarefa('visao');
            atualizacaoAdiada = null;
            setStatus('');
        }

        function aplicarResultado(data, escopo, geracao) {
            if (escopo !== escopoConsulta || geracao !== geracaoCarregamentoVisao) return false;
            if (document.querySelector('#tblResultado .compra-sugerida-input')) {
                atualizacaoAdiada = { data, escopo, geracao };
                return false;
            }
            atualizacaoAdiada = null;
            metaVisaoExibida = data._cache || null;
            colunasMesesAtuais = modules.resultados.copiarResultado(data.colunas_meses || []);
            itensVisaoAtual = modules.resultados.copiarResultado(data.itens || []);
            for (const item of itensVisaoAtual) {
                const sku = normalizarSku(item.sku);
                if (comprasSugeridasEditadas.has(sku)) item.compra_sugerida = comprasSugeridasEditadas.get(sku);
            }
            escopoVisaoExibida = escopo;
            situacaoVisao = !metaVisaoExibida || (metaVisaoExibida.estado === 'ready' && !metaVisaoExibida.stale && !metaVisaoExibida.atualizando && metaVisaoExibida.completo !== false) ? 'pronto' : 'carregando';
            renderAbaAtual();
            return true;
        }

        function concluirEdicaoVisao() {
            if (!atualizacaoAdiada) return false;
            const pendente = atualizacaoAdiada;
            atualizacaoAdiada = null;
            // O campo já foi confirmado/cancelado; a linha antiga pode ser desmontada.
            document.querySelector('#tblResultado .compra-sugerida-input')?.remove();
            const aplicada = aplicarResultado(pendente.data, pendente.escopo, pendente.geracao);
            if (aplicada) setStatus(ultimoStatus);
            return aplicada;
        }

        async function carregarVisao(meses) {
            if ([3, 6, 12].includes(Number(meses))) periodoAtual = Number(meses);
            atualizarBotoesPeriodo();
            if (abaAtual === 'listas_pedidos') { cancelarCarregamentoVisao(); return; }
            if (app.state.inicializacaoMediasPendente) return;
            const lojaSolicitada = lojaSelecionada;
            const params = new URLSearchParams({ meses: String(periodoAtual) });
            if (lojaSolicitada && lojaSolicitada !== '__todas') {
                params.set('loja', lojaSolicitada);
                params.set('store_id', String(storeIdSelecionado || ''));
            }
            const url = '/api/medias-compras/visao?' + params.toString();
            const headers = { ...modules.api.authHeaders() };
            const escopo = JSON.stringify([tenantKey, userScopeKey, url, headers]);
            if (escopoConsulta && escopoConsulta !== escopo) {
                edicoesPorEscopo.set(escopoConsulta, new Map(comprasSugeridasEditadas));
                while (edicoesPorEscopo.size > 16) edicoesPorEscopo.delete(edicoesPorEscopo.keys().next().value);
                comprasSugeridasEditadas.clear();
                for (const [sku, valor] of edicoesPorEscopo.get(escopo) || []) comprasSugeridasEditadas.set(sku, valor);
                atualizacaoAdiada = null;
                itensVisaoAtual = [];
                colunasMesesAtuais = [];
                escopoVisaoExibida = '';
                metaVisaoExibida = null;
                situacaoVisao = 'carregando';
                renderAbaAtual();
            }
            escopoConsulta = escopo;
            return modules.api.executarTarefa('visao', escopo, async tarefa => {
                const geracao = ++geracaoCarregamentoVisao;
                ocultarBalaoEstoqueEmTransito();
                const lojaTexto = lojaSolicitada && lojaSolicitada !== '__todas' ? ' da loja ' + lojaSolicitada : ' de todas as lojas';
                setStatus((escopoVisaoExibida === escopo ? 'Atualizando dados' : 'Carregando dados de vendas e estoque') + lojaTexto + '...');
                const ativa = () => tarefa.ativa() && geracao === geracaoCarregamentoVisao;
                atualizarSituacaoVisao('carregando');
                try {
                    return await modules.resultados.acompanharResultado(url, {
                        headers, signal: tarefa.signal, ativa,
                        limiteMs: 5 * 60 * 1000, avisoAposMs: 30000, limiteRequisicaoMs: 15000, recuperarFontes: true,
                        receber: data => { if (ativa()) aplicarResultado(data, escopo, geracao); },
                        indisponivel: meta => {
                            if (!ativa()) return;
                            const anterior = atualizacaoAdiada?.data?._cache || metaVisaoExibida;
                            // A idade do snapshot não invalida a última visão autorizada.
                            // O servidor decide a validade; a tela conserva os dados durante a atualização.
                            if (!meta.pendente_fontes && !(anterior?.autoridade && meta.autoridade && anterior.autoridade !== meta.autoridade)) return;
                            atualizacaoAdiada = null;
                            metaVisaoExibida = null;
                            itensVisaoAtual = [];
                            colunasMesesAtuais = [];
                            escopoVisaoExibida = '';
                            situacaoVisao = 'carregando';
                            renderAbaAtual();
                        },
                        estado: (meta, pendente, acompanhando) => {
                            if (!ativa()) return;
                            const pronta = meta?.estado === 'ready' && !meta.stale && !meta.atualizando && meta.completo !== false;
                            atualizarSituacaoVisao(pronta ? 'pronto' : pendente ? (acompanhando ? 'demorando' : 'pendente') : meta?.erro && !meta.atualizando ? 'erro' : 'carregando');
                            const texto = modules.resultados.textoEstadoResultado(meta, pendente, acompanhando);
                            ultimoStatus = texto;
                            setStatus(atualizacaoAdiada ? texto + ' A tabela será atualizada ao concluir a edição.' : texto);
                        },
                    });
                } catch (erro) {
                    if (ativa()) {
                        situacaoVisao = 'erro';
                        if ([401, 403, 404].includes(erro.status)) {
                            itensVisaoAtual = [];
                            colunasMesesAtuais = [];
                            atualizacaoAdiada = null;
                            metaVisaoExibida = null;
                            comprasSugeridasEditadas.clear();
                            edicoesPorEscopo.clear();
                        }
                        if (!itensVisaoAtual.length) renderAbaAtual();
                        ultimoStatus = 'Falha ao carregar dados: ' + erro.message;
                        setStatus(ultimoStatus);
                    }
                    return { atual: false };
                }
            });
        }

        async function confirmarVisaoAtualizada() {
            if (document.querySelector('#tblResultado .compra-sugerida-input')) throw new Error('Conclua a edição da sugestão antes de gerar o pedido.');
            const resultado = await carregarVisao(periodoAtual);
            if (!resultado?.atual) throw new Error('A atualização dos dados ainda está pendente. Aguarde antes de gerar o pedido.');
            return true;
        }

        global.addEventListener('pagehide', cancelarCarregamentoVisao);
        modules.resultados.aoMudarSessao(() => {
            cancelarCarregamentoVisao();
            itensVisaoAtual = [];
            colunasMesesAtuais = [];
            metaVisaoExibida = null;
            comprasSugeridasEditadas.clear();
            edicoesPorEscopo.clear();
            situacaoVisao = 'erro';
            renderAbaAtual();
            setStatus('A sessão mudou. Reabra a tela para carregar os dados atuais.');
        });
        return { carregarVisao, cancelarCarregamentoVisao, concluirEdicaoVisao, confirmarVisaoAtualizada, obterMensagemVisaoVazia };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
