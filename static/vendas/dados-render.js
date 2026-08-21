async function agruparPorSkuEmLotes(lista, mostrarDevolucoes, token, devolucoesParaAgrupar) {
    if (devolucoesParaAgrupar === undefined) devolucoesParaAgrupar = devolucaoItens;
    const agrupadoMapa = {};
    const CHUNK = 1200;
    let index = 0;

    const isLinhaCabecalho = (sku, produto) => {
        const s = String(sku || '').trim().toLowerCase();
        const p = String(produto || '').trim().toLowerCase();
        return s === 'sku' && p === 'produto';
    };

    if ((lista || []).some(item => item && item.__resumo)) {
        return (lista || []).map(item => ({
            ...item,
            pedidos: Number(item.pedidos || 0),
            itens: Number(item.itens ?? item.quantidade ?? 0),
            quantidade: Number(item.quantidade ?? item.itens ?? 0),
            devolucoes: Number(item.devolucoes || 0),
            itensDevolucao: Number(item.itensDevolucao || 0),
            valorDevolucao: Number(item.valorDevolucao || 0),
            valor: Number(item.valor || 0)
        })).sort((a, b) => {
            if (metricaGrafico === 'quantidade') {
                const diffQtd = (Number(b.itens) || 0) - (Number(a.itens) || 0);
                if (diffQtd !== 0) return diffQtd;
                const diffValor = obterValorExibicaoSku(b) - obterValorExibicaoSku(a);
                if (diffValor !== 0) return diffValor;
            } else {
                const diffValor = obterValorExibicaoSku(b) - obterValorExibicaoSku(a);
                if (diffValor !== 0) return diffValor;
                const diffQtd = (Number(b.itens) || 0) - (Number(a.itens) || 0);
                if (diffQtd !== 0) return diffQtd;
            }
            return String(a.sku || '').localeCompare(String(b.sku || ''), 'pt-BR');
        });
    }

    while (index < lista.length) {
        if (token !== filtrarToken) return null;
        const fim = Math.min(index + CHUNK, lista.length);
        for (; index < fim; index++) {
            const r = lista[index];
            if (vendaEhEbazarSomatorio(r)) continue;
            const sku = r.sku || 'N/D';
            if (isLinhaCabecalho(sku, r.produto)) continue;
            if (!agrupadoMapa[sku]) {
                agrupadoMapa[sku] = {
                    sku,
                    produto: r.produto || '-',
                    pedidos: 0,
                    itens: 0,
                    devolucoes: 0,
                    itensDevolucao: 0,
                    valorDevolucao: 0,
                    valor: 0
                };
            }
            agrupadoMapa[sku].pedidos += 1;
            agrupadoMapa[sku].itens += Number(r.quantidade || 0);
            agrupadoMapa[sku].valor += Number(r.valor || 0);
        }
        await aguardarProximoFrame();
    }

    if (mostrarDevolucoes) {
        let iDev = 0;
        while (iDev < devolucoesParaAgrupar.length) {
            if (token !== filtrarToken) return null;
            const fimDev = Math.min(iDev + CHUNK, devolucoesParaAgrupar.length);
            for (; iDev < fimDev; iDev++) {
                const dev = devolucoesParaAgrupar[iDev];
                const sku = dev.sku || 'N/D';
                if (!agrupadoMapa[sku]) {
                    agrupadoMapa[sku] = {
                        sku,
                        produto: dev.descricao || '-',
                        pedidos: 0,
                        itens: 0,
                        devolucoes: 0,
                        itensDevolucao: 0,
                        valorDevolucao: 0,
                        valor: 0
                    };
                }
                agrupadoMapa[sku].devolucoes += 1;
                agrupadoMapa[sku].itensDevolucao += Number(dev.quantidade || 0);
                agrupadoMapa[sku].valorDevolucao += Number(dev.valor_total || 0);
            }
            await aguardarProximoFrame();
        }
    }

    const agrupadoLista = Object.values(agrupadoMapa).sort((a, b) => {
        if (metricaGrafico === 'quantidade') {
            const diffQtd = (Number(b.itens) || 0) - (Number(a.itens) || 0);
            if (diffQtd !== 0) return diffQtd;
            const diffValor = obterValorExibicaoSku(b) - obterValorExibicaoSku(a);
            if (diffValor !== 0) return diffValor;
        } else {
            const diffValor = obterValorExibicaoSku(b) - obterValorExibicaoSku(a);
            if (diffValor !== 0) return diffValor;
            const diffQtd = (Number(b.itens) || 0) - (Number(a.itens) || 0);
            if (diffQtd !== 0) return diffQtd;
        }

        // Desempate estável por SKU para evitar "pulos" visuais entre renderizações.
        return String(a.sku || '').localeCompare(String(b.sku || ''), 'pt-BR');
    });

    return agrupadoLista;
}

