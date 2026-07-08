function montarMapaVendasMensaisSku(lista) {
    const mapa = new Map();
    (Array.isArray(lista) ? lista : []).forEach(row => {
        const key = String(primeiro(row, ['sku', 'SKU', 'codigo']) || '').trim().toUpperCase();
        const mesKey = chaveMesVenda(row);
        if (!key || !mesKey) return;
        const porMes = mapa.get(key) || {};
        const atual = porMes[mesKey] || { qtd: 0, valor: 0 };
        atual.qtd += numero(row.quantidade);
        atual.valor += numero(row.valor);
        porMes[mesKey] = atual;
        mapa.set(key, porMes);
    });
    return mapa;
}

function vendaMensalFull(skuKey, mesKey) {
    return (vendasFullMesesMap.get(String(skuKey || '').toUpperCase()) || {})[mesKey] || { qtd: 0, valor: 0 };
}

function colspanCoberturaFull() {
    return 9 + (mesesCoberturaFull.length || 6);
}

function renderHeaderCoberturaFull() {
    if (!fullCoverageHeaderRowEl) return;
    if (!mesesCoberturaFull.length) mesesCoberturaFull = ultimosSeisMesesFull();
    const meses = mesesCoberturaFull.map(mes => `<th class="num coverage-month-cell">${escapeHtml(mes.label)}</th>`).join('');
    fullCoverageHeaderRowEl.innerHTML = `
        <th>Produto</th>
        <th>Codigo ML</th>
        <th>SKU</th>
        <th class="num">Vendas 60 dias</th>
        ${meses}
        <th class="num">Transito</th>
        <th class="num">Estoque</th>
        <th>Cobertura</th>
        <th class="num">Vendas (R$)</th>
        <th>Acoes</th>
    `;
}

function categoriaCobertura(estoque, vendas60) {
    if (estoque <= 0) return 'sem_estoque';
    if (vendas60 <= 0) return 'sem_venda';
    const dias = estoque / Math.max(vendas60 / 60, 0.0001);
    if (dias <= 10) return 'critico';
    if (dias <= 15) return 'baixo';
    if (dias <= 45) return 'ideal';
    if (dias <= 60) return 'alto';
    if (dias <= 75) return 'muito_alto';
    return 'excessivo';
}

function diasCoberturaTexto(row) {
    if (row.categoria === 'sem_venda') return 'Sem venda';
    if (!Number.isFinite(row.diasCobertura)) return 'âˆž dias';
    return `${formatarNumero(row.diasCobertura)} dias`;
}

function montarLinhasCobertura(lista) {
    return (Array.isArray(lista) ? lista : []).map((row, indice) => {
        const key = chaveSkuFull(row);
        const venda = vendasFull60Map.get(key) || { qtd: 0, valor: 0, pedidos: new Set() };
        const estoque = estoqueFull(row);
        const vendas60 = venda.qtd || 0;
        const mediaDia = vendas60 / 60;
        const dias = estoque <= 0 ? 0 : (mediaDia > 0 ? estoque / mediaDia : Infinity);
        const categoria = categoriaCobertura(estoque, vendas60);
        const precoUnit = preco(row);
        return {
            row,
            indice,
            categoria,
            sku: key,
            estoque,
            vendas60,
            valor60: venda.valor || 0,
            pedidos60: venda.pedidos ? venda.pedidos.size : 0,
            estoqueValor: estoque * precoUnit,
            diasCobertura: dias,
            giro: mediaDia,
            preco: precoUnit
        };
    });
}

function somaLinhas(rows, campo) {
    return rows.reduce((acc, item) => acc + Number(item[campo] || 0), 0);
}

function filtrarLinhasCobertura(keys) {
    const set = new Set(keys);
    return coberturaRowsAtuais.filter(item => set.has(item.categoria));
}

function pct(valor, total) {
    if (!total) return 0;
    return Math.max(0, Math.min(100, (Number(valor || 0) / Number(total)) * 100));
}

function keysFiltroCobertura(filtro = coberturaFiltroAtivo) {
    return coberturaFiltros[filtro] || coberturaFiltros.todos;
}

