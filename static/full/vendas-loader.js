async function carregarVendasFull(force = false) {
    vendasFullCarregado = true;
    setSalesStatus('Carregando vendas Full...');
    if (btnAplicarVendasFull) btnAplicarVendasFull.disabled = true;
    try {
        const hoje = hojeISO();
        const ontem = somarDiasISO(hoje, -1);
        const semanaPassada = somarDiasISO(hoje, -7);
        const mesPassado = mesmoDiaMesAnteriorISO(hoje);
        const anoPassado = mesmoDiaAnoAnteriorISO(hoje);

        const [vendasHoje, vendasOntem, vendasSemana, vendasMes, vendasAno] = await Promise.all([
            buscarVendasFull(hoje, hoje, '', force),
            buscarVendasFull(ontem, ontem, '', force),
            buscarVendasFull(semanaPassada, semanaPassada, '', force),
            buscarVendasFull(mesPassado, mesPassado, '', force),
            buscarVendasFull(anoPassado, anoPassado, '', force)
        ]);

        const totalHoje = totalVendido(vendasHoje);
        const totalOntem = totalVendido(vendasOntem);
        const pedidosHoje = totalPedidos(vendasHoje);
        const ticket = pedidosHoje ? totalHoje / pedidosHoje : 0;
        const progresso = percentualDiaDecorrido();
        const projecaoLinear = totalHoje ? totalHoje / (progresso / 100) : 0;
        const projecaoAvancada = totalHoje ? (projecaoLinear * 0.68) + (Math.max(totalOntem, totalHoje) * 0.32) : 0;

        fullSalesTodayEl.textContent = formatarMoeda(totalHoje);
        fullSalesOrdersEl.textContent = pedidosHoje.toLocaleString('pt-BR');
        fullSalesTicketEl.textContent = formatarMoeda(ticket);
        fullSalesProjectionEl.textContent = formatarMoeda(projecaoLinear);
        fullSalesForecastEl.textContent = formatarMoeda(projecaoAvancada);
        fullDayProgressFillEl.style.width = `${progresso.toFixed(1)}%`;
        fullDayProgressLabelEl.textContent = `${progresso.toFixed(1)}% do dia decorrido`;

        renderPercentual(fullSalesTrendEl, variacaoPercentual(totalHoje, totalOntem));
        renderPercentual(fullSalesWeekComparisonEl, variacaoPercentual(totalHoje, totalVendido(vendasSemana)));
        renderPercentual(fullSalesMonthComparisonEl, variacaoPercentual(totalHoje, totalVendido(vendasMes)));
        renderPercentual(fullSalesYearComparisonEl, variacaoPercentual(totalHoje, totalVendido(vendasAno)));
        renderGraficoVendasFull(serieHoraria(vendasHoje), serieHoraria(vendasOntem));

        const conta = fullSalesAccountEl && fullSalesAccountEl.value !== CONTA_TODAS_FULL ? ` em ${fullSalesAccountEl.value}` : '';
        setSalesStatus(`Vendas Full atualizadas para hoje${conta}.`);
    } catch (e) {
        setSalesStatus(e && e.message ? `Erro ao carregar vendas Full: ${e.message}` : 'Erro ao carregar vendas Full.', 'error');
    } finally {
        if (btnAplicarVendasFull) btnAplicarVendasFull.disabled = false;
    }
}
