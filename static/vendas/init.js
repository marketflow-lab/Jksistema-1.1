// Carregar lojas e vendas salvas automaticamente
carregarPreferenciaGrafico();
if (data_inicio_param && data_fim_param) {
    periodoGrafico = 'manual';
}
agendarSegundoPlano(() => iaRestaurarOuCriarConversa().catch(() => {}), 900);
aplicarPreferenciaGraficoUI();
carregarLojas()
    .then(async () => {
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
            const restaurouCache = await restaurarCacheTelaVendasSeValido();
            if (restaurouCache) {
                agendarSegundoPlano(() => carregarGrafico(), 100);
                return null;
            }
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
