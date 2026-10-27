// Carregar lojas e vendas salvas automaticamente
carregarPreferenciaGrafico();
if (data_inicio_param && data_fim_param) {
    periodoGrafico = 'manual';
}
agendarSegundoPlano(() => iaRestaurarOuCriarConversa().catch(() => {}), 900);
aplicarPreferenciaGraficoUI();
const restauracaoInicialVendas = (async () => {
    const restaurouLista = typeof restaurarCacheTelaVendasSeValido === 'function'
        ? await restaurarCacheTelaVendasSeValido({ restaurarFiltro: true }) : false;
    if (typeof restaurarCacheGraficoVendasSeValido === 'function') await restaurarCacheGraficoVendasSeValido();
    return restaurouLista;
})().catch(() => false);
carregarLojas()
    .then(async () => {
        await restauracaoInicialVendas;
        const periodoNormalizado = await normalizarPeriodoGraficoRestaurado();
        if (
            periodoNormalizado === false
            && !periodoGraficoAtalhoPendente
            && periodoGrafico === 'max'
            && !periodoPrefSalvo?.origem
            && getDataIniISO() === periodoPrefSalvo?.inicio
            && getDataFimISO() === periodoPrefSalvo?.fim
        ) {
            await reaplicarPeriodoGraficoAposMudancaFiltro();
        }
        if (retornoDoSku) {
            statusEl.className = 'status-bar loading';
            statusEl.innerHTML = `${spinnerHtml}Retornando da visão SKU...`;
            void verificarSyncEmAndamentoNaEntrada();
        } else {
            void verificarSyncEmAndamentoNaEntrada();
        }

        if (getDataIniISO() && getDataFimISO()) {
            // Reads of confirmed data do not depend on synchronization status or each other.
            agendarSegundoPlano(() => carregarGrafico(), 100);
            return carregarVendas({ retornoRapido: retornoDoSku });
        }
        statusEl.className = 'status-bar';
        statusEl.textContent = '';
        return null;
    })
    .catch(e => {
        statusEl.className = 'status-bar error';
        statusEl.textContent = `Erro ao carregar dados: ${e.message}`;
    });