function chipCoberturaAtivo(key) {
    if (coberturaFiltroAtivo === key) return true;
    if (coberturaFiltroAtivo === 'todos') return key === 'todos';
    const keys = keysFiltroCobertura(coberturaFiltroAtivo);
    return key !== 'todos' && keys.length > 1 && keys.includes(key);
}

function linhasCoberturaFiltradas(rows = coberturaRowsAtuais) {
    if (coberturaFiltroAtivo === 'todos') return rows.slice();
    const keys = new Set(keysFiltroCobertura());
    return rows.filter(item => keys.has(item.categoria));
}

function abrirDetalhesCobertura() {
    if (!btnCoverageSummary || !btnCoverageDetails || !coverageDetailTable) return;
    btnCoverageDetails.classList.add('active');
    btnCoverageSummary.classList.remove('active');
    coverageDetailTable.hidden = false;
}

function aplicarFiltroCobertura(filtro) {
    coberturaFiltroAtivo = coberturaFiltros[filtro] ? filtro : 'todos';
    renderChipsCobertura(coberturaRowsAtuais);
    renderResumoCobertura(coberturaRowsAtuais);
    renderTabelaCobertura();
    abrirDetalhesCobertura();
}

function renderStack(el, segmentos, totalBase) {
    if (!el) return;
    if (!totalBase) {
        el.innerHTML = '<button type="button" class="cov-gray" data-coverage-filter="todos" style="width:100%">0%</button>';
        return;
    }
    el.innerHTML = segmentos.map(seg => {
        const p = pct(seg.valor, totalBase);
        const label = p >= 6 ? `${Math.round(p)}%` : '';
        const active = coberturaFiltroAtivo === seg.filter ? ' active' : '';
        const title = `${seg.title || 'Filtro'}: ${formatarNumero(p)}%`;
        return `<button type="button" class="${seg.className}${active}" data-coverage-filter="${escapeHtml(seg.filter || 'todos')}" style="width:${Math.max(p, p > 0 ? 2 : 0)}%" title="${escapeHtml(title)}">${label}</button>`;
    }).join('');
}

function renderChipsCobertura(rows) {
    if (!fullCoverageChipsEl) return;
    const contagens = Object.keys(coberturaCategorias).reduce((acc, key) => ({ ...acc, [key]: 0 }), {});
    contagens.todos = rows.length;
    rows.forEach(item => { contagens[item.categoria] = (contagens[item.categoria] || 0) + 1; });
    const ordem = ['todos', 'sem_estoque', 'critico', 'baixo', 'ideal', 'alto', 'muito_alto', 'excessivo', 'sem_venda'];
    fullCoverageChipsEl.innerHTML = ordem.map(key => `
        <button class="coverage-chip${chipCoberturaAtivo(key) ? ' active' : ''}" type="button" data-coverage-filter="${key}" data-kind="${key}">
            ${escapeHtml(coberturaCategorias[key].label)}
            <strong>${formatarNumero(contagens[key] || 0)}</strong>
        </button>
    `).join('');
}