async function filtrar() {
    filtrarToken += 1;
    const tokenAtual = filtrarToken;
    const termo = filtroTexto.value.trim().toLowerCase();
    const unidadeSel = unidadeNegocioSelect.value;
    const usarResumoServidor = dadosModoResumo === true;
    let lista = Array.isArray(dados) ? dados : [];

    if (!usarResumoServidor && lojaSelecionada && lojaSelecionada !== '__todas') {
        lista = lista.filter(row => mesmaLoja(row.loja_conta, lojaSelecionada));
    }
    if (!usarResumoServidor && unidadeSel && unidadeSel !== '__todos') {
        lista = lista.filter(row => mesmaUnidade(row.unidade_negocio, unidadeSel));
    }
    if (termo) {
        lista = lista.filter(row => String(row.sku || '').toLowerCase().includes(termo));
    }

    dadosFiltrados = lista;
    const mostrarDevolucoes = true;
    let devolucoesAtivas = [];

    // Filtra devolucaoItens pela loja selecionada antes de qualquer outro filtro
    const devolucaoItensFiltradas = usarResumoServidor ? [] : (lojaSelecionada && lojaSelecionada !== '__todas')
        ? devolucaoItens.filter(dev => mesmaLoja(dev.loja_conta, lojaSelecionada))
        : devolucaoItens;

    const normalizarUnidadeDevolucao = (dev) => normalizarChaveFiltro(
        dev?.unidade_negocio_virtual || dev?.unidade_negocio || '',
        true
    );

    if (!unidadeSel || unidadeSel === '__todos') {
        devolucoesAtivas = devolucaoItensFiltradas;
    } else {
        const unidadeAlvo = normalizarChaveFiltro(unidadeSel, true);
        devolucoesAtivas = devolucaoItensFiltradas.filter(dev => {
            const uv = normalizarUnidadeDevolucao(dev);
            return uv === unidadeAlvo;
        });
    }
    renderResumo(lista, devolucoesAtivas, mostrarDevolucoes);

    if (lista.length > 3000 && !syncEmAndamento) {
        statusEl.className = 'status-bar loading';
        statusEl.innerHTML = `${spinnerHtml}Processando tabela...`;
    }

    const agrupadoLista = await agruparPorSkuEmLotes(lista, mostrarDevolucoes, tokenAtual, devolucoesAtivas);
    if (!agrupadoLista || tokenAtual !== filtrarToken) return;

    renderTabelaAgrupada(agrupadoLista);
    agendarSalvarCacheTelaVendas();

    const faltantes = datasFaltantes(lista, getDataIniISO(), getDataFimISO(), devolucaoItensFiltradas);
    const totalDiasPeriodo = diasNoPeriodo(getDataIniISO(), getDataFimISO());
    const periodoGrande = totalDiasPeriodo > 60;
    const possivelNaoSincronizado = totalDiasPeriodo > 0 && (
        lista.length === 0 || faltantes.length >= Math.ceil(totalDiasPeriodo * 0.7)
    );

    if (!periodoSelecionadoPeloUsuario) {
        missingPromptShown = false;
        missingPromptKey = '';
        return;
    }

    if (faltantes.length) {
        if (!syncEmAndamento && !autoSyncAoTrocarLoja) {
            statusEl.className = 'status-bar empty';
            statusEl.textContent = `Existem ${faltantes.length} dia(s) sem vendas no período selecionado.`;
        }
        const promptKeyAtual = contextoPromptFaltantes();
        if (missingPromptKey !== promptKeyAtual) {
            missingPromptShown = false;
            missingPromptKey = promptKeyAtual;
        }
        if (!missingPromptShown && !syncEmAndamento) {
            missingPromptShown = true;
            statusEl.className = 'status-bar empty';
            statusEl.textContent = `Existem ${faltantes.length} dia(s) sem vendas no periodo selecionado. Clique em Atualizar para sincronizar manualmente.`;
            return;
        }
    } else {
        missingPromptShown = false;
        missingPromptKey = '';
    }
}

async function carregarLojas() {
    try {
        clientId = obterClientId();
        if (!clientId) {
            window.location.href = '/frontend_index.html';
            return;
        }
        const resp = await fetchComTimeout('/api/lojas', { headers: obterAuthHeaders() });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const lojas = await resp.json();
        lojasDisponiveis = Array.isArray(lojas) ? lojas : [];
        renderBotoesLojas(lojas);
    } catch (e) {
        statusEl.className = 'status-bar error';
        statusEl.textContent = `Erro ao carregar lojas: ${e.name === 'AbortError' ? 'tempo de resposta excedido' : e.message}`;
    }
}

