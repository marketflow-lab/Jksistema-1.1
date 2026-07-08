async function buscarAnunciosFullLoja(nomeLoja, force = false) {
    const nome = String(nomeLoja || '').trim();
    if (!nome) return [];
    const payload = await fetchJsonFullCached(
        chaveCacheFull('anuncios', [nome]),
        `/api/full/anuncios?loja=${encodeURIComponent(nome)}`,
        { ttlMs: FULL_CACHE_TTL_MS, force }
    );
    const lista = Array.isArray(payload) ? payload : (Array.isArray(payload.results) ? payload.results : []);
    return lista.map(item => ({ ...item, loja_sync: loja(item) === '-' ? nome : loja(item) }));
}

async function buscarAnunciosCurvaAbc(conta, force = false) {
    const contaSelecionada = String(conta || CONTA_TODAS_FULL);
    if (contaSelecionada !== CONTA_TODAS_FULL) return buscarAnunciosFullLoja(contaSelecionada, force);
    const lojas = lojasMl.map(item => String(item.nome || '').trim()).filter(Boolean);
    if (!lojas.length) return [];
    const resultados = await Promise.allSettled(lojas.map(nome => buscarAnunciosFullLoja(nome, force)));
    const falhas = resultados.filter(item => item.status === 'rejected');
    if (falhas.length && falhas.length === resultados.length) {
        throw new Error('Nao foi possivel consultar os anuncios Full das contas.');
    }
    return resultados.flatMap(item => item.status === 'fulfilled' ? item.value : []);
}

function periodoCurvaAbc() {
    const fim = hojeISO();
    return [
        { key: 'p12090', label: '120/90', inicio: somarDiasISO(fim, -119), fim: somarDiasISO(fim, -90) },
        { key: 'p9060', label: '90/60', inicio: somarDiasISO(fim, -89), fim: somarDiasISO(fim, -60) },
        { key: 'p6030', label: '60/30', inicio: somarDiasISO(fim, -59), fim: somarDiasISO(fim, -30) },
        { key: 'p300', label: '30/0', inicio: somarDiasISO(fim, -29), fim }
    ];
}

function aplicarClasseAbc(rows, periodKey) {
    const total = rows.reduce((acc, item) => acc + Number(item.periodos[periodKey]?.[abcOrder] || 0), 0);
    const ordenados = rows.slice().sort((a, b) => Number(b.periodos[periodKey]?.[abcOrder] || 0) - Number(a.periodos[periodKey]?.[abcOrder] || 0));
    let acumulado = 0;
    ordenados.forEach(item => {
        const metrica = Number(item.periodos[periodKey]?.[abcOrder] || 0);
        if (!total || metrica <= 0) {
            item.periodos[periodKey].classe = 'O';
            return;
        }
        const antes = acumulado / total;
        acumulado += metrica;
        if (antes < 0.8) item.periodos[periodKey].classe = 'A';
        else if (antes < 0.95) item.periodos[periodKey].classe = 'B';
        else item.periodos[periodKey].classe = 'C';
    });
}

function montarRowsCurvaAbc(anuncios, mapasPorPeriodo) {
    const periodos = periodoCurvaAbc();
    const rows = anuncios.map(row => {
        const key = chaveSkuFull(row);
        const periodosRow = {};
        periodos.forEach(periodo => {
            const venda = mapasPorPeriodo[periodo.key]?.get(key) || { qtd: 0, valor: 0 };
            periodosRow[periodo.key] = {
                valor: venda.valor || 0,
                quantidade: venda.qtd || 0,
                classe: 'O'
            };
        });
        return { row, sku: key, periodos: periodosRow };
    });
    periodos.forEach(periodo => aplicarClasseAbc(rows, periodo.key));
    return rows;
}

function ordenarRowsAbc(rows) {
    return rows.slice().sort((a, b) => {
        const atualA = Number(a.periodos.p300?.[abcOrder] || 0);
        const atualB = Number(b.periodos.p300?.[abcOrder] || 0);
        return atualB - atualA || String(a.sku || '').localeCompare(String(b.sku || ''), 'pt-BR', { numeric: true });
    });
}

function renderCelulaPeriodoAbc(item, periodKey) {
    const data = item.periodos[periodKey] || { valor: 0, quantidade: 0, classe: 'O' };
    const letra = String(data.classe || 'O').toLowerCase();
    return `
        <div class="abc-period">
            <span class="abc-letter ${letra}">${escapeHtml(data.classe || 'O')}</span>
            <div class="abc-values">
                <strong>${formatarMoeda(data.valor)}</strong>
                <span>Unidades: ${formatarNumero(data.quantidade)}</span>
            </div>
        </div>
    `;
}