function renderCardsCobertura() {
    if (!fullCoverageCardsEl) return;
    const cards = [
        {
            title: 'Em Ruptura',
            keys: ['sem_estoque'],
            className: '',
            amea: ['Perda de posicionamento e vendas', 'Clientes sem estoque para atender'],
            oportunidades: ['Repor estoque com urgencia', 'Revisar previsao de demanda']
        },
        {
            title: 'Em Risco',
            keys: ['critico', 'baixo'],
            className: 'risk',
            amea: ['Pode virar ruptura se nao repor', 'Risco de perder momentum e posicionamento'],
            oportunidades: ['Reposicao urgente para manter vendas', 'Aumentar margem para desacelerar giro']
        },
        {
            title: 'Cobertura Ideal',
            keys: ['ideal'],
            className: 'ideal',
            amea: ['Queda de giro pode levar rapidamente ao excesso', 'Vendas acima do esperado podem causar ruptura'],
            oportunidades: ['Manter estrategia atual', 'Monitoramento continuo para se manter aqui']
        },
        {
            title: 'Capital Imobilizado',
            keys: ['alto', 'muito_alto', 'excessivo', 'sem_venda'],
            className: 'capital',
            amea: ['Capital parado sem retorno', 'Taxas de armazenagem e perda de nota Full'],
            oportunidades: ['Promocoes e Ads para girar estoque', 'Otimizar anuncios para aumentar vendas']
        }
    ];
    fullCoverageCardsEl.innerHTML = cards.map(card => {
        const rows = filtrarLinhasCobertura(card.keys);
        const faturamento = somaLinhas(rows, 'valor60');
        const estoqueValor = somaLinhas(rows, 'estoqueValor');
        const giroMedio = rows.filter(item => Number.isFinite(item.diasCobertura) && item.diasCobertura > 0)
            .reduce((acc, item, _idx, arr) => acc + (item.diasCobertura / arr.length), 0);
        return `
            <article class="coverage-card ${card.className}">
                <h3>${escapeHtml(card.title)}</h3>
                <div class="coverage-card-metrics">
                    <div><span>Faturamento</span><strong>${formatarMoeda(faturamento)}</strong></div>
                    <div><span>Estoque</span><strong>${formatarMoeda(estoqueValor)}</strong></div>
                </div>
                <div class="coverage-card-note">
                    <strong>${formatarNumero(rows.length)} SKUs</strong>${giroMedio ? ` - Giro: ${formatarNumero(giroMedio)} dias` : ''}
                    <br><br><strong>Ameacas</strong><br>- ${card.amea.map(escapeHtml).join('<br>- ')}
                    <br><strong>Oportunidades</strong><br>- ${card.oportunidades.map(escapeHtml).join('<br>- ')}
                </div>
            </article>
        `;
    }).join('');
}

function renderResumoCobertura(rows) {
    const totalRevenue = somaLinhas(rows, 'valor60');
    const totalStock = somaLinhas(rows, 'estoqueValor');
    const semEstoque = filtrarLinhasCobertura(['sem_estoque']);
    const risco = filtrarLinhasCobertura(['critico', 'baixo']);
    const ideal = filtrarLinhasCobertura(['ideal']);
    const capital = filtrarLinhasCobertura(['alto', 'muito_alto', 'excessivo', 'sem_venda']);

    coverageRevenueTotalEl.textContent = formatarMoeda(totalRevenue);
    coverageStockTotalEl.textContent = formatarMoeda(totalStock);
    const coberturaAdequada = pct(somaLinhas(ideal, 'valor60'), totalRevenue);
    coverageRevenueSubEl.textContent = `${formatarNumero(coberturaAdequada)}% do faturamento com cobertura ideal.`;
    const giroSaudavel = pct(somaLinhas([...ideal, ...risco], 'estoqueValor'), totalStock);
    coverageTurnoverSubEl.textContent = `${formatarNumero(giroSaudavel)}% do estoque em giro monitorado nos ultimos 60 dias.`;

    renderStack(coverageRevenueStackEl, [
        { className: 'cov-gray', filter: 'sem_estoque', title: 'Sem estoque', valor: totalRevenue ? somaLinhas(semEstoque, 'valor60') : semEstoque.length },
        { className: 'cov-red', filter: 'risco', title: 'Em risco', valor: totalRevenue ? somaLinhas(risco, 'valor60') : risco.length },
        { className: 'cov-green', filter: 'ideal', title: 'Cobertura ideal', valor: totalRevenue ? somaLinhas(ideal, 'valor60') : ideal.length },
        { className: 'cov-blue', filter: 'capital', title: 'Capital imobilizado', valor: totalRevenue ? somaLinhas(capital, 'valor60') : capital.length }
    ], totalRevenue || rows.length);

    renderStack(coverageTurnoverStackEl, [
        { className: 'cov-gray', filter: 'sem_estoque', title: 'Sem estoque', valor: totalStock ? somaLinhas(semEstoque, 'estoqueValor') : semEstoque.length },
        { className: 'cov-red', filter: 'risco', title: 'Em risco', valor: totalStock ? somaLinhas(risco, 'estoqueValor') : risco.length },
        { className: 'cov-green', filter: 'ideal', title: 'Cobertura ideal', valor: totalStock ? somaLinhas(ideal, 'estoqueValor') : ideal.length },
        { className: 'cov-blue', filter: 'capital', title: 'Capital imobilizado', valor: totalStock ? somaLinhas(capital, 'estoqueValor') : capital.length }
    ], totalStock || rows.length);

    renderCardsCobertura();
}