async function buscarDadosComplementares(paramsBase) {
    const paramsDev = new URLSearchParams(paramsBase);
    paramsDev.append('agrupado', 'false');
    const paramsUnidades = new URLSearchParams();
    if (lojaSelecionada && lojaSelecionada !== '__todas') {
        paramsUnidades.append('loja', lojaSelecionada);
    }

    const [respNotasRes, respDevolucaoRes, respUnidadesRes] = await Promise.allSettled([
        fetchComTimeout('/api/notas-entrada' + (paramsBase.toString() ? `?${paramsBase.toString()}` : ''), { headers: obterAuthHeaders() }),
        fetchComTimeout('/api/notas-entrada/itens' + (paramsDev.toString() ? `?${paramsDev.toString()}` : ''), { headers: obterAuthHeaders() }),
        fetchComTimeout('/api/unidades-negocios' + (paramsUnidades.toString() ? `?${paramsUnidades.toString()}` : ''), { headers: obterAuthHeaders() })
    ]);

    const notas = (respNotasRes.status === 'fulfilled' && respNotasRes.value.ok)
        ? await respNotasRes.value.json()
        : [];
    const devolucoes = (respDevolucaoRes.status === 'fulfilled' && respDevolucaoRes.value.ok)
        ? await respDevolucaoRes.value.json()
        : [];
    const mapeamento = (respUnidadesRes.status === 'fulfilled' && respUnidadesRes.value.ok)
        ? ((await respUnidadesRes.value.json()).mapeamento || {})
        : {};

    return { notas, devolucoes, mapeamento };
}

function aplicarResumoVendas(payload, dataIniIso, dataFimIso) {
    const rows = Array.isArray(payload?.rows) ? payload.rows : [];
    dadosModoResumo = true;
    resumoTotaisVendas = payload?.totals || {};
    vendasResumoMeta = payload?.meta || {};
    notasEntrada = [];
    devolucaoItens = [];
    dados = rows.map(item => ({
        ...item,
        __resumo: true,
        data: item.data || dataFimIso || dataIniIso || '',
        data_inicio: dataIniIso || '',
        data_fim: dataFimIso || '',
        loja_conta: item.loja_conta || (lojaSelecionada === '__todas' ? '' : lojaSelecionada),
        unidade_negocio: item.unidade_negocio || '',
        produto: item.produto || '-',
        quantidade: Number(item.quantidade ?? item.itens ?? 0),
        itens: Number(item.itens ?? item.quantidade ?? 0),
        pedidos: Number(item.pedidos || 0),
        devolucoes: Number(item.devolucoes || 0),
        itensDevolucao: Number(item.itensDevolucao || 0),
        valorDevolucao: Number(item.valorDevolucao || 0),
        valor: Number(item.valor || 0)
    }));
}