function renderCurvaAbc() {
    if (!fullAbcTableBodyEl) return;
    const rows = ordenarRowsAbc(abcRows);
    if (!rows.length) {
        fullAbcTableBodyEl.innerHTML = '<tr><td colspan="6">Nenhum anuncio Full encontrado para montar a Curva ABC.</td></tr>';
        return;
    }
    const periodos = periodoCurvaAbc();
    const totalRow = `
        <tr class="abc-total-row">
            <td>Totais</td>
            ${periodos.map(periodo => {
                const valor = rows.reduce((acc, item) => acc + Number(item.periodos[periodo.key]?.valor || 0), 0);
                const qtd = rows.reduce((acc, item) => acc + Number(item.periodos[periodo.key]?.quantidade || 0), 0);
                return `<td>${formatarMoeda(valor)}<br>Unidades: ${formatarNumero(qtd)}</td>`;
            }).join('')}
            <td>Ordenado por ${abcOrder === 'valor' ? 'valor' : 'quantidade'}</td>
        </tr>
    `;
    fullAbcTableBodyEl.innerHTML = totalRow + rows.map(item => {
        const row = item.row;
        return `
            <tr>
                <td>
                    <div class="abc-product">
                        ${foto(row) ? `<img src="${escapeHtml(foto(row))}" alt="">` : '<span class="thumb"></span>'}
                        <div>
                            <span class="abc-code">${escapeHtml(idAnuncio(row) || '-')}</span>
                            <span class="abc-status">${escapeHtml(statusAnuncio(row) || '-')}</span>
                            <div class="abc-price">Preco: ${formatarMoeda(preco(row))}</div>
                            <button class="abc-promo" type="button" data-coverage-action="percent" data-sku="${escapeHtml(item.sku)}" data-mlb="${escapeHtml(idAnuncio(row) || '')}">Promocoes</button>
                        </div>
                    </div>
                </td>
                ${periodos.map(periodo => `<td>${renderCelulaPeriodoAbc(item, periodo.key)}</td>`).join('')}
                <td>
                    <div class="abc-actions">
                        <button type="button" data-coverage-action="ads" data-sku="${escapeHtml(item.sku)}" data-mlb="${escapeHtml(idAnuncio(row) || '')}">ADS</button>
                        <button type="button" data-coverage-action="chart" data-sku="${escapeHtml(item.sku)}" data-mlb="${escapeHtml(idAnuncio(row) || '')}">|||</button>
                        <button type="button" data-coverage-action="percent" data-sku="${escapeHtml(item.sku)}" data-mlb="${escapeHtml(idAnuncio(row) || '')}">%</button>
                    </div>
                </td>
            </tr>
        `;
    }).join('');
}

async function carregarCurvaAbcFull(force = false) {
    abcCarregado = true;
    const conta = fullAbcAccountEl ? String(fullAbcAccountEl.value || CONTA_TODAS_FULL) : CONTA_TODAS_FULL;
    if (btnAplicarAbcFull) btnAplicarAbcFull.disabled = true;
    setAbcStatus('Carregando anuncios e vendas para Curva ABC...');
    if (fullAbcTableBodyEl) fullAbcTableBodyEl.innerHTML = '<tr><td colspan="6">Carregando Curva ABC...</td></tr>';
    try {
        if (!lojasMl.length) await carregarLojasMl(force);
        const periodos = periodoCurvaAbc();
        const [anuncios, ...vendasPorPeriodo] = await Promise.all([
            buscarAnunciosCurvaAbc(conta, force),
            ...periodos.map(periodo => buscarVendasFull(periodo.inicio, periodo.fim, conta, force))
        ]);
        const mapasPorPeriodo = {};
        periodos.forEach((periodo, index) => {
            mapasPorPeriodo[periodo.key] = montarMapaVendasSku(vendasPorPeriodo[index] || []);
        });
        abcRows = montarRowsCurvaAbc(anuncios, mapasPorPeriodo);
        renderCurvaAbc();
        setAbcStatus(`${abcRows.length} anuncio(s) Full analisados na Curva ABC.`);
    } catch (e) {
        abcRows = [];
        renderCurvaAbc();
        setAbcStatus(e && e.message ? `Erro ao carregar Curva ABC: ${e.message}` : 'Erro ao carregar Curva ABC.', 'error');
    } finally {
        if (btnAplicarAbcFull) btnAplicarAbcFull.disabled = false;
    }
}
