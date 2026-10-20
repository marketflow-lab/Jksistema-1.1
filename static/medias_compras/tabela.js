(function installJKMediasTabela(global) {
    'use strict';

    const app = global.JKMedias;
    app.defineModule('tabela', ['core', 'state', 'api', 'transito', 'pedido-hover', 'filtros', 'tabela-colunas', 'tabela-virtual', 'visao-carregamento'], (context) => {
        let controladorTabelaVirtual = null;
        let escopoTabelaVirtual = '';

        function obterQuantidadesSugeridasEditadas() {
            const quantidades = {};
            Array.from(comprasSugeridasEditadas.entries())
                .sort(([skuA], [skuB]) => skuA.localeCompare(skuB, 'pt-BR', { numeric: true }))
                .forEach(([sku, quantidade]) => {
                    quantidades[sku] = quantidade;
                });
            return quantidades;
        }

        function iniciarEdicaoCompraSugerida(botao, skuCodificado) {
            const celula = botao && botao.closest ? botao.closest('td') : null;
            const sku = normalizarSku(decodeURIComponent(String(skuCodificado || '')));
            const item = (itensVisaoAtual || []).find(i => normalizarSku(i && i.sku) === sku);
            if (!celula || !item || celula.querySelector('.compra-sugerida-input')) return;
            const sessaoDaEdicao = context.modules.resultados.identidadeSessao();

            const valorAnterior = normalizarQuantidadeCompraSugerida(item.compra_sugerida) ?? 0;
            const input = document.createElement('input');
            input.type = 'number';
            input.min = '0';
            input.step = '1';
            input.inputMode = 'numeric';
            input.className = 'compra-sugerida-input';
            input.value = String(valorAnterior);
            input.setAttribute('aria-label', 'Sugestão de compra do SKU ' + sku);

            let finalizado = false;
            const finalizar = (confirmar) => {
                if (finalizado) return;
                if (!(itensVisaoAtual || []).includes(item) || sessaoDaEdicao !== context.modules.resultados.identidadeSessao()) {
                    finalizado = true;
                    return;
                }
                if (confirmar) {
                    const quantidade = normalizarQuantidadeCompraSugerida(input.value);
                    if (quantidade === null) {
                        input.setCustomValidity('Informe um número inteiro maior ou igual a zero.');
                        input.reportValidity();
                        setTimeout(() => input.focus(), 0);
                        return;
                    }
                    item.compra_sugerida = quantidade;
                    comprasSugeridasEditadas.set(sku, quantidade);
                }
                finalizado = true;
                if (!context.modules['visao-carregamento'].concluirEdicaoVisao()) renderAbaAtual();
            };

            input.addEventListener('input', () => input.setCustomValidity(''));
            input.addEventListener('keydown', (event) => {
                if (event.key === 'Enter') {
                    event.preventDefault();
                    finalizar(true);
                } else if (event.key === 'Escape') {
                    event.preventDefault();
                    finalizar(false);
                }
            });
            input.addEventListener('blur', () => finalizar(true));

            celula.replaceChildren(input);
            input.focus();
            input.select();
        }

        function obterItensVisiveis() {
            return (itensVisaoAtual || []).filter(i => !skusOcultosSet.has(normalizarSku(i.sku)));
        }

        function obterItensOcultados() {
            const itensMap = new Map((itensVisaoAtual || []).map(i => [normalizarSku(i.sku), i]));
            const itens = [];
            Array.from(skusOcultosSet)
                .sort((a, b) => a.localeCompare(b, 'pt-BR', { numeric: true }))
                .forEach(sku => {
                    if (itensMap.has(sku)) {
                        itens.push(itensMap.get(sku));
                    } else {
                        itens.push({
                            sku,
                            foto: '',
                            titulo_anuncio: '',
                            vendas_mensais: {},
                            total_vendas_periodo: 0,
                            saldo_atual_estoque: 0,
                        });
                    }
                });
            return itens;
        }

        function renderAbaAtual() {
            limparDetalhesListaPedido();
            ocultarBalaoEstoqueEmTransito();
            const titulo = document.getElementById('tituloTabela');
            const statusOcultados = document.getElementById('statusOcultados');
            const btnAbaOcultados = document.getElementById('btnAbaOcultados');
            const secaoTabelaPrincipal = document.getElementById('secaoTabelaPrincipal');
            const secaoListasPedidos = document.getElementById('secaoListasPedidos');
            const visiveisTodos = obterItensVisiveis();
            const ocultadosTodos = obterItensOcultados();
            const visiveis = filtrarItensPorPesquisaSku(visiveisTodos);
            const ocultados = filtrarItensPorPesquisaSku(ocultadosTodos);
            const pesquisaAtiva = Boolean(obterPesquisaSkuNormalizada());
            atualizarControlePesquisaSku();

            if (btnAbaOcultados) {
                btnAbaOcultados.textContent = "SKU's ocultados (" + numero(ocultadosTodos.length) + ')';
            }

            if (statusOcultados) {
                let textoStatus = 'Ocultados: ' + numero(ocultadosTodos.length) + ' | Exibidos na lista principal: ' + numero(visiveisTodos.length);
                if (pesquisaAtiva && abaAtual !== 'listas_pedidos') {
                    const totalFiltrado = abaAtual === 'ocultados' ? ocultados.length : visiveis.length;
                    const totalBase = abaAtual === 'ocultados' ? ocultadosTodos.length : visiveisTodos.length;
                    textoStatus += ' | Pesquisa SKU: ' + numero(totalFiltrado) + ' de ' + numero(totalBase);
                }
                statusOcultados.textContent = textoStatus;
            }

            if (abaAtual === 'listas_pedidos') {
                if (titulo) titulo.textContent = 'Lista de pedidos';
                if (statusOcultados) {
                    if (pesquisaAtiva) {
                        const listasFiltradas = (listasPedidosResumo || []).filter(listaPedidoResumoContemPesquisaSku);
                        statusOcultados.textContent = 'Pesquisa SKU nas listas: ' + numero(listasFiltradas.length) + ' de ' + numero((listasPedidosResumo || []).length) + '. Clique em uma lista para abrir o conteudo.';
                    } else {
                        statusOcultados.textContent = 'Listas salvas por cliente. Clique em uma lista para abrir o conteudo.';
                    }
                }
                if (secaoTabelaPrincipal) secaoTabelaPrincipal.classList.add('hidden');
                if (secaoListasPedidos) secaoListasPedidos.classList.remove('hidden');
                renderListaPedidosResumo();
                renderEditorListaPedido();
                carregarSkusListasPedidosParaPesquisa();
                return;
            }

            if (secaoTabelaPrincipal) secaoTabelaPrincipal.classList.remove('hidden');
            if (secaoListasPedidos) secaoListasPedidos.classList.add('hidden');

            if (abaAtual === 'ocultados') {
                if (titulo) titulo.textContent = "SKU's ocultados";
                renderTabela(colunasMesesAtuais, ocultados, 'ocultados');
                return;
            }

            if (titulo) titulo.textContent = 'SKUs e vendas por mês';
            renderTabela(colunasMesesAtuais, visiveis, 'lista');
        }

        function prepararEstruturaTabela(colunasMeses) {
            const tabela = document.getElementById('tblResultado');
            const head = document.getElementById('headRow');
            const colgroupAntigo = tabela.querySelector('colgroup');
            if (colgroupAntigo) colgroupAntigo.remove();
            const colgroup = document.createElement('colgroup');
            colElementsAtuais = [];
            head.innerHTML = '';
            const headers = ['SKU', 'Foto', 'Título anúncio']
                .concat(colunasMeses.map(c => c.key))
                .concat(['Total período', 'VMM', 'Estoque físico', 'Em trânsito', 'Posição estoque', 'Cobertura (meses)', 'Sugestão compra', 'Ocultar']);
            chavesColunasAtuais = ['sku', 'foto', 'titulo_anuncio']
                .concat(colunasMeses.map((_c, indice) => 'mes_' + indice))
                .concat(['total_periodo', 'media_mensal', 'saldo_estoque', 'estoque_transito', 'posicao_estoque', 'cobertura_meses', 'compra_sugerida', 'acao']);
            carregarPerfilLargurasColunas(periodoAtual);
            sanearLargurasColunas(chavesColunasAtuais);

            chavesColunasAtuais.forEach((colKey, idx) => {
                const col = document.createElement('col');
                if (colKey.startsWith('mes_')) {
                    col.classList.add('col-venda-mes', (idx - 3) % 2 === 0 ? 'col-venda-mes-a' : 'col-venda-mes-b');
                    if (idx === 3) col.classList.add('col-venda-mes-primeira');
                }
                if (colKey === 'total_periodo') col.classList.add('col-total-periodo');
                if (colKey === 'saldo_estoque') col.classList.add('col-estoque-fisico');
                if (colKey === 'posicao_estoque') col.classList.add('col-posicao-estoque');
                if (colKey === 'cobertura_meses') col.classList.add('col-cobertura-estoque');
                const largura = normalizarLarguraColuna(colKey, largurasColunas[colKey]);
                col.style.width = largura + 'px';
                col.style.minWidth = largura + 'px';
                colgroup.appendChild(col);
                colElementsAtuais.push(col);
            });
            tabela.insertBefore(colgroup, tabela.firstChild);
            headers.forEach((h, idx) => {
                const th = document.createElement('th');
                const ehColunaNumerica = idx >= 3 && idx < (headers.length - 1);
                if (ehColunaNumerica) th.className = 'num';
                const inicioMeses = 3;
                const fimMeses = inicioMeses + colunasMeses.length;
                const ehCabecalhoMes = idx >= inicioMeses && idx < fimMeses;
                if (ehCabecalhoMes) {
                    const { anoCurto, mesNome } = quebrarMesAno(h);
                    th.classList.add('th-mes');
                    th.innerHTML = '<span class="mes-ano-topo">' + escaparHtml(anoCurto) + '</span><span class="mes-nome-base">' + escaparHtml(mesNome) + '</span>';
                } else {
                    const label = document.createElement('span');
                    label.className = 'cabecalho-coluna-label';
                    label.textContent = h;
                    th.appendChild(label);
                }
                th.dataset.colKey = chavesColunasAtuais[idx] || ('col_' + idx);
                if (th.dataset.colKey.startsWith('mes_')) {
                    th.classList.add('col-venda-mes', (idx - inicioMeses) % 2 === 0 ? 'col-venda-mes-a' : 'col-venda-mes-b');
                    if (idx === inicioMeses) th.classList.add('col-venda-mes-primeira');
                }
                if (th.dataset.colKey === 'total_periodo') th.classList.add('col-total-periodo');
                if (th.dataset.colKey === 'saldo_estoque') th.classList.add('col-estoque-fisico');
                if (th.dataset.colKey === 'posicao_estoque') th.classList.add('col-posicao-estoque');
                if (th.dataset.colKey === 'cobertura_meses') th.classList.add('col-cobertura-estoque');
                th.dataset.colIndex = String(idx);
                aplicarLarguraColuna(th, th.dataset.colKey);
                head.appendChild(th);
            });
            habilitarResizeColunas();
            sincronizarLarguraTabelaPrincipal();
            const tbody = document.querySelector('#tblResultado tbody');
            tbody.innerHTML = '';
            return { headers, tbody };
        }

        function criarLinhaResultado(i, indice, colunasMeses, modo) {
            const tr = document.createElement('tr');
            tr.classList.add(indice % 2 === 0 ? 'linha-impar' : 'linha-par');

            const compraSugeridaNivel = Number(i.compra_sugerida || 0);
            const mediaMensalNivel = Number(i.media_mensal || 0);
            const saldoFisicoNivel = Number(i.saldo_atual_estoque || 0);
            const posicaoEstoqueNivel = Number(i.posicao_estoque || 0);
            const mesesRestantesNivel = calcularCoberturaMeses(posicaoEstoqueNivel, mediaMensalNivel) ?? 999;

            if (modo !== 'ocultados' && compraSugeridaNivel > 0) {
                if (saldoFisicoNivel <= 0 || mesesRestantesNivel <= 1.5) {
                    tr.classList.add('risco-critico');
                } else {
                    tr.classList.add('risco-atencao');
                }
            }

            const tds = [];
            tds.push('<td>' + escaparHtml(i.sku || '') + '</td>');

            const fotoUrl = obterUrlFoto(i.foto || '');
            if (fotoUrl) {
                tds.push('<td><img class="foto-sku" ' + atributoSrcFotoCadastro(fotoUrl) + ' alt="Foto ' + escaparHtml(i.sku || '') + '" loading="lazy"></td>');
            } else {
                tds.push('<td><span class="foto-empty">Sem foto</span></td>');
            }

            const alertaSemVenda = String(i.aviso_sem_venda || '').trim();
            const tituloAnuncio = escaparHtml(i.titulo_anuncio || '');
            const alertaHtml = alertaSemVenda ? '<div class="produto-alerta-sem-venda">' + escaparHtml(alertaSemVenda) + '</div>' : '';
            tds.push('<td class="titulo-anuncio" title="' + tituloAnuncio + '">' + tituloAnuncio + alertaHtml + '</td>');

            colunasMeses.forEach((c, idxMes) => {
                const v = i.vendas_mensais ? i.vendas_mensais[c.key] : 0;
                const classeMes = idxMes % 2 === 0 ? 'col-venda-mes-a' : 'col-venda-mes-b';
                const classePrimeiroMes = idxMes === 0 ? ' col-venda-mes-primeira' : '';
                tds.push('<td class="num col-venda-mes ' + classeMes + classePrimeiroMes + '">' + numero(v) + '</td>');
            });

            const compraSugerida = Number(i.compra_sugerida || 0);

            tds.push('<td class="num col-total-periodo"><strong>' + numero(i.total_vendas_periodo || 0) + '</strong></td>');
            tds.push('<td class="num">' + numero(i.media_mensal || 0) + '</td>');
            tds.push('<td class="num col-estoque-fisico"><strong>' + numero(i.saldo_atual_estoque || 0) + '</strong></td>');
            tds.push(renderizarCelulaEstoqueEmTransito(i));
            tds.push('<td class="num col-posicao-estoque"><strong>' + numero(i.posicao_estoque || 0) + '</strong></td>');
            tds.push('<td class="num col-cobertura-estoque" title="Posição de estoque dividida pela VMM">' + formatarCoberturaMeses(i.posicao_estoque, i.media_mensal) + '</td>');
            const classeCompraPositiva = compraSugerida > 0 ? ' positiva' : '';
            const skuCompraCodificado = encodeURIComponent(String(i.sku || ''));
            tds.push('<td class="num compra-sugerida-cell"><button type="button" class="compra-sugerida-editavel' + classeCompraPositiva + '" data-sku="' + skuCompraCodificado + '" title="Clique para editar a sugestão de compra" aria-label="Editar sugestão de compra do SKU ' + escaparHtml(i.sku || '') + '">' + numero(compraSugerida) + '</button></td>');

            const skuCodificado = encodeURIComponent(String(i.sku || ''));
            if (modo === 'ocultados') {
                tds.push('<td><button type="button" class="btn-success-soft" data-jk-action="reexibir-sku" data-sku="' + skuCodificado + '">Reexibir</button></td>');
            } else {
                tds.push('<td><button type="button" class="btn-danger-soft btn-icon-action" title="Ocultar SKU" aria-label="Ocultar SKU" data-jk-action="ocultar-sku" data-sku="' + skuCodificado + '">&minus;</button></td>');
            }

            tr.innerHTML = tds.join('');
            const detalhesTransito = normalizarDetalhesEstoqueEmTransito(i);
            configurarBalaoEstoqueEmTransito(
                tr.querySelector('.transito-quantidade--detalhes'),
                Number(i.estoque_em_transito || 0),
                detalhesTransito
            );
            const botaoCompraSugerida = tr.querySelector('.compra-sugerida-editavel');
            if (botaoCompraSugerida) {
                botaoCompraSugerida.addEventListener('click', () => {
                    iniciarEdicaoCompraSugerida(botaoCompraSugerida, botaoCompraSugerida.dataset.sku || '');
                });
            }
            return tr;
        }

        function renderTabela(colunasMeses, itens, modo) {
            ocultarBalaoEstoqueEmTransito();
            const raiz = document.getElementById('secaoTabelaPrincipal');
            const escopo = JSON.stringify([tenantKey, userScopeKey, storeIdSelecionado, lojaSelecionada, periodoAtual, modo, filtroSkuAtual]);
            const ancora = escopo === escopoTabelaVirtual && controladorTabelaVirtual
                ? controladorTabelaVirtual.capturarAncora() : null;
            if (controladorTabelaVirtual) controladorTabelaVirtual.destruir();
            controladorTabelaVirtual = null;
            escopoTabelaVirtual = escopo;
            const scrollLeft = raiz.scrollLeft;
            const { headers, tbody } = prepararEstruturaTabela(colunasMeses);
            const tabela = document.getElementById('tblResultado');
            if (!ancora) raiz.scrollTop = 0;
            if (!itens.length) {
                tabela.setAttribute('aria-rowcount', '1');
                tabela.dataset.totalRows = '0';
                const tr = document.createElement('tr');
                const mensagemCarregamento = modo === 'ocultados' ? '' : context.modules['visao-carregamento'].obterMensagemVisaoVazia();
                const msgVazio = mensagemCarregamento || (obterPesquisaSkuNormalizada()
                    ? 'Nenhum SKU encontrado para a pesquisa.'
                    : (modo === 'ocultados')
                    ? "Nenhum SKU ocultado."
                    : "Nenhum SKU encontrado para o período selecionado.");
                tr.innerHTML = '<td colspan="' + headers.length + '">' + msgVazio + '</td>';
                tbody.appendChild(tr);
            } else {
                controladorTabelaVirtual = context.modules['tabela-virtual'].criarTabelaVirtual({
                    raiz, tabela, tbody, colunas: headers.length, overscan: 10,
                    renderizarLinha: (item, indice) => criarLinhaResultado(item, indice, colunasMeses, modo),
                });
                controladorTabelaVirtual.atualizarItens(itens, { ancora });
            }
            autoAjustarLargurasColunasPrimeiroUso(itensVisaoAtual, colunasMeses);
            raiz.scrollLeft = scrollLeft;
        }

        return {
            obterQuantidadesSugeridasEditadas,
            iniciarEdicaoCompraSugerida,
            obterItensVisiveis,
            obterItensOcultados,
            renderAbaAtual,
            carregarVisao: context.modules['visao-carregamento'].carregarVisao,
            cancelarCarregamentoVisao: context.modules['visao-carregamento'].cancelarCarregamentoVisao,
            renderTabela
        };
    });
})(typeof globalThis !== 'undefined' ? globalThis : this);