async function carregarVendas(opcoes = {}) {
    const retornoRapido = !!opcoes.retornoRapido;
    const dataIniIso = getDataIniISO();
    const dataFimIso = getDataFimISO();
    const requestKey = JSON.stringify({
        dataIniIso,
        dataFimIso,
        loja: lojaSelecionada || '__todas',
        unidade: unidadeNegocioSelect?.value || '__todos'
    });
    if (carregarVendasPromise && carregarVendasRequestKey === requestKey) {
        return carregarVendasPromise;
    }
    if (carregarVendasController) carregarVendasController.abort();
    carregarVendasController = new AbortController();
    carregarVendasRequestKey = requestKey;
    const requestController = carregarVendasController;

    carregarVendasPromise = (async () => {
    if (!dataIniIso || !dataFimIso) {
        statusEl.className = 'status-bar';
        statusEl.textContent = '';
        return;
    }

    statusEl.className = 'status-bar loading';
    statusEl.innerHTML = `${spinnerHtml}Carregando vendas...`;
    try {
        clientId = clientId || obterClientId();
        if (!clientId) {
            console.warn('ClientId vazio - tentando carregar de localStorage novamente');
            const userData2 = JSON.parse(localStorage.getItem('user_data') || 'null');
            if (userData2 && userData2.client_id) {
                clientId = userData2.client_id;
                console.log('ClientId recuperado:', clientId);
            }
        }
        
        if (!clientId) {
            statusEl.className = 'status-bar error';
            statusEl.innerHTML = '⚠️ Sessão expirada. <a href="/frontend_index.html" style="color:white; text-decoration:underline;">Fazer login novamente</a>';
            console.warn('ClientId ainda vazio após nova tentativa');
            return;
        }
        const paramsResumo = new URLSearchParams();
        if (dataIniIso) paramsResumo.append('data_inicio', dataIniIso);
        if (dataFimIso) paramsResumo.append('data_fim', dataFimIso);
        if (lojaSelecionada && lojaSelecionada !== '__todas') paramsResumo.append('loja', lojaSelecionada);
        if (unidadeNegocioSelect.value && unidadeNegocioSelect.value !== '__todos') {
            paramsResumo.append('unidade_negocio', unidadeNegocioSelect.value);
        }
        const urlResumo = '/api/vendas/resumo' + (paramsResumo.toString() ? `?${paramsResumo.toString()}` : '');
        statusEl.innerHTML = `${spinnerHtml}Buscando resumo de vendas...`;
        const respResumo = await fetchComTimeout(urlResumo, {
            headers: obterAuthHeaders(),
            signal: requestController.signal
        }, 45000);
        if (!respResumo.ok) throw new Error(`HTTP ${respResumo.status}`);
        const payloadResumo = await respResumo.json();
        if (requestController.signal.aborted || carregarVendasRequestKey !== requestKey) return;
        aplicarResumoVendas(payloadResumo, dataIniIso, dataFimIso);

        statusEl.innerHTML = `${spinnerHtml}Renderizando resumo...`;
        await aguardarProximoFrame();
        atualizarUnidadesNegocio(dados, mapeamentoUnidades || {});
        await filtrar();
        if (!syncEmAndamento) {
            statusEl.className = 'status-bar';
            statusEl.textContent = '';
        }
        return;

        const params = new URLSearchParams();
        if (dataIniIso) params.append('data_inicio', dataIniIso);
        if (dataFimIso) params.append('data_fim', dataFimIso);
        if (lojaSelecionada && lojaSelecionada !== '__todas') params.append('loja', lojaSelecionada);
        params.append('resolver_nf', 'false');
        const url = '/api/vendas' + (params.toString() ? `?${params.toString()}` : '');
        statusEl.innerHTML = `${spinnerHtml}Buscando vendas...`;
        const resp = await fetchComTimeout(url, { headers: obterAuthHeaders() }, 45000);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        dados = await resp.json();

        if (retornoRapido) {
            notasEntrada = [];
            devolucaoItens = [];
            statusEl.innerHTML = `${spinnerHtml}Renderizando lista...`;
            await aguardarProximoFrame();
            atualizarUnidadesNegocio(dados, mapeamentoUnidades || {});
            await filtrar();
            if (!syncEmAndamento) {
                statusEl.className = 'status-bar';
                statusEl.textContent = '';
            }

            agendarSegundoPlano(async () => {
                try {
                    const comp = await buscarDadosComplementares(params);
                    notasEntrada = comp.notas;
                    devolucaoItens = comp.devolucoes;
                    atualizarUnidadesNegocio(dados, comp.mapeamento);
                    await filtrar();
                    if (!syncEmAndamento) {
                        statusEl.className = 'status-bar';
                        statusEl.textContent = '';
                    }
                } catch (e) {
                    console.warn('Falha ao carregar complementos em segundo plano:', e);
                }
            }, 120);
        } else {
            statusEl.innerHTML = `${spinnerHtml}Carregando dados complementares...`;
            const comp = await buscarDadosComplementares(params);
            notasEntrada = comp.notas;
            devolucaoItens = comp.devolucoes;

            statusEl.innerHTML = `${spinnerHtml}Aplicando filtros e cálculos...`;
            atualizarUnidadesNegocio(dados, comp.mapeamento);
            await filtrar();
            statusEl.className = 'status-bar';
            statusEl.textContent = '';
        }
    } catch (e) {
        if (e.name === 'AbortError' && carregarVendasRequestKey !== requestKey) return;
        statusEl.className = 'status-bar error';
        statusEl.textContent = `Erro ao carregar vendas: ${e.name === 'AbortError' ? 'tempo de resposta excedido' : e.message}`;
    }
    })();

    try {
        return await carregarVendasPromise;
    } finally {
        if (carregarVendasRequestKey === requestKey) {
            carregarVendasPromise = null;
            carregarVendasController = null;
        }
    }
}

filtroTexto.addEventListener('input', filtrar);
unidadeNegocioSelect.addEventListener('change', () => {
    let periodoAtualizadoPromise = Promise.resolve(true);
    if (typeof reaplicarPeriodoGraficoAposMudancaFiltro === 'function') {
        periodoAtualizadoPromise = reaplicarPeriodoGraficoAposMudancaFiltro();
    } else if (typeof invalidarPeriodoGraficoPendente === 'function') {
        invalidarPeriodoGraficoPendente();
    }
    if (periodoApplyTimer) clearTimeout(periodoApplyTimer);
    periodoApplyTimer = setTimeout(async () => {
        const periodoAtualizado = await periodoAtualizadoPromise;
        if (periodoAtualizado === false) return;
        await carregarVendas({ retornoRapido: true });
        agendarSegundoPlano(() => carregarGrafico(), 120);
    }, 250);
});
dataIni.addEventListener('change', () => { atualizarPeriodoComRecarregamento(true); });
dataFim.addEventListener('change', () => { atualizarPeriodoComRecarregamento(true); });
btnSync.addEventListener('click', executarSync);