function linhasCoberturaOrdenadas() {
    const ordem = coberturaCategorias;
    return linhasCoberturaFiltradas().sort((a, b) => {
        if (coberturaSort === 'venda') return b.valor60 - a.valor60 || a.sku.localeCompare(b.sku, 'pt-BR', { numeric: true });
        const ordemDiff = (ordem[a.categoria]?.ordem ?? 99) - (ordem[b.categoria]?.ordem ?? 99);
        const diasA = Number.isFinite(a.diasCobertura) ? a.diasCobertura : 99999;
        const diasB = Number.isFinite(b.diasCobertura) ? b.diasCobertura : 99999;
        return ordemDiff || diasA - diasB || a.sku.localeCompare(b.sku, 'pt-BR', { numeric: true });
    });
}

function renderTabelaCobertura() {
    if (!fullCoverageTableBodyEl) return;
    renderHeaderCoberturaFull();
    const rows = linhasCoberturaOrdenadas();
    if (!rows.length) {
        fullCoverageTableBodyEl.innerHTML = `<tr><td colspan="${colspanCoberturaFull()}">${lojaSelecionada ? 'Nenhum SKU encontrado para o filtro de cobertura selecionado.' : 'Selecione uma loja para calcular a cobertura Full.'}</td></tr>`;
        return;
    }
    fullCoverageTableBodyEl.innerHTML = rows.map(item => {
        const row = item.row;
        const categoria = coberturaCategorias[item.categoria] || coberturaCategorias.sem_venda;
        const vendasMensais = mesesCoberturaFull.map(mes => {
            const vendaMes = vendaMensalFull(item.sku, mes.key);
            const semVenda = !vendaMes.qtd && !vendaMes.valor;
            return `
                <td class="num coverage-month-cell ${semVenda ? 'coverage-month-empty' : ''}">
                    <span class="coverage-month-value">${formatarMoeda(vendaMes.valor)}</span>
                    <span class="coverage-month-sub">${formatarNumero(vendaMes.qtd)} un.</span>
                </td>
            `;
        }).join('');
        return `
            <tr>
                <td>
                    <div class="coverage-product">
                        ${foto(row) ? `<img src="${escapeHtml(foto(row))}" alt="">` : '<span class="thumb"></span>'}
                        <span>${escapeHtml(titulo(row))}</span>
                    </div>
                </td>
                <td><span class="coverage-code">${escapeHtml(idAnuncio(row) || '-')}</span></td>
                <td>${escapeHtml(item.sku || '-')}</td>
                <td class="num">${formatarNumero(item.vendas60)}</td>
                ${vendasMensais}
                <td class="num">0</td>
                <td class="num">${formatarNumero(item.estoque)}</td>
                <td><span class="coverage-badge ${item.categoria}">${escapeHtml(categoria.label)}</span> ${escapeHtml(diasCoberturaTexto(item))}</td>
                <td class="num">${formatarMoeda(item.valor60)}</td>
                <td>
                    <div class="coverage-mini-actions">
                        <button type="button" title="Anuncio" data-coverage-action="ads" data-sku="${escapeHtml(item.sku)}" data-mlb="${escapeHtml(idAnuncio(row) || '')}">ADS</button>
                        <button type="button" title="Promocao e margem" data-coverage-action="percent" data-sku="${escapeHtml(item.sku)}" data-mlb="${escapeHtml(idAnuncio(row) || '')}">%</button>
                        <button type="button" title="Grafico" data-coverage-action="chart" data-sku="${escapeHtml(item.sku)}" data-mlb="${escapeHtml(idAnuncio(row) || '')}">|||</button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

function renderCoberturaFull(lista) {
    const base = Array.isArray(lista) ? lista : [];
    coberturaRowsAtuais = montarLinhasCobertura(base);
    renderChipsCobertura(coberturaRowsAtuais);
    renderResumoCobertura(coberturaRowsAtuais);
    renderTabelaCobertura();
    if (!lojaSelecionada) {
        fullCoverageTableBodyEl.innerHTML = `<tr><td colspan="${colspanCoberturaFull()}">Selecione uma loja para ver a saude do estoque Full.</td></tr>`;
        return;
    }
    if (!vendasFull60Ready || !vendasFullMesesReady) {
        fullCoverageTableBodyEl.innerHTML = `<tr><td colspan="${colspanCoberturaFull()}">Carregando vendas Full dos ultimos 6 meses...</td></tr>`;
    }
}

async function carregarCoberturaFull(force = false) {
    mesesCoberturaFull = ultimosSeisMesesFull();
    vendasFull60Ready = false;
    vendasFullMesesReady = false;
    vendasFull60Map = new Map();
    vendasFullMesesMap = new Map();
    renderHeaderCoberturaFull();
    renderCoberturaFull(dados);
    if (!lojaSelecionada) return;
    try {
        const fim = hojeISO();
        const inicio = somarDiasISO(fim, -59);
        const inicioSeisMeses = mesesCoberturaFull[0]?.inicio || inicio;
        const [vendas60, vendasSeisMeses] = await Promise.all([
            buscarVendasFull(inicio, fim, lojaSelecionada, force),
            buscarVendasFull(inicioSeisMeses, fim, lojaSelecionada, force)
        ]);
        vendasFull60Map = montarMapaVendasSku(vendas60);
        vendasFullMesesMap = montarMapaVendasMensaisSku(vendasSeisMeses);
        vendasFull60Ready = true;
        vendasFullMesesReady = true;
        const termo = buscaEl.value.trim().toLowerCase();
        const listaAtual = termo
            ? dados.filter(row => [idAnuncio(row), sku(row), titulo(row), loja(row), statusAnuncio(row)]
                .some(valor => String(valor).toLowerCase().includes(termo)))
            : dados;
        renderCoberturaFull(ordenarLista(listaAtual));
    } catch (e) {
        vendasFull60Ready = true;
        vendasFullMesesReady = true;
        fullCoverageTableBodyEl.innerHTML = `<tr><td colspan="${colspanCoberturaFull()}">Nao foi possivel carregar vendas mensais Full: ${escapeHtml(e.message || e)}</td></tr>`;
    }
}

function exportarCoberturaFull() {
    const rows = linhasCoberturaOrdenadas();
    if (!rows.length) return;
    renderHeaderCoberturaFull();
    const mesesHeader = mesesCoberturaFull.flatMap(mes => [`${mes.label} Qtd`, `${mes.label} Valor`]);
    const header = ['Produto', 'Codigo ML', 'SKU', 'Vendas 60 dias', ...mesesHeader, 'Transito', 'Estoque', 'Cobertura', 'Vendas R$'];
    const linhas = rows.map(item => [
        titulo(item.row),
        idAnuncio(item.row),
        item.sku,
        item.vendas60,
        ...mesesCoberturaFull.flatMap(mes => {
            const vendaMes = vendaMensalFull(item.sku, mes.key);
            return [vendaMes.qtd, vendaMes.valor];
        }),
        0,
        item.estoque,
        diasCoberturaTexto(item),
        item.valor60
    ].map(valor => `"${String(valor ?? '').replace(/"/g, '""')}"`).join(';'));
    const blob = new Blob([[header.join(';'), ...linhas].join('\n')], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `cobertura_full_${lojaSelecionada || 'todas'}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
}

function abrirModuloFullAcao(url, title) {
    try {
        if (window.parent && window.parent !== window) {
            window.parent.postMessage({
                channel: 'jk-open-module-tab',
                payload: { url, title }
            }, '*');
            return;
        }
    } catch (_e) {}
    window.location.href = url;
}

function executarAcaoCobertura(action, skuValor, mlbValor) {
    const qs = new URLSearchParams();
    if (skuValor) qs.set('sku', skuValor);
    if (mlbValor) qs.set('mlb', mlbValor);
    if (action === 'percent') {
        abrirModuloFullAcao(`/frontend_promo.html?${qs.toString()}`, skuValor ? `Promocao ${skuValor}` : 'Promocao ML');
        return;
    }
    if (action === 'chart') {
        abrirModuloFullAcao(`/vendas_sku.html?${qs.toString()}`, skuValor ? `Vendas SKU ${skuValor}` : 'Vendas por SKU');
        return;
    }
    if (action === 'ads') {
        abrirModuloFullAcao(`/anunciosml.html?${qs.toString()}`, skuValor ? `Anuncios ${skuValor}` : 'Anuncios ML');
    }
}

